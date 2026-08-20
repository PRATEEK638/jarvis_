"""Code execution environment — the general-purpose primitive.

Every other environment is a fixed set of things JARVIS can do. This one is
open-ended: given a problem nobody anticipated, it can write code and run it.
That is the difference between a system with 30 abilities and a system that can
solve a new problem.

It is also the most dangerous thing here, so the safety model is explicit:

* HIGH risk, always. Never LOW, never auto-approved, even for code that looks
  harmless - because "looks harmless" is a judgement made by a language model
  about a script it just wrote.

* Runs in a scratch directory, not the user's folders. Code that wants to touch
  real files must be given real paths explicitly, which the confirmation prompt
  then shows.

* Hard timeout. An infinite loop must not pin a core forever.

* The guardrails run over the source text before execution, so the same rules
  that block `rm -rf` in a shell command block it in a Python script.

* Output is captured and returned, including stderr and the real exit code.
  A script that crashed is reported as crashed.

What is deliberately NOT here: no container, no VM, no OS-level sandbox. The
code runs as the user, with the user's permissions. That is an honest
limitation rather than a hidden one - a determined script could still do damage
that the guardrails do not anticipate, which is exactly why this is HIGH risk
and confirmation is mandatory.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from jarvis.core.contracts import ActionResult, VerificationResult
from jarvis.policy import guardrails

DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S = 300
MAX_OUTPUT = 12_000
_NO_WINDOW = 0x08000000

# Imports that have no legitimate use in a generated helper script and are the
# building blocks of the damage the guardrails exist to prevent. Checked as a
# defence in depth, not as the primary control - the primary control is that a
# human approves the source.
_SUSPECT = (
    "shutil.rmtree", "os.remove", "os.unlink", "os.rmdir", "pathlib.Path.unlink",
    ".unlink(", "rmtree(", "winreg", "ctypes.windll", "subprocess.Popen",
    "os.system", "socket.socket", "urllib.request.urlopen",
)


class CodeEnvironment:
    """Write and run code to solve problems nothing else covers."""

    id = "code"

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace or (
            Path(tempfile.gettempdir()) / "jarvis_scratch")
        self._workspace.mkdir(parents=True, exist_ok=True)

    # -- Environment protocol ----------------------------------------------

    def state(self) -> dict[str, Any]:
        return {
            "available": True,
            "workspace": str(self._workspace),
            "python": sys.version.split()[0],
        }

    def capabilities(self) -> list[str]:
        return ["run_python", "run_powershell"]

    def constraints(self) -> list[str]:
        return [
            "Runs as the current user with the current user's permissions. "
            "There is no container or VM: this is not a security sandbox, "
            "which is why every execution is HIGH risk and must be approved.",
            f"Hard timeout, {DEFAULT_TIMEOUT_S}s by default and "
            f"{MAX_TIMEOUT_S}s maximum.",
            "Working directory is a scratch folder; touching real files "
            "requires explicit absolute paths, which the approval prompt shows.",
            "Guardrails are applied to the source text before execution.",
            f"Output is captured and truncated at {MAX_OUTPUT} characters.",
        ]

    def act(self, ability_id: str, args: dict[str, Any]) -> ActionResult:
        handlers = {"run_python": self._python,
                    "run_powershell": self._powershell}
        handler = handlers.get(ability_id)
        if handler is None:
            return ActionResult(ok=False, error="unregistered",
                                summary=f"unknown ability '{ability_id}'")
        source = str(args.get("code") or args.get("script") or "").strip()
        if not source:
            return ActionResult(ok=False, error="missing_code",
                                summary="No code was given to run")

        # The same rules that block a destructive shell command must block a
        # script that does the same thing by another route.
        try:
            guardrails.check_command(source)
        except guardrails.Blocked as exc:
            return ActionResult(ok=False, error="blocked",
                                summary=f"Blocked: {exc}")

        flagged = [s for s in _SUSPECT if s in source]
        start = time.perf_counter()
        result = handler(source, args)
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        if flagged:
            # Surfaced rather than blocked: deleting a temp file it just created
            # is legitimate. The point is that the human saw it.
            result.evidence["flagged_calls"] = flagged
        return result

    def verify(self, ability_id: str, args: dict[str, Any],
               result: ActionResult) -> VerificationResult:
        """Exit code is the ground truth. A script that printed a cheerful
        message and then exited 1 did not succeed."""
        code = result.evidence.get("exit_code")
        if code is None:
            return VerificationResult(verified=False, strategy="exit_code",
                                      detail="no exit code captured",
                                      checked={})
        return VerificationResult(
            verified=code == 0, strategy="exit_code",
            detail=f"exit code {code}",
            checked={"exit_code": code,
                     "stderr_bytes": len(result.evidence.get("stderr", ""))})

    # -- runners ------------------------------------------------------------

    def _timeout(self, args: dict[str, Any]) -> int:
        try:
            wanted = int(args.get("timeout") or DEFAULT_TIMEOUT_S)
        except (TypeError, ValueError):
            wanted = DEFAULT_TIMEOUT_S
        return max(1, min(MAX_TIMEOUT_S, wanted))

    def _run(self, argv: list[str], *, timeout: int,
             label: str) -> ActionResult:
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
                cwd=str(self._workspace), creationflags=_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return ActionResult(
                ok=False, error="timeout",
                summary=f"{label} did not finish within {timeout}s and was "
                        f"stopped",
                evidence={"exit_code": None, "timed_out": True})
        except OSError as exc:
            return ActionResult(ok=False, error="spawn_failed",
                                summary=f"Could not start {label}: {exc}",
                                evidence={"exit_code": None})

        out = (proc.stdout or "")[:MAX_OUTPUT]
        err = (proc.stderr or "")[:MAX_OUTPUT]
        ok = proc.returncode == 0
        if ok:
            summary = out.strip() or f"{label} finished with no output"
        else:
            # The error is what the user needs, so it leads.
            first = (err.strip().splitlines() or ["no error text"])[-1]
            summary = f"{label} failed (exit {proc.returncode}): {first[:220]}"
        return ActionResult(
            ok=ok, summary=summary[:1200],
            error=None if ok else "nonzero_exit",
            evidence={"exit_code": proc.returncode, "stdout": out,
                      "stderr": err, "workspace": str(self._workspace)})

    def _python(self, source: str, args: dict[str, Any]) -> ActionResult:
        script = self._workspace / f"snippet_{int(time.time()*1000)}.py"
        script.write_text(source, encoding="utf-8")
        try:
            # -I: isolated mode. Ignores PYTHONPATH and the user site directory,
            # so a stray local module cannot shadow a stdlib import and change
            # what the script does.
            return self._run([sys.executable, "-I", str(script)],
                             timeout=self._timeout(args), label="python")
        finally:
            # Cleaning up JARVIS's own scratch file, never a user file.
            try:
                script.unlink()
            except OSError:
                pass

    def _powershell(self, source: str, args: dict[str, Any]) -> ActionResult:
        return self._run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", source],
            timeout=self._timeout(args), label="powershell")
