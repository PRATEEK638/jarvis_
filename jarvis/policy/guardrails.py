"""Hard guardrails.

Checked BEFORE the risk/confirmation gate and unreachable from it, so no
autonomy setting, prompt, or model output can route around them.

Two standing rules, set by the owner:
  1. Never delete the user's files.
  2. Never damage the operating system.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from jarvis.core.events import emit


class Blocked(Exception):
    """Raised when an action violates a hard guardrail."""


# Paths that must never be written to, moved, or removed.
_PROTECTED_ROOTS = [
    Path(os.environ.get("SystemRoot", r"C:\Windows")),
    Path(r"C:\Program Files"),
    Path(r"C:\Program Files (x86)"),
    Path(r"C:\ProgramData\Microsoft\Windows"),
    Path(os.environ.get("SystemDrive", "C:") + "\\") / "Boot",
]

# Command patterns that are refused outright, whatever the requested risk tier.
_FORBIDDEN_COMMANDS = [
    (re.compile(r"(?i)\bformat\s+[a-z]:"), "disk format"),
    (re.compile(r"(?i)\bmkfs\b"), "filesystem creation"),
    (re.compile(r"(?i)\bdd\s+if=.*\bof=/dev/"), "raw device write"),
    (re.compile(r"(?i)\brm\s+-[a-z]*[rf]"), "recursive/forced delete"),
    (re.compile(r"(?i)remove-item\b.*-recurse"), "recursive delete"),
    (re.compile(r"(?i)\bshutil\.rmtree\b"), "recursive tree delete"),
    (re.compile(r"(?i)\bdel\s+/[sq]"), "recursive delete"),
    (re.compile(r"(?i)\brmdir\s+/s"), "recursive directory delete"),
    (re.compile(r"(?i)vssadmin\s+delete\s+shadows"), "shadow copy deletion"),
    (re.compile(r"(?i)bcdedit\b"), "boot configuration change"),
    (re.compile(r"(?i)diskpart\b"), "partition management"),
    (re.compile(r"(?i)set-mppreference\b.*disable"), "disabling Defender"),
    (re.compile(r"(?i)\bnetsh\s+advfirewall\s+set\b.*off"), "disabling firewall"),
    (re.compile(r"(?i)\breg\s+delete\b"), "registry deletion"),
    (re.compile(r"(?i)remove-itemproperty\b"), "registry deletion"),
    (re.compile(r"(?i)\b(shutdown|restart-computer)\b"), "shutdown/restart"),
    (re.compile(r"(?i)stop-computer\b"), "shutdown"),
    (re.compile(r"(?i)cipher\s+/w"), "free-space wipe"),
    (re.compile(r"(?i)takeown\b|\bicacls\b.*/grant"), "ownership/ACL change"),
    (re.compile(r"(?i)\bnet\s+user\b.*/(add|delete)"), "account modification"),
]

# Ability ids that are refused because deletion is off the table entirely.
_FORBIDDEN_ABILITIES = {
    "delete_file",
    "delete_folder",
    "remove_path",
    "empty_recycle_bin",
}


def _is_protected(path: Path) -> str | None:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    for root in _PROTECTED_ROOTS:
        try:
            resolved.relative_to(root)
        except (ValueError, OSError):
            continue
        return str(root)
    return None


def check_command(command: str) -> None:
    """Refuse shell/Python commands that could destroy data or the OS."""
    for pattern, label in _FORBIDDEN_COMMANDS:
        if pattern.search(command):
            emit("guardrail.blocked", target="command", reason=label,
                 command=command[:400])
            raise Blocked(
                f"Refused: this command performs {label}, which is permanently "
                f"blocked (never delete your files, never damage the OS)."
            )


def check_path(path: str | os.PathLike[str], *, writing: bool = True) -> None:
    """Refuse writes anywhere inside a protected system location."""
    if not writing:
        return
    p = Path(str(path))
    root = _is_protected(p)
    if root:
        emit("guardrail.blocked", target="path", reason="protected_location",
             path=str(p))
        raise Blocked(
            f"Refused: {p} is inside a protected system location ({root}). "
            f"Writing there risks damaging the OS."
        )


def check_ability(ability_id: str, args: dict[str, Any]) -> None:
    """Full pre-execution check. Raises Blocked, or returns silently."""
    if ability_id in _FORBIDDEN_ABILITIES:
        emit("guardrail.blocked", target="ability", reason="deletion",
             ability=ability_id)
        raise Blocked(
            f"Refused: '{ability_id}' deletes data. Deletion is permanently "
            f"blocked. I can move things aside or rename them instead."
        )

    for key in ("command", "code", "script"):
        value = args.get(key)
        if isinstance(value, str) and value:
            check_command(value)

    for key in ("path", "destination", "dest", "target", "folder", "directory"):
        value = args.get(key)
        if isinstance(value, str) and value:
            check_path(value, writing=True)
