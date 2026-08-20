"""Persistent memory.

Three of the architecture's memory types are genuinely implemented here:
  working  — the current goal's context, in-process only
  episodic — every completed task, on disk, queryable
  semantic — durable facts the user asked JARVIS to remember

The remaining seventeen types declared in contracts.MemoryType are NOT
IMPLEMENTED; attempting to write one raises rather than silently discarding it.

Storage is SQLite so memory survives process restarts — the whole point of
claiming persistence. Semantic recall ranks by cosine similarity over local
embeddings (jarvis/memory/embeddings.py, Ollama's nomic-embed-text - genuine
on-device ML, not an API call) when that model is reachable, and falls back to
keyword/overlap scoring when it is not. Never fabricated: an unavailable
embedder degrades to the older method rather than silently returning nothing.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any

from jarvis.config import settings
from jarvis.memory import embeddings
from jarvis.core.contracts import (
    IMPLEMENTED_MEMORY_TYPES,
    MemoryRecord,
    MemoryType,
    TaskRecord,
)

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "of", "to", "in",
    "on", "for", "and", "or", "my", "your", "me", "you", "i", "it", "that",
    "this", "what", "which", "who", "whom", "do", "does", "did", "with", "at",
    "by", "from", "as", "about", "remember", "recall", "tell", "again",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


class MemoryStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = str(path or settings.DB_FILE)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id          TEXT PRIMARY KEY,
                type        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  REAL NOT NULL,
                source      TEXT,
                confidence  REAL,
                provenance  TEXT,
                tags        TEXT,
                embedding   TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);

            CREATE TABLE IF NOT EXISTS tasks (
                goal_id     TEXT PRIMARY KEY,
                objective   TEXT NOT NULL,
                created_at  REAL NOT NULL,
                ok          INTEGER NOT NULL,
                status      TEXT,
                tier        TEXT,
                message     TEXT,
                payload     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_task_created ON tasks(created_at);
            """
        )
        self._conn.commit()
        self._migrate_add_embedding_column()

    def _migrate_add_embedding_column(self) -> None:
        """A store created before embeddings existed lacks this column.

        SQLite has no "ADD COLUMN IF NOT EXISTS", so the existing columns are
        checked first - this must be safe to run against both a fresh database
        and one already carrying real remembered facts from a previous run.
        """
        cols = {row["name"] for row in
               self._conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "embedding" not in cols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN embedding TEXT")
            self._conn.commit()

    # -- semantic ----------------------------------------------------------

    def remember(self, content: str, *, source: str = "user",
                 tags: list[str] | None = None,
                 memory_type: MemoryType = MemoryType.SEMANTIC,
                 confidence: float = 1.0,
                 provenance: str = "") -> MemoryRecord:
        if memory_type not in IMPLEMENTED_MEMORY_TYPES:
            raise NotImplementedError(
                f"Memory type '{memory_type.value}' is declared in the "
                f"architecture but NOT IMPLEMENTED in this build. "
                f"Implemented: {sorted(t.value for t in IMPLEMENTED_MEMORY_TYPES)}"
            )
        record = MemoryRecord(type=memory_type, content=content.strip(),
                              source=source, tags=tags or [],
                              confidence=confidence, provenance=provenance)
        # Best-effort: a fact is still worth remembering even if the embedder
        # is unreachable right now. recall() falls back to keyword scoring for
        # any row where this is NULL, so a down embedder never loses data.
        vector = embeddings.embed(record.content)
        self._conn.execute(
            "INSERT INTO memories (id, type, content, created_at, source, "
            "confidence, provenance, tags, embedding) VALUES (?,?,?,?,?,?,?,?,?)",
            (record.id, record.type.value, record.content, record.created_at,
             record.source, record.confidence, record.provenance,
             json.dumps(record.tags),
             json.dumps(vector) if vector is not None else None),
        )
        self._conn.commit()
        return record

    def recall(self, query: str, *, limit: int = 5,
               memory_type: MemoryType = MemoryType.SEMANTIC) -> list[MemoryRecord]:
        """Rank stored facts against the query.

        Prefers real semantic similarity (local embeddings) over surface word
        overlap, e.g. "what's my student number" now matches a fact stored as
        "roll number is 21CS1234" despite sharing no words. Falls back to the
        keyword method per-row when a row has no stored embedding (embedder was
        down when it was written) or when the embedder is unreachable right
        now (query-time), so recall degrades gracefully rather than failing.
        """
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE type = ? ORDER BY created_at DESC",
            (memory_type.value,),
        ).fetchall()
        if not rows:
            return []

        query_vector = embeddings.embed(query)
        q_tokens = _tokens(query)
        # Cosine similarity (roughly 0-1 for related text) and Jaccard overlap
        # (0-1) happen to share a scale, but they are not the same measurement,
        # so each row is tagged with which one scored it and filtered on its
        # own terms rather than compared directly to the other's threshold.
        MIN_SEMANTIC_SCORE = 0.35   # below this, an embedding match is noise
        scored: list[tuple[float, MemoryRecord]] = []

        for row in rows:
            record = self._row_to_memory(row)
            raw_vector = row["embedding"] if "embedding" in row.keys() else None

            if query_vector is not None and raw_vector:
                try:
                    row_vector = json.loads(raw_vector)
                except json.JSONDecodeError:
                    row_vector = None
                if row_vector:
                    similarity = embeddings.cosine(query_vector, row_vector)
                    if similarity < MIN_SEMANTIC_SCORE:
                        continue
                    age_days = (time.time() - record.created_at) / 86400
                    scored.append(
                        (similarity + max(0.0, 0.02 - 0.0005 * age_days), record))
                    continue

            # Fallback: keyword/overlap scoring, unchanged from before embeddings
            # - used whenever this row has no embedding, or none could be
            # computed for the query right now.
            c_tokens = _tokens(record.content)
            if not c_tokens:
                continue
            overlap = len(q_tokens & c_tokens)
            if overlap == 0 and query.lower().strip() not in record.content.lower():
                continue
            score = overlap / max(len(q_tokens | c_tokens), 1)
            age_days = (time.time() - record.created_at) / 86400
            score += max(0.0, 0.05 - 0.001 * age_days)
            scored.append((score, record))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [record for _, record in scored[:limit]]

    def all_memories(self, memory_type: MemoryType | None = None,
                     limit: int = 100) -> list[MemoryRecord]:
        if memory_type:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE type = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (memory_type.value, limit)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def forget(self, memory_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"], type=MemoryType(row["type"]), content=row["content"],
            created_at=row["created_at"], source=row["source"] or "",
            confidence=row["confidence"] if row["confidence"] is not None else 1.0,
            provenance=row["provenance"] or "",
            tags=json.loads(row["tags"] or "[]"),
        )

    # -- episodic ----------------------------------------------------------

    def record_task(self, task: TaskRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO tasks (goal_id, objective, created_at, ok, "
            "status, tier, message, payload) VALUES (?,?,?,?,?,?,?,?)",
            (task.goal.id, task.goal.objective, task.goal.created_at,
             1 if task.ok else 0, task.goal.status,
             task.trace.tier_chosen.value if task.trace.tier_chosen else None,
             task.message, task.model_dump_json()),
        )
        self._conn.commit()

    def recent_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT goal_id, objective, created_at, ok, status, tier, message "
            "FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        total = self._conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
        ok = self._conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE ok = 1").fetchone()["c"]
        facts = self._conn.execute(
            "SELECT COUNT(*) c FROM memories WHERE type = 'semantic'").fetchone()["c"]
        by_tier = {
            r["tier"] or "unknown": r["c"] for r in self._conn.execute(
                "SELECT tier, COUNT(*) c FROM tasks GROUP BY tier").fetchall()
        }
        return {"tasks_total": total, "tasks_ok": ok,
                "success_rate": round(100 * ok / total, 1) if total else None,
                "facts_stored": facts, "tasks_by_tier": by_tier}

    def close(self) -> None:
        self._conn.close()


class WorkingMemory:
    """In-process scratch context for the current goal. Not persisted, by design."""

    def __init__(self, max_items: int = 20) -> None:
        self._items: list[str] = []
        self._max = max_items

    def add(self, note: str) -> None:
        self._items.append(note)
        if len(self._items) > self._max:
            self._items.pop(0)

    def context(self) -> str:
        return "\n".join(f"- {item}" for item in self._items)

    def clear(self) -> None:
        self._items.clear()
