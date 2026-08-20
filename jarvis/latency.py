"""Latency observability.

    python -m jarvis.latency

The article's most transferable point is that averages hide the problem. One
five-second interaction ruins a conversation, and a mean sitting at 900 ms
cheerfully conceals it. So this reports percentiles per stage, from the event
log that is already being written, and names the slowest stage rather than
leaving it to be inferred.

Percentiles are computed with nearest-rank on the sorted samples rather than
interpolated. With a few hundred samples per stage, interpolation invents
precision the data does not support, and nearest-rank has the useful property
that every reported number is a measurement that actually occurred.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

from jarvis.config import settings

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

console = Console()

# Event kind -> the stage it measures, and the field carrying the duration.
STAGES = {
    "model.call": ("model call", "latency_ms"),
    "ability.executed": ("tool execution", "duration_ms"),
    "goal.finished": ("whole request", "total_ms"),
    "voice.tools_finished": ("voice tool batch", "ms"),
    "delegate.answered": ("deep reasoning", "ms"),
}


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile: always an observation, never an interpolation."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1,
                       int(round(p / 100.0 * len(ordered) + 0.5)) - 1))
    return ordered[index]


def collect(path: Path | None = None) -> dict[str, list[float]]:
    log = path or settings.EVENT_LOG
    samples: dict[str, list[float]] = defaultdict(list)
    if not log.exists():
        return samples
    with log.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            stage = STAGES.get(event.get("kind"))
            if stage is None:
                continue
            label, field = stage
            value = event.get(field)
            if isinstance(value, (int, float)) and value >= 0:
                samples[label].append(float(value))
            # Per-route detail, because "model call" hides that one provider is
            # three times slower than another.
            if event.get("kind") == "model.call" and event.get("route"):
                samples[f"  via {event['route']}"].append(float(value or 0))
    return samples


def main() -> int:
    samples = collect()
    if not samples:
        console.print("[yellow]No timing data yet. Use JARVIS first.[/yellow]")
        return 0

    table = Table(title="Latency by stage (milliseconds)", box=None)
    table.add_column("stage", width=26)
    table.add_column("n", justify="right", width=6)
    table.add_column("p50", justify="right", width=8)
    table.add_column("p90", justify="right", width=8)
    table.add_column("p99", justify="right", width=8)
    table.add_column("worst", justify="right", width=8)

    rows = []
    for label, values in samples.items():
        rows.append((label, values, percentile(values, 50)))
    # Top-level stages first, then the per-route breakdown indented under them.
    rows.sort(key=lambda r: (r[0].startswith("  "), -r[2]))

    for label, values, p50 in rows:
        style = ""
        if not label.startswith("  ") and percentile(values, 90) > 5000:
            style = "yellow"
        table.add_row(
            f"[{style}]{label}[/{style}]" if style else label,
            str(len(values)),
            f"{p50:,.0f}", f"{percentile(values, 90):,.0f}",
            f"{percentile(values, 99):,.0f}", f"{max(values):,.0f}")
    console.print()
    console.print(table)

    top = [(label, values) for label, values in samples.items()
           if not label.startswith("  ")]
    if top:
        worst = max(top, key=lambda kv: percentile(kv[1], 90))
        console.print(f"\n[bold]Slowest stage at p90:[/bold] {worst[0]} "
                      f"({percentile(worst[1], 90):,.0f} ms)")
        console.print("[dim]p90 rather than the mean on purpose: one five-second "
                      "interaction ruins a conversation, and an average hides "
                      "it completely.[/dim]\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
