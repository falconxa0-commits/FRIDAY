"""FRIDAY CLI commands — every command produces real output.

Usage:
    friday                       # full TUI
    friday chat "msg"            # one-shot chat (streams)
    friday research "topic"      # research agent
    friday generate "prompt"     # CogView-3 image
    friday video "prompt"        # CogVideoX video
    friday morning               # morning briefing
    friday council "question"    # multi-provider comparison
    friday status                # integration status
    friday memory list           # stored memories
    friday memory search "q"     # semantic search
    friday memory export         # export to JSON
    friday bench                 # run benchmark suite
    friday trust                 # run hellfire_audit
    friday ledger                # audit log + chain status
    friday --watch               # ambient mode
    friday --voice               # voice mode
    friday --council             # TUI w/ council default
    friday plugin install <name> # marketplace install
    friday plugin list           # marketplace listing
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown

from cli.terminal import (
    COL_ACCENT, COL_PRIMARY, COL_SUCCESS, COL_WARN, COL_DANGER, COL_MUTED,
    boot_sequence, render_receipt, render_approval,
)

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _err(msg: str) -> int:
    console.print(Panel(msg, border_style=COL_DANGER, title="Error"))
    return 1


def _info(msg: str) -> None:
    console.print(Panel(msg, border_style=COL_ACCENT, title="Friday"))


async def _get_brain():
    from core.brain import FridayBrain
    return FridayBrain()


# ---------------------------------------------------------------------------
# friday chat "message"
# ---------------------------------------------------------------------------

async def cmd_chat(args: list[str]) -> int:
    if not args:
        return _err("Usage: friday chat \"your message\"")
    message = " ".join(args)
    console.print(f"[bold white]You:[/] {message}")
    console.print()
    try:
        brain = await _get_brain()
        full = ""
        async for chunk in brain.chat_stream(message):
            full += chunk
            console.print(chunk, end="", style=COL_ACCENT, highlight=False)
        console.print()
        if not full.strip():
            console.print(
                f"[{COL_WARN}]No response — GLM_API_KEY may not be set.[/]"
            )
        return 0
    except Exception as exc:
        return _err(f"Chat failed: {exc}")


# ---------------------------------------------------------------------------
# friday research "topic"
# ---------------------------------------------------------------------------

async def cmd_research(args: list[str]) -> int:
    if not args:
        return _err("Usage: friday research \"topic\"")
    topic = " ".join(args)
    console.print(f"[bold]Researching:[/] {topic}")
    console.print()
    try:
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent()
        result = await agent.execute(topic)
        console.print(
            Panel(
                result.get("findings", "(no findings)"),
                title=f"Findings — {topic}",
                border_style=COL_ACCENT,
            )
        )
        sources = result.get("sources", [])
        if sources:
            tbl = Table(title="Sources", border_style="#6B7280")
            tbl.add_column("Title", style="cyan")
            tbl.add_column("URL", style="blue")
            for s in sources:
                tbl.add_row(
                    s.get("title", "")[:60],
                    s.get("url", ""),
                )
            console.print(tbl)
        return 0
    except Exception as exc:
        return _err(f"Research failed: {exc}")


# ---------------------------------------------------------------------------
# friday generate "prompt"  (CogView-3)
# ---------------------------------------------------------------------------

async def cmd_generate(args: list[str]) -> int:
    if not args:
        return _err("Usage: friday generate \"image prompt\"")
    prompt = " ".join(args)
    console.print(f"[bold]Generating image (CogView-3):[/] {prompt}")
    try:
        from integrations.image_gen import ImageGen
        ig = ImageGen()
        if not ig.available():
            return _err(
                "ImageGen not available. Set GLM_API_KEY to enable CogView-3."
            )
        result = await ig.execute("generate_image", {"prompt": prompt})
        if result.get("status") == "success":
            url = (result.get("receipt", {}).get("data", {}) or {}).get("image_url", "")
            console.print(
                Panel(
                    f"Image URL: {url}\n\n"
                    "(ASCII preview omitted — install 'ascii-image-converter' "
                    "or use Pillow to render in terminal.)",
                    title="✓ Image Generated",
                    border_style=COL_SUCCESS,
                )
            )
            return 0
        return _err(f"Generation failed: {result.get('message')}")
    except Exception as exc:
        return _err(f"Generate failed: {exc}")


# ---------------------------------------------------------------------------
# friday video "prompt"  (CogVideoX)
# ---------------------------------------------------------------------------

async def cmd_video(args: list[str]) -> int:
    if not args:
        return _err("Usage: friday video \"video prompt\"")
    prompt = " ".join(args)
    console.print(f"[bold]Generating video (CogVideoX):[/] {prompt}")
    console.print(f"[dim](1–3 minutes — polling for completion…)[/]")
    try:
        from integrations.video_gen import VideoGen
        vg = VideoGen()
        if not vg.available():
            return _err(
                "VideoGen not available. Set GLM_API_KEY to enable CogVideoX."
            )
        result = await vg.execute("generate_video", {"prompt": prompt})
        if result.get("status") == "success":
            data = (result.get("receipt", {}).get("data", {}) or {})
            url = data.get("video_url") or data.get("task_id", "")
            console.print(
                Panel(
                    f"Video URL/Task: {url}",
                    title="✓ Video Generated",
                    border_style=COL_SUCCESS,
                )
            )
            return 0
        return _err(f"Video failed: {result.get('message')}")
    except Exception as exc:
        return _err(f"Video failed: {exc}")


# ---------------------------------------------------------------------------
# friday morning
# ---------------------------------------------------------------------------

async def cmd_morning(args: list[str]) -> int:
    console.print("[bold]Morning Briefing[/]")
    try:
        from skills.morning_briefing import MorningBriefing
        from core.brain import FridayBrain
        brain = await _get_brain()
        skill = MorningBriefing()
        result = await skill.run(brain, {})
        console.print(
            Panel(
                result.get("message", "(no briefing)"),
                title="☀ Morning Briefing",
                border_style=COL_ACCENT,
            )
        )
        return 0
    except Exception as exc:
        return _err(f"Morning briefing failed: {exc}")


# ---------------------------------------------------------------------------
# friday council "question"
# ---------------------------------------------------------------------------

async def cmd_council(args: list[str]) -> int:
    if not args:
        return _err("Usage: friday council \"question\"")
    question = " ".join(args)
    console.print(f"[bold]Council mode — asking all configured providers:[/]")
    console.print(f"  {question}")
    console.print()
    try:
        from core.council_mode import run_council
        result = await run_council(question)
        # Build comparison table
        tbl = Table(title="Council Responses", border_style=COL_PRIMARY)
        tbl.add_column("Provider", style="cyan")
        tbl.add_column("Response (first 200 chars)", style="white")
        for prov, text in result.get("responses", {}).items():
            tbl.add_row(prov, (text or "")[:200])
        console.print(tbl)

        comp = result.get("comparison", {})
        if comp.get("agreements"):
            console.print(
                Panel(
                    "\n".join(f"- {a}" for a in comp["agreements"][:5]),
                    title="Agreements",
                    border_style=COL_SUCCESS,
                )
            )
        if comp.get("unique_points"):
            for prov, pts in comp["unique_points"].items():
                if pts:
                    console.print(
                        Panel(
                            "\n".join(f"- {p}" for p in pts[:3]),
                            title=f"Unique to {prov}",
                            border_style=COL_ACCENT,
                        )
                    )
        return 0
    except Exception as exc:
        return _err(f"Council failed: {exc}")


# ---------------------------------------------------------------------------
# friday status
# ---------------------------------------------------------------------------

async def cmd_status(args: list[str]) -> int:
    from cli.terminal import _check_glm, _check_integrations, _check_ledger, _check_memory_count, _check_sentinel
    tbl = Table(title="Friday Status", border_style=COL_PRIMARY)
    tbl.add_column("Subsystem", style="cyan")
    tbl.add_column("Status", style="white")
    tbl.add_column("Detail", style="dim")

    for label, fn in [
        ("GLM Brain", _check_glm),
        ("Memory", _check_memory_count),
        ("Sentinel", _check_sentinel),
        ("Ledger", _check_ledger),
    ]:
        ok, detail = fn()
        status = "✓ LIVE" if ok else "✗ OFFLINE"
        colour = COL_SUCCESS if ok else COL_DANGER
        tbl.add_row(label, f"[{colour}]{status}[/]", detail)

    ok, detail, integrations = _check_integrations()
    tbl.add_row(
        "Integrations",
        f"[{COL_SUCCESS if ok else COL_DANGER}]{'✓' if ok else '✗'}[/]",
        detail,
    )
    console.print(tbl)

    if integrations:
        itbl = Table(title="Per-Integration Status", border_style=COL_ACCENT)
        itbl.add_column("Name", style="cyan")
        itbl.add_column("Status", style="white")
        for it in integrations:
            colour = COL_SUCCESS if it["live"] else COL_MUTED
            st = "LIVE" if it["live"] else "OFFLINE"
            itbl.add_row(it["name"], f"[{colour}]{st}[/]")
        console.print(itbl)
    return 0


# ---------------------------------------------------------------------------
# friday memory [list|search|export]
# ---------------------------------------------------------------------------

async def cmd_memory(args: list[str]) -> int:
    if not args:
        return _err("Usage: friday memory list|search|export")
    sub = args[0]
    rest = args[1:]

    from core.memory import FridayMemory
    mem = FridayMemory()

    if sub == "list":
        if not mem._memories:
            console.print("[dim]No memories stored.[/]")
            return 0
        tbl = Table(title="Memories", border_style=COL_PRIMARY)
        tbl.add_column("#", style="dim")
        tbl.add_column("Role", style="cyan")
        tbl.add_column("Content", style="white")
        tbl.add_column("Timestamp", style="dim")
        for i, m in enumerate(mem._memories):
            tbl.add_row(
                str(i),
                m.get("role", "?"),
                (m.get("content", "") or "")[:80],
                (m.get("timestamp", "") or "")[:19],
            )
        console.print(tbl)
        return 0

    if sub == "search":
        if not rest:
            return _err("Usage: friday memory search \"query\"")
        query = " ".join(rest)
        results = mem.retrieve_relevant_memories(query)
        if not results:
            console.print(f"[dim]No memories matched {query!r}.[/]")
            return 0
        tbl = Table(title=f"Search: {query}", border_style=COL_ACCENT)
        tbl.add_column("Score", style="dim")
        tbl.add_column("Content", style="white")
        for r in results:
            content = r.get("content", "") if isinstance(r, dict) else str(r)
            tbl.add_row("—", content[:100])
        console.print(tbl)
        return 0

    if sub == "export":
        out = {
            "exported_at": datetime.now().isoformat(),
            "memory_count": len(mem._memories),
            "memories": list(mem._memories),
            "session_facts": dict(getattr(mem, "_session_facts", {})),
        }
        fname = f"friday_memories_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(fname, "w") as f:
            json.dump(out, f, indent=2, default=str)
        console.print(
            Panel(
                f"Exported {out['memory_count']} memories to {fname}",
                border_style=COL_SUCCESS,
            )
        )
        return 0

    return _err(f"Unknown memory subcommand: {sub}")


# ---------------------------------------------------------------------------
# friday bench
# ---------------------------------------------------------------------------

async def cmd_bench(args: list[str]) -> int:
    console.print("[bold]Running benchmark suite…[/]")
    try:
        import subprocess
        proc = subprocess.run(
            [sys.executable, "benchmarks/run_benchmark.py"],
            capture_output=False,
        )
        return proc.returncode
    except FileNotFoundError:
        return _err("benchmarks/run_benchmark.py not found")
    except Exception as exc:
        return _err(f"Benchmark failed: {exc}")


# ---------------------------------------------------------------------------
# friday trust  — run hellfire audit
# ---------------------------------------------------------------------------

async def cmd_trust(args: list[str]) -> int:
    console.print("[bold]Running hellfire_audit.py…[/]")
    import subprocess
    env = {**os.environ, "PYTHONPATH": ".", "FRIDAY_API_TOKEN": "friday-cli"}
    proc = subprocess.run(
        [sys.executable, "scripts/hellfire_audit.py"],
        env=env,
    )
    return proc.returncode


# ---------------------------------------------------------------------------
# friday ledger
# ---------------------------------------------------------------------------

async def cmd_ledger(args: list[str]) -> int:
    from core.ledger import get_ledger
    ledger = get_ledger()
    entries = ledger.get_audit_log()
    valid = ledger.verify_chain()

    tbl = Table(
        title=f"Audit Log ({len(entries)} entries, chain {'VALID' if valid else 'BROKEN'})",
        border_style=COL_SUCCESS if valid else COL_DANGER,
    )
    tbl.add_column("#", style="dim")
    tbl.add_column("Component", style="cyan")
    tbl.add_column("Action", style="white")
    tbl.add_column("Status", style="white")
    tbl.add_column("By", style="dim")
    tbl.add_column("Hash (first 12)", style="dim")

    for i, e in enumerate(entries[-15:]):
        st = e.get("status", "—")
        st_colour = COL_SUCCESS if st == "approved" else COL_WARN
        tbl.add_row(
            str(i),
            e.get("component", "—"),
            e.get("action", "—"),
            f"[{st_colour}]{st}[/]",
            e.get("approved_by", "—"),
            (e.get("hash", "") or "")[:12],
        )
    console.print(tbl)
    return 0


# ---------------------------------------------------------------------------
# friday plugin [install|list]
# ---------------------------------------------------------------------------

async def cmd_plugin(args: list[str]) -> int:
    if not args:
        return _err("Usage: friday plugin install <name> | friday plugin list")
    sub = args[0]
    if sub == "list":
        return _plugin_list()
    if sub == "install":
        if len(args) < 2:
            return _err("Usage: friday plugin install <name>")
        return _plugin_install(args[1])
    return _err(f"Unknown plugin subcommand: {sub}")


def _plugin_list() -> int:
    from pathlib import Path
    mp = Path(__file__).resolve().parent.parent / "marketplace" / "plugins"
    if not mp.is_dir():
        console.print("[dim]No marketplace directory.[/]")
        return 0
    plugins = sorted([d.name for d in mp.iterdir() if d.is_dir()])
    if not plugins:
        console.print("[dim]Marketplace is empty.[/]")
        return 0
    tbl = Table(title="Marketplace Plugins", border_style=COL_PRIMARY)
    tbl.add_column("Name", style="cyan")
    tbl.add_column("Installed?", style="white")
    integrations_dir = Path(__file__).resolve().parent.parent / "integrations"
    for name in plugins:
        installed = (integrations_dir / f"{name}.py").exists()
        marker = "✓ installed" if installed else "— available"
        colour = COL_SUCCESS if installed else COL_MUTED
        tbl.add_row(name, f"[{colour}]{marker}[/]")
    console.print(tbl)
    return 0


def _plugin_install(name: str) -> int:
    from pathlib import Path
    import shutil
    src_dir = Path(__file__).resolve().parent.parent / "marketplace" / "plugins" / name
    if not src_dir.is_dir():
        return _err(f"Plugin '{name}' not found in marketplace/")
    # Find the plugin .py file inside the directory
    plugin_files = list(src_dir.glob("*.py"))
    if not plugin_files:
        return _err(f"Plugin '{name}' has no .py file")
    target = Path(__file__).resolve().parent.parent / "integrations" / plugin_files[0].name
    if target.exists():
        console.print(f"[dim]{name} already installed.[/]")
        return 0
    shutil.copy(plugin_files[0], target)
    console.print(
        Panel(
            f"Installed {name} → {target.name}\n"
            "Friday will auto-discover it on next start.",
            border_style=COL_SUCCESS,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# friday --watch | --voice | --council
# ---------------------------------------------------------------------------

async def cmd_watch(args: list[str]) -> int:
    console.print("[bold]Ambient watch mode — Ctrl+C to stop.[/]")
    try:
        from core.ambient import AmbientEngine
        engine = AmbientEngine()
        await engine.run_loop_cli()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        return _err(f"Ambient mode failed: {exc}")


async def cmd_voice(args: list[str]) -> int:
    console.print("[bold]Voice mode — wake word active. Ctrl+C to stop.[/]")
    try:
        from voice.conversational import ConversationalVoice
        v = ConversationalVoice()
        await v.listen_continuously_cli()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        return _err(f"Voice mode failed: {exc}")


async def cmd_council_tui(args: list[str]) -> int:
    """Start TUI with BRAIN_PROVIDER=council."""
    os.environ["BRAIN_PROVIDER"] = "council"
    status = await boot_sequence()
    from cli.terminal import run_tui
    await run_tui(status)
    return 0


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

# Subcommand map: name → (handler, takes_args)
# Flag-style entries start with --
COMMANDS = {
    "chat":       cmd_chat,
    "research":   cmd_research,
    "generate":   cmd_generate,
    "video":      cmd_video,
    "morning":    cmd_morning,
    "council":    cmd_council,
    "status":     cmd_status,
    "memory":     cmd_memory,
    "bench":      cmd_bench,
    "trust":      cmd_trust,
    "ledger":     cmd_ledger,
    "plugin":     cmd_plugin,
    "--watch":    cmd_watch,
    "--voice":    cmd_voice,
    "--council":  cmd_council_tui,
}


def dispatch(argv: list[str]) -> int:
    if not argv:
        # Default: TUI
        return asyncio.run(_run_tui_default())

    cmd = argv[0]
    rest = argv[1:]

    if cmd in ("-h", "--help", "help"):
        _print_help()
        return 0

    handler = COMMANDS.get(cmd)
    if handler is None:
        return _err(f"Unknown command: {cmd}\nRun 'friday help' for usage.")
    return asyncio.run(handler(rest))


async def _run_tui_default() -> int:
    status = await boot_sequence()
    from cli.terminal import run_tui
    try:
        await run_tui(status)
    except KeyboardInterrupt:
        pass
    return 0


def _print_help() -> None:
    console.print(
        Panel(
            "[bold]FRIDAY CLI[/bold]\n\n"
            "Usage:\n"
            "  friday                       — full TUI\n"
            "  friday chat \"msg\"            — one-shot chat\n"
            "  friday research \"topic\"      — research agent\n"
            "  friday generate \"prompt\"     — CogView-3 image\n"
            "  friday video \"prompt\"        — CogVideoX video\n"
            "  friday morning               — morning briefing\n"
            "  friday council \"question\"    — multi-provider comparison\n"
            "  friday status                — integration status\n"
            "  friday memory list|search|export\n"
            "  friday bench                 — run benchmark suite\n"
            "  friday trust                 — run hellfire_audit\n"
            "  friday ledger                — audit log + chain status\n"
            "  friday plugin install <name> — marketplace install\n"
            "  friday plugin list           — marketplace listing\n"
            "  friday --watch               — ambient mode\n"
            "  friday --voice               — voice mode\n"
            "  friday --council             — TUI w/ council default\n",
            border_style=COL_PRIMARY,
        )
    )
