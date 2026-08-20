"""Windows GUI environment — benchmark category 5.

Uses the UI Automation accessibility tree (via pywinauto) so JARVIS reasons about
real named controls rather than guessing pixel coordinates. Raw keyboard/mouse
injection exists only as a last-resort fallback, and typing always verifies the
target window is actually in the foreground first, so stray keystrokes cannot
land in whatever the user happens to be doing.
"""

from __future__ import annotations

import time
from typing import Any

from jarvis.core.contracts import ActionResult, VerificationResult
from jarvis.core.events import emit

_UIA_IMPORT_ERROR: str | None = None
try:  # pywinauto is optional at import time so the rest of JARVIS still runs
    from pywinauto import Desktop  # type: ignore
    from pywinauto.application import Application  # type: ignore
    _HAVE_UIA = True
except Exception as exc:  # noqa: BLE001 - any import failure means no GUI tier
    _HAVE_UIA = False
    _UIA_IMPORT_ERROR = str(exc)

CONTROL_LIMIT = 120
_INTERESTING = {
    "Button", "Edit", "CheckBox", "RadioButton", "ComboBox", "ListItem",
    "MenuItem", "TabItem", "Hyperlink", "Text", "Document", "TreeItem",
    "Slider", "SplitButton", "ToolBar",
    # Windows reports labels and result displays as Static (e.g. Calculator's
    # "Display is 12"). Without this, reading a window misses its output.
    "Static",
}


def _foreground_title() -> str:
    """Title of the window that currently has focus (empty string if unknown)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        _ = wintypes  # keep the import meaningful for type checkers
        return buf.value or ""
    except Exception:  # noqa: BLE001 - best-effort observation only
        return ""


class WindowsGUIEnvironment:
    """Read and drive on-screen controls through the accessibility tree."""

    id = "windows_gui"

    def __init__(self) -> None:
        self._desktop = Desktop(backend="uia") if _HAVE_UIA else None

    @property
    def available(self) -> bool:
        return _HAVE_UIA

    # -- Environment protocol ---------------------------------------------

    def state(self) -> dict[str, Any]:
        return {
            "available": _HAVE_UIA,
            "import_error": _UIA_IMPORT_ERROR,
            "foreground_window": _foreground_title(),
            "window_count": len(self._windows()) if _HAVE_UIA else 0,
        }

    def capabilities(self) -> list[str]:
        return ["list_windows", "focus_window", "read_ui", "click_ui", "type_text"]

    def constraints(self) -> list[str]:
        base = [
            "Typing requires the target window to be verified in the foreground; "
            "otherwise the action is refused rather than risking stray keystrokes.",
            "Reading the control tree of very large applications is capped "
            f"at {CONTROL_LIMIT} controls for responsiveness.",
        ]
        if not _HAVE_UIA:
            base.insert(0, f"UI Automation unavailable: {_UIA_IMPORT_ERROR}")
        return base

    def act(self, ability_id: str, args: dict[str, Any]) -> ActionResult:
        if not _HAVE_UIA:
            return ActionResult(
                ok=False,
                summary="GUI automation unavailable (pywinauto could not load)",
                error=_UIA_IMPORT_ERROR or "no_uia")
        handlers = {
            "list_windows": self._list_windows,
            "focus_window": self._focus_window,
            "read_ui": self._read_ui,
            "click_ui": self._click_ui,
            "type_text": self._type_text,
        }
        handler = handlers.get(ability_id)
        if handler is None:
            return ActionResult(ok=False, summary=f"unknown ability '{ability_id}'",
                                error="unregistered")
        start = time.perf_counter()
        try:
            result = handler(args)
        except Exception as exc:  # noqa: BLE001 - UIA throws a wide variety
            result = ActionResult(ok=False,
                                  summary=f"{type(exc).__name__}: {exc}",
                                  error=str(exc))
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        return result

    def verify(self, ability_id: str, args: dict[str, Any],
               result: ActionResult) -> VerificationResult:
        if ability_id == "focus_window":
            wanted = str(args.get("title", "")).lower()
            fg = _foreground_title()
            ok = bool(wanted) and wanted in fg.lower()
            return VerificationResult(
                verified=ok, strategy="foreground_title",
                detail=f"foreground is '{fg}'",
                checked={"wanted": wanted, "foreground": fg})

        if ability_id == "type_text":
            fg = result.evidence.get("foreground_after", "")
            return VerificationResult(
                verified=result.ok, strategy="foreground_confirmed_before_typing",
                detail=f"typed into '{fg}'",
                checked={"foreground": fg,
                         "chars": result.evidence.get("chars", 0)})

        if ability_id == "click_ui":
            return VerificationResult(
                verified=result.ok, strategy="control_invoked",
                detail=result.summary,
                checked={"control": result.evidence.get("control")})

        return VerificationResult(verified=result.ok, strategy="result_only",
                                  detail="observation-only ability", checked={})

    # -- handlers ----------------------------------------------------------

    def _windows(self) -> list[Any]:
        if self._desktop is None:
            return []
        try:
            return [w for w in self._desktop.windows() if w.window_text().strip()]
        except Exception:  # noqa: BLE001
            return []

    # Words people attach to a window name that are never part of the title.
    _TITLE_NOISE = ("window", "app", "application", "program", "the", "my")

    @classmethod
    def _title_variants(cls, title: str) -> list[str]:
        """Progressively looser forms of a requested window name.

        Users say "the Calculator window"; the actual title is "Calculator". A
        literal match on the whole phrase finds nothing, so the noise words are
        peeled off before giving up.
        """
        base = title.lower().strip().strip("\"'")
        variants = [base]
        words = base.split()
        while words and words[-1] in cls._TITLE_NOISE:
            words = words[:-1]
            if words:
                variants.append(" ".join(words))
        words = variants[-1].split() if variants else []
        while words and words[0] in cls._TITLE_NOISE:
            words = words[1:]
            if words:
                variants.append(" ".join(words))
        seen, out = set(), []
        for v in variants:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _match_window(self, title: str):
        windows = self._windows()
        for needle in self._title_variants(title):
            best = None
            for win in windows:
                text = win.window_text().lower()
                if needle == text:
                    return win
                if needle in text and best is None:
                    best = win
            if best is not None:
                return best
        # Last resort: any single distinctive word from the request.
        for word in self._title_variants(title)[-1].split():
            if len(word) < 3:
                continue
            for win in windows:
                if word in win.window_text().lower():
                    return win
        return None

    def _list_windows(self, args: dict[str, Any]) -> ActionResult:
        rows = []
        for win in self._windows():
            try:
                rows.append({"title": win.window_text(),
                             "class": win.class_name(),
                             "visible": bool(win.is_visible())})
            except Exception:  # noqa: BLE001
                continue
            if len(rows) >= 60:
                break
        return ActionResult(ok=True, summary=f"{len(rows)} open window(s)",
                            evidence={"windows": rows,
                                      "foreground": _foreground_title()})

    def _focus_window(self, args: dict[str, Any]) -> ActionResult:
        title = str(args.get("title") or args.get("window") or "").strip()
        if not title:
            return ActionResult(ok=False, summary="No window title given",
                                error="missing_title")
        win = self._match_window(title)
        if win is None:
            return ActionResult(
                ok=False,
                summary=f"No open window matching '{title}'",
                error="not_found",
                evidence={"open_windows": [w.window_text() for w in self._windows()][:20]})
        try:
            win.set_focus()
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, summary=f"Could not focus: {exc}",
                                error=str(exc))
        time.sleep(0.4)
        fg = _foreground_title()
        ok = title.lower() in fg.lower()
        return ActionResult(
            ok=ok,
            summary=f"Foreground is now '{fg}'" if ok
                    else f"Focus requested but foreground is '{fg}'",
            evidence={"requested": title, "foreground": fg,
                      "matched_title": win.window_text()})

    def _read_ui(self, args: dict[str, Any]) -> ActionResult:
        title = str(args.get("window") or args.get("title") or "").strip()
        win = self._match_window(title) if title else None
        if win is None:
            return ActionResult(
                ok=False, summary=f"No open window matching '{title}'",
                error="not_found",
                evidence={"open_windows": [w.window_text() for w in self._windows()][:20]})
        controls: list[dict[str, Any]] = []
        try:
            for element in win.descendants():
                try:
                    ctype = element.friendly_class_name()
                    if ctype not in _INTERESTING:
                        continue
                    name = (element.window_text() or "").strip()
                    if not name:
                        continue
                    rect = element.rectangle()
                    controls.append({
                        "name": name[:80], "type": ctype,
                        "x": (rect.left + rect.right) // 2,
                        "y": (rect.top + rect.bottom) // 2,
                        "enabled": bool(element.is_enabled()),
                    })
                except Exception:  # noqa: BLE001 - skip unreadable nodes
                    continue
                if len(controls) >= CONTROL_LIMIT:
                    break
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, summary=f"Could not read tree: {exc}",
                                error=str(exc))
        return ActionResult(
            ok=True,
            summary=f"{len(controls)} readable control(s) in '{win.window_text()}'",
            evidence={"window": win.window_text(), "controls": controls})

    def _click_ui(self, args: dict[str, Any]) -> ActionResult:
        title = str(args.get("window") or "").strip()
        control_name = str(args.get("control") or args.get("name") or "").strip()
        if not control_name:
            return ActionResult(ok=False, summary="No control name given",
                                error="missing_control")
        win = self._match_window(title) if title else None
        if win is None:
            return ActionResult(ok=False,
                                summary=f"No open window matching '{title}'",
                                error="not_found")
        needle = control_name.lower()
        try:
            win.set_focus()
        except Exception:  # noqa: BLE001 - focus is best-effort here
            pass
        for element in win.descendants():
            try:
                name = (element.window_text() or "").strip()
                if not name or needle not in name.lower():
                    continue
                emit("gui.click", window=win.window_text(), control=name)
                for method in ("invoke", "select", "toggle", "click_input"):
                    if hasattr(element, method):
                        getattr(element, method)()
                        return ActionResult(
                            ok=True,
                            summary=f"Activated '{name}' via {method}",
                            evidence={"window": win.window_text(),
                                      "control": name, "method": method})
            except Exception:  # noqa: BLE001 - try the next candidate
                continue
        return ActionResult(
            ok=False, summary=f"No control named '{control_name}' found",
            error="control_not_found")

    def _type_text(self, args: dict[str, Any]) -> ActionResult:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            return ActionResult(ok=False, summary="No text given",
                                error="missing_text")
        title = str(args.get("window") or "").strip()
        if not title:
            return ActionResult(
                ok=False,
                summary="Refusing to type without a target window "
                        "(a stray keystroke could land anywhere)",
                error="missing_window")
        win = self._match_window(title)
        if win is None:
            return ActionResult(ok=False,
                                summary=f"No open window matching '{title}'",
                                error="not_found")
        try:
            win.set_focus()
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, summary=f"Could not focus target: {exc}",
                                error=str(exc))
        time.sleep(0.4)
        fg = _foreground_title()
        if title.lower() not in fg.lower():
            return ActionResult(
                ok=False,
                summary=f"Refused: wanted '{title}' in front but foreground is "
                        f"'{fg}'. Not typing into the wrong window.",
                error="wrong_foreground",
                evidence={"wanted": title, "foreground": fg})
        control_name = str(args.get("control") or "").strip()
        target = win
        if control_name:
            for element in win.descendants():
                try:
                    if control_name.lower() in (element.window_text() or "").lower():
                        target = element
                        break
                except Exception:  # noqa: BLE001
                    continue
        emit("gui.type", window=win.window_text(), chars=len(text))
        target.type_keys(text, with_spaces=True, with_newlines=True,
                         set_foreground=False)
        if args.get("press_enter"):
            target.type_keys("{ENTER}", set_foreground=False)
        return ActionResult(
            ok=True, summary=f"Typed {len(text)} char(s) into '{fg}'",
            evidence={"window": win.window_text(), "chars": len(text),
                      "foreground_after": _foreground_title(),
                      "control": control_name or None})
