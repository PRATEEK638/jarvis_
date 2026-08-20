"""Voice mode entry point.

Connects the spoken session to the orchestrator and prints a live transcript,
so what JARVIS heard, what it did, and what it said are all visible while the
conversation is happening.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel

from jarvis.core.contracts import Risk
from jarvis.core.orchestrator import Orchestrator
from jarvis.voice.live import VoiceSession, voice_available

console = Console()

_STYLES = {
    "you": "bold cyan",
    "jarvis": "bold white",
    "action": "yellow",
    "result": "dim",
    "status": "dim italic",
}


def _confirm_aloud(ability_id: str, args: dict[str, Any], risk: Risk) -> bool:
    """Approval policy for spoken commands.

    Voice has no keyboard in the loop, so a medium-risk action cannot pause for
    a typed yes. Medium risk proceeds (it is reversible or scoped, and the
    guardrails have already run); high risk is refused and the reason is spoken
    back, rather than silently doing something irreversible on a misheard word.
    """
    if risk is Risk.HIGH:
        console.print(f"  [red]refused (high risk): {ability_id}[/red]")
        return False
    return True


def run_voice(*, voice: str | None = None) -> int:
    ok, reason = voice_available()
    if not ok:
        console.print(f"[red]Voice is unavailable: {reason}[/red]")
        return 2

    orch = Orchestrator(confirm=_confirm_aloud,
                        on_progress=lambda _m: None)

    def execute(ability_id: str, args: dict[str, Any]) -> str:
        return orch.call_ability(ability_id, args)

    def on_event(kind: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        style = _STYLES.get(kind, "")
        label = {"you": "you", "jarvis": "JARVIS", "action": "->",
                 "result": "  ", "status": "  ·"}.get(kind, kind)
        console.print(f"[{style}]{label}[/{style}] {text}"
                      if style else f"{label} {text}")

    console.print(Panel(
        "Speak naturally. JARVIS can act on this machine while you talk.\n"
        "Interrupt any time — it stops speaking when you start.\n"
        "Press Ctrl+C to end the session.",
        title="voice mode", border_style="cyan"))

    session = VoiceSession(execute, on_event=on_event, voice=voice)
    try:
        session.run()
    except KeyboardInterrupt:
        console.print("\n[dim]session ended[/dim]")
    except Exception as exc:                        # noqa: BLE001
        console.print(f"[red]Voice session failed: "
                      f"{type(exc).__name__}: {exc}[/red]")
        return 1
    finally:
        orch.close()
    return 0
