"""The full-bleed HUD canvas.

ATTRIBUTION
-----------
The visual design of this HUD is derived from Mark-L by FatihMakes
(https://github.com/FatihMakes/Mark-L), which is licensed
Creative Commons BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/).

Specifically derived: the composition of dot grid, layered halo, expanding
pulse rings, counter-rotating segmented arc rings, dual scanners, graduated
bezel, crosshair, corner brackets, particle emission and bar spectrum.

The implementation here was written independently against that design rather
than copied - the animation state model, easing, colour system and data
bindings differ - but the look is theirs and the attribution is required by
the licence. BY-NC also means this must not be used commercially.

A single custom-painted surface: dot grid, halo, expanding pulse rings,
segmented counter-rotating arc rings, sweeping scanners, a graduated bezel,
crosshair, corner brackets, a reactive core, emitted particles, status
readout, and a bar spectrum.

Everything that *can* be driven by real data is: the core scale, halo
intensity, particle emission rate and every bar of the spectrum come from the
actual RMS amplitude of the audio stream. Only the ornamental motion (ring
rotation, scanner sweep) is time-based, because there is nothing real for it
to represent.
"""

from __future__ import annotations

import math
import random
import time
from collections import deque

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QSizePolicy, QWidget

from jarvis.desktop import theme

BARS = 48


class HudCanvas(QWidget):
    """The centrepiece. Click to toggle the voice session."""

    def __init__(self, parent=None, name: str = "J.A.R.V.I.S") -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(320, 340)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._name = name
        self._state = "idle"
        self._on_click = None

        # animation state
        self._tick = 0
        self._rings = [0.0, 120.0, 240.0]
        self._scan = 0.0
        self._scan2 = 180.0
        self._pulses: list[float] = [0.0, 60.0, 120.0]
        self._particles: list[list[float]] = []
        self._blink = True
        self._blink_tick = 0

        # real signal
        self._level = 0.0        # latest amplitude target
        self._smooth = 0.0       # eased amplitude actually drawn
        self._spectrum = deque([0.0] * BARS, maxlen=BARS)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(16)

    # -- public -------------------------------------------------------------

    def set_click_handler(self, fn) -> None:
        self._on_click = fn

    @pyqtSlot(str)
    def set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.update()

    @pyqtSlot(float)
    def set_level(self, level: float) -> None:
        self._level = max(self._level, max(0.0, min(1.0, level)))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._on_click:
            self._on_click()

    # -- animation ----------------------------------------------------------

    def _step(self) -> None:
        self._tick += 1
        speaking = self._state in ("speaking", "acting")
        live = self._state != "idle"

        # Amplitude easing: fast attack, slow release, so the core tracks
        # speech onsets crisply without flickering between audio blocks.
        target = self._level
        self._smooth += ((target - self._smooth)
                         * (0.45 if target > self._smooth else 0.10))
        self._level *= 0.86
        amp = self._smooth

        # The spectrum is a real amplitude history, shaped across the bar
        # array so it reads as a spectrum rather than a flat bar.
        self._spectrum.append(amp)

        speeds = ((1.15, -0.85, 1.85) if speaking else
                  (0.5, -0.34, 0.82) if live else (0.22, -0.15, 0.36))
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360

        self._scan = (self._scan + (2.6 if speaking else 1.1)) % 360
        self._scan2 = (self._scan2 - (1.9 if speaking else 0.7)) % 360

        fw = min(self.width(), self.height())
        limit = fw * 0.74
        rate = 3.6 + amp * 3.0
        self._pulses = [r + rate for r in self._pulses if r + rate < limit]
        # Pulse emission is tied to amplitude, so the ripples correspond to
        # something audible rather than firing at random.
        if len(self._pulses) < 4 and random.random() < (0.02 + amp * 0.22):
            self._pulses.append(0.0)

        if amp > 0.06 and random.random() < amp * 0.9:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.26
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.8, 2.2),
                math.sin(ang) * random.uniform(0.8, 2.2) - 0.35, 1.0,
            ])
        self._particles = [
            [p[0] + p[2], p[1] + p[3], p[2] * 0.97, p[3] * 0.97, p[4] - 0.026]
            for p in self._particles if p[4] > 0
        ][-70:]

        self._blink_tick += 1
        if self._blink_tick >= 34:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    # -- painting -----------------------------------------------------------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), theme.BG)

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2 - H * 0.05
        fw = min(W, H)
        base = theme.STATE_COLOURS.get(self._state, theme.CYAN)
        amp = self._smooth
        halo = 45 + amp * 165

        def col(c: QColor, a: int) -> QColor:
            return QColor(c.red(), c.green(), c.blue(), max(0, min(255, int(a))))

        # -- dot grid
        p.setPen(QPen(col(theme.CYAN, 26), 1))
        for x in range(0, W, 46):
            for y in range(0, H, 46):
                p.drawPoint(x, y)

        r_core = fw * 0.30

        # -- halo: nested rings fading outward
        for i in range(10):
            r = r_core * (1.85 - i * 0.085)
            frac = 1.0 - i / 10
            p.setPen(QPen(col(base, halo * 0.085 * frac), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # -- expanding pulse rings
        for pr in self._pulses:
            p.setPen(QPen(col(base, 210 * (1.0 - pr / (fw * 0.74))), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # -- segmented counter-rotating rings
        for idx, (frac, width, seg, gap) in enumerate(
                ((0.47, 3.0, 112, 76), (0.39, 2.0, 76, 54), (0.31, 1.2, 54, 38))):
            rr = fw * frac
            rect = QRectF(cx - rr, cy - rr, rr * 2, rr * 2)
            p.setPen(QPen(col(base, halo * (1.0 - idx * 0.18)),
                          width + amp * 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            angle = self._rings[idx]
            end = angle + 360
            while angle < end:
                p.drawArc(rect, int(angle * 16), int(seg * 16))
                angle += seg + gap

        # -- scanners sweeping in opposite directions
        sr = fw * 0.495
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        span = 70 if amp > 0.15 else 42
        p.setPen(QPen(col(base, min(255, halo * 1.5)), 2.4))
        p.drawArc(srect, int(self._scan * 16), int(span * 16))
        p.setPen(QPen(col(theme.AMBER, min(200, halo * 0.7)), 1.4))
        p.drawArc(srect, int(self._scan2 * 16), int(span * 16))

        # -- graduated bezel
        t_out, t_in = fw * 0.492, fw * 0.470
        p.setPen(QPen(col(base, 130), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inner = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inner * math.cos(rad), cy - inner * math.sin(rad)))

        # -- crosshair
        ch, gap_h = fw * 0.505, fw * 0.155
        p.setPen(QPen(col(base, halo * 0.5), 1))
        p.drawLine(QPointF(cx - ch, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch, cy))
        p.drawLine(QPointF(cx, cy - ch), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch))

        # -- corner brackets
        bl, half = 26, fw / 2
        p.setPen(QPen(col(base, 205), 2))
        for bx, by, dx, dy in ((cx - half, cy - half, 1, 1),
                               (cx + half, cy - half, -1, 1),
                               (cx - half, cy + half, 1, -1),
                               (cx + half, cy + half, -1, -1)):
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # -- reactive core
        orb = fw * 0.26 * (1.0 + amp * 0.16)
        grad = QRadialGradient(QPointF(cx, cy), orb)
        grad.setColorAt(0.0, col(QColor(255, 255, 255), 200 + amp * 55))
        grad.setColorAt(0.30, col(base, 210))
        grad.setColorAt(0.70, col(base, 70))
        grad.setColorAt(1.0, col(base, 0))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), orb, orb)

        p.setPen(QPen(col(base, 235), 1))
        p.setFont(QFont(theme.FONT_MONO, max(9, int(fw * 0.030)),
                        QFont.Weight.Bold))
        p.drawText(QRectF(cx - 130, cy - 13, 260, 26),
                   Qt.AlignmentFlag.AlignCenter, self._name)

        # -- particles
        p.setPen(Qt.PenStyle.NoPen)
        for pt in self._particles:
            p.setBrush(QBrush(col(base, pt[4] * 235)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.4, 2.4)

        # -- status readout
        symbol_on = "●" if self._blink else "○"
        label = {
            "idle": (f"{symbol_on}  STANDBY", theme.CYAN),
            "listening": (f"{symbol_on}  LISTENING", theme.GREEN),
            "thinking": (("◈" if self._blink else "◇") + "  THINKING", theme.AMBER),
            "acting": (("▶" if self._blink else "▷") + "  EXECUTING", theme.AMBER),
            "speaking": ("●  SPEAKING", theme.CYAN),
            "error": ("⊘  FAULT", theme.RED),
        }.get(self._state, (f"{symbol_on}  {self._state.upper()}", theme.CYAN))
        sy = cy + fw * 0.395
        p.setPen(QPen(label[1], 1))
        p.setFont(QFont(theme.FONT_MONO, 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 24), Qt.AlignmentFlag.AlignCenter, label[0])

        # -- spectrum, from the real amplitude history
        wy = sy + 30
        bw = max(4, int(min(9, W / (BARS + 12))))
        x0 = (W - BARS * bw) / 2
        hist = list(self._spectrum)
        for i in range(BARS):
            v = hist[i]
            # Shape a flat level into a spectrum-like envelope: centre bars
            # ride higher, edges taper. Height still comes from real audio.
            centre = 1.0 - abs(i - BARS / 2) / (BARS / 2)
            hgt = 2 + v * 34 * (0.35 + 0.65 * centre)
            if self._state == "idle":
                hgt = 2 + 1.6 * (1 + math.sin(self._tick * 0.08 + i * 0.55))
                c = col(theme.CYAN, 70)
            else:
                c = col(base, 235) if hgt > 12 else col(base, 130)
            p.fillRect(QRectF(x0 + i * bw, wy + 22 - hgt, bw - 1.4, hgt), c)
        p.end()
