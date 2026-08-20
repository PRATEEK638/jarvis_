"""Scrolling amplitude history.

A rolling window of real RMS levels from the audio stream, mirrored about the
centre line. Input (you speaking) and output (JARVIS speaking) are drawn in
different colours so a conversation reads as a single timeline.
"""

from __future__ import annotations

from collections import deque

from PyQt6.QtCore import QPointF, Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget

from jarvis.desktop import theme

SLOTS = 140


class Waveform(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(52)
        self._in = deque([0.0] * SLOTS, maxlen=SLOTS)
        self._out = deque([0.0] * SLOTS, maxlen=SLOTS)
        self._pending_in = 0.0
        self._pending_out = 0.0

        # The audio callbacks fire far faster than a useful redraw rate, so the
        # peak between frames is held and sampled at ~30 fps instead of
        # repainting per audio block.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._sample)
        self._timer.start(33)

    @pyqtSlot(float)
    def push_input(self, level: float) -> None:
        self._pending_in = max(self._pending_in, level)

    @pyqtSlot(float)
    def push_output(self, level: float) -> None:
        self._pending_out = max(self._pending_out, level)

    def _sample(self) -> None:
        self._in.append(self._pending_in)
        self._out.append(self._pending_out)
        self._pending_in *= 0.35
        self._pending_out *= 0.35
        self.update()

    def _draw_series(self, p: QPainter, series, colour: QColor, mid: float,
                     scale: float) -> None:
        w = self.width()
        step = w / max(1, len(series) - 1)
        top, bottom = QPolygonF(), QPolygonF()
        for i, v in enumerate(series):
            x = i * step
            dy = v * scale
            top.append(QPointF(x, mid - dy))
            bottom.append(QPointF(x, mid + dy))
        pen = QPen(colour, 1.4)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(top)
        p.drawPolyline(bottom)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2
        scale = h / 2 - 4

        p.setPen(QPen(QColor(62, 240, 255, 30), 1.0))
        p.drawLine(0, int(mid), w, int(mid))

        self._draw_series(p, self._out, QColor(62, 240, 255, 200), mid, scale)
        self._draw_series(p, self._in, QColor(56, 255, 176, 170), mid, scale)
        p.end()
