"""Compact labelled metric with a fill bar.

Layout and the 65%/85% warning thresholds follow Mark-L by FatihMakes
(https://github.com/FatihMakes/Mark-L), CC BY-NC 4.0. See hud_canvas.py for
the full attribution note.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from jarvis.desktop import theme


class MetricBar(QWidget):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._value = 0.0
        self._text = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(78)

    def set_value(self, pct: float, text: str) -> None:
        self._value = max(0.0, min(100.0, float(pct or 0.0)))
        self._text = text
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        p.setBrush(QBrush(QColor(8, 18, 28)))
        p.setPen(QPen(QColor(62, 240, 255, 55), 1))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 3, 3)

        # Warn before saturation, not at it.
        colour = (theme.RED if self._value > 85
                  else theme.AMBER if self._value > 65 else theme.CYAN)

        bar_h, bar_x = 4, 6
        bar_y = h - bar_h - 5
        bar_w = w - 12
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(4, 22, 32)))
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)
        fill = bar_w * self._value / 100
        if fill > 0:
            p.setBrush(QBrush(colour))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill, bar_h), 2, 2)

        p.setFont(QFont(theme.FONT_MONO, 7, QFont.Weight.Bold))
        p.setPen(QPen(theme.DIM, 1))
        p.drawText(QRectF(8, 5, 52, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._label)

        p.setFont(QFont(theme.FONT_MONO, 9, QFont.Weight.Bold))
        p.setPen(QPen(colour if self._text != "--" else theme.DIM, 1))
        p.drawText(QRectF(0, 4, w - 7, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self._text)
        p.end()
