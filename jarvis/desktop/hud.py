"""The JARVIS heads-up display."""

from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from jarvis.abilities import registry as ability_registry
from jarvis.desktop import theme
from jarvis.desktop.bridge import ConfirmationRequest, Core
from jarvis.desktop.widgets.gauge import Gauge
from jarvis.desktop.widgets.reactor import ArcReactor
from jarvis.desktop.widgets.waveform import Waveform


def _panel(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("hud", "panel")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(12, 10, 12, 12)
    lay.setSpacing(6)
    if title:
        label = QLabel(title.upper())
        label.setProperty("hud", "section")
        lay.addWidget(label)
    return frame, lay


def _row(key: str, value: str, colour: QColor | None = None) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 2, 0, 2)
    k = QLabel(key)
    k.setStyleSheet(f"color:{theme.css(theme.DIM)}; font-size:11px;")
    v = QLabel(value)
    v.setStyleSheet(
        f"color:{theme.css(colour or theme.TEXT)}; font-size:11px;"
        f"font-family:'{theme.FONT_MONO}';")
    v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(k)
    lay.addStretch(1)
    lay.addWidget(v)
    return w


class HudWindow(QWidget):
    def __init__(self, core: Core) -> None:
        super().__init__()
        self.core = core
        self.setObjectName("root")
        self.setWindowTitle("J.A.R.V.I.S")
        # Minimum is what the three columns genuinely need; the opening size is
        # clamped to the available screen so the right-hand trace column is
        # never cut off on a smaller display.
        self.setMinimumSize(1100, 660)
        self.resize(1360, 840)
        self.setStyleSheet(theme.STYLESHEET)

        self._trace_cards: dict[str, QVBoxLayout] = {}
        self._build()
        self._wire()

        self._telemetry_timer = QTimer(self)
        self._telemetry_timer.timeout.connect(self._refresh_telemetry)
        self._telemetry_timer.start(2500)
        self._refresh_telemetry()
        self._refresh_registry()

    # -- construction -------------------------------------------------------

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        root.addWidget(self._build_left(), 0)
        root.addWidget(self._build_centre(), 1)
        root.addWidget(self._build_right(), 0)

    def _build_left(self) -> QWidget:
        col = QWidget()
        col.setMinimumWidth(210)
        col.setMaximumWidth(250)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        tele, tlay = _panel("telemetry")
        gauges = QHBoxLayout()
        gauges.setSpacing(4)
        self.g_cpu, self.g_ram, self.g_disk = Gauge("CPU"), Gauge("RAM"), Gauge("DISK")
        for g in (self.g_cpu, self.g_ram, self.g_disk):
            gauges.addWidget(g)
        tlay.addLayout(gauges)
        lay.addWidget(tele)

        reg, rlay = _panel("model registry")
        self.registry_box = QVBoxLayout()
        self.registry_box.setSpacing(0)
        rlay.addLayout(self.registry_box)
        lay.addWidget(reg)

        cap, clay = _panel("abilities")
        self.ability_count = QLabel("—")
        self.ability_count.setStyleSheet(
            f"color:{theme.css(theme.CYAN)}; font-size:26px; font-weight:700;")
        clay.addWidget(self.ability_count)
        hint = QLabel("registered capabilities")
        hint.setStyleSheet(f"color:{theme.css(theme.DIM)}; font-size:9px;")
        clay.addWidget(hint)
        lay.addWidget(cap)

        lay.addStretch(1)
        return col

    def _build_centre(self) -> QWidget:
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # -- header with reactor
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(18)

        self.reactor = ArcReactor(diameter=150)
        self.reactor.set_click_handler(self.core.toggle_voice)
        hl.addWidget(self.reactor)

        titles = QWidget()
        tl = QVBoxLayout(titles)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        tl.addStretch(1)
        t = QLabel("J . A . R . V . I . S")
        t.setProperty("hud", "title")
        tl.addWidget(t)
        s = QLabel("AUTONOMOUS COMPUTER CONTROL")
        s.setProperty("hud", "sub")
        tl.addWidget(s)
        self.state_label = QLabel("IDLE")
        self.state_label.setStyleSheet(
            f"color:{theme.css(theme.CYAN)}; font-size:12px; font-weight:700;"
            f"letter-spacing:3px;")
        tl.addWidget(self.state_label)
        self.wave = Waveform()
        tl.addWidget(self.wave)
        tl.addStretch(1)
        hl.addWidget(titles, 1)
        lay.addWidget(head)

        # -- conversation
        conv, cl = _panel()
        self.conv_area = QScrollArea()
        self.conv_area.setWidgetResizable(True)
        holder = QWidget()
        self.conv_box = QVBoxLayout(holder)
        self.conv_box.setContentsMargins(2, 2, 2, 2)
        self.conv_box.setSpacing(9)
        self.conv_box.addStretch(1)
        self.conv_area.setWidget(holder)
        cl.addWidget(self.conv_area)
        lay.addWidget(conv, 1)

        self._say("system",
                  "Ready. Click the reactor to talk, or type below.\n"
                  "Every action is verified against the real machine state, and "
                  "anything risky asks first.")

        # -- composer
        comp = QWidget()
        cml = QHBoxLayout(comp)
        cml.setContentsMargins(0, 0, 0, 0)
        cml.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Tell JARVIS what to do…")
        self.input.returnPressed.connect(self._submit)
        cml.addWidget(self.input, 1)
        self.send_btn = QPushButton("EXECUTE")
        self.send_btn.clicked.connect(self._submit)
        cml.addWidget(self.send_btn)
        self.voice_btn = QPushButton("VOICE")
        self.voice_btn.clicked.connect(self.core.toggle_voice)
        cml.addWidget(self.voice_btn)
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setProperty("hud", "danger")
        self.stop_btn.clicked.connect(self.core.stop_voice)
        cml.addWidget(self.stop_btn)
        lay.addWidget(comp)
        return col

    def _build_right(self) -> QWidget:
        col = QWidget()
        col.setMinimumWidth(280)
        col.setMaximumWidth(380)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        pending, plyt = _panel("awaiting approval")
        self.confirm_box = QVBoxLayout()
        self.confirm_box.setSpacing(8)
        plyt.addLayout(self.confirm_box)
        self.no_pending = QLabel("nothing pending")
        self.no_pending.setStyleSheet(
            f"color:{theme.css(theme.DIM2)}; font-size:10px;")
        plyt.addWidget(self.no_pending)
        lay.addWidget(pending)

        trace, tl = _panel("live execution trace")
        area = QScrollArea()
        area.setWidgetResizable(True)
        holder = QWidget()
        self.trace_box = QVBoxLayout(holder)
        self.trace_box.setContentsMargins(2, 2, 2, 2)
        self.trace_box.setSpacing(8)
        self.trace_box.addStretch(1)
        area.setWidget(holder)
        tl.addWidget(area)
        lay.addWidget(trace, 1)
        return col

    # -- wiring -------------------------------------------------------------

    def _wire(self) -> None:
        c = self.core
        c.goal_started.connect(self._on_goal_started)
        c.goal_finished.connect(self._on_goal_finished)
        c.goal_failed.connect(self._on_goal_failed)
        c.event_received.connect(self._on_event)
        c.confirm_requested.connect(self._on_confirm)
        c.state_changed.connect(self._on_state)
        c.transcript.connect(self._on_transcript)
        c.input_level.connect(self.wave.push_input)
        c.input_level.connect(self.reactor.set_level)
        c.output_level.connect(self.wave.push_output)
        c.output_level.connect(self.reactor.set_level)
        c.voice_state.connect(self._on_voice_state)

    # -- conversation -------------------------------------------------------

    def _say(self, who: str, text: str) -> None:
        colour = {"you": theme.CYAN, "jarvis": theme.GREEN,
                  "system": theme.DIM, "error": theme.RED}.get(who, theme.DIM)
        block = QWidget()
        lay = QVBoxLayout(block)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        tag = QLabel(who.upper())
        tag.setStyleSheet(
            f"color:{theme.css(colour)}; font-size:8px; font-weight:700;"
            f"letter-spacing:2px;")
        lay.addWidget(tag)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(
            f"color:{theme.css(theme.TEXT)}; font-size:12.5px;"
            f"font-family:'{theme.FONT_MONO}';"
            f"background:{theme.css(QColor(8, 16, 26), 0.75)};"
            f"border-left:2px solid {theme.css(colour)};"
            f"padding:8px 11px;")
        lay.addWidget(body)

        self.conv_box.insertWidget(self.conv_box.count() - 1, block)
        QTimer.singleShot(30, lambda: self.conv_area.verticalScrollBar().setValue(
            self.conv_area.verticalScrollBar().maximum()))

    @pyqtSlot(str, str)
    def _on_transcript(self, who: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if who in ("action", "result"):
            self._trace_line("voice", f"{who}: {text}",
                             theme.AMBER if who == "action" else theme.DIM)
            return
        # Live transcription streams in fragments; append to the trailing bubble
        # from the same speaker instead of stacking one bubble per word.
        last = self._last_block()
        if last is not None and last[0] == who:
            label = last[1]
            joiner = "" if text.startswith((" ", ",", ".", "?", "!")) else " "
            label.setText((label.text() + joiner + text).strip())
            return
        # A fragment that is only punctuation is the tail of a turn that has
        # already been rendered (or attributed to the other speaker). Opening a
        # new bubble for it produces the stray "." turns seen in testing.
        if not any(ch.isalnum() for ch in text):
            return
        self._say(who, text)

    def _last_block(self):
        idx = self.conv_box.count() - 2      # -1 is the stretch
        if idx < 0:
            return None
        item = self.conv_box.itemAt(idx)
        if item is None or item.widget() is None:
            return None
        w = item.widget()
        labels = w.findChildren(QLabel)
        if len(labels) < 2:
            return None
        return labels[0].text().lower(), labels[1]

    # -- goals --------------------------------------------------------------

    def _submit(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        if self.core.busy():
            self._say("system", "A task is already running — one at a time.")
            return
        self.input.clear()
        self._say("you", text)
        self.core.submit_goal(text)

    @pyqtSlot(str, str)
    def _on_goal_started(self, goal_id: str, objective: str) -> None:
        self._on_state("thinking")
        self._trace_card(goal_id, objective)

    @pyqtSlot(object)
    def _on_goal_finished(self, record: Any) -> None:
        self._on_state("idle")
        ok = bool(getattr(record, "ok", False))
        self._say("jarvis" if ok else "error",
                  getattr(record, "message", "") or "(no output)")
        gid = getattr(getattr(record, "goal", None), "id", "")
        for step in getattr(getattr(record, "plan", None), "steps", []) or []:
            colour = (theme.GREEN if step.status == "done"
                      else theme.RED if step.status == "failed" else theme.AMBER)
            self._trace_line(gid, f"{step.ability} → {step.status}", colour)

    @pyqtSlot(str, str)
    def _on_goal_failed(self, goal_id: str, error: str) -> None:
        self._on_state("error")
        self._say("error", error)
        self._trace_line(goal_id, error, theme.RED)
        QTimer.singleShot(2500, lambda: self._on_state("idle"))

    # -- state --------------------------------------------------------------

    @pyqtSlot(str)
    def _on_state(self, state: str) -> None:
        self.reactor.set_state(state)
        self.state_label.setText(state.upper())
        colour = theme.STATE_COLOURS.get(state, theme.CYAN)
        self.state_label.setStyleSheet(
            f"color:{theme.css(colour)}; font-size:12px; font-weight:700;"
            f"letter-spacing:3px;")

    @pyqtSlot(bool)
    def _on_voice_state(self, active: bool) -> None:
        self.voice_btn.setText("END VOICE" if active else "VOICE")
        self.voice_btn.setProperty("hud", "danger" if active else "")
        self.voice_btn.style().unpolish(self.voice_btn)
        self.voice_btn.style().polish(self.voice_btn)
        if not active:
            self._on_state("idle")

    # -- confirmation -------------------------------------------------------

    @pyqtSlot(object)
    def _on_confirm(self, req: ConfirmationRequest) -> None:
        self.no_pending.hide()
        card = QFrame()
        card.setStyleSheet(
            f"background:{theme.css(theme.AMBER, 0.07)};"
            f"border:1px solid {theme.css(theme.AMBER, 0.6)};")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(4)

        head = QLabel(f"{req.risk.value.upper()} RISK")
        head.setStyleSheet(
            f"color:{theme.css(theme.AMBER)}; font-size:9px; font-weight:700;"
            f"letter-spacing:2px; border:none;")
        lay.addWidget(head)

        name = QLabel(req.ability_id)
        name.setStyleSheet(
            f"color:{theme.css(theme.TEXT)}; font-size:12px; font-weight:700;"
            f"border:none;")
        lay.addWidget(name)

        args = QLabel(str(req.args)[:220])
        args.setWordWrap(True)
        args.setStyleSheet(
            f"color:{theme.css(theme.DIM)}; font-size:10px;"
            f"font-family:'{theme.FONT_MONO}'; border:none;")
        lay.addWidget(args)

        btns = QHBoxLayout()
        approve = QPushButton("APPROVE")
        approve.setProperty("hud", "ok")
        deny = QPushButton("DENY")
        deny.setProperty("hud", "danger")
        btns.addWidget(approve)
        btns.addWidget(deny)
        lay.addLayout(btns)

        def finish(ok: bool) -> None:
            self.core.resolve_confirmation(req, ok)
            head.setText("APPROVED" if ok else "DENIED")
            approve.setEnabled(False)
            deny.setEnabled(False)
            QTimer.singleShot(2200, card.deleteLater)

        approve.clicked.connect(lambda: finish(True))
        deny.clicked.connect(lambda: finish(False))
        self.confirm_box.addWidget(card)

    # -- trace --------------------------------------------------------------

    def _trace_card(self, goal_id: str, objective: str) -> QVBoxLayout:
        if goal_id in self._trace_cards:
            return self._trace_cards[goal_id]
        card = QFrame()
        card.setStyleSheet(
            f"background:{theme.css(QColor(8, 16, 26), 0.7)};"
            f"border:1px solid {theme.css(theme.CYAN, 0.2)};")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 9)
        lay.setSpacing(3)
        head = QLabel(objective[:70])
        head.setWordWrap(True)
        head.setStyleSheet(
            f"color:{theme.css(theme.CYAN)}; font-size:10px; font-weight:700;"
            f"border:none;")
        lay.addWidget(head)
        self.trace_box.insertWidget(self.trace_box.count() - 1, card)
        self._trace_cards[goal_id] = lay
        return lay

    def _trace_line(self, goal_id: str, text: str, colour: QColor) -> None:
        lay = self._trace_cards.get(goal_id) or self._trace_card(goal_id, goal_id)
        line = QLabel(text[:150])
        line.setWordWrap(True)
        line.setStyleSheet(
            f"color:{theme.css(colour)}; font-size:10px;"
            f"font-family:'{theme.FONT_MONO}'; border:none;")
        lay.addWidget(line)

    @pyqtSlot(dict)
    def _on_event(self, rec: dict) -> None:
        kind = rec.get("kind", "")
        gid = rec.get("goal_id") or "voice"
        if kind == "plan.created":
            self._trace_line(gid, f"plan: {rec.get('steps')} step(s) via "
                                  f"{rec.get('route', '')}", theme.CYAN)
        elif kind == "model.call":
            self._trace_line(gid, f"model: {rec.get('route', '')} "
                                  f"{rec.get('latency_ms', '')}ms", theme.DIM)
        elif kind == "ability.executed":
            self._on_state("acting")
            ok = rec.get("ok")
            self._trace_line(gid, f"{rec.get('ability')} "
                                  f"{'ok' if ok else 'FAILED'}",
                             theme.GREEN if ok else theme.RED)
        elif kind == "voice.tool_call":
            self._trace_line("voice", f"tool: {rec.get('ability')}", theme.AMBER)
        elif kind == "goal.unsupported":
            self._trace_line(gid, "capability gap", theme.AMBER)

    # -- telemetry ----------------------------------------------------------

    def _refresh_telemetry(self) -> None:
        try:
            st = self.core.orch.local_os.state()
        except Exception:      # noqa: BLE001 - telemetry must never break the UI
            return
        self.g_cpu.set_value(st.get("cpu_percent", 0))
        self.g_ram.set_value(st.get("ram_percent", 0))
        self.g_disk.set_value(st.get("disk_percent", 0))

    def _refresh_registry(self) -> None:
        self.ability_count.setText(str(len(ability_registry.all_abilities())))
        try:
            rows = self.core.orch.router.status()
        except Exception:      # noqa: BLE001
            return
        for row in rows:
            colour = theme.GREEN if row["available"] else theme.DIM2
            label = row["id"].split(":")[0]
            self.registry_box.addWidget(
                _row(label, "ready" if row["available"] else "off", colour))

    # -- lifecycle ----------------------------------------------------------

    def closeEvent(self, event) -> None:
        self.core.shutdown()
        super().closeEvent(event)
