"""What JARVIS knows about itself.

Answers "can you do this?" from the registries rather than from the model's
opinion of itself, so the answer is a fact about the running system.
"""

from __future__ import annotations

import platform
from typing import Any

from jarvis.abilities import registry as abilities
from jarvis.ontology import registry as ontology
from jarvis.skills import registry as skills

NAME = "JARVIS"
VERSION = "0.6"


def identity() -> dict[str, Any]:
    return {
        "name": NAME,
        "version": VERSION,
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "abilities": len(abilities.all_abilities()),
        "skills": [s.name for s in skills.all_skills()],
    }


def limits() -> list[str]:
    """Hard limits, stated the same way every time they are asked."""
    return [
        "I will never delete your files.",
        "I will never damage the operating system.",
        "Anything risky asks you first.",
        "I only report a task done after checking it really happened.",
    ]


def can_do(request: str) -> dict[str, Any]:
    """Honest answer to 'can you do X?'.

    Checks what is actually registered. When the answer is no, says which part
    is missing instead of a flat refusal.
    """
    from jarvis.core import coverage

    gap = coverage.detect_gap(request)
    if gap is None:
        return {"answer": "yes", "why": "", "matching_skills":
                [s.name for s in skills.select(request)]}

    related = ontology.search(request.split()[0] if request.split() else "")
    planned = [f"pack {p.id}: {p.name}" for p in related if not p.implemented][:3]
    return {"answer": "no", "why": gap, "planned": planned}


def status() -> dict[str, Any]:
    return {
        "identity": identity(),
        "limits": limits(),
        "coverage": ontology.coverage(),
        "next_buildable": [f"pack {p.id}: {p.name}"
                           for p in ontology.next_buildable(3)],
    }
