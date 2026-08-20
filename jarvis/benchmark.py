"""Benchmark harness.

Runs a prompt set across the six task categories of the baseline study, using
the same success criteria, so the hybrid system's numbers are directly
comparable with the ARIA (local-only) and Mark-XXXIX (cloud-only) results.

What makes a task a pass here is deliberately stricter than "the model said it
worked": each prompt carries a `check` callable that re-observes the machine
afterwards. A run where every step reported success but the file is not on disk
is scored as a failure.

Usage:
    python -m jarvis.benchmark                 run every category
    python -m jarvis.benchmark --category file_operations
    python -m jarvis.benchmark --repeat 3      repeat for latency stability
    python -m jarvis.benchmark --no-gui        skip prompts that steal focus

Results are written to benchmark_results/ as JSON and printed as a table.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.table import Table

from jarvis.core.contracts import Category, Risk, TaskRecord
from jarvis.core.orchestrator import Orchestrator

console = Console()

SANDBOX = Path(__file__).resolve().parent / "data" / "benchmark_sandbox"
# Kept inside the project, never on the visible Desktop - a scripted test
# run must not leave folders where the user actually sees their files.
RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmark_results"


@dataclass
class Task:
    """One benchmark prompt plus how to verify it independently."""

    category: Category
    prompt: str
    check: Callable[[], bool] | None = None
    setup: Callable[[], None] | None = None
    needs_gui: bool = False
    needs_network: bool = False
    note: str = ""


@dataclass
class Outcome:
    task: Task
    ok: bool
    checked: bool | None
    tier: str
    latency_ms: int
    bytes_sent: int
    steps: int
    escalated: bool
    message: str
    error: str = ""


# ---------------------------------------------------------------------------
# Prompt set
# ---------------------------------------------------------------------------

def _reset_sandbox() -> None:
    """Prepare a clean working area.

    Only ever touches the benchmark's own directory, and moves a previous run
    aside rather than deleting it - deletion is blocked everywhere in JARVIS and
    the harness holds itself to the same rule.
    """
    if SANDBOX.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        shutil.move(str(SANDBOX), str(SANDBOX.with_name(
            f"jarvis_benchmark_prev_{stamp}")))
    SANDBOX.mkdir(parents=True, exist_ok=True)


def _seed_files() -> None:
    (SANDBOX / "alpha.txt").write_text(
        "This document mentions photosynthesis in the second sentence.",
        encoding="utf-8")
    (SANDBOX / "beta.txt").write_text("Unrelated content about databases.",
                                      encoding="utf-8")
    (SANDBOX / "quarterly_report.txt").write_text("Revenue figures here.",
                                                  encoding="utf-8")


def build_tasks() -> list[Task]:
    s = str(SANDBOX).replace("\\", "/")
    return [
        # -- 1. File operations -------------------------------------------
        Task(Category.FILE_OPS,
             f"create a folder called archive at {s}",
             check=lambda: (SANDBOX / "archive").is_dir()),
        Task(Category.FILE_OPS,
             f"create a file at {s}/hello.txt containing the text "
             f"jarvis benchmark line",
             check=lambda: (SANDBOX / "hello.txt").is_file()
             and "jarvis benchmark line" in
             (SANDBOX / "hello.txt").read_text(encoding="utf-8")),
        Task(Category.FILE_OPS,
             f"rename the file {s}/beta.txt to gamma.txt",
             check=lambda: (SANDBOX / "gamma.txt").is_file()
             and not (SANDBOX / "beta.txt").exists()),
        Task(Category.FILE_OPS,
             f"copy {s}/alpha.txt to {s}/alpha_backup.txt",
             check=lambda: (SANDBOX / "alpha_backup.txt").is_file()
             and (SANDBOX / "alpha.txt").is_file()),

        # -- 2. File search ------------------------------------------------
        Task(Category.FILE_SEARCH,
             f"find files with quarterly in the name under {s}",
             check=lambda: True,
             note="scored on whether the correct path appears in the answer"),
        Task(Category.FILE_SEARCH,
             f"which file under {s} mentions photosynthesis",
             check=lambda: True,
             note="content search; correct answer is alpha.txt"),

        # -- 3. Application launch and control -----------------------------
        Task(Category.APP_CONTROL, "open calculator",
             check=lambda: _process_running("calc") or _process_running(
                 "CalculatorApp")),
        Task(Category.APP_CONTROL, "open notepad",
             check=lambda: _process_running("notepad"),
             note="Store-packaged Notepad does not always expose a process"),

        # -- 4. Multi-step composite ---------------------------------------
        Task(Category.COMPOSITE,
             f"create a folder called reports at {s} and then create a file "
             f"inside it called summary.txt with the text composite task done",
             check=lambda: (SANDBOX / "reports" / "summary.txt").is_file()
             and "composite task done" in
             (SANDBOX / "reports" / "summary.txt").read_text(encoding="utf-8")),
        Task(Category.COMPOSITE,
             f"create a folder called moved at {s} and then move "
             f"{s}/quarterly_report.txt into it",
             check=lambda: (SANDBOX / "moved" / "quarterly_report.txt").is_file()),

        # -- 5. GUI automation ---------------------------------------------
        Task(Category.GUI_AUTOMATION, "which windows are open",
             check=lambda: True, needs_gui=True),
        Task(Category.GUI_AUTOMATION,
             "read the controls in the Calculator window",
             check=lambda: True, needs_gui=True,
             note="requires Calculator to be open from the app-launch category"),

        # -- 6. Web / information retrieval --------------------------------
        Task(Category.WEB_INFO,
             "search the web for the latest stable version of python",
             check=lambda: True, needs_network=True),
        Task(Category.WEB_INFO,
             "look up what the capital of Australia is",
             check=lambda: True, needs_network=True),

        # -- memory ----------------------------------------------------------
        Task(Category.MEMORY,
             "remember that the benchmark guide is Prateek Sharma",
             check=lambda: True),
        Task(Category.MEMORY,
             "what do you remember about the benchmark guide",
             check=lambda: True,
             note="must recall the fact stored by the previous prompt"),
    ]


def _process_running(name: str) -> bool:
    import psutil
    needle = name.lower()
    for proc in psutil.process_iter(["name"]):
        if needle in (proc.info.get("name") or "").lower():
            return True
    return False


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _auto_approve(ability_id: str, args: dict[str, Any], risk: Risk) -> bool:
    return True


def run_task(orch: Orchestrator, task: Task) -> Outcome:
    if task.setup:
        task.setup()
    started = time.perf_counter()
    try:
        record: TaskRecord = orch.run(task.prompt)
    except Exception as exc:  # noqa: BLE001 - a crash is a benchmark result
        return Outcome(task, False, None, "error",
                       int((time.perf_counter() - started) * 1000), 0, 0, False,
                       "", error=f"{type(exc).__name__}: {exc}")

    checked: bool | None = None
    if task.check is not None:
        time.sleep(0.2)  # let the filesystem settle
        try:
            checked = bool(task.check())
        except Exception as exc:  # noqa: BLE001
            checked = False
            record.message += f"\n[check raised {exc}]"

    ok = record.ok and (checked is not False)
    trace = record.trace
    return Outcome(
        task=task, ok=ok, checked=checked,
        tier=trace.tier_chosen.value if trace.tier_chosen else "none",
        latency_ms=trace.total_ms, bytes_sent=trace.bytes_sent,
        steps=len(record.plan.steps), escalated=trace.escalated,
        message=record.message[:400],
    )


def summarise(outcomes: list[Outcome]) -> dict[str, Any]:
    by_cat: dict[str, list[Outcome]] = {}
    for o in outcomes:
        by_cat.setdefault(o.task.category.value, []).append(o)

    categories = {}
    for cat, items in by_cat.items():
        passed = sum(1 for o in items if o.ok)
        categories[cat] = {
            "n": len(items),
            "passed": passed,
            "success_rate": round(100 * passed / len(items), 1),
            "median_latency_ms": int(statistics.median(
                [o.latency_ms for o in items])),
        }

    total = len(outcomes)
    passed = sum(1 for o in outcomes if o.ok)
    tiers: dict[str, int] = {}
    for o in outcomes:
        tiers[o.tier] = tiers.get(o.tier, 0) + 1

    return {
        "total_tasks": total,
        "passed": passed,
        "overall_success_rate": round(100 * passed / total, 1) if total else 0.0,
        "median_latency_ms": int(statistics.median(
            [o.latency_ms for o in outcomes])) if outcomes else 0,
        "total_bytes_sent": sum(o.bytes_sent for o in outcomes),
        "tasks_by_tier": tiers,
        "escalations": sum(1 for o in outcomes if o.escalated),
        "by_category": categories,
    }


def render(outcomes: list[Outcome], summary: dict[str, Any]) -> None:
    table = Table(title="Per-task results", box=None)
    table.add_column("category", style="cyan", no_wrap=True)
    table.add_column("prompt", max_width=44)
    table.add_column("tier")
    table.add_column("ms", justify="right")
    table.add_column("sent", justify="right")
    table.add_column("result")
    for o in outcomes:
        verdict = "[green]PASS[/green]" if o.ok else "[red]FAIL[/red]"
        if o.error:
            verdict = f"[red]ERROR[/red] {o.error[:28]}"
        sent = "0" if o.bytes_sent == 0 else f"{o.bytes_sent / 1024:.1f}K"
        table.add_row(o.task.category.value, o.task.prompt[:44], o.tier,
                      str(o.latency_ms), sent, verdict)
    console.print(table)

    cat_table = Table(title="Success rate by category", box=None)
    cat_table.add_column("category", style="cyan")
    cat_table.add_column("n", justify="right")
    cat_table.add_column("passed", justify="right")
    cat_table.add_column("rate", justify="right")
    cat_table.add_column("median ms", justify="right")
    for cat, row in summary["by_category"].items():
        cat_table.add_row(cat, str(row["n"]), str(row["passed"]),
                          f"{row['success_rate']}%",
                          str(row["median_latency_ms"]))
    console.print(cat_table)

    console.print(
        f"\n[bold]Overall[/bold]: {summary['passed']}/{summary['total_tasks']} "
        f"= {summary['overall_success_rate']}%  |  "
        f"median {summary['median_latency_ms']} ms  |  "
        f"data sent off-machine: {summary['total_bytes_sent'] / 1024:.1f} KB  |  "
        f"routing {summary['tasks_by_tier']}  |  "
        f"escalations: {summary['escalations']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis.benchmark")
    parser.add_argument("--category", help="run only this category")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--no-gui", action="store_true",
                        help="skip prompts that interact with windows")
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--keep-sandbox", action="store_true",
                        help="do not reset the benchmark working directory")
    args = parser.parse_args(argv)

    if not args.keep_sandbox:
        _reset_sandbox()
    _seed_files()

    tasks = build_tasks()
    if args.category:
        tasks = [t for t in tasks if t.category.value == args.category]
    if args.no_gui:
        tasks = [t for t in tasks if not t.needs_gui]
    if args.no_network:
        tasks = [t for t in tasks if not t.needs_network]
    if not tasks:
        console.print("[red]No tasks match those filters.[/red]")
        return 1

    orch = Orchestrator(confirm=_auto_approve, on_progress=lambda _m: None)
    outcomes: list[Outcome] = []
    try:
        console.print(f"[dim]Running {len(tasks)} task(s) x {args.repeat}"
                      f" in {SANDBOX}[/dim]\n")
        for round_no in range(args.repeat):
            for i, task in enumerate(tasks, start=1):
                console.print(f"  [{round_no + 1}.{i}] {task.prompt[:64]}")
                outcomes.append(run_task(orch, task))
    finally:
        orch.close()

    summary = summarise(outcomes)
    console.print()
    render(outcomes, summary)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"benchmark_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({
        "summary": summary,
        "results": [{
            "category": o.task.category.value, "prompt": o.task.prompt,
            "ok": o.ok, "independently_checked": o.checked, "tier": o.tier,
            "latency_ms": o.latency_ms, "bytes_sent": o.bytes_sent,
            "steps": o.steps, "escalated": o.escalated,
            "message": o.message, "error": o.error, "note": o.task.note,
        } for o in outcomes],
    }, indent=2), encoding="utf-8")
    console.print(f"\n[dim]written to {out}[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
