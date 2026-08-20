"""Launch the JARVIS desktop application: `python -m jarvis.desktop`.

Also runs correctly under `pythonw.exe` (no console window). That needs care:
under pythonw, sys.stdout and sys.stderr are None, so any library that prints
or emits a warning during import raises AttributeError and the process dies
silently with no window and no message. Both streams are therefore redirected
to a log file before anything else is imported.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path


def _ensure_streams() -> None:
    """Give pythonw real stdout/stderr so imports cannot kill the app silently."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = Path(__file__).resolve().parent.parent / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    handle = open(log_dir / "desktop.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle


_ensure_streams()


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    from jarvis.desktop.bridge import Core
    from jarvis.desktop.hud import HudWindow

    # Ctrl-C in the launching terminal should close the window rather than be
    # swallowed by Qt's event loop.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")

    core = Core()
    window = HudWindow(core)
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        # Without this, a crash under pythonw leaves absolutely no trace.
        import traceback
        traceback.print_exc()
        raise
