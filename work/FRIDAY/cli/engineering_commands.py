"""Engineering system CLI commands.

Usage:
    friday eng status          — org status + task queue + KB stats
    friday eng tasks           — list active tasks
    friday eng task <id>       — show task details
    friday eng create <title>  — create a new task
    friday eng complete <id>   — mark a task complete
    friday eng kb list         — list knowledge base entries
    friday eng kb search <q>   — search the knowledge base
    friday eng kb show <id>    — show a KB entry
    friday eng releases        — list releases
    friday eng validate        — run the validation pipeline
"""
from __future__ import annotations

import asyncio
import json
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown

from cli.terminal import COL_ACCENT, COL_PRIMARY, COL_SUCCESS, COL_WARN, COL_DANGER, COL_MUTED

console = Console()


async def cmd_eng_status(args: list[str]) -> int:
    """Show engineering org status."""
    from core.engineering_org import get_engineering_org
    org = get_engineering_org()
    status = await org.get_org_status()

    # Leads table
    leads_tbl = Table(title="Engineering Org — Leads", border_style=COL_PRIMARY)
    leads_tbl.add_column("Role", style="cyan")
    leads_tbl.add_column("Name", style="white")
    leads_tbl.add_column("Phases", style="dim")
    leads_tbl.add_column("Completed", style="green")
    leads_tbl.add_column("Failed", style="red")
    for role, lead in status["leads"].items():
        phases = ", ".join(lead["phases"]) if lead["phases"] else "—"
        leads_tbl.add_row(
            role,
            lead["name"],
            phases,
            str(lead["tasks_completed"]),
            str(lead["tasks_failed"]),
        )
    console.print(leads_tbl)

    # Task queue stats
    tq = status["task_queue"]
    console.print(Panel(
        f"[bold]Active tasks:[/] {tq['active']}    "
        f"[bold]Completed:[/] {tq['completed']}    "
        f"[bold]Receipts:[/] {tq['receipts']}",
        title="Task Queue",
        border_style=COL_ACCENT,
    ))

    # KB stats
    kb = status["knowledge_base"]
    kb_tbl = Table(title="Knowledge Base", border_style=COL_ACCENT)
    kb_tbl.add_column("Type", style="cyan")
    kb_tbl.add_column("Count", style="white")
    for k, v in kb["by_type"].items():
        if v > 0:
            kb_tbl.add_row(k, str(v))
    console.print(kb_tbl)
    return 0


async def cmd_eng_tasks(args: list[str]) -> int:
    """List active tasks."""
    from core.task_system import get_task_queue, TaskStatus
    queue = get_task_queue()
    tasks = await queue.list_tasks(include_completed=False)

    if not tasks:
        console.print("[dim]No active tasks.[/]")
        return 0

    tbl = Table(title=f"Active Tasks ({len(tasks)})", border_style=COL_PRIMARY)
    tbl.add_column("ID", style="dim")
    tbl.add_column("Title", style="white")
    tbl.add_column("Phase", style="cyan")
    tbl.add_column("Priority", style="yellow")
    tbl.add_column("Status", style="green")
    tbl.add_column("Assigned", style="dim")

    for t in tasks:
        status_color = COL_SUCCESS if t.status == TaskStatus.COMPLETED else COL_WARN
        tbl.add_row(
            t.id[:8],
            t.title[:40],
            t.phase.value,
            t.priority.value,
            f"[{status_color}]{t.status.value}[/]",
            t.assigned_to or "—",
        )
    console.print(tbl)
    return 0


async def cmd_eng_task(args: list[str]) -> int:
    """Show task details."""
    if not args:
        console.print("[red]Usage: friday eng task <id>[/]")
        return 1
    task_id = args[0]
    from core.task_system import get_task_queue
    queue = get_task_queue()
    task = await queue.get_task(task_id)
    if not task:
        console.print(f"[red]Task {task_id} not found[/]")
        return 1

    console.print(Panel(
        f"[bold]Title:[/] {task.title}\n"
        f"[bold]ID:[/] {task.id}\n"
        f"[bold]Phase:[/] {task.phase.value}\n"
        f"[bold]Priority:[/] {task.priority.value}\n"
        f"[bold]Status:[/] {task.status.value}\n"
        f"[bold]Assigned to:[/] {task.assigned_to or '—'}\n"
        f"[bold]Created:[/] {task.created_at[:19]}\n"
        f"[bold]Updated:[/] {task.updated_at[:19]}\n"
        f"[bold]Completed:[/] {task.completed_at[:19] if task.completed_at else '—'}\n"
        f"[bold]Retries:[/] {task.retry_count}/{task.max_retries}\n"
        f"[bold]Dependencies:[/] {', '.join(task.dependencies) or '—'}\n"
        f"[bold]Blockers:[/] {', '.join(task.blockers) or '—'}\n"
        f"[bold]Receipt:[/] {task.receipt_hash[:16] + '…' if task.receipt_hash else '—'}\n\n"
        f"[bold]Description:[/]\n{task.description or '(none)'}",
        title=f"Task {task.id[:8]}",
        border_style=COL_ACCENT,
    ))

    if task.history:
        hist_tbl = Table(title="History", border_style=COL_MUTED)
        hist_tbl.add_column("Time", style="dim")
        hist_tbl.add_column("Event", style="white")
        hist_tbl.add_column("Actor", style="cyan")
        for h in task.history[-10:]:
            hist_tbl.add_row(h.timestamp[:19], h.event, h.actor)
        console.print(hist_tbl)
    return 0


async def cmd_eng_create(args: list[str]) -> int:
    """Create a new task."""
    if not args:
        console.print("[red]Usage: friday eng create <title> [--phase=PHASE] [--priority=PRIORITY][/]")
        return 1
    title = args[0]
    phase = "quality"
    priority = "medium"
    for arg in args[1:]:
        if arg.startswith("--phase="):
            phase = arg.split("=", 1)[1]
        elif arg.startswith("--priority="):
            priority = arg.split("=", 1)[1]

    from core.engineering_org import get_engineering_org
    from core.task_system import TaskPhase, TaskPriority
    try:
        tp = TaskPhase(phase)
    except ValueError:
        console.print(f"[red]Invalid phase: {phase}. Valid: {', '.join(p.value for p in TaskPhase)}[/]")
        return 1
    try:
        pr = TaskPriority(priority)
    except ValueError:
        console.print(f"[red]Invalid priority: {priority}[/]")
        return 1

    org = get_engineering_org()
    task = await org.create_and_assign_task(
        title=title,
        description=f"Created via CLI",
        phase=tp,
        priority=pr,
    )
    console.print(Panel(
        f"Created task [cyan]{task.id}[/]\n"
        f"Title: {task.title}\n"
        f"Phase: {task.phase.value}\n"
        f"Priority: {task.priority.value}\n"
        f"Assigned to: {task.assigned_to}",
        title="✓ Task Created",
        border_style=COL_SUCCESS,
    ))
    return 0


async def cmd_eng_complete(args: list[str]) -> int:
    """Mark a task complete."""
    if not args:
        console.print("[red]Usage: friday eng complete <id>[/]")
        return 1
    task_id = args[0]
    from core.task_system import get_task_queue
    queue = get_task_queue()
    receipt = await queue.complete_task(task_id, actor="cli-human")
    if receipt:
        console.print(Panel(
            f"✓ Completed task {task_id[:8]}\n"
            f"Receipt hash: {receipt.hash[:32]}…\n"
            f"Duration: {receipt.duration_seconds:.1f}s",
            border_style=COL_SUCCESS,
        ))
        return 0
    console.print(f"[red]Task {task_id} not found or not in progress[/]")
    return 1


async def cmd_eng_kb_list(args: list[str]) -> int:
    """List knowledge base entries."""
    from core.knowledge_base import get_knowledge_base, EntryType
    kb = get_knowledge_base()
    entries = await kb.list_entries()

    if not entries:
        console.print("[dim]Knowledge base is empty.[/]")
        return 0

    tbl = Table(title=f"Knowledge Base ({len(entries)} entries)", border_style=COL_PRIMARY)
    tbl.add_column("ID", style="dim")
    tbl.add_column("Type", style="cyan")
    tbl.add_column("Title", style="white")
    tbl.add_column("Status", style="green")
    tbl.add_column("Updated", style="dim")

    for e in entries[:30]:
        tbl.add_row(
            e.id[:8],
            e.type.value,
            e.title[:50],
            e.status.value,
            e.updated_at[:19],
        )
    console.print(tbl)
    return 0


async def cmd_eng_kb_search(args: list[str]) -> int:
    """Search the knowledge base."""
    if not args:
        console.print("[red]Usage: friday eng kb search <query>[/]")
        return 1
    query = " ".join(args)
    from core.knowledge_base import get_knowledge_base
    kb = get_knowledge_base()
    results = await kb.search(query)

    if not results:
        console.print(f"[dim]No results for '{query}'[/]")
        return 0

    console.print(f"[green]Found {len(results)} entries for '{query}':[/]")
    for e in results:
        console.print(Panel(
            f"[bold]{e.title}[/]\n"
            f"[dim]{e.type.value} · {e.status.value} · {e.id[:8]}[/]\n\n"
            f"{e.summary}",
            border_style=COL_ACCENT,
        ))
    return 0


async def cmd_eng_releases(args: list[str]) -> int:
    """List releases."""
    from core.release_pipeline import get_release_pipeline
    pipe = get_release_pipeline()
    releases = await pipe.list_releases()

    if not releases:
        console.print("[dim]No releases.[/]")
        return 0

    tbl = Table(title=f"Releases ({len(releases)})", border_style=COL_PRIMARY)
    tbl.add_column("Version", style="cyan", no_wrap=True)
    tbl.add_column("Type", style="white")
    tbl.add_column("Status", style="green")
    tbl.add_column("Date", style="dim")
    for r in releases:
        tbl.add_row(
            f"v{r.version}",
            r.release_type.value,
            r.status.value,
            r.release_date[:19],
        )
    console.print(tbl)
    return 0


async def cmd_eng_validate(args: list[str]) -> int:
    """Run the validation pipeline."""
    console.print("[bold]Running validation pipeline…[/]")
    from core.validation_pipeline import get_validation_pipeline
    pipe = get_validation_pipeline()
    report = await pipe.run(skip_tests="--quick" in args)

    tbl = Table(title="Validation Report", border_style=COL_SUCCESS if report.passed else COL_DANGER)
    tbl.add_column("Check", style="cyan")
    tbl.add_column("Status", style="white")
    tbl.add_column("Duration", style="dim")
    tbl.add_column("Message", style="white")
    for c in report.checks:
        color = COL_SUCCESS if c.status == "passed" else COL_DANGER if c.status == "failed" else COL_MUTED
        tbl.add_row(
            c.name,
            f"[{color}]{c.status.value}[/]",
            f"{c.duration_seconds:.2f}s",
            c.message[:60],
        )
    console.print(tbl)
    console.print(f"\n[bold]Overall:[/] {'✓ PASSED' if report.passed else '✗ FAILED'} — {report.summary}")
    console.print(f"[dim]Total: {report.total_duration_seconds:.2f}s[/]")
    return 0 if report.passed else 1


# Command map
ENG_COMMANDS = {
    "status": cmd_eng_status,
    "tasks": cmd_eng_tasks,
    "task": cmd_eng_task,
    "create": cmd_eng_create,
    "complete": cmd_eng_complete,
    "kb": None,  # has subcommands
    "releases": cmd_eng_releases,
    "validate": cmd_eng_validate,
}

KB_COMMANDS = {
    "list": cmd_eng_kb_list,
    "search": cmd_eng_kb_search,
}


async def cmd_eng(args: list[str]) -> int:
    """Engineering system dispatcher."""
    if not args:
        return await cmd_eng_status([])
    sub = args[0]
    rest = args[1:]

    if sub == "kb":
        if not rest:
            return await cmd_eng_kb_list([])
        kb_sub = rest[0]
        kb_args = rest[1:]
        handler = KB_COMMANDS.get(kb_sub)
        if handler:
            return await handler(kb_args)
        console.print(f"[red]Unknown KB subcommand: {kb_sub}. Valid: {', '.join(KB_COMMANDS)}[/]")
        return 1

    handler = ENG_COMMANDS.get(sub)
    if handler is None:
        console.print(f"[red]Unknown eng subcommand: {sub}[/]")
        console.print(f"[dim]Valid: {', '.join(ENG_COMMANDS.keys())}[/]")
        return 1
    return await handler(rest)
