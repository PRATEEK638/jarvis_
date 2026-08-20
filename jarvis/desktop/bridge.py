"""Threading bridge between Qt and the JARVIS core.

Qt's rule is absolute: only the main thread touches widgets. Everything the
core does - model calls, tool execution, audio - is blocking, slow, or both.
So each concern gets a worker, and results come back as signals.

    UI thread            worker threads                core
    ---------            --------------                ----
    HudWindow    <--signals--  GoalWorker      -->  Orchestrator.run()
                 <--signals--  VoiceWorker     -->  VoiceSession (asyncio)
                 <--signals--  EventRelay      <--  events.subscribe()

The confirmation gate deliberately keeps the orchestrator thread *blocked* on a
threading.Event until the user answers in the UI. That is the same mechanism the
web server uses, and it is what makes the gate real: the action genuinely cannot
proceed without an answer, rather than being asked about after the fact.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from jarvis.core import events
from jarvis.core.contracts import Risk, TaskRecord
from jarvis.core.orchestrator import Orchestrator


class ConfirmationRequest:
    """One pending risky action, awaiting a human decision."""

    __slots__ = ("id", "ability_id", "args", "risk", "event", "approved")

    def __init__(self, ability_id: str, args: dict[str, Any], risk: Risk) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.ability_id = ability_id
        self.args = args
        self.risk = risk
        self.event = threading.Event()
        self.approved = False


class Core(QObject):
    """Owns the Orchestrator and marshals every interaction with it."""

    # -- signals to the UI --------------------------------------------------
    goal_started = pyqtSignal(str, str)          # goal_id, objective
    goal_finished = pyqtSignal(object)           # TaskRecord
    goal_failed = pyqtSignal(str, str)           # goal_id, error
    event_received = pyqtSignal(dict)            # raw core event
    confirm_requested = pyqtSignal(object)       # ConfirmationRequest
    state_changed = pyqtSignal(str)              # idle/thinking/acting/...
    transcript = pyqtSignal(str, str)            # who, text
    input_level = pyqtSignal(float)
    output_level = pyqtSignal(float)
    voice_state = pyqtSignal(bool)               # session active?

    def __init__(self) -> None:
        super().__init__()
        self.orch = Orchestrator(confirm=self._confirm, on_progress=lambda _m: None)
        self._pending: dict[str, ConfirmationRequest] = {}
        self._lock = threading.Lock()
        self._goal_thread: QThread | None = None
        self._voice: VoiceWorker | None = None

        # Reuse the core's existing pub/sub - the desktop app needs no new
        # instrumentation to see what the orchestrator is doing.
        self._token = events.subscribe(self._on_core_event)

    # -- core event fan-out -------------------------------------------------

    def _on_core_event(self, record: dict[str, Any]) -> None:
        # Runs on whichever thread emitted. Qt queues the signal across to the
        # UI thread automatically, which is exactly what is wanted here.
        self.event_received.emit(record)

    # -- confirmation -------------------------------------------------------

    def _confirm(self, ability_id: str, args: dict[str, Any], risk: Risk) -> bool:
        """Blocks the calling (worker) thread until the UI answers."""
        req = ConfirmationRequest(ability_id, args, risk)
        with self._lock:
            self._pending[req.id] = req
        self.confirm_requested.emit(req)
        # Bounded: an unattended prompt must eventually resolve rather than
        # pinning a worker thread forever.
        answered = req.event.wait(timeout=300)
        with self._lock:
            self._pending.pop(req.id, None)
        if not answered:
            events.emit("confirmation.timeout", ability=ability_id)
            return False
        return req.approved

    def resolve_confirmation(self, req: ConfirmationRequest, approved: bool) -> None:
        req.approved = approved
        req.event.set()

    # -- goals --------------------------------------------------------------

    def submit_goal(self, objective: str) -> str | None:
        if self._goal_thread is not None and self._goal_thread.isRunning():
            return None       # one goal at a time; working memory is not shared
        goal_id = uuid.uuid4().hex[:10]
        worker = GoalWorker(self.orch, objective, goal_id)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self.goal_finished.emit)
        worker.failed.connect(self.goal_failed.emit)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        # Keep references or Qt will collect the thread mid-run.
        self._goal_thread = thread
        self._goal_worker = worker
        thread.start()
        self.goal_started.emit(goal_id, objective)
        return goal_id

    def busy(self) -> bool:
        return self._goal_thread is not None and self._goal_thread.isRunning()

    # -- voice --------------------------------------------------------------

    def voice_active(self) -> bool:
        return self._voice is not None and self._voice.isRunning()

    def toggle_voice(self) -> None:
        if self.voice_active():
            self.stop_voice()
        else:
            self.start_voice()

    def start_voice(self) -> None:
        if self.voice_active():
            return
        self._voice = VoiceWorker(self.orch)
        self._voice.transcript.connect(self.transcript.emit)
        self._voice.input_level.connect(self.input_level.emit)
        self._voice.output_level.connect(self.output_level.emit)
        self._voice.state_changed.connect(self.state_changed.emit)
        self._voice.finished.connect(lambda: self.voice_state.emit(False))
        self._voice.start()
        self.voice_state.emit(True)

    def stop_voice(self) -> None:
        if self._voice is not None:
            self._voice.stop()

    def shutdown(self) -> None:
        events.unsubscribe(self._token)
        self.stop_voice()
        if self._voice is not None:
            self._voice.wait(3000)
        if self._goal_thread is not None:
            self._goal_thread.quit()
            self._goal_thread.wait(3000)
        self.orch.close()


class GoalWorker(QObject):
    """Runs one goal to completion on a worker thread."""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, orch: Orchestrator, objective: str, goal_id: str) -> None:
        super().__init__()
        self._orch = orch
        self._objective = objective
        self._goal_id = goal_id

    def run(self) -> None:
        try:
            record: TaskRecord = self._orch.run(self._objective,
                                                goal_id=self._goal_id)
            self.finished.emit(record)
        except Exception as exc:      # noqa: BLE001 - must surface, not vanish
            self.failed.emit(self._goal_id, f"{type(exc).__name__}: {exc}")


class VoiceWorker(QThread):
    """Owns a Gemini Live session for the lifetime of a conversation.

    Uses the session's *native* transport: this machine's microphone and
    speakers directly, which is why the desktop app gets barge-in and
    conversational latency the browser build could not.
    """

    transcript = pyqtSignal(str, str)
    input_level = pyqtSignal(float)
    output_level = pyqtSignal(float)
    state_changed = pyqtSignal(str)

    def __init__(self, orch: Orchestrator) -> None:
        super().__init__()
        self._orch = orch
        self._session = None

    def run(self) -> None:
        from jarvis.voice.live import VoiceSession, voice_available

        ok, reason = voice_available()
        if not ok:
            self.transcript.emit("system", f"Voice unavailable: {reason}")
            self.state_changed.emit("error")
            return

        def execute(ability_id: str, args: dict[str, Any]) -> str:
            self.state_changed.emit("acting")
            try:
                return self._orch.call_ability(ability_id, args)
            finally:
                self.state_changed.emit("speaking")

        def on_event(kind: str, text: str) -> None:
            if kind in ("you", "jarvis"):
                self.transcript.emit(kind, text)
                self.state_changed.emit(
                    "listening" if kind == "you" else "speaking")
            elif kind == "status":
                self.state_changed.emit(
                    "listening" if text == "listening" else "idle")
            elif kind == "action":
                self.transcript.emit("action", text)
            elif kind == "result":
                self.transcript.emit("result", text)

        self._session = VoiceSession(
            execute,
            on_event=on_event,
            on_input_level=self.input_level.emit,
            on_output_level=self.output_level.emit,
        )
        try:
            self._session.run()
        except Exception as exc:      # noqa: BLE001
            self.transcript.emit("system", f"Voice session ended: {exc}")
            self.state_changed.emit("error")
        finally:
            self.state_changed.emit("idle")

    def stop(self) -> None:
        if self._session is not None:
            self._session.stop()
