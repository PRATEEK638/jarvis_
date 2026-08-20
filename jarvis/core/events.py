"""Structured event log.

Every decision, model call, ability execution and verification result is
appended as one JSON object per line. This is the audit trail, the debugging
tool, and the raw data for evaluation.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from jarvis.config import settings

_lock = threading.Lock()

# Anything that looks like a credential is scrubbed before it reaches disk.
_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"nvapi-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*\S+"),
]


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        out = value
        for pat in _SECRET_PATTERNS:
            out = pat.sub("[REDACTED]", out)
        return out
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _append(path: Path, record: dict[str, Any]) -> None:
    line = json.dumps(_scrub(record), ensure_ascii=False, default=str)
    with _lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# Live subscribers (the web UI's event stream). Kept separate from the disk
# log: a slow or disconnected subscriber must never be able to slow down or
# break the orchestrator, so delivery is fire-and-forget and failures are
# swallowed here rather than propagated.
_subscribers: dict[int, Any] = {}
_next_sub_id = 0


def subscribe(callback) -> int:
    """Register `callback(record: dict)` to receive every future event.

    Returns a token to pass to `unsubscribe`. Callbacks run synchronously on
    whatever thread called `emit` - keep them fast and non-blocking (the web
    layer only ever does a non-blocking queue put here).
    """
    global _next_sub_id
    with _lock:
        token = _next_sub_id
        _next_sub_id += 1
        _subscribers[token] = callback
    return token


def unsubscribe(token: int) -> None:
    with _lock:
        _subscribers.pop(token, None)


def emit(kind: str, **fields: Any) -> None:
    """Log one event. `kind` is a dotted name, e.g. 'ability.executed'."""
    record = {"ts": time.time(), "kind": kind, **fields}
    _append(settings.EVENT_LOG, record)
    if _subscribers:
        scrubbed = _scrub(record)
        for callback in list(_subscribers.values()):
            try:
                callback(scrubbed)
            except Exception:  # noqa: BLE001 - a broken UI must not break JARVIS
                pass


def emit_trace(trace: Any) -> None:
    """Persist a completed RouteTrace for evaluation."""
    payload = trace.model_dump() if hasattr(trace, "model_dump") else dict(trace)
    _append(settings.TRACE_LOG, {"ts": time.time(), **payload})


def read_traces(limit: int | None = None) -> list[dict[str, Any]]:
    path = settings.TRACE_LOG
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:] if limit else rows
