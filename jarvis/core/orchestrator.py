"""The orchestration loop.

    goal -> classify -> route -> plan -> policy gate -> execute -> verify
         -> recover once if needed -> synthesize -> remember

Two properties matter most here:

1. Completion is judged from evidence, not from the model's own claim. Every
   step is re-observed by its environment's verify() before the run is called
   successful, so a silently failed action cannot be reported as done.

2. A capability gap is reported honestly. If no registered ability can serve the
   request, the run ends with an explicit statement of what is missing.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from jarvis.abilities import registry
from jarvis.core import commitments, coverage, fastpath, planner
from jarvis.core.contracts import (
    ActionResult,
    Category,
    Goal,
    MemoryType,
    Plan,
    Risk,
    RouteTrace,
    Step,
    TaskRecord,
    Tier,
    VerificationResult,
)
from jarvis.core.events import emit, emit_trace
from jarvis.environments.local_os import LocalOSEnvironment
from jarvis.environments.repo import RepoEnvironment
from jarvis.environments.web import WebEnvironment
from jarvis.environments.windows_gui import WindowsGUIEnvironment
from jarvis.memory.store import MemoryStore, WorkingMemory
from jarvis.models.providers import (
    ModelError,
    ModelUnavailable,
    Provider,
    for_route,
)
from jarvis.models.router import Router, classify
from jarvis.policy import guardrails

# Called with (ability_id, args, risk) -> True to proceed. Injected by the UI.
ConfirmFn = Callable[[str, dict[str, Any], Risk], bool]


def always_allow(ability_id: str, args: dict[str, Any], risk: Risk) -> bool:
    return True


class Orchestrator:
    def __init__(self, *, confirm: ConfirmFn | None = None,
                 store: MemoryStore | None = None,
                 router: Router | None = None,
                 on_progress: Callable[[str], None] | None = None) -> None:
        self.router = router or Router()
        self.store = store or MemoryStore()
        self.confirm = confirm or always_allow
        self.working = WorkingMemory()
        # What JARVIS still owes the user, carried across restarts.
        self.commitments = commitments.CommitmentBook(self.store)
        self._progress = on_progress or (lambda _msg: None)

        self.local_os = LocalOSEnvironment()
        self.gui = WindowsGUIEnvironment()
        self.web = WebEnvironment()
        self.repo = RepoEnvironment()
        self.environments = {
            self.local_os.id: self.local_os,
            self.gui.id: self.gui,
            self.web.id: self.web,
            self.repo.id: self.repo,
        }

    # -- public ------------------------------------------------------------

    def run(self, objective: str, *, goal_id: str | None = None) -> TaskRecord:
        started = time.perf_counter()
        # goal_id lets a caller (the web layer) know the id before the run
        # finishes, so it can correlate live events to this request.
        goal = Goal(objective=objective.strip(), **({"id": goal_id} if goal_id else {}))
        trace = RouteTrace(goal_id=goal.id, objective=goal.objective)
        emit("goal.received", goal_id=goal.id, objective=goal.objective)

        classification = classify(goal.objective)
        trace.classification = classification
        self._progress(f"classified: {classification.difficulty.value}, "
                       f"{classification.privacy.value}")

        # Coverage is checked before planning: a small model asked for an
        # unsupported action tends to substitute a plausible different one
        # rather than refuse, so this cannot be left to the prompt.
        gap = coverage.detect_gap(goal.objective)
        if gap is not None:
            goal.status = "unsupported"
            emit("goal.unsupported", goal_id=goal.id, reason="capability_gap")
            return self._finish(goal, Plan(unsupported=gap), trace, False, gap,
                                started)

        # Unambiguous intents are executed without a model call at all: it is
        # faster, free, and strictly more accurate than asking an 8B model to
        # map the phrasing onto an ability.
        quick = fastpath.match(goal.objective)
        if quick is not None:
            trace.tier_chosen = Tier.DETERMINISTIC
            trace.reason = ("matched a deterministic intent rule, so no model "
                            "was called")
            self._progress("matched deterministic fast path")
            evidence = self._execute(quick, trace)
            failed = [s for s in quick.steps if s.status in {"failed", "denied"}]
            message = self._compose_message(goal, quick, evidence, trace, None)
            ok = not failed
            goal.status = "done" if ok else "failed"
            return self._finish(goal, quick, trace, ok, message, started)

        try:
            provider, tier, reason, chain = self.router.choose(classification,
                                                               trace)
        except ModelUnavailable as exc:
            return self._finish(goal, Plan(unsupported=str(exc)), trace,
                                False, str(exc), started)
        trace.tier_chosen = tier
        trace.reason = reason
        self._progress(f"routed to {tier.value} ({provider.route.id})")

        # -- plan, walking the candidate chain on failure
        plan, plan_error, provider = self._plan_with_failover(goal, chain, trace)
        if plan is None:
            message = f"I could not produce a valid plan: {plan_error}"
            return self._finish(goal, Plan(unsupported=message), trace, False,
                                message, started)

        if plan.unsupported:
            goal.status = "unsupported"
            message = plan.unsupported
            emit("goal.unsupported", goal_id=goal.id, reason=message)
            return self._finish(goal, plan, trace, False, message, started)

        if not plan.steps:
            answer = plan.answer or "Nothing to do."
            return self._finish(goal, plan, trace, True, answer, started)

        # -- execute
        evidence = self._execute(plan, trace)

        succeeded = [s for s in plan.steps if s.status == "done"]
        failed = [s for s in plan.steps if s.status in {"failed", "denied"}]

        # -- answer
        message = self._compose_message(goal, plan, evidence, trace, provider)

        ok = not failed and bool(succeeded)
        goal.status = "done" if ok else ("partial" if succeeded else "failed")
        return self._finish(goal, plan, trace, ok, message, started)

    def tier_status(self) -> dict[str, Any]:
        return self.router.describe()

    # -- planning ----------------------------------------------------------

    def _plan_with_failover(self, goal: Goal, chain: list[Any],
                            trace: RouteTrace
                            ) -> tuple[Plan | None, str, Provider | None]:
        """Try each candidate route in order until one produces a valid plan.

        Two distinct failures are handled differently: an unreachable provider is
        benched for the session, while a reachable provider that emits malformed
        or invalid JSON is simply passed over for this request. Both are recorded
        so the local tier's plan-validity rate is measurable.
        """
        context = self._context_for(goal.objective)
        errors: list[str] = []
        last_provider: Provider | None = None

        for attempt, route in enumerate(chain[:4]):
            provider = for_route(route)
            last_provider = provider
            if attempt > 0:
                trace.escalated = True
                trace.escalation_reason = (
                    f"{chain[attempt - 1].id} failed: {errors[-1][:160]}")
                trace.tier_chosen = route.tier
                self._progress(f"falling back to {route.id}")
            try:
                plan, call = planner.make_plan(provider, goal.objective,
                                               context=context)
                trace.calls.append(call)
                self.router.mark_ok(route.id)
                emit("plan.created", goal_id=goal.id, steps=len(plan.steps),
                     route=route.id, attempt=attempt + 1)
                return plan, "", provider
            except ModelUnavailable as exc:
                errors.append(f"{route.id}: {exc}")
                self.router.mark_failed(route.id, str(exc))
                emit("plan.route_unavailable", route=route.id, error=str(exc))
            except ModelError as exc:
                errors.append(f"{route.id}: {exc}")
                emit("plan.invalid", goal_id=goal.id, route=route.id,
                     error=str(exc))

        return None, "; ".join(errors) or "no route produced a plan", last_provider

    def _context_for(self, objective: str) -> str:
        """Working memory plus any stored facts relevant to this request."""
        parts: list[str] = []
        recalled = self.store.recall(objective, limit=3)
        if recalled:
            facts = "; ".join(r.content for r in recalled)
            parts.append(f"Known facts that may be relevant: {facts}")
        state = self.local_os.state()
        parts.append(f"User home directory: {state['home']}")

        # Closing the learning loop: a failure is only worth remembering if it
        # changes the next plan, so previous failures at similar work are put
        # in front of the planner rather than merely logged.
        warnings = commitments.past_failures(self.store, objective, limit=3)
        if warnings:
            joined = "\n  - ".join(warnings)
            parts.append(
                "Previous attempts at similar work FAILED as follows - do not "
                f"repeat the same approach blindly:\n  - {joined}")

        if self.working.context():
            parts.append(f"Earlier in this session:\n{self.working.context()}")
        return "\n".join(parts)

    # -- execution ---------------------------------------------------------

    def _execute(self, plan: Plan, trace: RouteTrace) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for step in plan.steps:
            if any(plan.steps[d - 1].status != "done"
                   for d in step.depends_on if 0 < d <= len(plan.steps)):
                step.status = "skipped"
                continue

            ability = registry.get(step.ability)
            if ability is None:  # planner validation should prevent this
                step.status = "failed"
                step.result = ActionResult(ok=False, summary="unknown ability",
                                           error="unregistered")
                continue

            # 1. Hard guardrails, before any confirmation logic.
            try:
                guardrails.check_ability(step.ability, step.args)
            except guardrails.Blocked as exc:
                step.status = "denied"
                step.result = ActionResult(ok=False, summary=str(exc),
                                           error="blocked_by_guardrail")
                evidence.append({"step": step.n, "ability": step.ability,
                                 "refused": str(exc)})
                continue

            # 2. Risk gate.
            if ability.risk in (Risk.MEDIUM, Risk.HIGH):
                if not self.confirm(step.ability, step.args, ability.risk):
                    step.status = "denied"
                    step.result = ActionResult(ok=False,
                                               summary="declined by user",
                                               error="not_confirmed")
                    emit("ability.declined", ability=step.ability)
                    evidence.append({"step": step.n, "ability": step.ability,
                                     "declined": True})
                    continue

            # 3. Execute, then verify by re-observing the world.
            step.status = "running"
            self._progress(f"step {step.n}: {step.ability}")
            result, verification = self._run_step(step)
            step.result, step.verification = result, verification

            if result.ok and verification.verified:
                step.status = "done"
            else:
                retried = self._retry_step(step)
                step.status = "done" if retried else "failed"

            emit("ability.executed", ability=step.ability, ok=step.status == "done",
                 verified=step.verification.verified if step.verification else None,
                 duration_ms=step.result.duration_ms if step.result else 0)

            entry: dict[str, Any] = {
                "step": step.n, "ability": step.ability, "args": step.args,
                "ok": step.status == "done", "summary": step.result.summary,
                "evidence": self._trim(step.result.evidence),
            }
            if step.verification:
                entry["verification"] = {
                    "verified": step.verification.verified,
                    "how": step.verification.strategy,
                    "detail": step.verification.detail,
                }
            evidence.append(entry)
            self.working.add(f"{step.ability}: {step.result.summary}")
        return evidence

    def call_ability(self, ability_id: str, args: dict) -> str:
        """Run one named ability and describe the real outcome in plain text.

        The voice session uses this: the model has already decided which action
        it wants, so there is nothing to plan. Everything after that decision --
        guardrails, the confirmation gate, execution, verification, memory --
        is the same path the CLI takes, so speaking a command is not a weaker
        or less-checked route into the machine than typing one.
        """
        ability = registry.get(ability_id)
        if ability is None:
            return (f"There is no ability called '{ability_id}'. "
                    f"I cannot do that yet.")

        step = Step(n=1, ability=ability_id, args=args or {},
                    why="requested by voice")
        try:
            guardrails.check_ability(ability_id, step.args)
        except guardrails.Blocked as exc:
            emit("voice.blocked", ability=ability_id, reason=str(exc))
            return f"Blocked: {exc}"

        if ability.risk is not Risk.LOW and not self.confirm(
                ability_id, step.args, ability.risk):
            return "Not approved, so I did not do it."

        result, verification = self._run_step(step)
        step.result, step.verification = result, verification
        self.working.add(f"{ability_id}: {result.summary}")

        if not result.ok:
            return f"That did not work: {result.summary}"
        if verification.verified is False:
            return (f"{result.summary} -- but I could not confirm it: "
                    f"{verification.detail}")
        detail = _voice_detail(result)
        return f"{result.summary}{detail}"

    def _run_step(self, step: Step) -> tuple[ActionResult, VerificationResult]:
        ability = registry.get(step.ability)
        assert ability is not None

        if ability.environment == "memory":
            return self._run_memory_step(step)

        if step.ability == "research":
            return self._run_research(step)

        env = self.environments.get(ability.environment)
        if env is None:
            result = ActionResult(
                ok=False,
                summary=f"environment '{ability.environment}' is not registered",
                error="no_environment")
            return result, VerificationResult(verified=False, strategy="none",
                                              detail="environment missing")
        result = env.act(step.ability, step.args)
        verification = env.verify(step.ability, step.args, result)
        return result, verification

    def _retry_step(self, step: Step) -> bool:
        """One recovery attempt. Only retried when the failure looks transient."""
        result = step.result
        if result is None:
            return False
        transient = {"timeout", "not_confirmed"} | {
            e for e in [result.error] if e and "Network" in e}
        if result.error in {"blocked_by_guardrail", "not_confirmed", "unregistered",
                            "missing_name", "missing_text", "missing_command",
                            "wrong_foreground"}:
            return False
        if result.error not in transient and result.ok:
            # Action reported success but verification disagreed: re-verify once,
            # since some filesystem/window changes settle slightly late.
            ability = registry.get(step.ability)
            env = self.environments.get(ability.environment) if ability else None
            if env is None:
                return False
            time.sleep(0.6)
            verification = env.verify(step.ability, step.args, result)
            step.verification = verification
            emit("step.reverified", ability=step.ability,
                 verified=verification.verified)
            return verification.verified
        if result.error in transient:
            emit("step.retry", ability=step.ability, reason=result.error)
            new_result, new_verification = self._run_step(step)
            step.result, step.verification = new_result, new_verification
            return new_result.ok and new_verification.verified
        return False

    # -- special abilities --------------------------------------------------

    def _run_memory_step(self, step: Step) -> tuple[ActionResult, VerificationResult]:
        if step.ability == "remember":
            content = str(step.args.get("content", "")).strip()
            if not content:
                r = ActionResult(ok=False, summary="nothing to remember",
                                 error="missing_content")
                return r, VerificationResult(verified=False, strategy="none",
                                             detail="no content")
            record = self.store.remember(content, source="user")
            back = self.store.recall(content, limit=1)
            stored = bool(back) and back[0].id == record.id
            return (
                ActionResult(ok=True, summary=f"Remembered: {content}",
                             evidence={"id": record.id, "content": content,
                                       "db": self.store.path}),
                VerificationResult(
                    verified=stored, strategy="stored_and_readable",
                    detail="written to SQLite and read back"
                           if stored else "write could not be read back",
                    checked={"id": record.id, "readback": stored}),
            )

        query = str(step.args.get("query", "")).strip()
        hits = self.store.recall(query, limit=5)
        return (
            ActionResult(
                ok=True,
                summary=(f"{len(hits)} stored fact(s) matching '{query}'"
                         if hits else f"nothing stored about '{query}'"),
                evidence={"query": query,
                          "facts": [{"content": h.content,
                                     "stored_at": h.created_at} for h in hits]}),
            VerificationResult(verified=True, strategy="result_only",
                               detail="read-only lookup"),
        )

    def _run_research(self, step: Step) -> tuple[ActionResult, VerificationResult]:
        query = str(step.args.get("query") or step.args.get("q") or "").strip()
        if not query:
            r = ActionResult(ok=False, summary="no research question given",
                             error="missing_query")
            return r, VerificationResult(verified=False, strategy="none", detail="")
        self._progress(f"researching: {query}")
        bundle = self.web.research(query, pages=3)
        sources = bundle.get("sources", [])
        if not sources:
            r = ActionResult(
                ok=False,
                summary="No sources could be retrieved (offline, or the search "
                        "endpoint returned nothing).",
                error="no_sources", evidence=bundle)
            return r, VerificationResult(verified=False, strategy="results_returned",
                                         detail="0 sources")
        return (
            ActionResult(
                ok=True,
                summary=f"Read {len(sources)} source(s) for '{query}'",
                evidence={"query": query,
                          "sources": [{"n": i + 1, "title": s["title"],
                                       "url": s["url"], "text": s["text"][:3500]}
                                      for i, s in enumerate(sources)]}),
            VerificationResult(verified=True, strategy="results_returned",
                               detail=f"{len(sources)} sources fetched",
                               checked={"source_count": len(sources)}),
        )

    # -- answer ------------------------------------------------------------

    def _compose_message(self, goal: Goal, plan: Plan,
                         evidence: list[dict[str, Any]], trace: RouteTrace,
                         provider: Provider | None) -> str:
        # A deterministic fast-path run has no provider by design: calling one
        # here just to phrase the answer would undo the point of the shortcut.
        if provider is None:
            return self._plain_summary(plan)

        # Abilities that gather information rather than change state: their
        # evidence is the answer, so it needs writing up rather than listing.
        needs_prose = any(
            s.ability in {"research", "web_search", "fetch_page", "read_file",
                          "recall", "search_in_files", "find_files",
                          "system_state", "list_processes", "list_dir",
                          "read_ui", "list_windows"}
            for s in plan.steps if s.status == "done")

        if needs_prose:
            try:
                text, call = planner.synthesize(
                    provider, goal.objective, planner.format_evidence(evidence))
                trace.calls.append(call)
                if text:
                    return text
            except (ModelError, ModelUnavailable) as exc:
                emit("synthesis.failed", error=str(exc))

        return self._plain_summary(plan)

    @staticmethod
    def _plain_summary(plan: Plan) -> str:
        """Model-free rendering of what happened, straight from step evidence."""
        lines: list[str] = []
        for step in plan.steps:
            mark = {"done": "OK", "failed": "FAILED", "denied": "REFUSED",
                    "skipped": "SKIPPED"}.get(step.status, step.status.upper())
            detail = step.result.summary if step.result else ""
            lines.append(f"[{mark}] {step.ability}: {detail}")

            # Recall is worth expanding: the stored facts are the answer.
            if (step.ability == "recall" and step.result
                    and step.result.evidence.get("facts")):
                for fact in step.result.evidence["facts"]:
                    lines.append(f"        - {fact['content']}")

            if step.status == "failed" and step.verification and \
                    not step.verification.verified:
                lines.append(f"        verification: {step.verification.detail}")
        return "\n".join(lines)

    # -- bookkeeping -------------------------------------------------------

    @staticmethod
    def _trim(evidence: dict[str, Any]) -> dict[str, Any]:
        """Keep evidence small enough to put in a prompt without losing substance."""
        out: dict[str, Any] = {}
        for key, value in evidence.items():
            if isinstance(value, str) and len(value) > 3000:
                out[key] = value[:3000] + "..."
            elif isinstance(value, list) and len(value) > 25:
                out[key] = value[:25]
            else:
                out[key] = value
        return out

    def _finish(self, goal: Goal, plan: Plan, trace: RouteTrace, ok: bool,
                message: str, started: float) -> TaskRecord:
        trace.total_ms = int((time.perf_counter() - started) * 1000)
        if goal.status == "open":
            goal.status = "done" if ok else "failed"
        goal.completed_at = time.time()
        record = TaskRecord(goal=goal, plan=plan, trace=trace, ok=ok,
                            message=message)
        self.store.record_task(record)

        # Learn from what went wrong, so the same approach is not retried
        # blindly next time (vision packs 40, 55, 56).
        for step in plan.steps:
            if step.status in ("failed", "denied") and step.result is not None:
                commitments.record_failure(
                    self.store, objective=goal.objective,
                    ability=step.ability,
                    error=step.result.summary or step.result.error or "")

        # If the answer contained a promise, hold JARVIS to it (pack 75).
        if message:
            self.commitments.record(message, context=goal.objective)
        emit_trace(trace)
        emit("goal.finished", goal_id=goal.id, ok=ok, status=goal.status,
             total_ms=trace.total_ms)
        return record

    def close(self) -> None:
        self.store.close()


def _voice_detail(result: ActionResult) -> str:
    """Pull the few facts worth saying aloud out of an action's evidence."""
    ev = result.evidence or {}
    for key in ("text", "answer", "content"):
        value = ev.get(key)
        if isinstance(value, str) and value.strip():
            snippet = " ".join(value.split())[:400]
            return f". {snippet}"
    matches = ev.get("matches") or ev.get("results")
    if isinstance(matches, list) and matches:
        first = matches[0]
        if isinstance(first, dict):
            label = first.get("path") or first.get("title") or first.get("url")
            if label:
                more = f" and {len(matches) - 1} more" if len(matches) > 1 else ""
                return f". First is {label}{more}"
    return ""
