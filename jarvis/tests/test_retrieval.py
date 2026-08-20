"""Retrieval fabric tests.

These assert the properties each algorithm is *for*, not merely that it runs:
BM25 must beat embeddings on rare exact tokens, RRF must fuse incomparable
score scales, MMR must suppress duplicates, and stopping must cut noise.
"""

from __future__ import annotations

import pytest

from jarvis.memory.retrieval import (
    BM25,
    Doc,
    RetrievalFabric,
    Scored,
    coverage,
    expand,
    mmr,
    reciprocal_rank_fusion,
    stop_early,
    tokenize,
)

CORPUS = [
    "my roll number is 21CS1234",
    "the wifi password for the lab is bluebird92",
    "the professor for DBMS is Dr Mehta",
    "HTTP status 429 means too many requests, you are being rate limited",
    "the DBMS lab is on the third floor of block C",
    "my student ID card expires in June",
]


@pytest.fixture
def docs():
    return [Doc(id=str(i), text=t) for i, t in enumerate(CORPUS)]


class TestTokenizing:
    def test_stopwords_and_single_characters_are_dropped(self):
        assert tokenize("the a of my file") == ["file"]

    def test_expansion_covers_simple_morphology(self):
        out = set(expand("running files"))
        assert "run" in out and "file" in out


class TestBM25:
    def test_finds_a_rare_exact_token(self, docs):
        """The case embeddings are weakest on: an identifier or error code."""
        hits = BM25(docs).search("429", limit=3)
        assert hits, "BM25 returned nothing for an exact token"
        assert "429" in hits[0].doc.text

    def test_scores_are_positive_and_ordered(self, docs):
        hits = BM25(docs).search("DBMS lab", limit=5)
        assert hits
        assert all(h.score > 0 for h in hits)
        assert hits == sorted(hits, key=lambda h: -h.score)

    def test_absent_term_returns_nothing(self, docs):
        assert BM25(docs).search("kubernetes", limit=5) == []

    def test_empty_corpus_is_safe(self):
        assert BM25([]).search("anything") == []


class TestFusion:
    def test_rrf_rewards_agreement_between_rankings(self, docs):
        a = [Scored(docs[0], 9.9, "bm25"), Scored(docs[1], 5.0, "bm25")]
        b = [Scored(docs[1], 0.9, "dense"), Scored(docs[2], 0.8, "dense")]
        fused = reciprocal_rank_fusion([a, b], limit=3)
        # docs[1] is the only one both rankings contain, so it must win even
        # though it is top in neither - that is the point of RRF.
        assert fused[0].doc.id == docs[1].id
        assert "+" in fused[0].how

    def test_fusion_ignores_incomparable_score_scales(self, docs):
        """BM25 is unbounded, cosine is -1..1. Rank, not score, must decide."""
        huge = [Scored(docs[0], 1000.0, "bm25")]
        small = [Scored(docs[0], 0.01, "dense")]
        fused = reciprocal_rank_fusion([huge, small], limit=1)
        assert fused[0].score == pytest.approx(2 / 61)


class TestDiversity:
    def test_mmr_suppresses_a_near_duplicate(self):
        a = Doc(id="a", text="the DBMS lab is on the third floor")
        dup = Doc(id="b", text="the DBMS lab is on the third floor indeed")
        other = Doc(id="c", text="my roll number is 21CS1234")
        picked = mmr([Scored(a, 1.0, "x"), Scored(dup, 0.99, "x"),
                      Scored(other, 0.5, "x")], limit=2, diversity=0.6)
        assert {s.doc.id for s in picked} == {"a", "c"}


class TestStopping:
    def test_consensus_filters_single_retriever_noise(self, docs):
        results = [Scored(docs[0], 0.0164, "bm25+dense"),
                   Scored(docs[1], 0.0161, "dense"),
                   Scored(docs[2], 0.0159, "dense")]
        kept = stop_early(results, max_items=3, retrievers_used=2)
        assert len(kept) == 1 and kept[0].how == "bm25+dense"

    def test_single_retriever_falls_back_to_a_score_floor(self, docs):
        results = [Scored(docs[0], 1.0, "bm25"), Scored(docs[1], 0.9, "bm25"),
                   Scored(docs[2], 0.01, "bm25")]
        kept = stop_early(results, max_items=3, retrievers_used=1)
        assert len(kept) == 2

    def test_never_returns_empty_when_something_matched(self, docs):
        results = [Scored(docs[0], 0.001, "dense")]
        assert stop_early(results, retrievers_used=2)


class TestCoverage:
    def test_full_coverage_when_every_term_appears(self, docs):
        assert coverage("roll number", [Scored(docs[0], 1.0, "x")]) == 1.0

    def test_zero_coverage_is_reported_honestly(self, docs):
        assert coverage("kubernetes helm", [Scored(docs[0], 1.0, "x")]) == 0.0


class TestFabric:
    def test_lexical_only_still_works_without_embeddings(self, docs):
        out = RetrievalFabric(docs).search("429", limit=3)
        assert out["retrievers"] == ["bm25"]
        assert out["results"] and "429" in out["results"][0].doc.text

    def test_empty_result_is_reported_not_faked(self, docs):
        out = RetrievalFabric(docs).search("kubernetes", limit=3)
        assert out["results"] == [] and out["coverage"] == 0.0
