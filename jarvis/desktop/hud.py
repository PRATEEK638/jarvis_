"""The JARVIS heads-up display.

Layout follows the shape a control interface actually needs: a status rail on
the left, the HUD canvas and conversation in the middle, and an operations
column (approvals, execution trace) on the right, under a single header strip.

Everything shown here is real. The canvas is driven by measured audio
amplitude, the metrics by psutil, the registry by the live model router, and
the trace by the orchestrator's own event bus.
"""

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
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from jarvis.abilities import registry as ability_registry
from jarvis.desktop import theme
from jarvis.desktop.bridge import ConfirmationRequest, Core
from jarvis.desktop.widgets.hud_canvas import HudCanvas
from jarvis.desktop.widgets.metricbar import MetricBar

MONO = theme.FONT_MONO


def _label(text: str, size: int, colour: QColor, *, bold: bool = False,
           spacing: float = 0.0, mono: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(
        f"color:{theme.css(colour)}; font-size:{size}px;"
        f"{'font-weight:700;' if bold else ''}"
        f"{f'letter-spacing:{spacing}px;' if spacing else ''}"
        f"font-family:'{MONO if mono else theme.FONT_HUD}';"
        f"background:transparent; border:none;")
    return lab


def _section(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setStyleSheet(
        f"background:{theme.css(QColor(1, 13, 20), 0.92)};"
        f"border:1px solid {theme.css(theme.CYAN, 0.16)};")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(9, 7, 9, 9)
    lay.setSpacing(5)
    head = _label(f"◈  {title.upper()}", 8, theme.CYAN, bold=True, spacing=1.4)
    lay.addWidget(head)
    return frame, lay


class HudWindow(QWidget):
    def __init__(self, core: Core) -> None:
        super().__init__()
        self.core = core
        self.setObjectName("root")
        self.setWindowTitle("J.A.R.V.I.S — MARK I")
        self.setMinimumSize(1040, 660)
        self.resize(1400, 880)
        self.setStyleSheet(theme.STYLESHEET)

        self._trace_cards: dict[str, QVBoxLayout] = {}
        self._registry_rows: dict[str, QLabel] = {}
        self._build()
        self._wire()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(2000)
        self._refresh()
        self._refresh_registry()

    # -- construction -------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())

        body = QHBoxLayout()
        body.setContentsMargins(10, 10, 10, 10)
        body.setSpacing(10)
        body.addWidget(self._left(), 0)

        centre = QSplitter(Qt.Orientation.Vertical)
        centre.setStyleSheet("QSplitter::handle{background:rgba(62,240,255,40);"
                             "height:1px;}")
        self.canvas = HudCanvas()
        self.canvas.set_click_handler(self.core.toggle_voice)
        centre.addWidget(self.canvas)
        centre.addWidget(self._conversation())
        centre.setSizes([460, 320])
        body.addWidget(centre, 1)

        body.addWidget(self._right(), 0)
        root.addLayout(body, 1)
        root.addWidget(self._composer())

    def _header(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(46)
        bar.setStyleSheet(
            f"background:{theme.css(QColor(1, 13, 20))};"
            f"border-bottom:1px solid {theme.css(theme.CYAN, 0.3)};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        lay.addWidget(_label("◤ J.A.R.V.I.S", 15, theme.CYAN, bold=True,
                             spacing=2.5))
        lay.addWidget(_label("AUTONOMOUS COMPUTER CONTROL", 8, theme.DIM,
                             spacing=2))
        lay.addStretch(1)
        self.hdr_state = _label("● STANDBY", 10, theme.CYAN, bold=True, spacing=1.5)
        lay.addWidget(self.hdr_state)
        self.hdr_clock = _label("--:--:--", 11, theme.DIM)
        lay.addWidget(self.hdr_clock)
        return bar

    def _left(self) -> QWidget:
        col = QWidget()
        col.setFixedWidth(214)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)

        sysf, syslay = _section("system")
        self.m_cpu = MetricBar("CPU")
        self.m_ram = MetricBar("MEMORY")
        self.m_disk = MetricBar("DISK")
        for m in (self.m_cpu, self.m_ram, self.m_disk):
            syslay.addWidget(m)
        lay.addWidget(sysf)

        regf, reglay = _section("model routes")
        self.registry_box = QVBoxLayout()
        self.registry_box.setSpacing(1)
        reglay.addLayout(self.registry_box)
        lay.addWidget(regf)

        capf, caplay = _section("capabilities")
        row = QHBoxLayout()
        self.cap_count = _label("—", 24, theme.CYAN, bold=True)
        row.addWidget(self.cap_count)
        row.addStretch(1)
        row.addWidget(_label("ABILITIES", 8, theme.DIM, spacing=1))
        caplay.addLayout(row)
        self.voice_status = _label("voice: checking", 8, theme.DIM)
        caplay.addWidget(self.voice_status)
        lay.addWidget(capf)

        lay.addStretch(1)
        return col

    def _conversation(self) -> QWidget:
        frame, lay = _section("dialogue")
        area = QScrollArea()
        area.setWidgetResizable(True)
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        self.conv_box = QVBoxLayout(holder)
        self.conv_box.setContentsMargins(1, 1, 1, 1)
        self.conv_box.setSpacing(7)
        self.conv_box.addStretch(1)
        area.setWidget(holder)
        self.conv_area = area
        lay.addWidget(area)
        self._say("system",
                  "Systems online. Click the core to speak, or type below.\n"
                  "Actions are verified against real machine state; anything "
                  "risky requires your approval.")
        return frame

    def _right(self) -> QWidget:
        col = QWidget()
        col.setFixedWidth(330)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)

        appf, applay = _section("authorisation")
        self.confirm_box = QVBoxLayout()
        self.confirm_box.setSpacing(6)
        applay.addLayout(self.confirm_box)
        self.no_pending = _label("no pending requests", 9, theme.DIM2)
        applay.addWidget(self.no_pending)
        lay.addWidget(appf)

        trf, trlay = _section("execution trace")
        area = QScrollArea()
        area.setWidgetResizable(True)
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        self.trace_box = QVBoxLayout(holder)
        self.trace_box.setContentsMargins(1, 1, 1, 1)
        self.trace_box.setSpacing(6)
        self.trace_box.addStretch(1)
        area.setWidget(holder)
        trlay.addWidget(area)
        lay.addWidget(trf, 1)
        return col

    def _composer(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(
            f"background:{theme.css(QColor(1, 13, 20))};"
            f"border-top:1px solid {theme.css(theme.CYAN, 0.3)};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        lay.addWidget(_label("▶", 13, theme.CYAN, bold=True))
        self.input = QLineEdit()
        self.input.setPlaceholderText("Enter directive…")
        self.input.returnPressed.connect(self._submit)
        lay.addWidget(self.input, 1)

        self.send_btn = QPushButton("EXECUTE")
        self.send_btn.clicked.connect(self._submit)
        lay.addWidget(self.send_btn)
        self.voice_btn = QPushButton("◉ VOICE")
        self.voice_btn.clicked.connect(self.core.toggle_voice)
        lay.addWidget(self.voice_btn)
        self.stop_btn = QPushButton("■ STOP")
        self.stop_btn.setProperty("hud", "danger")
        self.stop_btn.clicked.connect(self.core.stop_voice)
        lay.addWidget(self.stop_btn)
        return bar

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
        c.input_level.connect(self.canvas.set_level)
        c.output_level.connect(self.canvas.set_level)
        c.voice_state.connect(self._on_voice_state)

    # -- dialogue -----------------------------------------------------------

    def _say(self, who: str, text: str) -> None:
        colour = {"you": theme.CYAN, "jarvis": theme.GREEN,
                  "system": theme.DIM, "error": theme.RED}.get(who, theme.DIM)
        block = QWidget()
        block.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(block)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(_label(f"▪ {who.upper()}", 8, colour, bold=True, spacing=1.2))

        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(
            f"color:{theme.css(theme.TEXT)}; font-size:12px;"
            f"font-family:'{MONO}';"
            f"background:{theme.css(QColor(2, 16, 24), 0.85)};"
            f"border-left:2px solid {theme.css(colour)};"
            f"padding:7px 10px;")
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
        last = self._last_block()
        if last is not None and last[0] == who:
            label = last[1]
            joiner = "" if text.startswith((" ", ",", ".", "?", "!")) else " "
            label.setText((label.text() + joiner + text).strip())
            return
        # A fragment of pure punctuation is the tail of a turn already shown;
        # opening a new bubble for it produces stray "." entries.
        if not any(ch.isalnum() for ch in text):
            return
        self._say(who, text)

    def _last_block(self):
        idx = self.conv_box.count() - 2
        if idx < 0:
            return None
        item = self.conv_box.itemAt(idx)
        if item is None or item.widget() is None:
            return None
        labels = item.widget().findChildren(QLabel)
        if len(labels) < 2:
            return None
        return labels[0].text().replace("▪", "").strip().lower(), labels[1]

    # -- goals --------------------------------------------------------------

    def _submit(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        if self.core.busy():
            self._say("system", "A directive is already executing.")
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
        self.canvas.set_state(state)
        colour = theme.STATE_COLOURS.get(state, theme.CYAN)
        text = {"idle": "● STANDBY", "listening": "● LISTENING",
                "thinking": "◈ THINKING", "acting": "▶ EXECUTING",
                "speaking": "● SPEAKING", "error": "⊘ FAULT"}.get(
            state, f"● {state.upper()}")
        self.hdr_state.setText(text)
        self.hdr_state.setStyleSheet(
            f"color:{theme.css(colour)}; font-size:10px; font-weight:700;"
            f"letter-spacing:1.5px; font-family:'{MONO}';"
            f"background:transparent; border:none;")

    @pyqtSlot(bool)
    def _on_voice_state(self, active: bool) -> None:
        self.voice_btn.setText("◉ END" if active else "◉ VOICE")
        self.voice_btn.setProperty("hud", "danger" if active else "")
        self.voice_btn.style().unpolish(self.voice_btn)
        self.voice_btn.style().polish(self.voice_btn)
        if not active:
            self._on_state("idle")

    # -- authorisation ------------------------------------------------------

    @pyqtSlot(object)
    def _on_confirm(self, req: ConfirmationRequest) -> None:
        self.no_pending.hide()
        card = QFrame()
        card.setStyleSheet(
            f"background:{theme.css(theme.AMBER, 0.07)};"
            f"border:1px solid {theme.css(theme.AMBER, 0.55)};")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(9, 7, 9, 9)
        lay.setSpacing(3)
        head = _label(f"⚠ {req.risk.value.upper()} RISK", 9, theme.AMBER,
                      bold=True, spacing=1.2)
        lay.addWidget(head)
        lay.addWidget(_label(req.ability_id, 12, theme.TEXT, bold=True))
        args = QLabel(str(req.args)[:200])
        args.setWordWrap(True)
        args.setStyleSheet(
            f"color:{theme.css(theme.DIM)}; font-size:9px;"
            f"font-family:'{MONO}'; background:transparent; border:none;")
        lay.addWidget(args)

        btns = QHBoxLayout()
        ok = QPushButton("AUTHORISE")
        ok.setProperty("hud", "ok")
        no = QPushButton("DENY")
        no.setProperty("hud", "danger")
        btns.addWidget(ok)
        btns.addWidget(no)
        lay.addLayout(btns)

        def finish(approved: bool) -> None:
            self.core.resolve_confirmation(req, approved)
            head.setText("✓ AUTHORISED" if approved else "✗ DENIED")
            ok.setEnabled(False)
            no.setEnabled(False)
            QTimer.singleShot(2200, card.deleteLater)

        ok.clicked.connect(lambda: finish(True))
        no.clicked.connect(lambda: finish(False))
        self.confirm_box.addWidget(card)

    # -- trace --------------------------------------------------------------

    def _trace_card(self, goal_id: str, objective: str) -> QVBoxLayout:
        if goal_id in self._trace_cards:
            return self._trace_cards[goal_id]
        card = QFrame()
        card.setStyleSheet(
            f"background:{theme.css(QColor(2, 16, 24), 0.8)};"
            f"border-left:2px solid {theme.css(theme.CYAN, 0.6)};")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(2)
        head = QLabel(objective[:80])
        head.setWordWrap(True)
        head.setStyleSheet(
            f"color:{theme.css(theme.CYAN)}; font-size:9.5px; font-weight:700;"
            f"font-family:'{MONO}'; background:transparent; border:none;")
        lay.addWidget(head)
        self.trace_box.insertWidget(self.trace_box.count() - 1, card)
        self._trace_cards[goal_id] = lay
        return lay

    def _trace_line(self, goal_id: str, text: str, colour: QColor) -> None:
        lay = self._trace_cards.get(goal_id) or self._trace_card(goal_id, goal_id)
        line = QLabel(f"  {text[:140]}")
        line.setWordWrap(True)
        line.setStyleSheet(
            f"color:{theme.css(colour)}; font-size:9.5px;"
            f"font-family:'{MONO}'; background:transparent; border:none;")
        lay.addWidget(line)

    @pyqtSlot(dict)
    def _on_event(self, rec: dict) -> None:
        kind = rec.get("kind", "")
        gid = rec.get("goal_id") or "voice"
        if kind == "plan.created":
            self._trace_line(gid, f"plan · {rec.get('steps')} step(s) · "
                                  f"{rec.get('route', '')}", theme.CYAN)
        elif kind == "model.call":
            self._trace_line(gid, f"model · {rec.get('route', '')} · "
                                  f"{rec.get('latency_ms', '')}ms", theme.DIM)
        elif kind == "ability.executed":
            self._on_state("acting")
            ok = rec.get("ok")
            self._trace_line(gid, f"{rec.get('ability')} · "
                                  f"{'ok' if ok else 'FAILED'}",
                             theme.GREEN if ok else theme.RED)
        elif kind == "voice.tool_call":
            self._trace_line("voice", f"tool · {rec.get('ability')}", theme.AMBER)
        elif kind == "voice.input_gain":
            self._trace_line("voice", f"gain · {rec.get('gain')}x", theme.DIM)
        elif kind == "goal.unsupported":
            self._trace_line(gid, "capability gap", theme.AMBER)

    # -- refresh ------------------------------------------------------------

    def _refresh(self) -> None:
        self.hdr_clock.setText(time.strftime("%H:%M:%S"))
        try:
            st = self.core.orch.local_os.state()
        except Exception:      # noqa: BLE001 - telemetry must never break the UI
            return
        self.m_cpu.set_value(st.get("cpu_percent", 0),
                             f"{st.get('cpu_percent', 0):.0f}%")
        self.m_ram.set_value(st.get("ram_percent", 0),
                             f"{st.get('ram_used_gb', 0):.1f}G")
        self.m_disk.set_value(st.get("disk_percent", 0),
                              f"{st.get('disk_free_gb', 0):.0f}G")

    def _refresh_registry(self) -> None:
        self.cap_count.setText(str(len(ability_registry.all_abilities())))
        try:
            from jarvis.interface.voice import status as vstatus
            v = vstatus()
            live = v["microphone"]["available"] and v["transcription"]["available"]
            self.voice_status.setText("voice: ready" if live else "voice: unavailable")
            self.voice_status.setStyleSheet(
                f"color:{theme.css(theme.GREEN if live else theme.RED)};"
                f"font-size:8px; font-family:'{MONO}';"
                f"background:transparent; border:none;")
        except Exception:      # noqa: BLE001
            pass
        try:
            rows = self.core.orch.router.status()
        except Exception:      # noqa: BLE001
            return
        for row in rows:
            colour = theme.GREEN if row["available"] else theme.DIM2
            w = QWidget()
            w.setStyleSheet("background:transparent;")
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 1, 0, 1)
            h.addWidget(_label(row["id"].split(":")[0][:12], 9, theme.DIM))
            h.addStretch(1)
            h.addWidget(_label("●" if row["available"] else "○", 9, colour))
            self.registry_box.addWidget(w)

    def closeEvent(self, event) -> None:
        self.core.shutdown()
        super().closeEvent(event)
