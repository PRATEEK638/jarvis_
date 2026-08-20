"""Git repository environment.

Written deliberately as a test of the architecture's central claim (vision
Part 70/72): that "fix the server", "fix the Excel file" and "fix the website"
are the same shape, and only the environment adapter changes. If that claim is
true, the fourth environment should cost far less than the first.

Read-only by design for this first cut. Everything here observes: status, log,
diff, branches, code search. Nothing commits, pushes, checks out or resets -
those are destructive-adjacent and belong behind the risk gate with their own
verification, which is a separate piece of work rather than something to bolt
on quietly.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from jarvis.core.contracts import ActionResult, VerificationResult

# git is expected on PATH; absence is reported rather than raised.
TIMEOUT_S = 20
MAX_OUTPUT = 8000


def _run(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            timeout=TIMEOUT_S,
            # Never let git open an editor or a credential prompt: this runs
            # unattended, and a blocked prompt would hang the whole agent.
            env={"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true",
                 "PATH": __import__("os").environ.get("PATH", "")},
        )
        return proc.returncode, proc.stdout[:MAX_OUTPUT], proc.stderr[:2000]
    except FileNotFoundError:
        return 127, "", "git is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"git did not respond within {TIMEOUT_S}s"


class RepoEnvironment:
    """Observe a git repository."""

    id = "repo"

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root else Path.cwd()

    # -- Environment protocol ----------------------------------------------

    def _resolve(self, args: dict[str, Any]) -> Path:
        raw = args.get("path") or args.get("repo")
        return Path(str(raw)).expanduser() if raw else self._root

    def _is_repo(self, path: Path) -> bool:
        code, out, _ = _run(["rev-parse", "--is-inside-work-tree"], path)
        return code == 0 and out.strip() == "true"

    def state(self) -> dict[str, Any]:
        path = self._root
        if not path.is_dir():
            return {"available": False, "why": f"{path} is not a directory"}
        if not self._is_repo(path):
            return {"available": False, "root": str(path),
                    "why": "not a git repository"}
        _, branch, _ = _run(["rev-parse", "--abbrev-ref", "HEAD"], path)
        _, dirty, _ = _run(["status", "--porcelain"], path)
        changed = [ln for ln in dirty.splitlines() if ln.strip()]
        return {
            "available": True, "root": str(path),
            "branch": branch.strip(), "changed_files": len(changed),
            "clean": not changed,
        }

    def capabilities(self) -> list[str]:
        return ["repo_status", "repo_log", "repo_diff", "repo_search",
                "repo_branches"]

    def constraints(self) -> list[str]:
        return [
            "Read-only: this environment never commits, pushes, checks out or "
            "resets. Those need their own verification and risk gating.",
            "Requires git on PATH and a directory that is inside a work tree.",
            "Output is truncated at 8000 characters per command.",
        ]

    def act(self, ability_id: str, args: dict[str, Any]) -> ActionResult:
        handlers = {
            "repo_status": self._status, "repo_log": self._log,
            "repo_diff": self._diff, "repo_search": self._search,
            "repo_branches": self._branches,
        }
        handler = handlers.get(ability_id)
        if handler is None:
            return ActionResult(ok=False, error="unregistered",
                                summary=f"unknown ability '{ability_id}'")
        path = self._resolve(args)
        if not path.is_dir():
            return ActionResult(ok=False, error="not_found",
                                summary=f"No such directory: {path}")
        if not self._is_repo(path):
            return ActionResult(
                ok=False, error="not_a_repo",
                summary=f"{path} is not a git repository")
        start = time.perf_counter()
        result = handler(path, args)
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        return result

    def verify(self, ability_id: str, args: dict[str, Any],
               result: ActionResult) -> VerificationResult:
        # Every ability here observes rather than changes, so verification is
        # "did we actually get evidence back", not "did the world change".
        n = result.evidence.get("count")
        if n is not None:
            return VerificationResult(
                verified=result.ok, strategy="records_returned",
                detail=f"{n} record(s)", checked={"count": n})
        return VerificationResult(verified=result.ok, strategy="result_only",
                                  detail="observation-only ability", checked={})

    # -- handlers -----------------------------------------------------------

    def _status(self, path: Path, args: dict[str, Any]) -> ActionResult:
        _, branch, _ = _run(["rev-parse", "--abbrev-ref", "HEAD"], path)
        code, porcelain, err = _run(["status", "--porcelain"], path)
        if code != 0:
            return ActionResult(ok=False, summary=err.strip(), error="git_error")
        rows = []
        for line in porcelain.splitlines():
            if len(line) > 3:
                rows.append({"state": line[:2].strip(), "path": line[3:]})
        _, ahead, _ = _run(
            ["rev-list", "--left-right", "--count", "@{u}...HEAD"], path)
        behind_ahead = ahead.split() if ahead.strip() else []
        summary = (f"On {branch.strip()}, "
                   + (f"{len(rows)} file(s) changed" if rows else "clean"))
        if len(behind_ahead) == 2:
            summary += f", {behind_ahead[1]} ahead / {behind_ahead[0]} behind"
        return ActionResult(ok=True, summary=summary,
                            evidence={"branch": branch.strip(), "files": rows,
                                      "count": len(rows)})

    def _log(self, path: Path, args: dict[str, Any]) -> ActionResult:
        n = max(1, min(50, int(args.get("count", 10) or 10)))
        code, out, err = _run(
            ["log", f"-{n}", "--pretty=format:%h|%an|%ar|%s"], path)
        if code != 0:
            return ActionResult(ok=False, summary=err.strip(), error="git_error")
        commits = []
        for line in out.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({"sha": parts[0], "author": parts[1],
                                "when": parts[2], "subject": parts[3]})
        return ActionResult(
            ok=True, summary=f"{len(commits)} recent commit(s)",
            evidence={"commits": commits, "count": len(commits)})

    def _diff(self, path: Path, args: dict[str, Any]) -> ActionResult:
        code, out, err = _run(["diff", "--stat"], path)
        if code != 0:
            return ActionResult(ok=False, summary=err.strip(), error="git_error")
        lines = [ln for ln in out.splitlines() if ln.strip()]
        return ActionResult(
            ok=True,
            summary=(lines[-1].strip() if lines else "no uncommitted changes"),
            evidence={"stat": lines, "count": max(0, len(lines) - 1)})

    def _branches(self, path: Path, args: dict[str, Any]) -> ActionResult:
        code, out, err = _run(
            ["branch", "--all", "--format=%(refname:short)"], path)
        if code != 0:
            return ActionResult(ok=False, summary=err.strip(), error="git_error")
        names = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return ActionResult(ok=True, summary=f"{len(names)} branch(es)",
                            evidence={"branches": names, "count": len(names)})

    def _search(self, path: Path, args: dict[str, Any]) -> ActionResult:
        query = str(args.get("query") or args.get("pattern") or "").strip()
        if not query:
            return ActionResult(ok=False, summary="No search query given",
                                error="missing_query")
        # git grep only searches tracked files, which is what is wanted here:
        # it skips build output, virtualenvs and anything gitignored.
        code, out, err = _run(["grep", "-n", "-I", "--", query], path)
        if code not in (0, 1):          # 1 simply means "no matches"
            return ActionResult(ok=False, summary=err.strip() or "search failed",
                                error="git_error")
        hits = []
        for line in out.splitlines()[:60]:
            parts = line.split(":", 2)
            if len(parts) == 3:
                hits.append({"file": parts[0], "line": parts[1],
                             "text": parts[2].strip()[:160]})
        return ActionResult(
            ok=True,
            summary=(f"{len(hits)} match(es) for '{query}'" if hits
                     else f"no tracked file contains '{query}'"),
            evidence={"hits": hits, "count": len(hits), "query": query})
