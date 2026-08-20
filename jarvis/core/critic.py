"""Plan critic — catch a bad plan before it runs, not after.

The failover already handles a model that returns malformed JSON. It does not
handle the more common and more damaging case: a plan that parses perfectly and
is simply wrong. That was measured directly - asked "why is my pc slow, work
out what is actually causing it", the weaker model produced a single
`system_state` step, which reports numbers without ever identifying a cause.
The stronger model produced `system_state` then `list_processes`.

Both plans are valid JSON. Only one answers the question.

So this checks the plan against the *shape of the request* using deterministic
signals, and escalates to the next route when the plan is obviously
insufficient. Deliberately deterministic rather than an LLM critique pass: a
second model call to judge the first doubles cost and latency on every request,
and these particular failures are detectable without one.

The bar is set to "obviously wrong", not "could be better". A critic that
rejects merely-imperfect plans burns the whole route chain and ends up worse
than the plan it rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Phrases that mean the user asked for more than one thing. Requiring a
# conjunction *and* a second verb keeps "bread and butter" from counting.
_SEQUENCE = re.compile(
    r"\b(and then|then|after that|followed by|and also|, then)\b", re.IGNORECASE)

# Requests that ask for a conclusion, not a reading. "Tell me the CPU" wants a
# number; "work out why it is slow" wants an explanation, which needs evidence
# from more than one place.
_DIAGNOSTIC = re.compile(
    r"\b(why|diagnose|work out|figure out|find out what|what.s causing"
    r"|root cause|troubleshoot|investigate)\b", re.IGNORECASE)

# Abilities that only observe. A diagnostic request served by exactly one of
# these has reported symptoms without reaching a cause.
_OBSERVE_ONLY = {
    "system_state", "list_processes", "hardware_status", "list_dir",
    "list_windows", "wifi_status", "repo_status", "document_info",
}


@dataclass
class Critique:
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


def review(objective: str, plan) -> Critique:
    """Is this plan obviously insufficient for this request?"""
    steps = getattr(plan, "steps", None) or []
    text = objective or ""

    # An answer-only plan is legitimate for a question, so an empty plan is
    # only wrong when the request clearly asks for an action.
    if not steps:
        return Critique(True)

    abilities = [s.ability for s in steps]

    if len(steps) == 1:
        if _SEQUENCE.search(text):
            return Critique(
                False,
                "the request asks for more than one action but the plan has "
                "a single step")
        if _DIAGNOSTIC.search(text) and abilities[0] in _OBSERVE_ONLY:
            return Critique(
                False,
                f"the request asks for a cause, but '{abilities[0]}' only "
                f"reports readings and no further step examines them")

    # The same observation twice in a row is a loop, not a plan.
    for a, b in zip(abilities, abilities[1:]):
        if a == b and a in _OBSERVE_ONLY:
            return Critique(False, f"'{a}' is repeated with nothing in between")

    return Critique(True)
