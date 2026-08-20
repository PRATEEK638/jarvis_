"""Asynchronous delegation to a stronger model.

The realtime voice model is optimised for conversation: fast, natural, good at
turn-taking. It is not the model you want deciding a genuinely hard question.
But swapping to a bigger model for the whole session would make every "what's
my CPU at" cost a second of thinking time.

So the pattern OpenAI describes for GPT-Live applies directly, and needs no
special infrastructure to implement: keep the voice model on the live audio
path, and hand the hard question sideways to a stronger model while the
conversation stays alive.

    user speaks
        |
    voice model  ---- easy ----> answers immediately
        |
      hard
        |
        +--> "Let me think about that properly."   (spoken at once)
        |
        +--> nemotron 550B / gemini      (runs off the audio path)
                    |
              answer returns
                    |
             voice model speaks it

The important property is that the audio path is never blocked. The voice model
keeps listening, the user can interrupt, and the deep answer arrives as another
turn when it is ready.

This is exposed as an ability rather than wired into the session loop, because
that way the voice model itself decides when a question deserves it - which is
the judgement it is actually good at - and the decision shows up in the trace
like any other tool call.
"""

from __future__ import annotations

import time
from typing import Any

from jarvis.core.contracts import ActionResult, VerificationResult
from jarvis.core.events import emit

# Only routes worth the extra latency. Ordered by measured planning quality,
# strongest first; the session falls through on failure exactly as the planner
# does.
DEEP_ROUTES = ("nvidia:nim", "gemini:2.5-flash", "openrouter:free")

DEEP_SYSTEM = """You are the reasoning half of a voice assistant. A question was
asked aloud that deserves more thought than a conversational reply.

Answer it properly, then compress it for speech: two or three sentences, no
lists, no headings, no markdown. The result will be read out, so write what a
person would actually say.

If the question cannot be answered without information you do not have, say
exactly what is missing in one sentence instead of guessing."""


class DelegateEnvironment:
    """Hand a hard question to a stronger model without stalling the voice."""

    id = "delegate"

    def state(self) -> dict[str, Any]:
        from jarvis.models import registry
        available = [r for r in DEEP_ROUTES if registry.get(r) is not None]
        return {"available": bool(available), "routes": available}

    def capabilities(self) -> list[str]:
        return ["think_harder"]

    def constraints(self) -> list[str]:
        return [
            "Adds seconds of latency by design; only worth it for questions "
            "the conversational model would answer badly.",
            "Runs off the live audio path, so the conversation stays "
            "interruptible while it works.",
            "Returns prose shaped for speech, not a document.",
        ]

    def act(self, ability_id: str, args: dict[str, Any]) -> ActionResult:
        if ability_id != "think_harder":
            return ActionResult(ok=False, error="unregistered",
                                summary=f"unknown ability '{ability_id}'")
        question = str(args.get("question") or args.get("query") or "").strip()
        if not question:
            return ActionResult(ok=False, error="missing_question",
                                summary="What should I think about?")

        from jarvis.models import registry
        from jarvis.models.providers import ModelError, ModelUnavailable, for_route

        context = str(args.get("context") or "").strip()
        prompt = (f"{question}\n\nWhat is already known:\n{context}"
                  if context else question)

        started = time.perf_counter()
        errors = []
        for route_id in DEEP_ROUTES:
            route = registry.get(route_id)
            if route is None:
                continue
            try:
                text, call = for_route(route).generate(
                    DEEP_SYSTEM, prompt, json_mode=False, purpose="deep")
            except (ModelUnavailable, ModelError) as exc:
                errors.append(f"{route_id}: {exc}")
                continue
            answer = (text or "").strip()
            if not answer:
                errors.append(f"{route_id}: empty answer")
                continue
            ms = int((time.perf_counter() - started) * 1000)
            emit("delegate.answered", route=route_id, ms=ms,
                 chars=len(answer))
            return ActionResult(
                ok=True, summary=answer[:1500],
                evidence={"answer": answer, "route": route_id,
                          "latency_ms": call.latency_ms, "total_ms": ms})

        return ActionResult(
            ok=False, error="no_route",
            summary="I could not reach a stronger model just now.",
            evidence={"errors": errors})

    def verify(self, ability_id: str, args: dict[str, Any],
               result: ActionResult) -> VerificationResult:
        chars = len(result.evidence.get("answer", ""))
        return VerificationResult(
            verified=result.ok and chars > 0, strategy="answer_returned",
            detail=f"{chars} characters from "
                   f"{result.evidence.get('route', 'no route')}",
            checked={"chars": chars})
