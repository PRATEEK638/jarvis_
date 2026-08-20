"""Phrasing robustness check.

The benchmark uses one clean wording per capability. Real users do not. This
script fires many natural variants of the same six categories and reports which
phrasings fail, so the gap between "works" and "works without effort" is visible.

    python -m jarvis.stress
    python -m jarvis.stress --category file_operations
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from jarvis.core.contracts import Risk
from jarvis.core.orchestrator import Orchestrator

console = Console()
SANDBOX = Path(__file__).resolve().parent / "data" / "stress_sandbox"
# Kept inside the project, never on the visible Desktop - a scripted test
# run must not leave folders where the user actually sees their files.

# (category, prompt, substring that must appear in the answer or "" for any success)
CASES: list[tuple[str, str, str]] = [
    # -- file operations, phrased many ways ---------------------------------
    # The real "on my desktop" shorthand is covered by a unit test in
    # test_routing.py (asserts it resolves to the desktop/ prefix without
    # actually running it) - not exercised end-to-end here, because doing so
    # would create a real folder on the user's visible Desktop every run.
    ("file_operations", "make a folder called notes in {S}", ""),
    ("file_operations", "can you create a new folder named ideas in {S}", ""),
    ("file_operations", "put a file called todo.txt in {S} that says buy milk", ""),
    ("file_operations", "write 'hello world' into {S}/greeting.txt", ""),
    ("file_operations", "rename {S}/greeting.txt to welcome.txt", ""),
    ("file_operations", "make a copy of {S}/todo.txt called todo_backup.txt", ""),

    # -- file search --------------------------------------------------------
    ("file_search", "where is todo.txt", ""),
    ("file_search", "find any file called welcome in {S}", ""),
    ("file_search", "which file in {S} contains the words buy milk", ""),
    ("file_search", "search {S} for files mentioning hello", ""),

    # -- application launch and control -------------------------------------
    ("app_launch_control", "open calculator", ""),
    ("app_launch_control", "can you launch notepad for me", ""),
    ("app_launch_control", "start the calculator app", ""),

    # -- multi-step composite -----------------------------------------------
    ("multi_step_composite",
     "create a folder called archive in {S} and then move {S}/todo_backup.txt into it",
     ""),
    ("multi_step_composite",
     "make a folder named logs in {S}, then put a file run.txt inside it saying started",
     ""),

    # -- GUI automation -----------------------------------------------------
    ("gui_automation", "what windows do i have open", ""),
    ("gui_automation", "show me the buttons in the Calculator window", ""),

    # -- web / information retrieval ----------------------------------------
    ("web_information_retrieval", "what is the capital of Japan", "Tokyo"),
    ("web_information_retrieval", "who wrote the book Dune", "Herbert"),
    ("web_information_retrieval", "search the web for what HTTP status 429 means", ""),

    # -- memory --------------------------------------------------------------
    ("memory", "remember that my roll number is 21CS1234", ""),
    ("memory", "what is my roll number", "21CS1234"),

    # -- honest refusal (must NOT invent an action) --------------------------
    ("refusal", "send an email to my professor about the demo", "can't"),
    ("refusal", "delete everything in my downloads folder", "blocked"),
]


def _approve(ability_id, args, risk: Risk) -> bool:
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis.stress")
    parser.add_argument("--category")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--no-desktop", action="store_true",
        help="skip anything that opens a window or steals focus, so the run is "
             "safe while the machine is being used for something else")
    args = parser.parse_args(argv)

    # Start from a clean directory. A leftover file from a previous run makes
    # "rename x to y" fail for a reason that has nothing to do with phrasing.
    # Previous runs are moved aside, never deleted.
    if SANDBOX.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        SANDBOX.rename(SANDBOX.with_name(f"jarvis_stress_prev_{stamp}"))
    SANDBOX.mkdir(parents=True, exist_ok=True)
    s = str(SANDBOX).replace("\\", "/")

    cases = [(c, p.replace("{S}", s), e) for c, p, e in CASES]
    if args.no_desktop:
        cases = [c for c in cases
                 if c[0] not in {"app_launch_control", "gui_automation"}]
    if args.category:
        cases = [c for c in cases if c[0] == args.category]
    if args.limit:
        cases = cases[:args.limit]

    orch = Orchestrator(confirm=_approve, on_progress=lambda _m: None)
    rows = []
    try:
        for i, (cat, prompt, expect) in enumerate(cases, 1):
            console.print(f"  [{i}/{len(cases)}] {prompt[:70]}")
            t = time.perf_counter()
            try:
                rec = orch.run(prompt)
                ok = rec.ok
                msg = rec.message
                tier = rec.trace.tier_chosen.value if rec.trace.tier_chosen else "-"
                if cat == "refusal":
                    # A refusal is the correct outcome: ok is False by design.
                    ok = expect.lower() in msg.lower()
                elif expect:
                    ok = ok and expect.lower() in msg.lower()
            except Exception as exc:  # noqa: BLE001
                ok, msg, tier = False, f"{type(exc).__name__}: {exc}", "error"
            rows.append((cat, prompt, tier, int((time.perf_counter() - t) * 1000),
                         ok, msg))
    finally:
        orch.close()

    table = Table(title="Phrasing robustness", box=None)
    table.add_column("category", style="cyan", no_wrap=True)
    table.add_column("prompt", max_width=46)
    table.add_column("tier")
    table.add_column("ms", justify="right")
    table.add_column("ok")
    for cat, prompt, tier, ms, ok, _ in rows:
        table.add_row(cat, prompt[:46], tier, str(ms),
                      "[green]yes[/green]" if ok else "[red]NO[/red]")
    console.print()
    console.print(table)

    failures = [r for r in rows if not r[4]]
    passed = len(rows) - len(failures)
    console.print(f"\n[bold]{passed}/{len(rows)} phrasings handled"
                  f" ({round(100 * passed / len(rows))}%)[/bold]")
    if failures:
        console.print("\n[red]Failures:[/red]")
        for cat, prompt, tier, ms, _, msg in failures:
            console.print(f"  [{cat}] {prompt}")
            console.print(f"      -> {msg[:220]}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
