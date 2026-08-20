"""The arc reactor: JARVIS's state, made visible.

Three concentric rings rotating at different rates around a core that pulses
with real audio amplitude. Colour encodes state (listening / thinking /
speaking / acting), so what the system is doing is readable at a glance without
reading any text.

The amplitude driving the core is the actual RMS of the audio stream, supplied
by VoiceSession's level taps - not an animation imitating sound.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget

from jarvis.desktop import theme


class ArcReactor(QWidget):
    """Animated status core. Click to toggle the voice session."""

    def __init__(self, parent=None, diameter: int = 190) -> None:
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._phase = 0.0
        self._state = "idle"
        self._level = 0.0          # target amplitude, 0-1
        self._smooth = 0.0         # displayed amplitude, eased toward target
        self._on_click = None

        # ~60 fps. The easing below is tuned to this interval.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(16)

    # -- public ------------------------------------------------------------

    def set_click_handler(self, fn) -> None:
        self._on_click = fn

    @pyqtSlot(str)
    def set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.update()

    @pyqtSlot(float)
    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))

    # -- internals ---------------------------------------------------------

    def _advance(self) -> None:
        self._phase += 0.02
        # Attack fast, release slow: matches how a level meter should feel, and
        # stops the core flickering between audio blocks.
        target = self._level
        if target > self._smooth:
            self._smooth += (target - self._smooth) * 0.45
        else:
            self._smooth += (target - self._smooth) * 0.08
        self._level *= 0.90        # decay if no new audio arrives
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._on_click:
            self._on_click()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        base = theme.STATE_COLOURS.get(self._state, theme.CYAN)
        amp = self._smooth

        # Outer glow, breathing gently at idle and hard when speaking.
        breathe = 0.5 + 0.5 * math.sin(self._phase * 1.6)
        glow_r = (w / 2) * (0.72 + 0.16 * amp + 0.04 * breathe)
        grad = QRadialGradient(QPointF(cx, cy), glow_r)
        grad.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(),
                                    int(70 + 110 * amp)))
        grad.setColorAt(0.55, QColor(base.red(), base.green(), base.blue(),
                                     int(22 + 44 * amp)))
        grad.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 0))
        p.setBrush(grad)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # Three rotating arc rings.
        rings = (
            (0.92, 1.00, 62,  1.8, 0.55),
            (0.76, -1.70, 40, 1.4, 0.75),
            (0.60, 2.40, 28,  1.1, 0.95),
        )
        for scale, speed, span, width, alpha in rings:
            r = (w / 2 - 8) * scale
            rect = QRectF(cx - r, cy - r, r * 2, r * 2)
            pen = QPen(QColor(base.red(), base.green(), base.blue(),
                              int(255 * alpha)))
            pen.setWidthF(width + amp * 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            start = int((self._phase * speed * 180 / math.pi) * 16) % (360 * 16)
            for k in range(3):     # three arc segments per ring
                p.drawArc(rect, start + k * 120 * 16, span * 16)

        # Faint full circles for structure between the moving arcs.
        p.setPen(QPen(QColor(base.red(), base.green(), base.blue(), 34), 1.0))
        for scale in (0.92, 0.76, 0.60):
            r = (w / 2 - 8) * scale
            p.drawEllipse(QPointF(cx, cy), r, r)

        # Tick marks around the rim - the "instrument" feel.
        p.setPen(QPen(QColor(base.red(), base.green(), base.blue(), 70), 1.0))
        r_out = (w / 2 - 8) * 0.99
        for i in range(48):
            a = (i / 48) * 2 * math.pi + self._phase * 0.25
            r_in = r_out * (0.955 if i % 4 else 0.925)
            p.drawLine(QPointF(cx + math.cos(a) * r_in, cy + math.sin(a) * r_in),
                       QPointF(cx + math.cos(a) * r_out, cy + math.sin(a) * r_out))

        # Core, scaling with amplitude.
        core_r = (w / 2) * (0.20 + 0.13 * amp)
        core_grad = QRadialGradient(QPointF(cx, cy), core_r * 1.9)
        core_grad.setColorAt(0.0, QColor(255, 255, 255, 235))
        core_grad.setColorAt(0.35, QColor(base.red(), base.green(), base.blue(), 220))
        core_grad.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 0))
        p.setBrush(core_grad)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), core_r * 1.9, core_r * 1.9)
        p.end()
