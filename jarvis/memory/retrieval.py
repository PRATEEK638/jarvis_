"""Retrieval fabric (vision pack 10).

The document asks for a *fabric*, not one vector search, and is explicit about
why (Part 18): "Build a Retrieval Fabric, NOT one vector database."

Implemented here, as real algorithms rather than library calls:

  BM25              - Okapi BM25 lexical scoring with the standard k1/b
  Dense             - cosine over local embeddings (memory/embeddings.py)
  Hybrid + RRF      - Reciprocal Rank Fusion over both rankings
  MMR               - Maximal Marginal Relevance for diversity
  Query expansion   - deterministic morphological expansion
  Budgeting/stopping- stop when marginal gain falls below a floor
  Evidence coverage - how much of the query the result set actually addresses

Why these and not the rest: BM25, RRF and MMR are exact, cheap and need no
extra model, so they are honest wins today. Cross-encoder reranking, ColBERT
late interaction, HyDE and Self-RAG each need another model or another LLM
round trip, so they are left as declared extension points rather than
half-built - see docs/VISION_STATUS.md.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Sequence

# BM25's usual defaults. k1 controls term-frequency saturation, b how much
# document length is penalised.
BM25_K1 = 1.5
BM25_B = 0.75

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "of", "to", "in", "on",
    "for", "and", "or", "my", "your", "me", "you", "i", "it", "that", "this",
    "what", "which", "who", "do", "does", "did", "with", "at", "by", "from",
    "as", "about",
}


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower())
            if w not in _STOP and len(w) > 1]


_DOUBLE_OK = set("bdfglmnprt")     # consonants English actually doubles
_ES_AFTER = ("s", "x", "z", "ch", "sh")


def _stem(word: str) -> str | None:
    """Strip one inflection, applying the two rules naive stripping gets wrong.

    Both were caught by tests rather than reasoned about in advance:
      "running" -> "runn" unless the doubled consonant is collapsed -> "run"
      "files"   -> "fil"  unless "es" is only stripped after s/x/z/ch/sh,
                          so plain "s" applies instead -> "file"
    """
    if word.endswith("ing") and len(word) >= 6:
        base = word[:-3]
        if len(base) >= 4 and base[-1] == base[-2] and base[-1] in _DOUBLE_OK:
            base = base[:-1]                  # running -> runn -> run
        return base
    if word.endswith("ed") and len(word) >= 5:
        base = word[:-2]
        if len(base) >= 4 and base[-1] == base[-2] and base[-1] in _DOUBLE_OK:
            base = base[:-1]
        return base
    if word.endswith("es") and len(word) >= 5:
        # "boxes" -> "box", but "files" must not become "fil".
        if any(word[:-2].endswith(end) for end in _ES_AFTER):
            return word[:-2]
        return word[:-1]                      # files -> file
    if word.endswith("s") and not word.endswith("ss") and len(word) >= 4:
        return word[:-1]
    return None


def expand(query: str) -> list[str]:
    """Cheap morphological expansion, no model required.

    "running" also matches "run"; "file" also matches "files". Deterministic on
    purpose - an LLM rewrite is a separate technique with a separate cost.
    """
    terms = tokenize(query)
    extra: set[str] = set()
    for t in terms:
        stem = _stem(t)
        if stem and len(stem) >= 3:
            extra.add(stem)
        extra.add(t + "s")
    return sorted(set(terms) | extra)


@dataclass
class Doc:
    """One retrievable thing, whatever its source."""

    id: str
    text: str
    source: str = "memory"
    vector: list[float] | None = None
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = tokenize(self.text)


@dataclass
class Scored:
    doc: Doc
    score: float
    how: str            # which retriever produced this


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------

class BM25:
    """Okapi BM25. Exact, cheap, and strong on rare exact terms - which is
    precisely where dense embeddings are weakest (identifiers, error codes,
    file names)."""

    def __init__(self, docs: Sequence[Doc]) -> None:
        self.docs = list(docs)
        self.n = len(self.docs)
        self.doc_freq: Counter[str] = Counter()
        for d in self.docs:
            self.doc_freq.update(set(d.tokens))
        self.avg_len = (sum(len(d.tokens) for d in self.docs) / self.n
                        if self.n else 0.0)

    def _idf(self, term: str) -> float:
        df = self.doc_freq.get(term, 0)
        if df == 0:
            return 0.0
        # The +0.5 smoothing keeps a term appearing in every document from
        # going negative, which the unsmoothed form does.
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def search(self, query: str, limit: int = 10) -> list[Scored]:
        if not self.n:
            return []
        terms = expand(query)
        out: list[Scored] = []
        for doc in self.docs:
            if not doc.tokens:
                continue
            counts = Counter(doc.tokens)
            dl = len(doc.tokens)
            score = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                denom = tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / max(self.avg_len, 1e-9))
                score += self._idf(term) * (tf * (BM25_K1 + 1)) / denom
            if score > 0:
                out.append(Scored(doc=doc, score=score, how="bm25"))
        out.sort(key=lambda s: -s.score)
        return out[:limit]


# ---------------------------------------------------------------------------
# Fusion and diversity
# ---------------------------------------------------------------------------

RRF_K = 60          # the constant from the original RRF paper


def reciprocal_rank_fusion(rankings: Sequence[Sequence[Scored]],
                           limit: int = 10) -> list[Scored]:
    """Combine rankings by position, not by score.

    This is why RRF is the right way to fuse BM25 with dense retrieval: their
    scores are on incomparable scales (BM25 is unbounded, cosine is -1..1), so
    averaging them is meaningless. Rank is comparable.
    """
    fused: dict[str, float] = {}
    best: dict[str, Doc] = {}
    hows: dict[str, set[str]] = {}
    for ranking in rankings:
        for position, scored in enumerate(ranking, start=1):
            key = scored.doc.id
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + position)
            best.setdefault(key, scored.doc)
            hows.setdefault(key, set()).add(scored.how)
    merged = [Scored(doc=best[k], score=v, how="+".join(sorted(hows[k])))
              for k, v in fused.items()]
    merged.sort(key=lambda s: -s.score)
    return merged[:limit]


def _overlap(a: Doc, b: Doc) -> float:
    """Jaccard similarity, used as the cheap redundancy measure for MMR."""
    sa, sb = set(a.tokens), set(b.tokens)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def mmr(candidates: Sequence[Scored], *, limit: int = 5,
        diversity: float = 0.3) -> list[Scored]:
    """Maximal Marginal Relevance: relevance minus redundancy.

    Without this, top-k over near-duplicate memories returns the same fact
    five times and crowds out the second useful one.
    """
    pool = list(candidates)
    chosen: list[Scored] = []
    while pool and len(chosen) < limit:
        best_i, best_val = 0, -1e9
        for i, cand in enumerate(pool):
            redundancy = max((_overlap(cand.doc, c.doc) for c in chosen),
                             default=0.0)
            value = (1 - diversity) * cand.score - diversity * redundancy
            if value > best_val:
                best_i, best_val = i, value
        chosen.append(pool.pop(best_i))
    return chosen


# ---------------------------------------------------------------------------
# Budgeting, stopping, coverage
# ---------------------------------------------------------------------------

def stop_early(results: Sequence[Scored], *, floor: float = 0.35,
               max_items: int = 5, retrievers_used: int = 1) -> list[Scored]:
    """Retrieval stopping: keep taking results until they stop being useful.

    A fixed top-k either pads the context with noise or truncates a genuinely
    rich answer.

    A score floor alone does not work on fused results, and it is worth being
    precise about why: RRF scores are 1/(60+rank), so rank 1 scores 0.0164 and
    rank 2 scores 0.0161. Every result looks equally good, and a ratio test
    keeps everything. Measured directly - "rate limited 429" returned the
    correct document first and then two unrelated ones that all passed a 0.35
    floor.

    Consensus is the discriminating signal instead: when two retrievers ran, a
    document only one of them found is weak evidence, unless it is the single
    best result. The floor still applies to the single-retriever case, where
    raw scores are on one comparable scale.
    """
    if not results:
        return []
    if retrievers_used > 1:
        agreed = [r for r in results[:max_items] if "+" in r.how]
        return agreed or [results[0]]
    best = results[0].score
    kept = [r for r in results[:max_items] if best <= 0 or r.score >= best * floor]
    return kept or [results[0]]


def coverage(query: str, results: Sequence[Scored]) -> float:
    """Evidence coverage: what fraction of the query's content words appear
    anywhere in the retrieved set. A low number means the answer is probably
    unsupported, whatever the scores say."""
    terms = set(tokenize(query))
    if not terms:
        return 1.0
    seen: set[str] = set()
    for r in results:
        seen |= set(r.doc.tokens)
    return round(len(terms & seen) / len(terms), 3)


# ---------------------------------------------------------------------------
# The fabric
# ---------------------------------------------------------------------------

class RetrievalFabric:
    """Hybrid retrieval over a document set, with diversity and stopping."""

    def __init__(self, docs: Sequence[Doc],
                 embed: Callable[[str], list[float] | None] | None = None,
                 cosine: Callable[[list[float], list[float]], float] | None = None
                 ) -> None:
        self.docs = list(docs)
        self.bm25 = BM25(self.docs)
        self._embed = embed
        self._cosine = cosine

    def dense(self, query: str, limit: int = 10) -> list[Scored]:
        if self._embed is None or self._cosine is None:
            return []
        qv = self._embed(query)
        if qv is None:
            return []
        out = []
        for doc in self.docs:
            if not doc.vector:
                continue
            out.append(Scored(doc=doc, score=self._cosine(qv, doc.vector),
                              how="dense"))
        out.sort(key=lambda s: -s.score)
        return out[:limit]

    def search(self, query: str, *, limit: int = 5,
               diversity: float = 0.3) -> dict[str, object]:
        """Hybrid: BM25 + dense, fused by RRF, diversified by MMR, then cut
        where the marginal gain dies. Returns the results and the numbers
        needed to judge whether to trust them."""
        lexical = self.bm25.search(query, limit=limit * 4)
        vector = self.dense(query, limit=limit * 4)

        rankings = [r for r in (lexical, vector) if r]
        if not rankings:
            return {"results": [], "coverage": 0.0, "retrievers": [],
                    "fused": 0}
        fused = (reciprocal_rank_fusion(rankings, limit=limit * 3)
                 if len(rankings) > 1 else rankings[0][:limit * 3])
        diversified = mmr(fused, limit=limit, diversity=diversity)
        final = stop_early(diversified, max_items=limit,
                           retrievers_used=len(rankings))
        return {
            "results": final,
            "coverage": coverage(query, final),
            "retrievers": (["bm25"] if lexical else []) + (["dense"] if vector else []),
            "fused": len(fused),
        }
