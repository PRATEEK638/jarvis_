"""JARVIS command line interface.

    python -m jarvis                       interactive session
    python -m jarvis "create a folder..."  single request
    python -m jarvis --status              show tier availability
    python -m jarvis --stats               evaluation stats from past runs
    python -m jarvis --abilities           registered capabilities
    python -m jarvis --yes ...             skip confirmation prompts (logged)

The route panel after each request shows which tier ran it and why. That is the
system's central design claim made visible rather than asserted.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from jarvis.abilities import registry
from jarvis.core.contracts import Risk, TaskRecord
from jarvis.core.events import emit
from jarvis.core.orchestrator import Orchestrator

# The Windows console defaults to cp1252, which cannot encode box-drawing or any
# non-Latin-1 character a web page or filename might contain. Force UTF-8 so
# output never dies on an encoding error mid-render.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

console = Console()

BANNER = r"""
   _   _   ___ _   _ ___ ___
  | | /_\ | _ \ | | |_ _/ __|   Personal Cognitive Operating Layer
 _| |/ _ \|   / |_| || |\__ \   hybrid local / cloud routing
|__/_/ \_\_|_\\___/|___|___/    verified execution
"""


def _confirm(ability_id: str, args: dict[str, Any], risk: Risk) -> bool:
    detail = ", ".join(f"{k}={v!r}" for k, v in args.items())
    if len(detail) > 300:
        detail = detail[:300] + "..."
    colour = "yellow" if risk is Risk.MEDIUM else "red"
    console.print(Panel(
        f"[bold]{ability_id}[/bold]\n{detail}",
        title=f"[{colour}]{risk.value.upper()} RISK — approve?[/{colour}]",
        border_style=colour, expand=False))
    try:
        reply = console.input("  proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return reply in {"y", "yes"}


def _auto_confirm(ability_id: str, args: dict[str, Any], risk: Risk) -> bool:
    emit("confirmation.auto_approved", ability=ability_id, risk=risk.value)
    console.print(f"  [dim]auto-approved ({risk.value} risk): {ability_id}[/dim]")
    return True


def _render(record: TaskRecord) -> None:
    trace = record.trace

    body = Text(record.message or "(no output)")
    console.print(Panel(body,
                        title="[bold cyan]JARVIS[/bold cyan]",
                        border_style="cyan" if record.ok else "red",
                        subtitle=f"[dim]{record.goal.status}[/dim]"))

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()

    cls = trace.classification
    if cls:
        table.add_row("request", f"{cls.difficulty.value} - {cls.privacy.value}"
                                 + (" - needs web" if cls.needs_web else "")
                                 + (" - needs GUI" if cls.needs_gui else ""))
        if cls.rationale:
            table.add_row("because", cls.rationale)
    if trace.tier_chosen:
        tier_colour = "green" if trace.tier_chosen.value == "local" else "magenta"
        table.add_row("tier", f"[{tier_colour}]{trace.tier_chosen.value.upper()}"
                              f"[/{tier_colour}] — {trace.reason}")
    if trace.escalated:
        table.add_row("escalated", trace.escalation_reason or "yes")
    if trace.degraded:
        table.add_row("[yellow]degraded[/yellow]",
                      "preferred tier unavailable; ran with reduced capability")

    for call in trace.calls:
        sent = "0 bytes left the machine" if call.bytes_sent == 0 \
            else f"{call.bytes_sent / 1024:.1f} KB sent"
        table.add_row(f"{call.purpose}", f"{call.model} - {call.latency_ms} ms - {sent}"
                                         + ("" if call.ok else " - FAILED"))

    for step in record.plan.steps:
        verified = ""
        if step.verification:
            verified = (" - verified" if step.verification.verified
                        else f" - NOT verified ({step.verification.detail})")
        table.add_row(f"step {step.n}", f"{step.ability} -> {step.status}{verified}")

    table.add_row("total", f"{trace.total_ms} ms")
    console.print(Panel(table, title="[dim]route trace[/dim]",
                        border_style="grey37"))


def _show_status(orch: Orchestrator) -> None:
    rows = orch.router.status()
    table = Table(title="Model registry", box=None)
    table.add_column("route"); table.add_column("tier")
    table.add_column("cost", justify="center")
    table.add_column("q", justify="right")
    table.add_column("status")
    for row in rows:
        cost = "free" if row["free"] else "[yellow]metered[/yellow]"
        privacy = " [green]private[/green]" if row["private"] else ""
        status = ("[green]ready[/green]" if row["available"]
                  else f"[dim]{row['why']}[/dim]")
        table.add_row(row["id"], row["tier"] + privacy, cost,
                      str(row["quality"]), status)
    console.print(table)
    console.print(f"[dim]free RAM: {orch.router.describe()['free_ram_gb']} GB[/dim]")

    env_table = Table(title="Environments", box=None)
    env_table.add_column("environment"); env_table.add_column("state")
    for env_id, env in orch.environments.items():
        try:
            state = env.state()
        except Exception as exc:      # noqa: BLE001 - one bad env must not hide the rest
            env_table.add_row(env_id, f"[red]error: {exc}[/red]")
            continue
        # Rendered from whatever the environment actually reports rather than
        # from a per-id branch. The previous version assumed every environment
        # exposed cpu_percent, so adding the repo environment crashed --status
        # outright: any new adapter would have broken it the same way.
        if "cpu_percent" in state:
            detail = (f"CPU {state['cpu_percent']}% - "
                      f"RAM {state.get('ram_used_gb')}/"
                      f"{state.get('ram_total_gb')} GB - "
                      f"{state.get('disk_free_gb')} GB free")
        elif "online" in state:
            detail = "online" if state["online"] else "offline"
        elif state.get("available") is False:
            detail = (f"unavailable: "
                      f"{state.get('why') or state.get('import_error') or 'unknown'}")
        elif "branch" in state:
            detail = (f"{state['branch']} - "
                      + ("clean" if state.get("clean")
                         else f"{state.get('changed_files')} changed"))
        elif state.get("available"):
            detail = "ready"
        else:
            # Last resort: show the first couple of real values instead of a
            # blank cell, so a new environment is still legible here.
            detail = ", ".join(f"{k}={v}" for k, v in list(state.items())[:2]) or "-"
        env_table.add_row(env_id, detail)
    console.print(env_table)

    from jarvis.interface.voice import status as voice_status
    v = voice_status()
    vt = Table(title="Voice", box=None)
    vt.add_column("part"); vt.add_column("state")
    vt.add_row("microphone", "[green]ready[/green]" if v["microphone"]["available"]
               else f"[red]{v['microphone']['error']}[/red]")
    vt.add_row("speech out", "[green]ready[/green] (Windows SAPI)"
               if v["speech_out"]["available"]
               else f"[red]{v['speech_out']['error']}[/red]")
    vt.add_row("transcription",
               f"[green]ready[/green] ({v['transcription']['model']}"
               + (", dedicated key" if v["transcription"]["dedicated_key"] else "")
               + ")" if v["transcription"]["available"]
               else "[red]needs a Gemini key[/red]")
    console.print(vt)
    console.print("[dim]run 'jarvis --voice', or type 'voice' in a session[/dim]")

    if not any(r["available"] and r["tier"] == "cloud" for r in rows):
        console.print(Panel(
            "No cloud API key configured, so JARVIS is running local-only: every "
            "file, search, app and GUI capability works and nothing leaves this "
            "machine, but web/knowledge tasks are limited.\n"
            "Add keys to [bold]jarvis/config/keys.json[/bold]:\n"
            '  {"gemini": "...", "openrouter": "...", "nvidia": "...", '
            '"sarvam": "..."}',
            title="[yellow]local-only mode[/yellow]", border_style="yellow"))


def _show_stats(orch: Orchestrator) -> None:
    stats = orch.store.stats()
    table = Table(title="Evaluation data collected so far", box=None)
    table.add_column("metric"); table.add_column("value")
    table.add_row("tasks run", str(stats["tasks_total"]))
    table.add_row("succeeded", str(stats["tasks_ok"]))
    table.add_row("success rate",
                  f"{stats['success_rate']}%" if stats["success_rate"] is not None
                  else "n/a")
    table.add_row("facts remembered", str(stats["facts_stored"]))
    for tier, count in (stats["tasks_by_tier"] or {}).items():
        table.add_row(f"routed to {tier}", str(count))
    console.print(table)

    recent = orch.store.recent_tasks(8)
    if recent:
        rt = Table(title="Recent runs", box=None)
        rt.add_column("objective", max_width=52); rt.add_column("tier")
        rt.add_column("ok")
        for row in recent:
            rt.add_row(row["objective"][:52], row["tier"] or "-",
                       "[green]yes[/green]" if row["ok"] else "[red]no[/red]")
        console.print(rt)


def _show_abilities() -> None:
    table = Table(title="Registered abilities", box=None)
    table.add_column("category", style="cyan"); table.add_column("ability")
    table.add_column("risk"); table.add_column("verification", style="dim")
    for ability in registry.all_abilities():
        table.add_row(ability.category.value, ability.signature(),
                      ability.risk.value, ability.verification)
    console.print(table)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jarvis", description="JARVIS — hybrid local/cloud desktop agent")
    parser.add_argument("request", nargs="*", help="what you want done")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="approve medium/high risk actions without asking")
    parser.add_argument("--status", action="store_true",
                        help="show model tiers and environment state")
    parser.add_argument("--stats", action="store_true",
                        help="show collected evaluation statistics")
    parser.add_argument("--abilities", action="store_true",
                        help="list registered abilities")
    parser.add_argument("--quiet", action="store_true",
                        help="print only the answer, no route trace")
    parser.add_argument("--voice", action="store_true",
                        help="talk to JARVIS: listen, act, speak the answer")
    parser.add_argument("--speak", action="store_true",
                        help="type as usual, but read answers aloud")
    parser.add_argument("--voice-name", default=None,
                        help="Live voice: Charon, Puck, Kore, Fenrir or Aoede")
    parser.add_argument("--half-duplex", action="store_true",
                        help="force the older listen-then-speak voice mode")
    args = parser.parse_args(argv)

    if args.abilities:
        _show_abilities()
        return 0

    confirm = _auto_confirm if args.yes else _confirm
    progress = (lambda msg: None) if args.quiet else \
        (lambda msg: console.print(f"  [dim]- {msg}[/dim]"))
    orch = Orchestrator(confirm=confirm, on_progress=progress)

    try:
        if args.status:
            _show_status(orch)
            return 0
        if args.stats:
            _show_stats(orch)
            return 0

        if args.voice:
            # Preferred path: the Live API carries audio both ways over one
            # socket, so JARVIS can be interrupted mid-sentence and answers in
            # a natural voice. The half-duplex loop is kept as a fallback for
            # when that is unavailable, because a working listen-then-speak
            # mode beats no voice at all.
            from jarvis.voice.live import voice_available
            ready, why = voice_available()
            if ready and not args.half_duplex:
                from jarvis.voice.runner import run_voice
                orch.close()
                return run_voice(voice=args.voice_name)
            if not args.half_duplex:
                console.print(f"[yellow]Live voice unavailable ({why}); "
                              f"using listen-then-speak mode.[/yellow]")
            return _voice_loop(orch)

        speaker = None
        if args.speak:
            from jarvis.interface.voice import Speaker
            speaker = Speaker()
            if not speaker.available:
                console.print(f"[yellow]speech output unavailable: "
                              f"{speaker.error}[/yellow]")
                speaker = None

        if args.request:
            record = orch.run(" ".join(args.request))
            if args.quiet:
                console.print(record.message)
            else:
                _render(record)
            if speaker:
                speaker.say(record.message)
            return 0 if record.ok else 1

        # interactive
        console.print(Text(BANNER, style="bold cyan"))
        _show_status(orch)
        console.print("[dim]Type a request, or 'exit'. "
                      "'status', 'stats', 'abilities' also work.[/dim]\n")
        while True:
            try:
                line = console.input("[bold cyan]you >[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
            if not line:
                continue
            low = line.lower()
            if low in {"exit", "quit", "bye"}:
                break
            if low == "status":
                _show_status(orch)
                continue
            if low == "stats":
                _show_stats(orch)
                continue
            if low == "abilities":
                _show_abilities()
                continue
            if low in {"voice", "listen"}:
                _voice_loop(orch)
                continue
            try:
                record = orch.run(line)
                _render(record)
                if speaker:
                    speaker.say(record.message)
            except KeyboardInterrupt:
                console.print("[yellow]interrupted[/yellow]")
        return 0
    finally:
        orch.close()


def _voice_loop(orch: Orchestrator) -> int:
    """Listen, act, speak. Ctrl-C to leave."""
    from jarvis.interface.voice import Microphone, Speaker, Transcriber, status

    info = status()
    mic, speaker, stt = Microphone(), Speaker(), Transcriber()

    missing = [name for name, ok in (
        ("microphone", info["microphone"]["available"]),
        ("transcription", info["transcription"]["available"]),
    ) if not ok]
    if missing:
        console.print(Panel(
            f"Voice needs {', '.join(missing)}, which is unavailable.\n"
            f"mic: {info['microphone']['error']}\n"
            f"transcription: needs a Gemini key in config/keys.json",
            title="[red]voice unavailable[/red]", border_style="red"))
        return 1

    console.print(Panel(
        "Speak when you see [bold green]listening[/bold green]. Stop talking and "
        "JARVIS will act.\nSay [bold]exit[/bold] or press Ctrl-C to go back to "
        "typing."
        + ("" if speaker.available else
           f"\n[yellow]speech output unavailable: {speaker.error}[/yellow]"),
        title="[cyan]voice mode[/cyan]", border_style="cyan"))

    while True:
        try:
            console.print("[bold green]listening...[/bold green] [dim](speak now)[/dim]")
            wav = mic.record_utterance()
            if wav is None:
                console.print("[dim]  nothing heard[/dim]")
                continue

            console.print("[dim]  transcribing...[/dim]")
            try:
                said = stt.transcribe(wav)
            except RuntimeError as exc:
                console.print(f"[red]  transcription failed: {exc}[/red]")
                continue
            if not said:
                console.print("[dim]  no speech recognised[/dim]")
                continue

            console.print(f"[bold cyan]you said ›[/bold cyan] {said}")
            low = said.strip().lower().rstrip(".!?")
            if low in {"exit", "quit", "stop", "goodbye", "bye"}:
                if speaker.available:
                    speaker.say("Going back to text mode.")
                return 0

            record = orch.run(said)
            _render(record)
            if speaker.available:
                speaker.say(record.message)
        except KeyboardInterrupt:
            console.print("\n[yellow]leaving voice mode[/yellow]")
            speaker.stop()
            return 0


if __name__ == "__main__":
    sys.exit(main())
