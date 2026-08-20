"""Radial gauge for a single 0-100 telemetry value."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from jarvis.desktop import theme


class Gauge(QWidget):
    def __init__(self, label: str, parent=None, size: int = 74) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size + 14)
        self._label = label
        self._value = 0.0

    def set_value(self, value: float) -> None:
        value = max(0.0, min(100.0, float(value or 0.0)))
        if abs(value - self._value) > 0.05:
            self._value = value
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = self.width()
        r = size / 2 - 7
        cx, cy = size / 2, size / 2
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)

        # Warn well before saturation: a gauge that only turns red at 100% has
        # told you too late to act on it.
        colour = (theme.RED if self._value > 85
                  else theme.AMBER if self._value > 65 else theme.CYAN)

        p.setPen(QPen(QColor(62, 240, 255, 34), 5))
        p.drawArc(rect, 0, 360 * 16)

        p.setPen(QPen(colour, 5, cap=Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 90 * 16, -int(360 * 16 * self._value / 100))

        p.setPen(QPen(colour))
        f = QFont(theme.FONT_HUD, 12)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, cy - 11, size, 22),
                   Qt.AlignmentFlag.AlignCenter, f"{int(self._value)}")

        p.setPen(QPen(theme.DIM))
        p.setFont(QFont(theme.FONT_HUD, 7))
        p.drawText(QRectF(0, size - 4, size, 16),
                   Qt.AlignmentFlag.AlignCenter, self._label)
        p.end()
