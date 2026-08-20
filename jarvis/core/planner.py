"""Planning: natural language goal -> validated Plan of ability calls.

The plan is produced as JSON and then validated against the ability registry
before anything executes. A model that invents an ability name or omits a
required argument produces a rejected plan, not a runtime surprise.
"""

from __future__ import annotations

import json
from typing import Any

from jarvis.abilities import registry
from jarvis.core import persona
from jarvis.core.events import emit
from jarvis.skills import registry as skills
from jarvis.core.contracts import Plan, Step
from jarvis.models.providers import ModelError, Provider, extract_json

SYSTEM_PROMPT = """You are JARVIS, the planning component of a desktop automation \
system running on Windows. You translate the user's request into a JSON plan of \
ability calls. You never execute anything yourself and you never invent abilities.

AVAILABLE ABILITIES:
{catalogue}

Reply with ONE JSON object, no prose, in exactly this shape:
{{
  "reasoning": "<one short sentence on your approach>",
  "steps": [
    {{"ability": "<exact ability id from the list>", "args": {{}}, "why": "<short>"}}
  ],
  "answer": null,
  "unsupported": null
}}

RULES:
- Use only ability ids from the list above, spelled exactly.
- Provide every required argument. Never guess a file path the user did not give;
  if a path is genuinely needed and absent, put the question in "unsupported".
- A request needing several actions becomes several steps in order.
- If the request needs no action at all (a greeting, or a question you can answer
  from general knowledge), leave "steps" empty and put your reply in "answer".
- If no combination of the listed abilities can do what was asked, leave "steps"
  empty and explain what is missing in "unsupported". Never pretend.
- For questions about current or external information, use the "research" ability.
- Prefer "find_files" to locate by filename and "search_in_files" to locate by
  text content inside files.
- Paths like "desktop/report.txt" are understood; pass them through as given.

CONTEXT:
{context}
"""


def _validate(raw: dict[str, Any]) -> Plan:
    """Turn model output into a Plan, rejecting anything not in the registry."""
    if not isinstance(raw, dict):
        raise ModelError("plan is not a JSON object")

    answer = raw.get("answer")
    unsupported = raw.get("unsupported")
    reasoning = str(raw.get("reasoning") or "")[:500]

    raw_steps = raw.get("steps") or []
    if not isinstance(raw_steps, list):
        raise ModelError("'steps' is not a list")

    steps: list[Step] = []
    for i, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict):
            raise ModelError(f"step {i} is not an object")
        ability_id = str(item.get("ability") or item.get("tool") or "").strip()
        ability = registry.get(ability_id)
        if ability is None:
            raise ModelError(
                f"step {i} names unknown ability '{ability_id}'. "
                f"Valid ids: {', '.join(a.id for a in registry.all_abilities())}")
        args = item.get("args") or item.get("arguments") or {}
        if not isinstance(args, dict):
            raise ModelError(f"step {i} args is not an object")
        missing = [p for p in ability.required if not str(args.get(p, "")).strip()]
        if missing:
            raise ModelError(
                f"step {i} ({ability_id}) is missing required argument(s): "
                f"{', '.join(missing)}")
        steps.append(Step(n=i, ability=ability_id, args=args,
                          why=str(item.get("why") or "")[:200]))

    return Plan(
        steps=steps, reasoning=reasoning,
        answer=str(answer) if isinstance(answer, str) and answer.strip() else None,
        unsupported=(str(unsupported)
                     if isinstance(unsupported, str) and unsupported.strip()
                     else None),
    )


def make_plan(provider: Provider, objective: str, *,
              context: str = "") -> tuple[Plan, Any]:
    """Ask a model for a plan and validate it. Raises ModelError on bad output."""
    system = SYSTEM_PROMPT.format(
        catalogue=registry.catalogue_for_prompt(),
        context=context or "(no additional context)",
    )
    # Domain expertise, when the request clearly falls in a known domain. This
    # is what turns "can call the tools" into "approaches the problem the way a
    # competent practitioner would". Empty for anything that does not clearly
    # match, so ordinary requests are not weighed down by an irrelevant
    # playbook.
    expertise = skills.prompt_for(objective)
    if expertise:
        system = system + chr(10) * 2 + expertise
        emit("skills.applied",
             skills=[sk.name for sk in skills.select(objective)])
    text, call = provider.generate(system, objective, json_mode=True,
                                   purpose="plan")
    raw = extract_json(text)
    return _validate(raw), call


ANSWER_SYSTEM = """You are JARVIS. Answer the user using ONLY the evidence below, \
which was gathered by real tool execution on the user's machine or from web pages \
that were actually fetched.

Rules:
- Be direct and concise. No preamble.
- Use the concrete numbers, paths and filenames from the evidence.
- When evidence came from web sources, cite them as [1], [2] matching the list.
- If the evidence does not answer the question, say exactly what is missing.
- Never invent a value that is not in the evidence."""


def synthesize(provider: Provider, objective: str, evidence: str) -> tuple[str, Any]:
    """Turn collected evidence into the user-facing answer."""
    user = f"USER REQUEST:\n{objective}\n\nEVIDENCE:\n{evidence}"
    # Character is applied to the answer, never to planning or policy.
    system = persona.system_prompt() + chr(10) * 2 + ANSWER_SYSTEM
    text, call = provider.generate(system, user, json_mode=False,
                                    purpose="answer")
    return text.strip(), call


def format_evidence(items: list[dict[str, Any]]) -> str:
    """Compact, model-readable rendering of step results."""
    return json.dumps(items, ensure_ascii=False, indent=1, default=str)[:14_000]
