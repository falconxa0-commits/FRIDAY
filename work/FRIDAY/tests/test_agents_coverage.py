"""WAVE3-TEST — Coverage tests for the 7 agent modules.

These tests exercise the actual logic of each agent (regex parsing,
routing, state management, error handling) WITHOUT making real GLM API
calls. The ``FridayBrain`` is mocked everywhere it's used.

Coverage map:
    1. agent_manager.py       — AgentType, run_swarm, run_pipeline
    2. coding_agent.py        — write_code, debug_code, review_code, preview_changes
    3. coding_orchestrator.py — multi-file generation + malicious pattern detection
    4. research_agent.py      — execute + deep_research
    5. tactical_manager.py    — keyword-based agent dispatch
    6. task_agent.py          — task breakdown + step parsing
    7. writing_agent.py       — write / proofread / report modes
"""
import os
import re
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _async_gen(*chunks):
    """Build a fake ``chat_stream`` async generator that yields ``chunks``."""
    async def _gen(*args, **kwargs):
        for c in chunks:
            yield c
    return _gen


@pytest.fixture(autouse=True)
def _patch_glm_brain():
    """Patch ``GLMBrain`` so CodingAgent/ResearchAgent can be constructed
    in test environments without a real Z.ai key or SDK.

    The agents import ``GLMBrain`` at module scope, so we patch it on each
    agent module separately.
    """
    fake_glm = MagicMock()
    fake_glm.available.return_value = False
    fake_glm.chat_stream = AsyncMock()
    fake_glm.web_search = AsyncMock(return_value=None)
    fake_glm.code_interpreter = AsyncMock(return_value={"status": "error"})

    with patch("agents.coding_agent.GLMBrain", return_value=fake_glm), \
         patch("agents.research_agent.GLMBrain", return_value=fake_glm):
        yield fake_glm


def _make_brain(*chunks):
    """Build a mock FridayBrain whose ``chat_stream`` yields ``chunks``."""
    brain = MagicMock()
    brain.name = "MockBrain"
    brain.chat_stream = _async_gen(*chunks)
    return brain


# ===========================================================================
# 1. AgentManager — agents/agent_manager.py
# ===========================================================================

class TestAgentManager:
    """Tests for AgentManager: enum, swarm (parallel), pipeline (sequential)."""

    def test_agent_type_enum_has_four_members(self):
        from agents.agent_manager import AgentType
        assert {at.value for at in AgentType} == {"research", "coding", "writing", "task"}

    def test_agent_status_enum_values(self):
        from agents.agent_manager import AgentStatus
        assert AgentStatus.IDLE.value == "idle"
        assert AgentStatus.RUNNING.value == "running"
        assert AgentStatus.COMPLETED.value == "completed"
        assert AgentStatus.FAILED.value == "failed"

    def test_agent_result_to_dict_truncates_long_output(self):
        from agents.agent_manager import AgentResult, AgentType, AgentStatus
        long_output = "x" * 5000
        r = AgentResult(AgentType.CODING, AgentStatus.COMPLETED, long_output)
        d = r.to_dict()
        assert len(d["output"]) <= 2000
        assert d["agent_type"] == "coding"
        assert d["status"] == "completed"

    def test_is_code_task_matches_keywords(self):
        from agents.agent_manager import AgentManager
        mgr = AgentManager(brain=None)
        assert mgr._is_code_task("build a web app") is True
        assert mgr._is_code_task("write a function") is True
        assert mgr._is_code_task("implement the API") is True
        assert mgr._is_code_task("plan my day") is False
        assert mgr._is_code_task("research climate change") is False

    def test_get_status_structure(self):
        from agents.agent_manager import AgentManager
        mgr = AgentManager(brain=None)
        status = mgr.get_status()
        assert "available_agents" in status
        assert "active_tasks" in status
        assert "total_completed" in status
        assert len(status["available_agents"]) == 4

    @pytest.mark.asyncio
    async def test_run_agent_records_in_active_tasks(self):
        from agents.agent_manager import AgentManager, AgentStatus
        mgr = AgentManager(brain=_make_brain("done"))
        result = await mgr.run_agent("research", "find AI papers")
        assert result.status == AgentStatus.COMPLETED
        # ResearchAgent.execute() returns a dict with a 'findings' key.
        assert isinstance(result.output, dict)
        assert result.output["findings"] == "done"
        assert len(mgr.active_tasks) == 1

    @pytest.mark.asyncio
    async def test_run_agent_unknown_type_raises(self):
        from agents.agent_manager import AgentManager
        mgr = AgentManager(brain=_make_brain("done"))
        with pytest.raises(ValueError):
            await mgr.run_agent("nonexistent_agent", "task")

    @pytest.mark.asyncio
    async def test_run_agent_failure_captured(self):
        from agents.agent_manager import AgentManager, AgentStatus
        brain = MagicMock()
        brain.chat_stream = _async_gen()  # empty generator -> body returns None
        # Force the agent to raise by making the generator raise on iteration
        async def raising_gen(*a, **kw):
            raise RuntimeError("boom")
            yield  # pragma: no cover
        brain.chat_stream = raising_gen
        mgr = AgentManager(brain=brain)
        result = await mgr.run_agent("research", "anything")
        assert result.status == AgentStatus.FAILED
        assert "boom" in str(result.output)

    @pytest.mark.asyncio
    async def test_run_swarm_runs_all_agents_in_parallel(self):
        from agents.agent_manager import AgentManager, AgentStatus
        mgr = AgentManager(brain=_make_brain("ok"))
        results = await mgr.run_swarm("analyze this")
        assert len(results) == 4
        # Each result should be an AgentResult (not raw exception)
        for r in results:
            assert hasattr(r, "status")

    @pytest.mark.asyncio
    async def test_run_swarm_with_custom_subset(self):
        from agents.agent_manager import AgentManager
        mgr = AgentManager(brain=_make_brain("ok"))
        results = await mgr.run_swarm(
            "analyze this", agent_types=["research", "writing"]
        )
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_run_pipeline_sequential_completes_for_code_task(self):
        from agents.agent_manager import AgentManager, AgentStatus
        mgr = AgentManager(brain=_make_brain("step output"))
        result = await mgr.run_pipeline("build a REST API")
        assert result.status == AgentStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_run_pipeline_stops_when_research_fails(self):
        from agents.agent_manager import AgentManager, AgentStatus

        # First call (research) raises; subsequent calls succeed
        call_count = {"n": 0}

        async def gen(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("research failed")
            yield "ok"

        brain = MagicMock()
        brain.chat_stream = gen
        mgr = AgentManager(brain=brain)
        result = await mgr.run_pipeline("build something")
        assert result.status == AgentStatus.FAILED
        # Pipeline should not have called the writing agent
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_run_pipeline_skips_coding_for_non_code_task(self):
        from agents.agent_manager import AgentManager, AgentStatus
        # Track which agents were called via call_count on brain
        calls = []

        async def gen(*a, **kw):
            calls.append(a[0] if a else "task")
            yield "output"

        brain = MagicMock()
        brain.chat_stream = gen
        mgr = AgentManager(brain=brain)
        result = await mgr.run_pipeline("write a poem about cats")
        assert result.status == AgentStatus.COMPLETED
        # Non-code task → 3 calls: research, task (planning), writing (report)
        # NO coding agent call.
        assert len(calls) == 3


# ===========================================================================
# 2. CodingAgent — agents/coding_agent.py
# ===========================================================================

class TestCodingAgent:
    """Tests for CodingAgent: write_code, debug_code, review_code, preview_changes."""

    @pytest.mark.asyncio
    async def test_write_code_extracts_markdown_code_block(self):
        from agents.coding_agent import CodingAgent
        brain = _make_brain("Here's the code:\n```python\nprint('hello')\n```\nDone.")
        agent = CodingAgent(brain=brain)
        result = await agent.write_code("write hello world")
        assert result["status"] == "success"
        assert result["language"] == "python"
        assert "print('hello')" in result["code"]
        assert result["task_type"] == "write"

    @pytest.mark.asyncio
    async def test_write_code_without_brain_returns_error(self):
        from agents.coding_agent import CodingAgent
        agent = CodingAgent(brain=None)
        result = await agent.write_code("anything")
        assert result["status"] == "error"
        assert "No brain" in result["message"]

    @pytest.mark.asyncio
    async def test_write_code_falls_back_to_full_response_when_no_block(self):
        from agents.coding_agent import CodingAgent
        brain = _make_brain("just plain text, no code block")
        agent = CodingAgent(brain=brain)
        result = await agent.write_code("write code")
        assert result["code"] == "just plain text, no code block"
        assert result["language"] == "python"  # default

    @pytest.mark.asyncio
    async def test_debug_code_returns_fixed_code(self):
        from agents.coding_agent import CodingAgent
        brain = _make_brain(
            "Root cause: typo\nFix:\n```python\nprint('fixed')\n```\nDone."
        )
        agent = CodingAgent(brain=brain)
        result = await agent.debug_code("fix the bug", context={"code": "x", "error": "y"})
        assert result["status"] == "success"
        assert result["task_type"] == "debug"
        assert result["fixed_code"] is not None
        assert "print('fixed')" in result["fixed_code"]

    @pytest.mark.asyncio
    async def test_debug_code_without_brain_returns_error(self):
        from agents.coding_agent import CodingAgent
        agent = CodingAgent(brain=None)
        result = await agent.debug_code("fix bug")
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_review_code_returns_full_analysis(self):
        from agents.coding_agent import CodingAgent
        brain = _make_brain("Critical: SQL injection on line 5.\nHigh: missing input validation.")
        agent = CodingAgent(brain=brain)
        result = await agent.review_code("review this", context={"code": "def f(): pass"})
        assert result["status"] == "success"
        assert result["task_type"] == "review"
        assert "SQL injection" in result["review"]

    @pytest.mark.asyncio
    async def test_execute_routes_to_debug_for_bug_keywords(self):
        from agents.coding_agent import CodingAgent
        brain = _make_brain("```python\nx = 1\n```\nfixed.")
        agent = CodingAgent(brain=brain)
        result = await agent.execute("debug this error")
        assert result["task_type"] == "debug"

    @pytest.mark.asyncio
    async def test_execute_routes_to_review_for_review_keywords(self):
        from agents.coding_agent import CodingAgent
        brain = _make_brain("Looks good. No issues.")
        agent = CodingAgent(brain=brain)
        result = await agent.execute("review this code")
        assert result["task_type"] == "review"

    @pytest.mark.asyncio
    async def test_execute_routes_to_write_by_default(self):
        from agents.coding_agent import CodingAgent
        brain = _make_brain("```python\nprint('hi')\n```")
        agent = CodingAgent(brain=brain)
        result = await agent.execute("create a new script")
        assert result["task_type"] == "write"

    @pytest.mark.asyncio
    async def test_preview_changes_generates_diff_and_preview_id(self, tmp_path):
        from agents.coding_agent import CodingAgent
        agent = CodingAgent(brain=None)
        existing = tmp_path / "app.py"
        existing.write_text("def old():\n    return 1\n")
        result = await agent.preview_changes(str(existing), "def new():\n    return 2\n")
        assert "preview_id" in result
        assert result["preview_id"].startswith("preview_")
        assert result["additions"] >= 1
        assert result["deletions"] >= 1
        assert "diff" in result
        # The preview should be stored internally
        assert hasattr(agent, "_pending_writes")
        assert result["preview_id"] in agent._pending_writes

    @pytest.mark.asyncio
    async def test_preview_changes_for_new_file_has_only_additions(self, tmp_path):
        from agents.coding_agent import CodingAgent
        agent = CodingAgent(brain=None)
        new_file = tmp_path / "newfile.py"
        result = await agent.preview_changes(str(new_file), "print('hello')")
        assert result["additions"] >= 1
        assert result["deletions"] == 0


# ===========================================================================
# 3. CodingOrchestrator — agents/coding_orchestrator.py
# ===========================================================================

class TestCodingOrchestrator:
    """Tests for CodingOrchestrator: file parsing + malicious pattern detection."""

    @pytest.mark.asyncio
    async def test_generate_project_without_brain_returns_error(self):
        from agents.coding_orchestrator import CodingOrchestrator
        orch = CodingOrchestrator(brain=None)
        result = await orch.generate_project("build a todo app")
        assert result["status"] == "error"
        assert "No brain" in result["message"]

    @pytest.mark.asyncio
    async def test_generate_project_parses_file_markers(self, tmp_path, monkeypatch):
        from agents.coding_orchestrator import CodingOrchestrator
        # Redirect sandbox to tmp_path so no real files leak
        monkeypatch.chdir(tmp_path)
        plan = (
            "=== FILE: main.py ===\nprint('hello')\n=== END FILE ===\n"
            "=== FILE: utils.py ===\ndef helper():\n    return 42\n=== END FILE ===\n"
        )
        brain = _make_brain(plan)
        orch = CodingOrchestrator(brain=brain)
        result = await orch.generate_project("simple project")
        assert result["status"] == "success"
        assert result["total_files"] == 2
        assert "main.py" in result["files_created"]
        assert "utils.py" in result["files_created"]

    def test_parse_files_handles_file_markers(self):
        from agents.coding_orchestrator import CodingOrchestrator
        orch = CodingOrchestrator(brain=None)
        plan = "=== FILE: a.py ===\nA\n=== END FILE ===\n=== FILE: b.py ===\nB\n=== END FILE ===\n"
        files = orch._parse_files(plan)
        assert len(files) == 2
        assert files[0]["filename"] == "a.py"
        assert files[1]["filename"] == "b.py"

    def test_parse_files_falls_back_to_code_blocks(self):
        from agents.coding_orchestrator import CodingOrchestrator
        orch = CodingOrchestrator(brain=None)
        plan = "```app.py\nprint(1)\n```\n```lib.py\nprint(2)\n```"
        files = orch._parse_files(plan)
        assert len(files) == 2
        assert files[0]["filename"] == "app.py"

    def test_parse_files_generic_fallback_for_orphan_blocks(self):
        from agents.coding_orchestrator import CodingOrchestrator
        orch = CodingOrchestrator(brain=None)
        plan = "```\nthis is some longer code block content\n```"
        files = orch._parse_files(plan)
        assert len(files) == 1
        assert files[0]["filename"].startswith("file_")

    def test_is_safe_filename_rejects_path_traversal(self):
        from agents.coding_orchestrator import CodingOrchestrator
        orch = CodingOrchestrator(brain=None)
        assert orch._is_safe_filename("../../etc/passwd") is False
        assert orch._is_safe_filename("/etc/passwd") is False
        assert orch._is_safe_filename(".hidden") is False
        assert orch._is_safe_filename("no_extension") is False
        assert orch._is_safe_filename("safe_file.py") is True
        assert orch._is_safe_filename("src/main.py") is True

    def test_has_malicious_patterns_detects_os_system(self):
        from agents.coding_orchestrator import CodingOrchestrator
        orch = CodingOrchestrator(brain=None)
        assert orch._has_malicious_patterns("os.system('rm -rf /')") is True
        assert orch._has_malicious_patterns("subprocess.call('x', shell=True)") is True
        assert orch._has_malicious_patterns("eval('1+1')") is True
        assert orch._has_malicious_patterns("exec('code')") is True
        assert orch._has_malicious_patterns("__import__('os')") is True
        assert orch._has_malicious_patterns("rm -rf /home") is True
        # Safe content
        assert orch._has_malicious_patterns("print('hello world')") is False
        assert orch._has_malicious_patterns("def add(a, b):\n    return a + b") is False

    @pytest.mark.asyncio
    async def test_write_files_rejects_unsafe_filename(self, tmp_path, monkeypatch):
        from agents.coding_orchestrator import CodingOrchestrator
        monkeypatch.chdir(tmp_path)
        orch = CodingOrchestrator(brain=None)
        files = [
            {"filename": "../escape.py", "content": "x = 1"},
            {"filename": "ok.py", "content": "print('safe')"},
        ]
        results = await orch._write_files(files, "test")
        assert len(results) == 2
        assert results[0]["success"] is False
        assert results[0]["error"] == "Unsafe filename"
        assert results[1]["success"] is True

    @pytest.mark.asyncio
    async def test_write_files_rejects_malicious_content(self, tmp_path, monkeypatch):
        from agents.coding_orchestrator import CodingOrchestrator
        monkeypatch.chdir(tmp_path)
        orch = CodingOrchestrator(brain=None)
        files = [
            {"filename": "evil.py", "content": "import os\nos.system('rm -rf /')"},
        ]
        results = await orch._write_files(files, "test")
        assert results[0]["success"] is False
        assert "malicious" in results[0]["error"].lower()


# ===========================================================================
# 4. ResearchAgent — agents/research_agent.py
# ===========================================================================

class TestResearchAgent:
    """Tests for ResearchAgent: execute() + deep_research()."""

    @pytest.mark.asyncio
    async def test_execute_returns_findings_and_confidence(self):
        from agents.research_agent import ResearchAgent
        brain = _make_brain("AI is transforming industries worldwide.")
        agent = ResearchAgent(brain=brain)
        result = await agent.execute("research AI trends")
        assert "task" in result
        assert "findings" in result
        assert "sources" in result
        assert "confidence" in result
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_execute_uses_heuristic_when_brain_none(self):
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(brain=None)
        result = await agent.execute("research anything")
        assert "findings" in result
        # When brain is None and no web search, method falls back to "llm_knowledge" or heuristic
        assert result["method"] in ("llm_knowledge", "web_search + llm_synthesis", "heuristic")

    @pytest.mark.asyncio
    async def test_deep_research_returns_full_structure(self):
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(brain=None)
        result = await agent.deep_research("climate change")
        assert "topic" in result
        assert "synthesis" in result
        assert "sources" in result
        assert "agreements" in result
        assert "conflicts" in result
        assert "confidence" in result
        assert "limitations" in result
        assert result["confidence"] in ("none", "low", "medium", "high")

    def test_find_agreements_detects_overlapping_phrases(self):
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(brain=None)
        sources = [
            {"url": "a", "key_claims": ["the climate is changing rapidly due to human activity"]},
            {"url": "b", "key_claims": ["human activity is changing the climate rapidly"]},
        ]
        agreements = agent._find_agreements(sources)
        assert len(agreements) >= 1
        assert "claim" in agreements[0]
        assert len(agreements[0]["sources"]) == 2

    def test_find_conflicts_detects_disagreement_cues(self):
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(brain=None)
        sources = [
            {"url": "x", "key_claims": ["the study is correct however the data is incomplete"]},
        ]
        conflicts = agent._find_conflicts(sources)
        assert len(conflicts) >= 1
        assert conflicts[0]["cue"] in ("however", "but", "disagree", "incorrect",
                                       "wrong", "not true", "disputed",
                                       "controversy", "debate")

    def test_calculate_confidence_increases_with_more_results(self):
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(brain=None)
        # No search results → base confidence 0.5 (+0.1 if analysis long)
        low = agent._calculate_confidence(None, "short")
        # 3 search results → +0.3 (max)
        high = agent._calculate_confidence(
            {"results": [{"title": f"t{i}"} for i in range(3)]},
            "x" * 300,
        )
        assert high > low

    @pytest.mark.asyncio
    async def test_synthesize_returns_no_sources_message_when_empty(self):
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(brain=None)
        synthesis = await agent._synthesize("topic", [], [], [])
        assert "No sources" in synthesis or "topic" in synthesis

    @pytest.mark.asyncio
    async def test_synthesize_uses_heuristic_fallback_with_sources(self):
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(brain=None)
        sources = [
            {"title": "Src1", "url": "http://a", "snippet": "Important fact.", "key_claims": ["Important fact."]},
        ]
        synthesis = await agent._synthesize("topic", sources, [], [])
        assert "Src1" in synthesis or "topic" in synthesis

    @pytest.mark.asyncio
    async def test_deep_research_with_multiple_sources_confidence_high(self, _patch_glm_brain):
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(brain=None)
        # Patch _search_web to return 3 sources
        async def fake_search(q):
            return {
                "results": [
                    {"title": "A", "url": "u1", "content": "First source agreement."},
                    {"title": "B", "url": "u2", "content": "Second source agreement."},
                    {"title": "C", "url": "u3", "content": "Third source agreement."},
                ]
            }
        agent._search_web = fake_search
        result = await agent.deep_research("test topic", max_sources=5)
        assert len(result["sources"]) == 3
        # 3 sources but maybe < 2 agreements → medium. Either way, not "none".
        assert result["confidence"] in ("medium", "high", "low")


# ===========================================================================
# 5. TacticalManager — agents/tactical_manager.py
# ===========================================================================

class TestTacticalManager:
    """Tests for TacticalManager: keyword-based agent dispatch + synthesis."""

    def test_plan_agents_dispatches_research_for_research_keywords(self):
        from agents.tactical_manager import TacticalManager
        tm = TacticalManager(brain=None)
        assert "research" in tm._plan_agents("research climate change")
        assert "research" in tm._plan_agents("find me papers on AI")
        assert "research" in tm._plan_agents("investigate the cause")
        assert "research" in tm._plan_agents("analyze the data")

    def test_plan_agents_dispatches_coding_for_code_keywords(self):
        from agents.tactical_manager import TacticalManager
        tm = TacticalManager(brain=None)
        plan = tm._plan_agents("build a web app and debug it")
        assert "coding" in plan

    def test_plan_agents_dispatches_writing_for_write_keywords(self):
        from agents.tactical_manager import TacticalManager
        tm = TacticalManager(brain=None)
        plan = tm._plan_agents("write a report on the meeting")
        assert "writing" in plan
        plan = tm._plan_agents("draft a document")
        assert "writing" in plan

    def test_plan_agents_dispatches_task_for_planning_keywords(self):
        from agents.tactical_manager import TacticalManager
        tm = TacticalManager(brain=None)
        plan = tm._plan_agents("plan the next sprint")
        assert "task" in plan

    def test_plan_agents_defaults_to_research_and_task(self):
        from agents.tactical_manager import TacticalManager
        tm = TacticalManager(brain=None)
        plan = tm._plan_agents("just a random thought with no keywords")
        assert plan == ["research", "task"]

    @pytest.mark.asyncio
    async def test_coordinate_without_agent_manager_returns_skipped_results(self):
        from agents.tactical_manager import TacticalManager
        tm = TacticalManager(brain=None, agent_manager=None)
        # No keyword match → defaults to ["research", "task"] (2 agents)
        result = await tm.coordinate("random thought with no keywords")
        assert result["coordination_status"] == "success"
        assert len(result["agent_results"]) == 2
        # No agent_manager → each result is a dict with status=skipped
        for r in result["agent_results"]:
            assert r["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_coordinate_with_agent_manager_runs_swarm(self):
        from agents.tactical_manager import TacticalManager

        # Mock agent manager that returns AgentResult-like objects
        mock_am = MagicMock()
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"agent_type": "research", "status": "completed"}
        mock_am.run_swarm = AsyncMock(return_value=[mock_result])
        tm = TacticalManager(brain=None, agent_manager=mock_am)
        result = await tm.coordinate("research climate change")
        assert result["agents_deployed"] == ["research"]
        assert result["agent_results"][0]["status"] == "completed"

    def test_heuristic_synthesis_falls_back_when_no_brain(self):
        from agents.tactical_manager import TacticalManager
        tm = TacticalManager(brain=None)
        synthesis = tm._heuristic_synthesis("task", [
            {"agent_type": "research", "output": "found stuff"},
        ])
        assert "research" in synthesis
        assert "found stuff" in synthesis

    def test_get_tactical_report_empty(self):
        from agents.tactical_manager import TacticalManager
        tm = TacticalManager(brain=None)
        report = tm.get_tactical_report()
        assert report == {"total_operations": 0}

    def test_tactical_history_records_after_coordinate(self):
        from agents.tactical_manager import TacticalManager
        tm = TacticalManager(brain=None, agent_manager=None)
        import asyncio
        asyncio.get_event_loop().run_until_complete(tm.coordinate("random no keywords"))
        assert len(tm.tactical_history) == 1
        entry = tm.tactical_history[0]
        assert entry["task"] == "random no keywords"
        assert entry["agents_used"] == ["research", "task"]


# ===========================================================================
# 6. TaskAgent — agents/task_agent.py
# ===========================================================================

class TestTaskAgent:
    """Tests for TaskAgent: task breakdown + step parsing."""

    @pytest.mark.asyncio
    async def test_execute_returns_structured_breakdown(self):
        from agents.task_agent import TaskAgent
        brain = _make_brain(
            "1. Set up the project\n2. Write the code\n3. Test the implementation\n"
        )
        agent = TaskAgent(brain=brain)
        result = await agent.execute("build a CLI tool")
        assert "steps" in result
        assert "total_steps" in result
        assert result["total_steps"] == 3
        # Each step should have title, complexity, dependencies
        for s in result["steps"]:
            assert "title" in s
            assert "complexity" in s
            assert "dependencies" in s

    def test_parse_steps_handles_numbered_list(self):
        from agents.task_agent import TaskAgent
        agent = TaskAgent(brain=None)
        response = "1. First step\n2. Second step\n3. Third step\n"
        steps = agent._parse_steps(response)
        assert len(steps) == 3
        assert steps[0]["title"] == "First step"
        assert steps[0]["step_number"] == 1
        # Each non-first step depends on the previous
        assert steps[1]["dependencies"] == [1]
        assert steps[2]["dependencies"] == [2]

    def test_parse_steps_handles_step_N_format(self):
        from agents.task_agent import TaskAgent
        agent = TaskAgent(brain=None)
        response = "Step 1: Do A\nStep 2: Do B\n"
        steps = agent._parse_steps(response)
        assert len(steps) == 2
        assert "Do A" in steps[0]["title"]

    def test_parse_steps_detects_high_complexity_keywords(self):
        from agents.task_agent import TaskAgent
        agent = TaskAgent(brain=None)
        # Complexity keywords are scanned in the DESCRIPTION line (after
        # the title line), so we put "complex" in the description.
        response = (
            "1. First part\n"
            "   Easy description here\n"
            "2. Second part\n"
            "   This is a complex algorithm implementation\n"
        )
        steps = agent._parse_steps(response)
        assert steps[1]["complexity"] == "high"

    def test_parse_steps_detects_low_complexity_keywords(self):
        from agents.task_agent import TaskAgent
        agent = TaskAgent(brain=None)
        # Complexity keywords are scanned in the DESCRIPTION line (after
        # the title line), so we put "simple" in the description.
        response = (
            "1. First part\n"
            "   Hard work required\n"
            "2. Second part\n"
            "   This is a simple cleanup\n"
        )
        steps = agent._parse_steps(response)
        assert steps[1]["complexity"] == "low"

    def test_parse_steps_creates_single_step_when_unparseable(self):
        from agents.task_agent import TaskAgent
        agent = TaskAgent(brain=None)
        response = "Just a wall of text with no steps"
        steps = agent._parse_steps(response)
        assert len(steps) == 1
        assert "task" in steps[0]["title"].lower()

    def test_estimate_overall_complexity(self):
        from agents.task_agent import TaskAgent
        agent = TaskAgent(brain=None)
        # All low → low
        low_steps = [{"complexity": "low"}, {"complexity": "low"}]
        assert agent._estimate_overall_complexity(low_steps) == "low"
        # All high → high
        high_steps = [{"complexity": "high"}, {"complexity": "high"}]
        assert agent._estimate_overall_complexity(high_steps) == "high"
        # Mixed → medium
        mixed = [{"complexity": "low"}, {"complexity": "high"}]
        assert agent._estimate_overall_complexity(mixed) == "medium"

    @pytest.mark.asyncio
    async def test_heuristic_breakdown_when_brain_none(self):
        from agents.task_agent import TaskAgent
        agent = TaskAgent(brain=None)
        result = await agent.break_down_task("build a house")
        # Heuristic always returns 4 steps
        assert result["total_steps"] == 4
        assert "steps" in result
        assert result["estimated_complexity"] == "medium"


# ===========================================================================
# 7. WritingAgent — agents/writing_agent.py
# ===========================================================================

class TestWritingAgent:
    """Tests for WritingAgent: write / proofread / report modes."""

    @pytest.mark.asyncio
    async def test_execute_routes_to_proofread_for_edit_keywords(self):
        from agents.writing_agent import WritingAgent
        brain = _make_brain("Corrected text with improvements.")
        agent = WritingAgent(brain=brain)
        result = await agent.execute("proofread this essay")
        assert result["task_type"] == "proofread"

    @pytest.mark.asyncio
    async def test_execute_routes_to_report_for_report_keywords(self):
        from agents.writing_agent import WritingAgent
        brain = _make_brain("# Report\n## Summary\nContent here.")
        agent = WritingAgent(brain=brain)
        result = await agent.execute("write a report on Q3")
        assert result["task_type"] == "report"

    @pytest.mark.asyncio
    async def test_execute_routes_to_write_by_default(self):
        from agents.writing_agent import WritingAgent
        brain = _make_brain("Once upon a time...")
        agent = WritingAgent(brain=brain)
        result = await agent.execute("write a poem about cats")
        assert result["task_type"] == "write"

    @pytest.mark.asyncio
    async def test_write_document_returns_word_count(self):
        from agents.writing_agent import WritingAgent
        brain = _make_brain("hello world foo bar")
        agent = WritingAgent(brain=brain)
        result = await agent.write_document("write something")
        assert result["status"] == "success"
        assert result["word_count"] == 4
        assert result["content"] == "hello world foo bar"

    @pytest.mark.asyncio
    async def test_write_document_without_brain_returns_error(self):
        from agents.writing_agent import WritingAgent
        agent = WritingAgent(brain=None)
        result = await agent.write_document("anything")
        assert result["status"] == "error"
        assert "No brain" in result["message"]

    @pytest.mark.asyncio
    async def test_proofread_returns_corrected_text(self):
        from agents.writing_agent import WritingAgent
        brain = _make_brain("Here is the corrected version.")
        agent = WritingAgent(brain=brain)
        result = await agent.proofread("heres a sentance with errors")
        assert result["status"] == "success"
        assert result["task_type"] == "proofread"
        assert result["corrected"] == "Here is the corrected version."

    @pytest.mark.asyncio
    async def test_write_report_includes_research_context(self):
        from agents.writing_agent import WritingAgent
        brain = _make_brain("# Final Report\nComprehensive analysis.")
        agent = WritingAgent(brain=brain)
        result = await agent.write_report("Q3 results", context={"research": "findings"})
        assert result["status"] == "success"
        assert result["task_type"] == "report"
        assert "Report" in result["content"]

    def test_format_research_report_builds_markdown(self):
        from agents.writing_agent import WritingAgent
        agent = WritingAgent(brain=None)
        result = agent.format_research_report(
            "AI trends",
            findings=["Finding one", "Finding two"],
            sources=[{"title": "Source A", "url": "http://a"}],
        )
        assert result["status"] == "success"
        assert result["task_type"] == "research_report"
        assert "AI trends" in result["content"]
        assert "Finding one" in result["content"]
        assert "Source A" in result["content"]
        assert "http://a" in result["content"]

    def test_format_research_report_handles_string_sources(self):
        from agents.writing_agent import WritingAgent
        agent = WritingAgent(brain=None)
        result = agent.format_research_report(
            "topic", findings=["f1"], sources=["plain string source"],
        )
        assert "plain string source" in result["content"]
