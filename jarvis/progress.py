"""Progress against the vision document.

    python -m jarvis.progress

There is no single honest percentage, and picking one would be the dishonest
move. "How much of JARVIS is built" depends entirely on the denominator:

  - of the 12-step loop the document's own spine describes?
  - of the 101 pack headings?
  - of the ~1481 individual capabilities those headings contain?
  - of a system a company would actually ship?
  - of the full endgame, including robotics and self-improvement?

Every one of those is a fair question with a very different answer, so all of
them are reported. The lowest is the one to take seriously.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.table import Table

# The Windows console defaults to cp1252, which cannot encode the block
# characters the bars are drawn with. Same fix as interface/cli.py.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from jarvis.abilities import registry as abilities
from jarvis.ontology import registry as ontology
from jarvis.skills import registry as skills

console = Console()

# The document's spine (line 27). This is the loop everything else elaborates.
LOOP = [
    ("PERCEIVE", True, "text, voice, screen state, files, system state"),
    ("UNDERSTAND", True, "classification and planning"),
    ("REMEMBER", True, "6 of 20 memory types, persistent"),
    ("REASON", True, "7 model routes, local and cloud"),
    ("PLAN", True, "multi-step plans with failover"),
    ("SIMULATE", False, "no dry-run before risky actions"),
    ("AUTHORIZE", True, "risk tiers and a real blocking gate"),
    ("ACT", True, "5 environments, 28 abilities"),
    ("OBSERVE", True, "every action returns real evidence"),
    ("VERIFY", True, "re-checks the world, not the model's claim"),
    ("LEARN", True, "failure memory feeds the next plan"),
    ("IMPROVE", False, "no self-improvement loop"),
]

# Judgement calls, stated openly rather than buried. "Shippable" asks what a
# company could put in front of a paying customer; "endgame" includes the
# research-blocked parts (robotics, capability discovery, self-improvement).
SHIPPABLE_ESTIMATE = 0.04
ENDGAME_ESTIMATE = 0.01


def bar(fraction: float, width: int = 34) -> str:
    filled = max(0, min(width, round(fraction * width)))
    colour = "green" if fraction >= 0.6 else "yellow" if fraction >= 0.25 else "red"
    return (f"[{colour}]{'█' * filled}[/{colour}]"
            f"[grey37]{'░' * (width - filled)}[/grey37]")


def main() -> int:
    cov = ontology.coverage()
    loop_done = sum(1 for _, ok, _ in LOOP if ok)

    measures = [
        ("The core loop (the document's spine)", loop_done, len(LOOP)),
        ("Pack headings touched at all", cov["implemented_or_partial"],
         cov["total_packs"]),
        ("Individual capabilities listed", cov["capabilities_built"],
         cov["capabilities_total"]),
        ("Of something shippable to a customer",
         round(SHIPPABLE_ESTIMATE * cov["capabilities_total"]),
         cov["capabilities_total"]),
        ("Of the full endgame (incl. robotics, self-improvement)",
         round(ENDGAME_ESTIMATE * cov["capabilities_total"]),
         cov["capabilities_total"]),
    ]

    console.print()
    console.print("[bold cyan]JARVIS — progress against the vision "
                  "document[/bold cyan]")
    console.print("[dim]Five honest denominators. The lowest is the one that "
                  "matters.[/dim]\n")

    table = Table(box=None, pad_edge=False)
    table.add_column("measured against", width=44)
    table.add_column("progress", width=36)
    table.add_column("", justify="right", width=13)
    for label, done, total in measures:
        frac = done / max(1, total)
        table.add_row(label, bar(frac), f"{done}/{total}  {frac*100:.1f}%")
    console.print(table)

    console.print("\n[bold]The 12-step loop, step by step[/bold]")
    line = "  "
    for name, ok, _ in LOOP:
        line += (f"[green]{name}[/green]" if ok else f"[red]{name}[/red]") + " → "
    console.print(line.rstrip(" → "))
    for name, ok, note in LOOP:
        if not ok:
            console.print(f"    [red]missing[/red] {name}: {note}")

    console.print("\n[bold]By subsystem[/bold]")
    sub = Table(box=None, pad_edge=False)
    sub.add_column("subsystem", width=14)
    sub.add_column("", width=26)
    sub.add_column("", justify="right", width=9)
    for name, data in sorted(cov["by_subsystem"].items(),
                             key=lambda kv: -kv[1]["percent"]):
        frac = data["percent"] / 100
        sub.add_row(name, bar(frac, width=24),
                    f"{data['implemented']}/{data['total']}")
    console.print(sub)

    console.print(f"\n[bold]Actually running right now:[/bold] "
                  f"{len(abilities.all_abilities())} abilities · "
                  f"{len(skills.all_skills())} skill playbooks · "
                  f"5 environments · 7 model routes")

    zero = [p for p in ontology.all_packs()
            if p.sub_implemented == 0 and p.sub_capabilities >= 15]
    console.print(f"[bold]Completely untouched:[/bold] {len(zero)} large packs "
                  f"({sum(p.sub_capabilities for p in zero)} capabilities)")

    console.print("\n[dim]Why the numbers differ so much: a pack is a "
                  "discipline, not a feature. What is built is the mechanical "
                  "half — files, apps, search, memory, routing, verification. "
                  "What is missing is the judgement half — learning by "
                  "watching, discovering unknown apps, self-improvement. "
                  "Those are research problems, not remaining effort.[/dim]\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
