"""Loader and queries over the capability ontology."""

from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

from jarvis.ontology.schema import Pack, Status, Subsystem

DATA = Path(__file__).resolve().parent / "packs.json"


@lru_cache(maxsize=1)
def _load() -> tuple[dict[int, Pack], dict[str, Subsystem]]:
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    packs = {p["id"]: Pack(**p) for p in raw["packs"]}
    grouped: dict[str, list[int]] = {}
    for pack in packs.values():
        grouped.setdefault(pack.subsystem, []).append(pack.id)
    subsystems = {
        name: Subsystem(name=name, purpose=purpose, module="",
                        pack_ids=sorted(grouped.get(name, [])))
        for name, purpose in raw["subsystems"].items()
    }
    return packs, subsystems


def all_packs() -> list[Pack]:
    return sorted(_load()[0].values(), key=lambda p: p.id)


def get(pack_id: int) -> Pack | None:
    return _load()[0].get(pack_id)


def subsystems() -> list[Subsystem]:
    return sorted(_load()[1].values(), key=lambda s: s.name)


def by_subsystem(name: str) -> list[Pack]:
    return [p for p in all_packs() if p.subsystem == name]


def by_status(status: Status) -> list[Pack]:
    return [p for p in all_packs() if p.status is status]


def search(term: str) -> list[Pack]:
    term = term.lower().strip()
    if not term:
        return []
    return [p for p in all_packs()
            if term in p.name.lower() or term in p.purpose.lower()
            or term in p.subsystem.lower()]


def dependents_of(pack_id: int) -> list[Pack]:
    """Packs that would be unblocked, or improved, by building this one."""
    return [p for p in all_packs() if pack_id in p.depends_on]


def blocking(pack_id: int) -> list[Pack]:
    """Dependencies of this pack that are not yet implemented."""
    pack = get(pack_id)
    if pack is None:
        return []
    return [dep for dep in (get(d) for d in pack.depends_on)
            if dep is not None and not dep.implemented]


def coverage() -> dict[str, object]:
    """Honest headline numbers, computed rather than asserted."""
    packs = all_packs()
    counts = Counter(p.status.value for p in packs)
    per_subsystem = {}
    for sub in subsystems():
        members = by_subsystem(sub.name)
        done = sum(1 for p in members if p.implemented)
        per_subsystem[sub.name] = {
            "total": len(members), "implemented": done,
            "percent": round(100 * done / max(1, len(members))),
        }
    implemented = sum(1 for p in packs if p.implemented)
    return {
        "total_packs": len(packs),
        "implemented_or_partial": implemented,
        "percent": round(100 * implemented / max(1, len(packs))),
        "by_status": {
            "A_implemented": counts.get("A", 0),
            "B_partial": counts.get("B", 0),
            "C_foundation": counts.get("C", 0),
            "D_planned": counts.get("D", 0),
            "E_research_limit": counts.get("E", 0),
        },
        "by_subsystem": per_subsystem,
    }


def next_buildable(limit: int = 5) -> list[Pack]:
    """Unbuilt packs whose dependencies are all satisfied, most-unblocking first.

    This is the ontology earning its keep: rather than guessing what to build
    next, the dependency graph is asked which unbuilt work is actually
    reachable now and would unblock the most other capability.
    """
    ready = [p for p in all_packs()
             if not p.implemented
             and p.maturity.value == "possible_today"
             and not blocking(p.id)]
    ready.sort(key=lambda p: (-len(dependents_of(p.id)), p.id))
    return ready[:limit]
