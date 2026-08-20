"""Build training data out of JARVIS's own event log.

This is the honest version of "train your own models". A general assistant LLM
cannot be trained here - that needs thousands of GPU-hours and a datacentre,
which is exactly why the 550B route is used over an API. What *can* be trained,
on this laptop, in seconds, is a model over JARVIS's own recorded behaviour.

The event log already holds the supervision:

    goal.received     -> the request, in the user's own words
    plan.created      -> what was planned for it
    ability.executed  -> what ran, and whether it worked
    model.call        -> which route, how long, success

Pairing a goal with the ability that actually succeeded for it gives a labelled
dataset with no annotation work at all. Every real use adds an example, so the
classifier improves with use rather than staying fixed - which is the only kind
of self-improvement that is honest here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import settings


@dataclass
class Example:
    text: str            # what the user asked for
    ability: str         # the ability that actually ran and succeeded
    ok: bool


def _iter_events(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def build(path: Path | None = None) -> list[Example]:
    """Pair each goal with the ability that served it.

    Events are a flat stream, so goals and executions are correlated by
    position: an ability execution belongs to the most recent goal seen. That
    is sound here because the orchestrator runs one goal at a time.
    """
    log = path or settings.EVENT_LOG
    examples: list[Example] = []
    current: str | None = None
    claimed = False           # only the first ability of a goal is the label

    for event in _iter_events(log):
        kind = event.get("kind")
        if kind == "goal.received":
            current = (event.get("objective") or "").strip()
            claimed = False
        elif kind == "ability.executed" and current and not claimed:
            ability = event.get("ability")
            if not ability:
                continue
            # Memory abilities are excluded: "remember X" is already handled
            # deterministically, so training on it teaches nothing.
            if ability in ("remember", "recall"):
                current, claimed = None, True
                continue
            examples.append(Example(text=current, ability=ability,
                                    ok=bool(event.get("ok"))))
            claimed = True
    return examples


def usable(examples: list[Example], *, min_per_class: int = 3
           ) -> tuple[list[Example], dict[str, int]]:
    """Keep only successful examples from classes with enough support.

    Two filters, both deliberate:
      - failures are dropped, because a goal whose ability failed is not
        evidence that the ability was the right choice;
      - a class seen once or twice cannot be learned, and including it inflates
        reported accuracy while making the model confidently wrong.
    """
    good = [e for e in examples if e.ok and len(e.text) > 4]
    counts: dict[str, int] = {}
    for e in good:
        counts[e.ability] = counts.get(e.ability, 0) + 1
    kept = [e for e in good if counts[e.ability] >= min_per_class]
    return kept, counts


def summary(path: Path | None = None) -> dict[str, object]:
    raw = build(path)
    kept, counts = usable(raw)
    return {
        "raw_pairs": len(raw),
        "usable": len(kept),
        "classes": len({e.ability for e in kept}),
        "per_class": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    }
