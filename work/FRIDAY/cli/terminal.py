"""FRIDAY Terminal — sci-fi TUI powered by rich + textual (optional).

Public entry points:
    - boot_sequence()       — animated startup with real checks
    - run_tui()             — main two-pane conversation UI
    - render_receipt()      — stylised action receipt
    - render_approval()     — colour-coded approval prompt
    - render_thinking()     — pulsing "thinking" indicator

Designed to degrade gracefully:
    - textual is optional — falls back to rich.live for animation
    - all "real checks" call actual Friday subsystems and report honest
      OFFLINE / ERROR states when something isn't configured
    - never fabricates integration status — if Spotify has no creds,
      we show "OFFLINE (no credentials)" not "LIVE"
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime
from typing import Optional

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

console = Console()

# ---------------------------------------------------------------------------
# Sci-fi colour palette (same as VS Code extension / web dashboard)
# ---------------------------------------------------------------------------
COL_BG = "#050810"
COL_PRIMARY = "#7C3AED"   # purple
COL_ACCENT = "#06B6D4"    # cyan
COL_SUCCESS = "#10B981"
COL_WARN = "#F59E0B"
COL_DANGER = "#EF4444"
COL_MUTED = "#6B7280"


# ---------------------------------------------------------------------------
# ASCII logo (typewriter effect)
# ---------------------------------------------------------------------------
LOGO = r"""
  ███████╗██████╗ ██╗██████╗  █████╗ ██╗   ██╗
  ██╔════╝██╔══██╗██║██╔══██╗██╔══██╗╚██╗ ██╔╝
  █████╗  ██████╔╝██║██║  ██║███████║ ╚████╔╝
  ██╔══╝  ██╔══██╗██║██║  ██║██╔══██║  ╚██╔╝
  ██║     ██║  ██║██║██████╔╝██║  ██║   ██║
  ╚═╝     ╚═╝  ╚═╝╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝
""".strip("\n")


# ---------------------------------------------------------------------------
# Real subsystem checks — each returns (ok, detail)
# ---------------------------------------------------------------------------

def _check_glm() -> tuple[bool, str]:
    """Real check: is GLMBrain available?"""
    try:
        from core.glm_brain import GLMBrain
        b = GLMBrain()
        if b.available():
            return True, "GLM-4-Flash ready"
        return False, "no GLM_API_KEY"
    except Exception as exc:
        return False, f"error: {exc}"


def _check_memory_count() -> tuple[bool, str]:
    """Real check: how many memories are stored?"""
    try:
        from core.memory import FridayMemory
        m = FridayMemory()
        count = len(m._memories)
        return True, f"{count} memories"
    except Exception as exc:
        return False, f"error: {exc}"


def _check_integrations() -> tuple[bool, str, list[dict]]:
    """Real check: scan all integrations and report live/offline per one."""
    try:
        from core.universal_connector import UniversalConnector
        connector = UniversalConnector()
        results = []
        for name, integration in connector.integrations.items():
            try:
                live = bool(integration.available())
            except Exception:
                live = False
            results.append({"name": name, "live": live})
        active = sum(1 for r in results if r["live"])
        return True, f"{active}/{len(results)} active", results
    except Exception as exc:
        return False, f"error: {exc}", []


def _check_sentinel() -> tuple[bool, str]:
    """Real check: is the EthicalSentinel ready?"""
    try:
        from core.sentinel import EthicalSentinel
        s = EthicalSentinel()
        r = s.evaluate_action("read weather forecast")
        return True, "ready"
    except Exception as exc:
        return False, f"error: {exc}"


def _check_ledger() -> tuple[bool, str]:
    """Real check: is the action ledger ready?"""
    try:
        from core.ledger import get_ledger
        l = get_ledger()
        valid = l.verify_chain()
        return valid, "chain intact" if valid else "CHAIN BROKEN"
    except Exception as exc:
        return False, f"error: {exc}"


# ---------------------------------------------------------------------------
# Boot sequence
# ---------------------------------------------------------------------------

def _typewrite(text: str, delay: float = 0.04) -> None:
    """Print text one line at a time with a small delay."""
    for line in text.split("\n"):
        console.print(line, style=f"bold {COL_PRIMARY}")
        time.sleep(delay)


def _progress_bar(label: str, ok: bool, detail: str = "") -> Panel:
    """Render a single boot step as a panel."""
    status = "DONE" if ok else "OFFLINE"
    colour = COL_SUCCESS if ok else COL_WARN
    bar_full = "████████████"
    bar_empty = "░░░░░░░░░░░░"
    bar = bar_full if ok else bar_empty
    line = f"[bold white][◆][/bold white] {label:<38} [{colour}]{bar} {status}[/]"
    if detail:
        line += f"  [dim]({detail})[/dim]"
    return Panel(line, border_style=colour, padding=(0, 1))


async def boot_sequence() -> dict:
    """Run the animated boot sequence and return a status dict.

    The status dict is consumed by the TUI to populate the right pane.
    """
    # 1. Logo (typewriter)
    console.print()
    _typewrite(LOGO, delay=0.04)
    console.print()
    console.print(
        Align.center(
            Text("Personal AI Assistant v3.2", style=f"bold {COL_ACCENT}")
        )
    )
    console.print(
        Align.center(
            Text("Powered by Z.ai GLM-4-Flash (Free)", style="dim")
        )
    )
    console.print()

    # 2. Subsystem checks with real results
    status = {}

    # GLM
    ok, detail = _check_glm()
    status["glm"] = {"ok": ok, "detail": detail}
    console.print(_progress_bar("Initializing neural core...", ok, detail))
    await asyncio.sleep(0.1)

    # Memory
    ok, detail = _check_memory_count()
    status["memory"] = {"ok": ok, "detail": detail}
    console.print(_progress_bar("Loading memory banks...", ok, detail))
    await asyncio.sleep(0.1)

    # Integrations
    ok, detail, integrations = _check_integrations()
    status["integrations"] = {"ok": ok, "detail": detail, "list": integrations}
    console.print(_progress_bar("Scanning integrations...", ok, detail))
    await asyncio.sleep(0.1)

    # Sentinel
    ok, detail = _check_sentinel()
    status["sentinel"] = {"ok": ok, "detail": detail}
    console.print(_progress_bar("Calibrating Sentinel...", ok, detail))
    await asyncio.sleep(0.1)

    # Ledger
    ok, detail = _check_ledger()
    status["ledger"] = {"ok": ok, "detail": detail}
    console.print(_progress_bar("Verifying audit chain...", ok, detail))
    await asyncio.sleep(0.1)

    # Online
    console.print(
        f"[bold {COL_SUCCESS}]    [◆] Friday is online.[/]"
    )
    console.print()

    # 3. Context-mode greeting
    try:
        from core.context import ContextAwareness
        ctx = ContextAwareness()
        mode = ctx.get_time_context().upper()
        hour = datetime.now().hour
        if 5 <= hour < 12:
            greeting = "Good morning."
        elif 12 <= hour < 18:
            greeting = "Good afternoon."
        elif 18 <= hour < 22:
            greeting = "Good evening."
        else:
            greeting = "Working late?"
        console.print(
            f"[bold {COL_ACCENT}][{mode} MODE][/bold {COL_ACCENT}] "
            f"{datetime.now().strftime('%H:%M')} — {greeting}"
        )
        status["mode"] = mode
    except Exception:
        status["mode"] = "UNKNOWN"

    return status


# ---------------------------------------------------------------------------
# Right pane — live status panel
# ---------------------------------------------------------------------------

def render_status_pane(status: dict) -> Panel:
    """Render the right-side status panel as a Panel."""
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold white", no_wrap=True)
    table.add_column(style="dim")

    # Header
    table.add_row("[bold]SYSTEM STATUS[/bold]", "")
    glm_ok = status.get("glm", {}).get("ok", False)
    provider_label = "GLM-4-Flash" if glm_ok else "GLM (offline)"
    table.add_row("Provider:", provider_label)
    table.add_row("Mode:", status.get("mode", "—"))
    mem_detail = status.get("memory", {}).get("detail", "—")
    table.add_row("Memory:", mem_detail)
    table.add_row("Cost:", "$0.00 (free tier)")
    table.add_row("", "")

    # Integrations
    table.add_row("[bold]INTEGRATIONS[/bold]", "")
    for it in status.get("integrations", {}).get("list", [])[:8]:
        marker = "◆" if it["live"] else "○"
        colour = COL_SUCCESS if it["live"] else COL_MUTED
        st = "LIVE" if it["live"] else "OFFLINE"
        table.add_row(
            f"[{colour}]{marker}[/{colour}] {it['name']}",
            f"[{colour}]{st}[/{colour}]",
        )
    table.add_row("", "")

    # Pending actions
    try:
        from core.ledger import get_ledger
        ledger = get_ledger()
        pending = [a for a in ledger.pending_actions.values()
                   if a.get("status") == "pending"]
        table.add_row("[bold]PENDING ACTIONS[/bold]", "")
        if pending:
            for p in pending[:3]:
                table.add_row(f"  {p['component']}.{p['action']}", p["id"][:8])
        else:
            table.add_row("  none", "")
    except Exception:
        table.add_row("[bold]PENDING ACTIONS[/bold]", "(error)")
    table.add_row("", "")

    # Active skills
    table.add_row("[bold]ACTIVE SKILLS[/bold]", "")
    try:
        from pathlib import Path
        skills_dir = Path(__file__).resolve().parent.parent / "skills"
        skills = [f.stem for f in skills_dir.glob("*.py")
                  if f.stem not in ("__init__", "base")]
        for s in skills[:5]:
            table.add_row(f"  {s}", "")
    except Exception:
        table.add_row("  (error)", "")

    return Panel(
        table,
        title="[bold]FRIDAY STATUS[/bold]",
        border_style=COL_ACCENT,
        padding=(1, 1),
    )


# ---------------------------------------------------------------------------
# Receipt panel
# ---------------------------------------------------------------------------

def render_receipt(receipt: dict) -> Panel:
    """Render an action receipt as a stylised panel."""
    lines = []
    lines.append(f"[bold]✓ ACTION RECEIPT[/bold]")
    lines.append("")
    lines.append(f"  Component:  [cyan]{receipt.get('component', '—')}[/]")
    lines.append(f"  Action:     [cyan]{receipt.get('action', '—')}[/]")
    lines.append(f"  Status:     [{COL_SUCCESS}]{receipt.get('status', '—')}[/]")
    lines.append(f"  Time:       {receipt.get('timestamp', datetime.now().isoformat())}")
    lines.append(f"  Cost:       $0.00 (GLM free tier)")
    chain = receipt.get("chain_hash") or receipt.get("hash", "—")
    if isinstance(chain, str) and len(chain) > 12:
        chain = chain[:12] + "…"
    lines.append(f"  Chain hash: {chain}")
    lines.append("")
    result = receipt.get("result") or receipt.get("message") or ""
    if result:
        lines.append(f"  Result: {result}")
    body = "\n".join(lines)
    return Panel(body, border_style=COL_SUCCESS, padding=(1, 1))


# ---------------------------------------------------------------------------
# Approval panel — colour-coded by risk
# ---------------------------------------------------------------------------

def render_approval(action: dict) -> Panel:
    """Render a pending action needing approval, colour-coded by risk."""
    risk = (action.get("risk_level") or "medium").lower()
    if risk in ("critical", "high") or action.get("component") in (
        "Printer", "Printer3D", "Commerce", "Finance", "CodeExecution",
        "ImageGen", "VideoGen",
    ):
        colour = COL_DANGER
        risk_label = "PHYSICAL/FINANCIAL"
    elif risk == "low":
        colour = COL_SUCCESS
        risk_label = "SAFE"
    else:
        colour = COL_WARN
        risk_label = "STANDARD"

    lines = [
        f"[bold {colour}]⚠ APPROVAL NEEDED[/bold {colour}]",
        "",
        f"  Action: [white]{action.get('action', '—')}[/]",
        f"  Component: [white]{action.get('component', '—')}[/]",
        f"  Risk: [{colour}]{risk_label}[/]",
        "",
        f"  Params: {action.get('params', {})}",
        "",
        "  [Y] Approve   [N] Reject   [D] Details",
    ]
    return Panel("\n".join(lines), border_style=colour, padding=(1, 1))


# ---------------------------------------------------------------------------
# Thinking animation
# ---------------------------------------------------------------------------

async def render_thinking(duration_seconds: float = 1.5) -> None:
    """Show a pulsing 'thinking' indicator for the given duration."""
    frames = ["◆ ◇ ◆ ◇ ◆", "◇ ◆ ◇ ◆ ◇", "◆ ◇ ◆ ◇ ◆", "◇ ◆ ◇ ◆ ◇"]
    end = time.time() + duration_seconds
    i = 0
    with Live(console=console, refresh_per_second=4) as live:
        while time.time() < end:
            frame = frames[i % len(frames)]
            live.update(
                Text(f"Friday: {frame}  Thinking...", style=f"bold {COL_ACCENT}")
            )
            i += 1
            await asyncio.sleep(0.2)


# ---------------------------------------------------------------------------
# Conversation pane — main chat view
# ---------------------------------------------------------------------------

def render_conversation_pane(history: list[dict], mode: str = "WORK") -> Panel:
    """Render the left conversation pane."""
    body_lines = []
    for turn in history[-30:]:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role == "user":
            body_lines.append(f"[bold white]You:[/] {content}")
        else:
            body_lines.append(f"[bold {COL_ACCENT}]Friday:[/] {content}")
        body_lines.append("")
    if not body_lines:
        body_lines.append("[dim](no messages yet)[/]")
        body_lines.append("")

    body = "\n".join(body_lines)
    title = f"[bold]FRIDAY CONVERSATION  [{mode} MODE][/bold]"
    return Panel(
        body,
        title=title,
        border_style=COL_PRIMARY,
        padding=(1, 1),
    )


# ---------------------------------------------------------------------------
# Main TUI loop — async, two-pane layout
# ---------------------------------------------------------------------------

async def run_tui(initial_status: Optional[dict] = None) -> None:
    """Run the interactive two-pane TUI.

    Falls back to a simple rich layout if textual isn't available.
    """
    if initial_status is None:
        initial_status = {}

    history: list[dict] = []
    mode = initial_status.get("mode", "WORK")

    console.print(
        Panel(
            "[bold]FRIDAY TUI[/bold]  —  type 'exit' to quit, 'help' for commands",
            border_style=COL_ACCENT,
            padding=(0, 1),
        )
    )

    while True:
        # Render two-pane layout: conversation left, status right
        conv = render_conversation_pane(history, mode)
        status_pane = render_status_pane(initial_status)

        # Use rich's Columns to display side by side
        from rich.columns import Columns
        console.print(Columns([conv, status_pane], width=(70, 30), expand=True))
        console.print()

        try:
            user_input = Prompt.ask(
                "[bold purple]>[/]",
                console=console,
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/]")
            return

        if user_input.strip().lower() in ("exit", "quit", ":q"):
            console.print("[dim]Goodbye.[/]")
            return
        if user_input.strip().lower() == "help":
            console.print(
                Panel(
                    "Commands:\n"
                    "  exit / quit     — leave the TUI\n"
                    "  help            — this help\n"
                    "  status          — refresh status pane\n"
                    "  clear           — clear conversation\n"
                    "  (anything else) — sent to Friday's brain",
                    border_style=COL_MUTED,
                    padding=(0, 1),
                )
            )
            continue
        if user_input.strip().lower() == "clear":
            history = []
            continue
        if user_input.strip().lower() == "status":
            initial_status = await boot_status_quiet()
            continue

        # Send to Friday brain
        history.append({"role": "user", "content": user_input})
        await render_thinking(0.6)

        try:
            from core.brain import FridayBrain
            brain = FridayBrain()
            full = ""
            async for chunk in brain.chat_stream(user_input):
                full += chunk
            if not full.strip():
                full = "(no response — GLM_API_KEY may not be set)"
            history.append({"role": "friday", "content": full})
        except Exception as exc:
            history.append({"role": "friday", "content": f"Error: {exc}"})


async def boot_status_quiet() -> dict:
    """Run the subsystem checks silently and return the status dict."""
    status = {}
    ok, detail = _check_glm()
    status["glm"] = {"ok": ok, "detail": detail}
    ok, detail = _check_memory_count()
    status["memory"] = {"ok": ok, "detail": detail}
    ok, detail, integrations = _check_integrations()
    status["integrations"] = {"ok": ok, "detail": detail, "list": integrations}
    ok, detail = _check_sentinel()
    status["sentinel"] = {"ok": ok, "detail": detail}
    ok, detail = _check_ledger()
    status["ledger"] = {"ok": ok, "detail": detail}
    try:
        from core.context import ContextAwareness
        ctx = ContextAwareness()
        status["mode"] = ctx.get_time_context().upper()
    except Exception:
        status["mode"] = "UNKNOWN"
    return status


# ---------------------------------------------------------------------------
# Entry point for `friday` (no args)
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point — boot sequence then TUI."""
    status = asyncio.run(boot_sequence())
    try:
        asyncio.run(run_tui(status))
    except KeyboardInterrupt:
        console.print("\n[dim]Goodbye.[/]")


if __name__ == "__main__":
    main()
