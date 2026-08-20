"""Visual language for the HUD.

One place for colour and type so the widgets stay consistent and the whole
interface can be re-skinned without touching layout code.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor

# -- palette ---------------------------------------------------------------
BG          = QColor(4, 8, 14)
BG_PANEL    = QColor(10, 18, 28, 200)
CYAN        = QColor(62, 240, 255)
CYAN_DIM    = QColor(20, 90, 110)
CYAN_FAINT  = QColor(62, 240, 255, 40)
AMBER       = QColor(255, 176, 32)
RED         = QColor(255, 59, 78)
GREEN       = QColor(56, 255, 176)
TEXT        = QColor(205, 238, 245)
DIM         = QColor(95, 138, 150)
DIM2        = QColor(58, 91, 100)

# State -> accent colour. The whole HUD tints to match what JARVIS is doing,
# so its state is readable from across the room without reading any text.
STATE_COLOURS = {
    "idle":      CYAN,
    "listening": GREEN,
    "thinking":  AMBER,
    "speaking":  CYAN,
    "acting":    AMBER,
    "error":     RED,
}

FONT_HUD = "Segoe UI"
FONT_MONO = "Consolas"


def css(c: QColor, alpha: float = 1.0) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


STYLESHEET = f"""
QWidget#root {{
    background: {css(BG)};
}}
QLabel {{
    color: {css(TEXT)};
    font-family: '{FONT_HUD}';
}}
QLabel[hud="title"] {{
    color: {css(CYAN)};
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 4px;
}}
QLabel[hud="sub"] {{
    color: {css(DIM)};
    font-size: 9px;
    letter-spacing: 2px;
}}
QLabel[hud="section"] {{
    color: {css(CYAN)};
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QFrame[hud="panel"] {{
    background: {css(BG_PANEL)};
    border: 1px solid {css(CYAN, 0.22)};
    border-radius: 2px;
}}
QLineEdit {{
    background: {css(QColor(6, 12, 20), 0.9)};
    border: 1px solid {css(CYAN, 0.25)};
    border-radius: 2px;
    color: {css(TEXT)};
    padding: 10px 14px;
    font-family: '{FONT_MONO}';
    font-size: 13px;
    selection-background-color: {css(CYAN_DIM)};
}}
QLineEdit:focus {{
    border: 1px solid {css(CYAN, 0.8)};
}}
QPushButton {{
    background: {css(CYAN, 0.07)};
    border: 1px solid {css(CYAN, 0.3)};
    border-radius: 2px;
    color: {css(CYAN)};
    padding: 9px 16px;
    font-family: '{FONT_HUD}';
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QPushButton:hover {{
    background: {css(CYAN, 0.18)};
    border: 1px solid {css(CYAN, 0.9)};
}}
QPushButton:disabled {{
    color: {css(DIM2)};
    border: 1px solid {css(DIM2, 0.4)};
    background: transparent;
}}
QPushButton[hud="danger"] {{
    color: {css(RED)};
    border: 1px solid {css(RED, 0.5)};
}}
QPushButton[hud="danger"]:hover {{
    background: {css(RED, 0.15)};
    border: 1px solid {css(RED)};
}}
QPushButton[hud="ok"] {{
    color: {css(GREEN)};
    border: 1px solid {css(GREEN, 0.5)};
}}
QPushButton[hud="ok"]:hover {{
    background: {css(GREEN, 0.15)};
    border: 1px solid {css(GREEN)};
}}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 6px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {css(CYAN_DIM)}; border-radius: 3px; min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""
