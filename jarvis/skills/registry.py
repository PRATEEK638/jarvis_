"""Skill registry — domain expertise as data, not code.

A tool tells JARVIS *what it can do*. A skill tells it *how a competent person
approaches this kind of problem*: the order to work in, what to check first,
the traps, and what counts as done. That distinction is what separates an
assistant that can technically call `run_command` from one that debugs like an
engineer.

Each skill is a markdown file in `library/` with YAML-ish front matter:

    ---
    name: debugging
    description: Diagnose a failing program or command
    triggers: [error, exception, traceback, failing, broken, crash]
    ---
    ...the playbook...

Adding a field of expertise is therefore adding one file. No code changes, no
registration call — which is the extensibility requirement from the vision
document applied to knowledge rather than tools.

Selection is deliberately conservative: a skill must match at least
MIN_TRIGGER_HITS distinct triggers before it is injected. Loading the wrong
playbook is worse than loading none, because it confidently points the model
at the wrong method.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

LIBRARY = Path(__file__).resolve().parent / "library"

# Two independent signals, not one. A single keyword hit is usually incidental
# ("there was an error in the article I'm reading" is not a debugging task).
MIN_TRIGGER_HITS = 2
MAX_SKILLS_INJECTED = 2


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str]
    body: str
    path: Path
    tokens_estimate: int = field(default=0)

    def __post_init__(self) -> None:
        self.tokens_estimate = max(1, len(self.body) // 4)


_cache: list[Skill] | None = None


def _parse(path: Path) -> Skill | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not raw.startswith("---"):
        return None
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return None
    meta, body = parts[1], parts[2].strip()

    fields: dict[str, str] = {}
    for line in meta.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()

    name = fields.get("name") or path.stem
    triggers_raw = fields.get("triggers", "").strip().strip("[]")
    triggers = [t.strip().strip("'\"").lower()
                for t in triggers_raw.split(",") if t.strip()]
    if not triggers or not body:
        return None
    return Skill(name=name, description=fields.get("description", ""),
                 triggers=triggers, body=body, path=path)


def all_skills(*, refresh: bool = False) -> list[Skill]:
    global _cache
    if _cache is not None and not refresh:
        return _cache
    found: list[Skill] = []
    if LIBRARY.is_dir():
        for path in sorted(LIBRARY.glob("*.md")):
            skill = _parse(path)
            if skill is not None:
                found.append(skill)
    _cache = found
    return found


def _hits(text: str, skill: Skill) -> int:
    """How many distinct triggers appear as whole words in the request."""
    count = 0
    for trigger in skill.triggers:
        pattern = r"\b" + re.escape(trigger) + r"(?:s|es|ed|ing)?\b"
        if re.search(pattern, text):
            count += 1
    return count


def select(objective: str, *, limit: int = MAX_SKILLS_INJECTED) -> list[Skill]:
    """The skills genuinely relevant to this request, best match first."""
    text = objective.lower()
    scored: list[tuple[int, Skill]] = []
    for skill in all_skills():
        hits = _hits(text, skill)
        if hits >= MIN_TRIGGER_HITS:
            scored.append((hits, skill))
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    return [skill for _, skill in scored[:limit]]


def prompt_for(objective: str) -> str:
    """Expertise to append to the planner's system prompt, or '' if none fits."""
    skills = select(objective)
    if not skills:
        return ""
    blocks = [
        "RELEVANT EXPERTISE — apply these established approaches rather than "
        "improvising:",
    ]
    for skill in skills:
        blocks.append(f"\n## {skill.name}\n{skill.body}")
    return "\n".join(blocks)


def describe() -> list[dict[str, object]]:
    return [{"name": s.name, "description": s.description,
             "triggers": s.triggers, "approx_tokens": s.tokens_estimate}
            for s in all_skills()]
