"""FRIDAY Benchmark Runner — tests tasks against the real brain and integrations.

Supports GLM-powered tasks (web search, image gen, video gen, deep research,
code tutor) as well as traditional integration tasks.  Produces a markdown
results report.
"""

import asyncio
import json
import os
import sys
import datetime

sys.path.append(os.getcwd())

from core.brain import FridayBrain

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


if __name__ == "__main__":
    asyncio.run(run_benchmark())
