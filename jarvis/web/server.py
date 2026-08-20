"""The local web control interface.

This is a thin surface over the *same* Orchestrator the CLI and voice runner
use -- one Ability registry, one Router, one Policy gate, one Memory store, one
event log. The server adds no separate intelligence: it turns HTTP/WebSocket
requests into `orch.run()` / `orch.call_ability()` calls, and turns the
orchestrator's own event stream into what the browser sees.

    goal (text or voice) -> Orchestrator -> abilities/environments -> verify
                                  |
                                  v
                          events.emit() (existing)
                                  |
                                  v
                    /ws/events  <-- every connected browser tab

Endpoints:
    GET  /                    the control interface
    GET  /api/status          model registry, environments, voice readiness
    GET  /api/abilities       every registered capability
    GET  /api/history         recent completed tasks
    POST /api/goal            submit a text command; runs in the background
    POST /api/confirm/{id}    resolve a pending medium/high-risk confirmation
    WS   /ws/events           live stream of every orchestrator event
    WS   /ws/voice            browser microphone <-> Gemini Live <-> orchestrator

Concurrency: one Orchestrator instance, one goal executed at a time (a
single-worker thread pool serializes them) -- this is a single-user control
surface, not a multi-tenant API, and the orchestrator's working memory is not
built to be shared across concurrent goals.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from jarvis.abilities import registry as ability_registry
from jarvis.core import events
from jarvis.core.contracts import Risk
from jarvis.core.orchestrator import Orchestrator
from jarvis.interface.voice import status as text_voice_status

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="JARVIS control interface")

# One orchestrator, one goal at a time -- see module docstring.
_orch = Orchestrator(confirm=lambda a, args, risk: _web_confirm(a, args, risk),
                     on_progress=lambda _msg: None)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-goal")


# ---------------------------------------------------------------------------
# Confirmation bridge: a synchronous orchestrator callback, resolved by an
# async HTTP request from the browser. This is the one place where the web
# layer and the orchestrator's blocking call style have to meet.
# ---------------------------------------------------------------------------

class _Pending:
    __slots__ = ("id", "ability_id", "args", "risk", "event", "approved")

    def __init__(self, ability_id: str, args: dict[str, Any], risk: Risk) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.ability_id = ability_id
        self.args = args
        self.risk = risk
        self.event = threading.Event()
        self.approved = False


_pending: dict[str, _Pending] = {}
_pending_lock = threading.Lock()
CONFIRM_TIMEOUT_S = 300  # an unattended prompt must eventually give up, not hang forever


def _web_confirm(ability_id: str, args: dict[str, Any], risk: Risk) -> bool:
    """Runs on the orchestrator's worker thread; blocks until the browser answers.

    Used for BOTH text commands (orch.run) and voice tool calls
    (orch.call_ability) -- one confirmation surface for one brain, regardless
    of which input path triggered the risky action.
    """
    pending = _Pending(ability_id, args, risk)
    with _pending_lock:
        _pending[pending.id] = pending
    events.emit("confirmation.requested", id=pending.id, ability=ability_id,
                args=args, risk=risk.value)
    answered = pending.event.wait(timeout=CONFIRM_TIMEOUT_S)
    with _pending_lock:
        _pending.pop(pending.id, None)
    if not answered:
        events.emit("confirmation.timeout", id=pending.id, ability=ability_id)
        return False
    events.emit("confirmation.resolved", id=pending.id, approved=pending.approved)
    return pending.approved


class ConfirmDecision(BaseModel):
    approve: bool


@app.post("/api/confirm/{confirmation_id}")
async def confirm(confirmation_id: str, decision: ConfirmDecision) -> dict[str, Any]:
    with _pending_lock:
        pending = _pending.get(confirmation_id)
    if pending is None:
        return JSONResponse({"error": "no such pending confirmation, or it "
                                      "already timed out"}, status_code=404)
    pending.approved = decision.approve
    pending.event.set()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Text goals
# ---------------------------------------------------------------------------

class GoalRequest(BaseModel):
    objective: str


@app.post("/api/goal")
async def submit_goal(body: GoalRequest) -> dict[str, Any]:
    objective = body.objective.strip()
    if not objective:
        return JSONResponse({"error": "empty objective"}, status_code=400)
    goal_id = uuid.uuid4().hex[:12]
    events.emit("web.goal_submitted", goal_id=goal_id, objective=objective)

    def _run() -> None:
        try:
            record = _orch.run(objective, goal_id=goal_id)
            events.emit("web.goal_finished", goal_id=goal_id,
                        record=record.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 - must reach the UI, not vanish
            events.emit("web.goal_error", goal_id=goal_id,
                        error=f"{type(exc).__name__}: {exc}")

    _executor.submit(_run)
    return {"goal_id": goal_id, "status": "started"}


# ---------------------------------------------------------------------------
# Status / introspection
# ---------------------------------------------------------------------------

def _gather_status() -> dict[str, Any]:
    """Synchronous and potentially slow (live probes, subprocess calls) -
    always run off the event loop, or one slow route would stall every
    connected browser tab, including the live event stream."""
    env_state: dict[str, Any] = {}
    for env_id, env in _orch.environments.items():
        try:
            env_state[env_id] = env.state()
        except Exception as exc:  # noqa: BLE001
            env_state[env_id] = {"error": str(exc)}
    return {
        "models": _orch.router.status(),
        "registry": _orch.router.describe(),
        "environments": env_state,
        "voice": text_voice_status(),
    }


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return await run_in_threadpool(_gather_status)


@app.get("/api/abilities")
async def abilities() -> list[dict[str, Any]]:
    return [a.model_dump(mode="json") for a in ability_registry.all_abilities()]


@app.get("/api/history")
async def history(limit: int = 20) -> list[dict[str, Any]]:
    return _orch.store.recent_tasks(limit=limit)


@app.get("/api/memory")
async def memory(memory_type: str = "semantic") -> list[dict[str, Any]]:
    from jarvis.core.contracts import MemoryType
    try:
        mt = MemoryType(memory_type)
    except ValueError:
        return JSONResponse({"error": f"unknown memory type '{memory_type}'"},
                            status_code=400)
    return [m.model_dump(mode="json") for m in _orch.store.all_memories(mt)]


# ---------------------------------------------------------------------------
# Live event stream -- the "honest window": every browser tab sees exactly
# what the orchestrator itself is doing, via the same events.emit() calls
# that already drive the disk log.
# ---------------------------------------------------------------------------

@app.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)

    def on_event(record: dict[str, Any]) -> None:
        # Called from whatever thread emitted the event (often a background
        # goal-execution thread). Hand off without blocking that thread.
        try:
            loop.call_soon_threadsafe(queue.put_nowait, record)
        except (asyncio.QueueFull, RuntimeError):
            pass  # a dropped status line is better than lag or a crash

    token = events.subscribe(on_event)
    try:
        while True:
            record = await queue.get()
            await ws.send_json(record)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        events.unsubscribe(token)


# ---------------------------------------------------------------------------
# Voice bridge: browser mic -> Gemini Live -> orchestrator abilities -> browser
# speaker. See jarvis/voice/live.py for the session itself; this endpoint only
# adapts its transport to a WebSocket instead of this machine's own mic/speaker.
# ---------------------------------------------------------------------------

@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    from jarvis.config import settings
    from jarvis.voice.live import VoiceSession

    await ws.accept()

    key = settings.get_key("gemini_voice") or settings.get_key("gemini")
    if not key:
        await ws.send_json({"type": "error",
                            "text": "no Gemini key configured for voice"})
        await ws.close()
        return

    mic_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=50)
    send_queue: asyncio.Queue[Any] = asyncio.Queue()

    async def mic_chunks():
        while True:
            chunk = await mic_queue.get()
            if chunk is None:
                return
            yield chunk

    def audio_out(pcm: bytes) -> None:
        send_queue.put_nowait(pcm)

    def on_interrupt() -> None:
        # Drop whatever is queued but not yet sent, then tell the browser to
        # stop playback immediately -- this is barge-in, not just a new turn.
        try:
            while True:
                send_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        send_queue.put_nowait({"type": "interrupt"})

    def on_event(kind: str, text: str) -> None:
        send_queue.put_nowait({"type": "event", "kind": kind, "text": text})

    def execute(ability_id: str, args: dict[str, Any]) -> str:
        # The SAME orchestrator instance as text goals: same guardrails, same
        # confirmation bridge, same verification, same memory.
        return _orch.call_ability(ability_id, args)

    session = VoiceSession(execute, on_event=on_event,
                           mic_chunks=mic_chunks(), audio_out=audio_out,
                           on_interrupt=on_interrupt)

    async def sender_loop() -> None:
        while True:
            item = await send_queue.get()
            if isinstance(item, (bytes, bytearray)):
                await ws.send_bytes(item)
            else:
                await ws.send_json(item)

    async def receiver_loop() -> None:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                await mic_queue.put(None)
                return
            data = message.get("bytes")
            if data is not None:
                try:
                    mic_queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass
                continue
            text = message.get("text")
            if text:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "stop":
                    session.stop()
                    await mic_queue.put(None)
                    return

    sender_task = asyncio.create_task(sender_loop())
    receiver_task = asyncio.create_task(receiver_loop())
    try:
        await session.run_async()
    except Exception as exc:  # noqa: BLE001 - must reach the browser, not vanish
        try:
            await ws.send_json({"type": "error",
                                "text": f"{type(exc).__name__}: {exc}"})
        except Exception:  # noqa: BLE001 - socket may already be gone
            pass
    finally:
        sender_task.cancel()
        receiver_task.cancel()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.on_event("shutdown")
def _shutdown() -> None:
    _executor.shutdown(wait=False)
    _orch.close()
