"""FRIDAY Benchmark Runner — tests tasks against the real brain and integrations.

Supports GLM-powered tasks (web search, image gen, video gen, deep research,
code tutor) as well as traditional integration tasks.  Produces a markdown
results report.

In addition to the original GLM end-to-end task sweep, this runner now
dispatches the five WAVE1-PERF micro-benchmarks:

    * ``startup``        — cold-start breakdown by component
    * ``chat_latency``   — TTFT / full-response with mocked GLM
    * ``memory``         — RSS growth at each milestone
    * ``vector_search``  — InMemoryVectorStore scaling analysis
    * ``profile_brain``  — cProfile of ``FridayBrain.chat_stream``

Run individual benchmarks::

    python benchmarks/run_benchmark.py --only=startup
    python benchmarks/run_benchmark.py --only=chat_latency
    python benchmarks/run_benchmark.py --only=memory
    python benchmarks/run_benchmark.py --only=vector_search
    python benchmarks/run_benchmark.py --only=profile_brain
    python benchmarks/run_benchmark.py --only=tasks      # original GLM task sweep

Run everything::

    python benchmarks/run_benchmark.py --all
"""

import argparse
import asyncio
import importlib
import json
import os
import sys
import datetime

sys.path.append(os.getcwd())

from core.brain import FridayBrain

# ──────────────────────────────────────────────────────────────────────────────
# WAVE1-PERF micro-benchmark registry
# ──────────────────────────────────────────────────────────────────────────────
#
# Each entry is (name, module, description). We import lazily so a single
# broken benchmark doesn't prevent the others from running.

PERF_BENCHMARKS = {
    "startup":       ("benchmarks.benchmark_startup",       "Cold-start breakdown by component"),
    "chat_latency":  ("benchmarks.benchmark_chat_latency",  "TTFT / full-response with mocked GLM"),
    "memory":        ("benchmarks.benchmark_memory",        "RSS growth at each milestone"),
    "vector_search": ("benchmarks.benchmark_vector_search", "InMemoryVectorStore scaling analysis"),
    "profile_brain": ("benchmarks.profile_brain",           "cProfile of FridayBrain.chat_stream"),
}


def run_perf_benchmark(name: str, **kwargs) -> dict:
    """Dispatch one WAVE1-PERF benchmark by name.

    Each benchmark module exposes a top-level ``run(**kwargs)`` function
    (or ``run_profile`` for profile_brain) that returns a JSON-serialisable
    dict and writes its own ``results_*.json`` file.

    Only kwargs that the runner actually accepts are forwarded — this
    lets ``--iterations=N`` work for benchmarks that take an
    ``iterations`` parameter (startup, chat_latency) without breaking
    the ones that don't (memory, vector_search, profile_brain).
    """
    import inspect

    if name not in PERF_BENCHMARKS:
        raise ValueError(
            f"Unknown benchmark '{name}'. "
            f"Available: {sorted(PERF_BENCHMARKS.keys())}"
        )
    module_name, description = PERF_BENCHMARKS[name]
    print(f"\n{'=' * 70}")
    print(f"  PERF BENCHMARK: {name} — {description}")
    print(f"{'=' * 70}\n")
    module = importlib.import_module(module_name)
    # Each module exposes either ``run`` or ``run_profile``.
    runner = getattr(module, "run", None) or getattr(module, "run_profile")

    # Filter kwargs to those the runner accepts.
    try:
        sig = inspect.signature(runner)
        accepted = set(sig.parameters.keys())
        # If the runner has **kwargs, accept everything.
        if any(p.kind == inspect.Parameter.VAR_KEYWORD
               for p in sig.parameters.values()):
            filtered = kwargs
        else:
            filtered = {k: v for k, v in kwargs.items() if k in accepted}
            skipped = {k: v for k, v in kwargs.items() if k not in accepted}
            if skipped:
                print(f"[runner] Note: {name} does not accept "
                      f"{sorted(skipped.keys())}; ignoring.")
    except (TypeError, ValueError):
        filtered = kwargs

    return runner(**filtered)


# ──────────────────────────────────────────────────────────────────────────────
# GLM-specific task handlers (use GLMBrain directly for accuracy)
# ──────────────────────────────────────────────────────────────────────────────

async def _test_glm_web_search(task: dict) -> dict:
    """Test GLM web search capability."""
    try:
        from core.glm_brain import GLMBrain
        glm = GLMBrain()
        if not glm.available():
            return {"passed": False, "reason": "GLM not available"}
        results = await glm.web_search(task["task"])
        return {"passed": len(results) > 0, "reason": f"Got {len(results)} results"}
    except Exception as e:
        return {"passed": False, "reason": str(e)}


async def _test_image_gen(task: dict) -> dict:
    """Test image generation via ImageGen integration."""
    try:
        from integrations.image_gen import ImageGen
        ig = ImageGen()
        if not ig.available():
            return {"passed": False, "reason": "ImageGen not available (no GLM key)"}
        result = await ig.execute("generate_image", {"prompt": task["task"]})
        status = result.get("status", "")
        has_url = bool(result.get("receipt", {}).get("data", {}).get("image_url"))
        return {"passed": status == "success" or has_url, "reason": result.get("message", "")[:100]}
    except Exception as e:
        return {"passed": False, "reason": str(e)}


async def _test_video_gen(task: dict) -> dict:
    """Test video generation via VideoGen integration."""
    try:
        from integrations.video_gen import VideoGen
        vg = VideoGen()
        if not vg.available():
            return {"passed": False, "reason": "VideoGen not available (no GLM key)"}
        result = await vg.execute("generate_video", {"prompt": task["task"]})
        status = result.get("status", "")
        return {"passed": status == "success", "reason": result.get("message", "")[:100]}
    except Exception as e:
        return {"passed": False, "reason": str(e)}


async def _test_deep_research(task: dict) -> dict:
    """Test deep research skill."""
    try:
        from skills.deep_research import DeepResearchSkill
        from core.glm_brain import GLMBrain
        glm = GLMBrain()
        if not glm.available():
            return {"passed": False, "reason": "GLM not available for deep research"}
        skill = DeepResearchSkill()
        result = await skill.run(glm, {"topic": task["task"]})
        status = result.get("status", "")
        has_findings = bool(result.get("receipt", {}).get("data", {}).get("findings"))
        return {"passed": status == "success" or has_findings, "reason": result.get("message", "")[:100]}
    except Exception as e:
        return {"passed": False, "reason": str(e)}


async def _test_code_tutor(task: dict) -> dict:
    """Test code tutor skill."""
    try:
        from skills.code_tutor import CodeTutorSkill
        from core.glm_brain import GLMBrain
        glm = GLMBrain()
        if not glm.available():
            return {"passed": False, "reason": "GLM not available for code tutor"}
        skill = CodeTutorSkill()
        result = await skill.run(glm, {"question": task["task"]})
        status = result.get("status", "")
        return {"passed": status == "success", "reason": result.get("message", "")[:100]}
    except Exception as e:
        return {"passed": False, "reason": str(e)}


async def _test_morning_briefing(task: dict) -> dict:
    """Test morning briefing via ProactiveEngine."""
    try:
        from core.proactive import ProactiveEngine
        from core.brain import FridayBrain
        brain = FridayBrain()
        engine = ProactiveEngine(brain)
        briefing = await engine.daily_briefing()
        return {"passed": len(briefing) > 20, "reason": briefing[:100]}
    except Exception as e:
        return {"passed": False, "reason": str(e)}


async def _test_inbox_triage(task: dict) -> dict:
    """Test inbox triage via Gmail integration."""
    try:
        from integrations.gmail_integration import GmailIntegration
        gmail = GmailIntegration()
        if not gmail.available():
            return {"passed": False, "reason": "Gmail not available"}
        result = await gmail.execute("get_unread_emails")
        return {"passed": result.get("status") == "success", "reason": result.get("message", "")[:100]}
    except Exception as e:
        return {"passed": False, "reason": str(e)}


async def _test_code_execution(task: dict) -> dict:
    """Test code execution via GLM code interpreter."""
    try:
        from core.glm_brain import GLMBrain
        glm = GLMBrain()
        if not glm.available():
            return {"passed": False, "reason": "GLM not available"}
        result = await glm.code_interpreter("print(sum(range(1, 101)))")
        return {"passed": result.get("status") == "success", "reason": str(result.get("output", ""))[:100]}
    except Exception as e:
        return {"passed": False, "reason": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Task routing
# ──────────────────────────────────────────────────────────────────────────────

_GLMSPECIFIC_TOOLS = {
    "GLMWebSearch": _test_glm_web_search,
    "ImageGen": _test_image_gen,
    "VideoGen": _test_video_gen,
    "DeepResearch": _test_deep_research,
    "CodeTutor": _test_code_tutor,
    "MorningBriefing": _test_morning_briefing,
    "InboxTriage": _test_inbox_triage,
    "CodeExecution": _test_code_execution,
}


async def run_benchmark():
    tasks_path = "benchmarks/tasks.json"
    if not os.path.exists(tasks_path):
        print("Tasks file not found.")
        return

    with open(tasks_path, "r") as f:
        tasks = json.load(f)

    brain = FridayBrain()
    results = []
    print(f"Friday: Commencing Benchmark Run ({len(tasks)} tasks)...")

    for t in tasks:
        print(f"Running Task {t['id']}: {t['task']}")
        expected_tool = t["expected_tool"]
        transcript = ""
        passed = False
        reason = ""

        # ── GLM-specific tasks have dedicated handlers ──────────────
        if expected_tool in _GLMSPECIFIC_TOOLS:
            handler = _GLMSPECIFIC_TOOLS[expected_tool]
            handler_result = await handler(t)
            passed = handler_result["passed"]
            reason = handler_result.get("reason", "")
            transcript = reason
        else:
            # ── Standard brain-stream test ──────────────────────────
            try:
                async for chunk in brain.chat_stream(t["task"]):
                    transcript += chunk

                if f"Accessing {expected_tool}..." in transcript:
                    passed = True
                elif expected_tool.lower() in transcript.lower():
                    passed = True
            except Exception as e:
                transcript = f"FAILED DURING EXECUTION: {e}"

        results.append({
            "id": t["id"],
            "task": t["task"],
            "expected_tool": expected_tool,
            "passed": passed,
            "transcript_summary": (transcript or reason)[:100].replace("\n", " ") + "...",
        })

    # Save results
    pass_count = sum(1 for r in results if r["passed"])
    pass_rate = (pass_count / len(tasks)) * 100

    report = f"# FRIDAY BENCHMARK RESULTS — {datetime.date.today()}\n\n"
    report += f"**Overall Pass Rate:** {pass_rate:.0f}% ({pass_count}/{len(tasks)})\n\n"
    report += "| ID | Task | Expected Tool | Status | Summary |\n"
    report += "|----|------|---------------|--------|---------|\n"
    for r in results:
        status = "✅ PASS" if r["passed"] else "❌ FAIL"
        report += (
            f"| {r['id']} | {r['task']} | {r['expected_tool']} | "
            f"{status} | {r['transcript_summary']} |\n"
        )

    with open("benchmarks/results.md", "w") as f:
        f.write(report)

    print(f"Benchmark complete. Pass Rate: {pass_rate:.0f}%. Results in benchmarks/results.md")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="FRIDAY benchmark runner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--only",
        choices=sorted(PERF_BENCHMARKS.keys()) + ["tasks"],
        help="Run only the named benchmark.",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Run every benchmark (perf micro-benchmarks + GLM task sweep).",
    )
    p.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Override iteration count for benchmarks that accept it.",
    )
    return p


def main():
    args = _build_parser().parse_args()

    if args.all:
        # Run every perf benchmark, then the GLM task sweep.
        for name in PERF_BENCHMARKS:
            try:
                if args.iterations is not None:
                    run_perf_benchmark(name, iterations=args.iterations)
                else:
                    run_perf_benchmark(name)
            except Exception as exc:
                print(f"[runner] Benchmark '{name}' failed: {exc}")
        asyncio.run(run_benchmark())
        return

    if args.only:
        if args.only == "tasks":
            asyncio.run(run_benchmark())
            return
        if args.iterations is not None:
            run_perf_benchmark(args.only, iterations=args.iterations)
        else:
            run_perf_benchmark(args.only)
        return

    # Default: print help.
    _build_parser().print_help()


if __name__ == "__main__":
    main()
