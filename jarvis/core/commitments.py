"""Goals, commitments and follow-through (vision packs 13, 50, 75).

The vision is precise about why this exists (Part 50):

    "A task isn't complete because the AI generated an answer. It is complete
     when: objective achieved + verification passed + required follow-up
     completed."

and (Part 49):

    "Then JARVIS doesn't forget its own outstanding responsibilities."

So a commitment is not a to-do list item the user writes. It is something
JARVIS itself took on - "I will watch that build", "I still owe you that
summary" - recorded at the moment it is made, and carried across restarts so
the promise outlives the conversation that created it.

Storage reuses the memory store rather than adding a second database: a
commitment is a memory of type COMMITMENT, so it inherits persistence,
provenance and recall for free.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from jarvis.core.contracts import MemoryType
from jarvis.core.events import emit

# Phrases that mean JARVIS has taken something on. Deliberately narrow: a
# commitment wrongly recorded nags the user forever, which is worse than
# missing one.
_PROMISE = re.compile(
    r"\b(i (?:will|'ll|shall)|i am going to|i'm going to|let me|i can keep"
    r"|i will keep|i'll keep|remind you|follow up|check back|monitor"
    r"|keep an eye|get back to you)\b",
    re.IGNORECASE,
)

# Things that only *sound* like promises.
_NOT_PROMISE = re.compile(
    r"\b(i (?:will not|won't|cannot|can't)|would you like|shall i|should i)\b",
    re.IGNORECASE,
)

OPEN = "open"
DONE = "done"
CANCELLED = "cancelled"


@dataclass
class Commitment:
    id: str
    text: str
    created_at: float
    status: str
    due: float | None = None
    context: str = ""

    @property
    def overdue(self) -> bool:
        return (self.status == OPEN and self.due is not None
                and time.time() > self.due)

    def describe(self) -> str:
        age_h = (time.time() - self.created_at) / 3600
        when = ("just now" if age_h < 1
                else f"{int(age_h)}h ago" if age_h < 48
                else f"{int(age_h / 24)}d ago")
        flag = " (overdue)" if self.overdue else ""
        return f"{self.text} - promised {when}{flag}"


class CommitmentBook:
    """Everything JARVIS has taken on and not yet discharged."""

    def __init__(self, store) -> None:
        self._store = store

    # -- recording ----------------------------------------------------------

    def detect(self, text: str) -> str | None:
        """Return the promise in `text`, or None if it is not one."""
        if not text or _NOT_PROMISE.search(text):
            return None
        match = _PROMISE.search(text)
        if not match:
            return None
        # Keep the sentence the promise lives in, not the whole reply.
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            if _PROMISE.search(sentence) and not _NOT_PROMISE.search(sentence):
                cleaned = sentence.strip()
                return cleaned if 8 <= len(cleaned) <= 220 else None
        return None

    def record(self, text: str, *, context: str = "",
               due: float | None = None) -> Commitment | None:
        promise = self.detect(text)
        if promise is None:
            return None
        payload = json.dumps({"text": promise, "status": OPEN,
                              "due": due, "context": context[:200]})
        record = self._store.remember(payload, source="jarvis",
                                      memory_type=MemoryType.COMMITMENT,
                                      provenance=context[:200])
        emit("commitment.made", id=record.id, text=promise)
        return Commitment(id=record.id, text=promise,
                          created_at=record.created_at, status=OPEN,
                          due=due, context=context)

    # -- reading ------------------------------------------------------------

    def _all(self) -> list[Commitment]:
        out: list[Commitment] = []
        for record in self._store.all_memories(MemoryType.COMMITMENT, limit=200):
            try:
                data = json.loads(record.content)
            except json.JSONDecodeError:
                continue
            out.append(Commitment(
                id=record.id, text=data.get("text", ""),
                created_at=record.created_at,
                status=data.get("status", OPEN),
                due=data.get("due"), context=data.get("context", "")))
        return out

    def open_items(self) -> list[Commitment]:
        items = [c for c in self._all() if c.status == OPEN]
        # Overdue first, then oldest - the order a person would want them.
        items.sort(key=lambda c: (not c.overdue, c.created_at))
        return items

    def overdue(self) -> list[Commitment]:
        return [c for c in self.open_items() if c.overdue]

    # -- discharging --------------------------------------------------------

    def close(self, commitment_id: str, status: str = DONE) -> bool:
        for record in self._store.all_memories(MemoryType.COMMITMENT, limit=200):
            if record.id != commitment_id:
                continue
            try:
                data = json.loads(record.content)
            except json.JSONDecodeError:
                return False
            data["status"] = status
            # The store has no update; rewrite as a new record and retire the
            # old one. Deletion here is JARVIS's own bookkeeping, never a user
            # file, so it stays within the rule that matters.
            self._store.forget(record.id)
            self._store.remember(json.dumps(data), source="jarvis",
                                 memory_type=MemoryType.COMMITMENT)
            emit("commitment.closed", id=commitment_id, status=status)
            return True
        return False

    def summary(self) -> str:
        items = self.open_items()
        if not items:
            return ""
        late = [c for c in items if c.overdue]
        lines = [f"You have {len(items)} thing(s) I still owe you"
                 + (f", {len(late)} overdue" if late else "") + ":"]
        for c in items[:5]:
            lines.append(f"  - {c.describe()}")
        return "\n".join(lines)


# -- failure memory (packs 40, 55, 56) --------------------------------------

def record_failure(store, *, objective: str, ability: str, error: str,
                   cause: str = "") -> None:
    """Remember why something failed, not merely that it did.

    The vision (Part 56) is specific: "JARVIS shouldn't only remember 'Task
    failed.' It should remember 'Task failed because authentication tokens
    expired during long-running execution.'"
    """
    payload = json.dumps({
        "objective": objective[:200], "ability": ability,
        "error": error[:300], "cause": cause[:200] or _classify(error),
        "at": time.time(),
    })
    store.remember(payload, source="jarvis", memory_type=MemoryType.FAILURE,
                   provenance=objective[:120])
    emit("failure.recorded", ability=ability, cause=_classify(error))


_CAUSES = (
    ("not_found", ("not found", "no such file", "cannot find", "notfound",
                   "could not resolve", "no matching", "does not exist")),
    ("permission", ("permission", "access is denied", "forbidden", "denied")),
    ("already_exists", ("already exists", "file exists")),
    ("network", ("timeout", "unreachable", "connection", "dns", "offline")),
    ("quota", ("429", "quota", "rate limit", "credit")),
    ("bad_arguments", ("invalid", "missing", "expected", "argument")),
)


def _classify(error: str) -> str:
    low = (error or "").lower()
    for name, needles in _CAUSES:
        if any(n in low for n in needles):
            return name
    return "unknown"


def past_failures(store, objective: str, *, limit: int = 3) -> list[str]:
    """Warnings from previous attempts at something similar."""
    hits = store.recall(objective, limit=limit, memory_type=MemoryType.FAILURE)
    out = []
    for record in hits:
        try:
            data = json.loads(record.content)
        except json.JSONDecodeError:
            continue
        out.append(f"{data.get('ability', '?')} failed "
                   f"({data.get('cause', 'unknown')}): {data.get('error', '')[:120]}")
    return out
