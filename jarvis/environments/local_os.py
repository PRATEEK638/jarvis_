"""Local operating system environment.

Covers benchmark categories 1 (file operations), 2 (file search),
3 (application launch and control) and live system state.

Interface preference follows the architecture rule: native Python/OS API first,
PowerShell only where Windows exposes nothing better, never pixel guessing.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

import psutil

from jarvis.core.contracts import ActionResult, VerificationResult
from jarvis.core.events import emit
from jarvis.policy import guardrails

# Directories searched when the user does not name one.
DEFAULT_SEARCH_ROOTS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Pictures",
]

_IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", "AppData", "Windows", "$Recycle.Bin",
    "venv", ".venv", "site-packages", ".cache", "dist-info",
}

MAX_SCAN_FILES = 40_000
MAX_MATCHES = 200
MAX_CONTENT_BYTES = 2_000_000
TEXT_SUFFIXES = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".log", ".ini", ".cfg",
    ".yaml", ".yml", ".html", ".css", ".xml", ".tex", ".bat", ".ps1", ".sh",
    ".java", ".c", ".cpp", ".h", ".rs", ".go", ".sql", ".env", ".toml",
}

# Friendly name -> launch target. Anything not listed is tried as-is.
APP_ALIASES = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "settings": "ms-settings:",
    "browser": "msedge.exe",
    "edge": "msedge.exe",
    "chrome": "chrome.exe",
    "brave": "brave.exe",
    "firefox": "firefox.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "vscode": "code.cmd",
    "vs code": "code.cmd",
    "code": "code.cmd",
    "wordpad": "write.exe",
}


def _expand(raw: str) -> Path:
    """Resolve a user-supplied path, honouring ~, %VARS% and 'desktop/x' shorthand."""
    text = str(raw).strip().strip('"').strip("'")
    text = os.path.expandvars(text)
    p = Path(text).expanduser()
    if not p.is_absolute():
        low = text.replace("\\", "/").lower()
        for name in ("desktop", "documents", "downloads", "pictures", "videos", "music"):
            if low.startswith(name + "/") or low == name:
                rest = text.replace("\\", "/")[len(name):].lstrip("/")
                base = Path.home() / name.capitalize()
                return base / rest if rest else base
        p = Path.cwd() / p
    return p


_DIALOG_CLASS = "#32770"   # the standard Windows dialog-box window class
_WM_CLOSE = 0x0010


def _close_stray_error_dialog(app_name: str) -> bool:
    """Close a Windows dialog that popped up while resolving `app_name`.

    Scoped deliberately narrowly: only a #32770 dialog whose title bar is
    exactly the app name we just tried to launch is closed (that is what
    cmd's `start` names its own "Windows cannot find X" error box) - never a
    dialog matched only by having no title, which could be anything on the
    user's desktop unrelated to this action.
    """
    user32 = ctypes.windll.user32
    found = False

    def callback(hwnd, _lparam):
        nonlocal found
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, 256)
        if class_name.value != _DIALOG_CLASS:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if title.value.strip().lower() == app_name.strip().lower():
            user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
            found = True
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(callback)
    user32.EnumWindows(proc, 0)
    return found


def _iter_files(roots: list[Path], *, want_text_only: bool = False):
    scanned = 0
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            dirnames[:] = [
                d for d in dirnames
                if d not in _IGNORE_DIRS and not d.startswith(".")
            ]
            for name in filenames:
                scanned += 1
                if scanned > MAX_SCAN_FILES:
                    return
                p = Path(dirpath) / name
                if want_text_only and p.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                yield p


def _powershell(script: str, timeout: int = 30) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


class LocalOSEnvironment:
    """Filesystem, processes, applications and live machine state."""

    id = "local_os"

    # -- Environment protocol ---------------------------------------------

    def state(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        du = psutil.disk_usage(str(Path.home().anchor or "C:\\"))
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.3),
            "cpu_cores_logical": psutil.cpu_count(logical=True),
            "ram_total_gb": round(vm.total / 1e9, 2),
            "ram_used_gb": round(vm.used / 1e9, 2),
            "ram_percent": vm.percent,
            "disk_total_gb": round(du.total / 1e9, 2),
            "disk_free_gb": round(du.free / 1e9, 2),
            "disk_percent": du.percent,
            "process_count": len(psutil.pids()),
            "cwd": str(Path.cwd()),
            "home": str(Path.home()),
        }

    def capabilities(self) -> list[str]:
        return sorted(self._handlers().keys())

    def constraints(self) -> list[str]:
        return [
            "Deletion of any file or folder is permanently blocked.",
            "Writes into Windows/Program Files and other system roots are blocked.",
            f"Content search scans at most {MAX_SCAN_FILES} files and text formats only.",
        ]

    def act(self, ability_id: str, args: dict[str, Any]) -> ActionResult:
        handler = self._handlers().get(ability_id)
        if handler is None:
            return ActionResult(ok=False, summary=f"unknown ability '{ability_id}'",
                                error="unregistered")
        start = time.perf_counter()
        try:
            result = handler(args)
        except guardrails.Blocked:
            raise
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result = ActionResult(ok=False, summary=f"{type(exc).__name__}: {exc}",
                                  error=str(exc))
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        return result

    def verify(self, ability_id: str, args: dict[str, Any],
               result: ActionResult) -> VerificationResult:
        """Re-observe the filesystem / process table. Never trust the claim."""
        if ability_id in {"create_file", "write_file"}:
            path = _expand(args.get("path", ""))
            exists = path.is_file()
            checked: dict[str, Any] = {"path": str(path), "exists": exists}
            if exists:
                checked["size_bytes"] = path.stat().st_size
                wanted = args.get("content")
                if isinstance(wanted, str) and wanted:
                    try:
                        actual = path.read_text(encoding="utf-8", errors="replace")
                        checked["content_matches"] = wanted.strip() in actual
                    except OSError:
                        checked["content_matches"] = None
            ok = exists and checked.get("content_matches") is not False
            return VerificationResult(
                verified=ok, strategy="file_exists_with_content",
                detail=f"{path} {'exists' if exists else 'is missing'}",
                checked=checked)

        if ability_id == "create_folder":
            path = _expand(args.get("path", ""))
            return VerificationResult(
                verified=path.is_dir(), strategy="dir_exists",
                detail=f"{path} {'exists' if path.is_dir() else 'is missing'}",
                checked={"path": str(path), "is_dir": path.is_dir()})

        if ability_id in {"move_path", "rename_path", "copy_path"}:
            dest = result.evidence.get("destination")
            src = result.evidence.get("source")
            dest_ok = bool(dest) and Path(dest).exists()
            src_gone = ability_id == "copy_path" or (bool(src) and not Path(src).exists())
            return VerificationResult(
                verified=dest_ok and src_gone, strategy="path_moved",
                detail=f"destination {'present' if dest_ok else 'missing'}",
                checked={"destination": dest, "destination_exists": dest_ok,
                         "source": src, "source_removed": src_gone})

        if ability_id == "open_app":
            target = str(args.get("name", "")).lower()
            running = self._find_process(target)
            return VerificationResult(
                verified=bool(running), strategy="process_running",
                detail=f"{len(running)} matching process(es)",
                checked={"query": target, "pids": running[:5]})

        if ability_id == "run_command":
            code = result.evidence.get("exit_code")
            return VerificationResult(
                verified=code == 0, strategy="exit_code",
                detail=f"exit code {code}", checked={"exit_code": code})

        return VerificationResult(
            verified=result.ok, strategy="result_only",
            detail="no independent check available for this ability",
            checked={})

    # -- handlers ----------------------------------------------------------

    def _handlers(self):
        return {
            "system_state": self._system_state,
            "create_folder": self._create_folder,
            "create_file": self._create_file,
            "write_file": self._create_file,
            "read_file": self._read_file,
            "list_dir": self._list_dir,
            "copy_path": self._copy_path,
            "move_path": self._move_path,
            "rename_path": self._rename_path,
            "find_files": self._find_files,
            "search_in_files": self._search_in_files,
            "open_app": self._open_app,
            "list_processes": self._list_processes,
            "run_command": self._run_command,
        }

    def _system_state(self, args: dict[str, Any]) -> ActionResult:
        st = self.state()
        summary = (
            f"CPU {st['cpu_percent']}% on {st['cpu_cores_logical']} logical cores - "
            f"RAM {st['ram_used_gb']}/{st['ram_total_gb']} GB ({st['ram_percent']}%) - "
            f"Disk {st['disk_free_gb']} GB free of {st['disk_total_gb']} GB - "
            f"{st['process_count']} processes"
        )
        return ActionResult(ok=True, summary=summary, evidence=st)

    def _create_folder(self, args: dict[str, Any]) -> ActionResult:
        path = _expand(args["path"])
        guardrails.check_path(path)
        existed = path.exists()
        path.mkdir(parents=True, exist_ok=True)
        return ActionResult(
            ok=True,
            summary=f"{'Already present' if existed else 'Created folder'}: {path}",
            evidence={"path": str(path), "created": not existed})

    def _create_file(self, args: dict[str, Any]) -> ActionResult:
        path = _expand(args["path"])
        guardrails.check_path(path)
        content = args.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if args.get("append") else "w"
        with path.open(mode, encoding="utf-8") as fh:
            fh.write(content)
        size = path.stat().st_size
        return ActionResult(
            ok=True, summary=f"Wrote {size} bytes to {path}",
            evidence={"path": str(path), "size_bytes": size,
                      "chars_written": len(content)})

    def _read_file(self, args: dict[str, Any]) -> ActionResult:
        path = _expand(args["path"])
        if not path.is_file():
            return ActionResult(ok=False, summary=f"No such file: {path}",
                                error="not_found")
        limit = int(args.get("max_chars", 20_000))
        data = path.read_text(encoding="utf-8", errors="replace")
        truncated = len(data) > limit
        return ActionResult(
            ok=True,
            summary=f"Read {path.name} ({len(data)} chars"
                    f"{', truncated' if truncated else ''})",
            evidence={"path": str(path), "content": data[:limit],
                      "total_chars": len(data), "truncated": truncated})

    def _list_dir(self, args: dict[str, Any]) -> ActionResult:
        path = _expand(args.get("path") or str(Path.home()))
        if not path.is_dir():
            return ActionResult(ok=False, summary=f"Not a directory: {path}",
                                error="not_a_directory")
        entries = []
        for child in sorted(path.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
            entries.append({
                "name": child.name,
                "type": "file" if child.is_file() else "dir",
                "size_bytes": child.stat().st_size if child.is_file() else None,
            })
            if len(entries) >= 300:
                break
        return ActionResult(
            ok=True, summary=f"{len(entries)} entries in {path}",
            evidence={"path": str(path), "entries": entries})

    def _resolve_destination(self, src: Path, raw_dest: str) -> Path:
        dest = _expand(raw_dest)
        if dest.is_dir():
            return dest / src.name
        if raw_dest.endswith(("/", "\\")):
            return dest / src.name
        return dest

    def _copy_path(self, args: dict[str, Any]) -> ActionResult:
        src = _expand(args["source"])
        if not src.exists():
            return ActionResult(ok=False, summary=f"Source not found: {src}",
                                error="not_found")
        dest = self._resolve_destination(src, args["destination"])
        guardrails.check_path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)
        return ActionResult(
            ok=True, summary=f"Copied {src.name} -> {dest}",
            evidence={"source": str(src), "destination": str(dest)})

    def _missing_source(self, src: Path, raw: str) -> ActionResult:
        """Not-found that helps: name near-matches in the same folder if any."""
        hint = ""
        if src.parent.is_dir():
            stem = src.stem.lower()
            similar = [p.name for p in src.parent.iterdir()
                       if stem and stem in p.name.lower()][:5]
            if similar:
                hint = f" Nearby: {', '.join(similar)}."
        return ActionResult(
            ok=False, summary=f"Source not found: {src}.{hint}",
            error="not_found",
            evidence={"requested": raw, "resolved": str(src),
                      "parent_exists": src.parent.is_dir()})

    def _move_path(self, args: dict[str, Any]) -> ActionResult:
        raw = str(args["source"])
        src = _expand(raw)
        if not src.exists():
            return self._missing_source(src, raw)
        dest = self._resolve_destination(src, args["destination"])
        guardrails.check_path(dest)
        guardrails.check_path(src)  # moving out of a system root is also a write
        if dest.exists():
            # Overwriting would destroy a file the user never mentioned.
            return ActionResult(
                ok=False,
                summary=f"'{dest.name}' already exists in {dest.parent}; "
                        f"nothing was moved or overwritten",
                error="destination_exists",
                evidence={"source": str(src), "destination": str(dest)})
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return ActionResult(
            ok=True, summary=f"Moved {src.name} -> {dest}",
            evidence={"source": str(src), "destination": str(dest)})

    def _rename_path(self, args: dict[str, Any]) -> ActionResult:
        raw = str(args["source"])
        src = _expand(raw)
        if not src.exists():
            return self._missing_source(src, raw)
        # "rename X to Y" always means the final component, even if a full path
        # is supplied for Y.
        new_name = Path(str(args["new_name"]).strip().strip("\"'")
                        .replace("\\", "/")).name
        dest = src.parent / new_name
        guardrails.check_path(dest)
        if dest.exists() and dest != src:
            # Windows raises a bare WinError 183 here. Saying which name is
            # taken is more useful than surfacing the errno, and silently
            # overwriting would destroy a file the user did not mention.
            return ActionResult(
                ok=False,
                summary=f"'{dest.name}' already exists in {dest.parent}; "
                        f"nothing was renamed",
                error="destination_exists")
        src.rename(dest)
        return ActionResult(
            ok=True, summary=f"Renamed {src.name} -> {dest.name}",
            evidence={"source": str(src), "destination": str(dest)})

    def _search_roots(self, args: dict[str, Any]) -> list[Path]:
        raw = args.get("root") or args.get("path")
        if raw:
            return [_expand(str(raw))]
        return [r for r in DEFAULT_SEARCH_ROOTS if r.exists()]

    def _find_files(self, args: dict[str, Any]) -> ActionResult:
        needle = str(args.get("name") or args.get("query") or "").strip().lower()
        if not needle:
            return ActionResult(ok=False, summary="No filename pattern given",
                                error="missing_name")
        roots = self._search_roots(args)
        matches = []
        for path in _iter_files(roots):
            if needle in path.name.lower():
                try:
                    size = path.stat().st_size
                except OSError:
                    size = None
                matches.append({"path": str(path), "name": path.name,
                                "size_bytes": size})
                if len(matches) >= MAX_MATCHES:
                    break
        summary = (f"Found {len(matches)} file(s) matching '{needle}'"
                   if matches else f"No file matching '{needle}' under "
                                   f"{', '.join(str(r) for r in roots)}")
        return ActionResult(ok=True, summary=summary,
                            evidence={"query": needle, "match_count": len(matches),
                                      "matches": matches,
                                      "roots": [str(r) for r in roots]})

    def _search_in_files(self, args: dict[str, Any]) -> ActionResult:
        phrase = str(args.get("text") or args.get("query") or "").strip()
        if not phrase:
            return ActionResult(ok=False, summary="No search phrase given",
                                error="missing_text")
        needle = phrase.lower()
        roots = self._search_roots(args)
        matches = []
        for path in _iter_files(roots, want_text_only=True):
            try:
                if path.stat().st_size > MAX_CONTENT_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            low = text.lower()
            if needle in low:
                idx = low.index(needle)
                snippet = text[max(0, idx - 60): idx + len(phrase) + 60]
                line_no = text.count("\n", 0, idx) + 1
                matches.append({"path": str(path), "line": line_no,
                                "snippet": " ".join(snippet.split())})
                if len(matches) >= MAX_MATCHES:
                    break
        summary = (f"Found '{phrase}' in {len(matches)} file(s)" if matches
                   else f"'{phrase}' not found in any text file under "
                        f"{', '.join(str(r) for r in roots)}")
        return ActionResult(ok=True, summary=summary,
                            evidence={"query": phrase, "match_count": len(matches),
                                      "matches": matches,
                                      "roots": [str(r) for r in roots]})

    @staticmethod
    def _find_process(query: str) -> list[int]:
        query = query.lower().replace(".exe", "").strip()
        if not query:
            return []
        pids = []
        for proc in psutil.process_iter(["pid", "name"]):
            name = (proc.info.get("name") or "").lower()
            if query in name.replace(".exe", ""):
                pids.append(proc.info["pid"])
        return pids

    def _open_app(self, args: dict[str, Any]) -> ActionResult:
        name = str(args.get("name") or args.get("app") or "").strip()
        if not name:
            return ActionResult(ok=False, summary="No application named",
                                error="missing_name")
        target = APP_ALIASES.get(name.lower(), name)
        before = set(self._find_process(Path(target).stem))
        try:
            if target.endswith(":"):  # ms-settings: style URI
                os.startfile(target)  # noqa: S606 - launching a known URI
            else:
                # cmd's `start` resolves more than a plain ShellExecute call
                # does (App Paths, some Store-app friendly names), which is why
                # it is kept over os.startfile despite the next problem: on an
                # unresolvable name it shows ITS OWN blocking Windows dialog
                # ("Windows cannot find <name>") that a caught exception cannot
                # suppress, since it is a separate top-level window - left
                # alone it sits on screen waiting for a human click, which is
                # not acceptable for an autonomous action.
                subprocess.Popen(  # noqa: S603
                    ["cmd.exe", "/c", "start", "", target],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except OSError as exc:
            return ActionResult(ok=False, summary=f"Could not launch {name}: {exc}",
                                error=str(exc))

        stem = Path(target).stem
        pids: list[int] = []
        deadline = time.time() + 8
        dialog_closed = False
        while time.time() < deadline:
            pids = self._find_process(stem)
            if set(pids) - before:
                break
            if not dialog_closed:
                dialog_closed = _close_stray_error_dialog(name)
            time.sleep(0.3)
        new = sorted(set(pids) - before)
        ok = bool(pids)
        summary = (f"Launched {name} (pid {new[0]})" if new
                  else f"{name} is running" if ok
                  else f"Launched {name} but no matching process appeared - "
                       f"Windows could not resolve that name" if dialog_closed
                  else f"Launched {name} but no matching process appeared")
        return ActionResult(
            ok=ok, summary=summary,
            evidence={"name": name, "target": target, "pids": pids,
                      "new_pids": new, "resolution_failed": dialog_closed})

    def _list_processes(self, args: dict[str, Any]) -> ActionResult:
        top = int(args.get("top", 12))
        rows = []
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            info = proc.info
            mem = info.get("memory_info")
            rows.append({"pid": info["pid"], "name": info.get("name") or "?",
                         "ram_mb": round(mem.rss / 1e6, 1) if mem else 0.0})
        rows.sort(key=lambda r: r["ram_mb"], reverse=True)
        rows = rows[:top]
        return ActionResult(
            ok=True, summary=f"Top {len(rows)} processes by memory",
            evidence={"processes": rows})

    def _run_command(self, args: dict[str, Any]) -> ActionResult:
        command = str(args.get("command") or "").strip()
        if not command:
            return ActionResult(ok=False, summary="No command given",
                                error="missing_command")
        guardrails.check_command(command)
        emit("shell.exec", command=command[:400])
        try:
            code, out, err = _powershell(command, timeout=int(args.get("timeout", 45)))
        except subprocess.TimeoutExpired:
            return ActionResult(ok=False, summary="Command timed out",
                                error="timeout")
        text = out or err
        return ActionResult(
            ok=code == 0,
            summary=f"exit {code}" + (f": {text[:300]}" if text else ""),
            evidence={"command": command, "exit_code": code,
                      "stdout": out[:8000], "stderr": err[:2000]})
