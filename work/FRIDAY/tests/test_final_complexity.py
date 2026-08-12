"""Tests for the IRON-CROWN-FINAL complexity-reduction refactor.

Verifies that the helpers extracted from the five high-complexity
functions identified by EngineeringIntelligence still exist, still
have the expected signatures, and still produce correct results.

Coverage
--------
1. ``ActionLedger.wait_for_voice_approval`` helpers (core/ledger.py)
   - :meth:`_build_voice_description`
   - :meth:`_voice_speak`
   - :meth:`_voice_listen_once`
   - :meth:`_process_voice_response`
   - :meth:`_handle_voice_timeout`
   - :meth:`_finalize_voice_approval`
   - :meth:`_run_voice_approval_loop`

2. ``EngineeringOrg.execute_task`` helpers (core/engineering_org.py)
   - :meth:`_run_work_function`
   - :meth:`_run_validation`
   - :meth:`_complete_or_fail`

3. ``ProviderRouter._claude_stream_with_tools`` helpers (core/provider_router.py)
   - :meth:`_call_claude_with_tools`
   - :meth:`_process_claude_tool_call`
   - :meth:`_extract_tool_result`
   - :meth:`_store_assistant_response`

4. ``FutureSimulator._parse_simulation_paths`` helpers (core/simulator.py)
   - :meth:`_parse_path_token`
   - :meth:`_normalize_path`

5. ``serve_stdio`` helpers (mcp_server.py, module-level)
   - :func:`_handle_mcp_request`
   - :func:`_send_mcp_response`

Run::

    python -m pytest tests/test_final_complexity.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def ledger():
    """Fresh ActionLedger with temp persistence files."""
    from core.ledger import ActionLedger

    old_secret = os.environ.get("FRIDAY_LEDGER_HMAC_SECRET")
    os.environ["FRIDAY_LEDGER_HMAC_SECRET"] = "test-final-complexity-secret"
    ActionLedger._HMAC_SECRET = None

    with tempfile.TemporaryDirectory() as tmpdir:
        old_persist = ActionLedger.PERSIST_PATH
        old_chain = ActionLedger.CHAIN_PERSIST_PATH
        ActionLedger.PERSIST_PATH = os.path.join(tmpdir, "pending.json")
        ActionLedger.CHAIN_PERSIST_PATH = os.path.join(tmpdir, "chain.json")
        l = ActionLedger()
        l.audit_log = os.path.join(tmpdir, "audit.log")
        yield l
        ActionLedger.PERSIST_PATH = old_persist
        ActionLedger.CHAIN_PERSIST_PATH = old_chain

    ActionLedger._HMAC_SECRET = None
    if old_secret is not None:
        os.environ["FRIDAY_LEDGER_HMAC_SECRET"] = old_secret
    else:
        os.environ.pop("FRIDAY_LEDGER_HMAC_SECRET", None)


@pytest.fixture()
def org(tmp_path, monkeypatch):
    """Fresh EngineeringOrg with fast validation pipeline."""
    import core.task_system
    import core.knowledge_base
    import core.validation_pipeline
    import core.engineering_org
    core.task_system._queue = None
    core.knowledge_base._kb = None
    core.validation_pipeline._pipeline = None
    core.engineering_org._org = None

    from core.validation_pipeline import (
        ValidationPipeline, ValidationReport, CheckStatus,
    )

    class FastPipeline(ValidationPipeline):
        async def run(self, skip_tests=True):
            report = ValidationReport()
            report.overall_status = CheckStatus.PASSED
            report.checks = []
            return report

    monkeypatch.setattr(
        "core.engineering_org.get_validation_pipeline", lambda: FastPipeline(),
    )

    from core.engineering_org import EngineeringOrg
    yield EngineeringOrg()

    core.task_system._queue = None
    core.knowledge_base._kb = None
    core.validation_pipeline._pipeline = None
    core.engineering_org._org = None


# ===========================================================================
# 1. ActionLedger voice-approval helpers
# ===========================================================================
class TestActionLedgerVoiceHelpers:
    """Verify wait_for_voice_approval() extracted helpers."""

    def test_helpers_exist_on_action_ledger(self):
        """All voice-approval helpers must be defined on ActionLedger."""
        from core.ledger import ActionLedger
        for name in (
            "_build_voice_description",
            "_voice_speak",
            "_voice_listen_once",
            "_process_voice_response",
            "_handle_voice_timeout",
            "_finalize_voice_approval",
            "_run_voice_approval_loop",
        ):
            assert hasattr(ActionLedger, name), f"ActionLedger missing {name}"

    def test_build_voice_description_includes_all_fields(self):
        """_build_voice_description includes component, action, params, yes/no."""
        from core.ledger import ActionLedger
        action_data = {
            "component": "Weather",
            "action": "get_weather",
            "params": {"city": "Lagos"},
        }
        desc = ActionLedger._build_voice_description(action_data)
        assert "Weather" in desc
        assert "get_weather" in desc
        assert "Lagos" in desc
        assert "yes" in desc.lower()
        assert "no" in desc.lower()

    def test_build_voice_description_defaults_to_unknown(self):
        """_build_voice_description uses 'unknown' when fields are missing."""
        from core.ledger import ActionLedger
        desc = ActionLedger._build_voice_description({})
        assert "unknown" in desc

    @pytest.mark.asyncio
    async def test_voice_speak_prints_when_no_speaker(self, capsys):
        """_voice_speak prints [VOICE APPROVAL] message when speaker is None."""
        from core.ledger import ActionLedger
        await ActionLedger._voice_speak(None, "hello")
        captured = capsys.readouterr()
        assert "[VOICE APPROVAL] hello" in captured.out

    @pytest.mark.asyncio
    async def test_voice_speak_uses_async_when_available(self):
        """_voice_speak prefers speaker.speak_async over speaker.speak."""
        from core.ledger import ActionLedger
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        await ActionLedger._voice_speak(speaker, "hi")
        speaker.speak_async.assert_awaited_once_with("hi")
        speaker.speak.assert_not_called()

    @pytest.mark.asyncio
    async def test_voice_listen_once_returns_empty_when_no_listener(self):
        """_voice_listen_once returns '' when listener is None."""
        from core.ledger import ActionLedger
        result = await ActionLedger._voice_listen_once(None, 5)
        assert result == ""

    @pytest.mark.asyncio
    async def test_handle_voice_timeout_speaks_on_first_attempt(self):
        """_handle_voice_timeout speaks rejection on attempts==1."""
        from core.ledger import ActionLedger
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        result = await ActionLedger._handle_voice_timeout("aid-1", 1, speaker)
        assert result is False
        speaker.speak_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_voice_timeout_silent_on_subsequent(self):
        """_handle_voice_timeout stays silent on attempts > 1."""
        from core.ledger import ActionLedger
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        result = await ActionLedger._handle_voice_timeout("aid-1", 2, speaker)
        assert result is False
        speaker.speak_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_voice_response_yes_returns_true(self, ledger):
        """_process_voice_response returns True for 'yes' words."""
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        verdict = await ledger._process_voice_response(
            "yes please", "aid-1", speaker, attempts=1, max_retries=1,
        )
        assert verdict is True

    @pytest.mark.asyncio
    async def test_process_voice_response_no_returns_false(self, ledger):
        """_process_voice_response returns False for 'no' words."""
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        verdict = await ledger._process_voice_response(
            "no way", "aid-1", speaker, attempts=1, max_retries=1,
        )
        assert verdict is False

    @pytest.mark.asyncio
    async def test_process_voice_response_ambiguous_with_retries_returns_none(self, ledger):
        """Ambiguous response with retries left returns None (re-ask)."""
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        verdict = await ledger._process_voice_response(
            "banana", "aid-1", speaker, attempts=1, max_retries=2,
        )
        assert verdict is None

    @pytest.mark.asyncio
    async def test_process_voice_response_ambiguous_no_retries_returns_false(self, ledger):
        """Ambiguous response with no retries left returns False (reject)."""
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        verdict = await ledger._process_voice_response(
            "banana", "aid-1", speaker, attempts=2, max_retries=1,
        )
        assert verdict is False

    @pytest.mark.asyncio
    async def test_process_voice_response_empty_routes_to_timeout(self, ledger):
        """Empty transcript routes to _handle_voice_timeout."""
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        verdict = await ledger._process_voice_response(
            "", "aid-1", speaker, attempts=1, max_retries=1,
        )
        assert verdict is False
        speaker.speak_async.assert_awaited_once()  # timeout message

    @pytest.mark.asyncio
    async def test_finalize_voice_approval_approves(self, ledger):
        """_finalize_voice_approval(action_id, action_data, True) approves."""
        aid = ledger.queue_action(
            "Weather", "get_weather", {"loc": "Lagos"}, risk_level="high",
        )
        action_data = ledger.pending_actions[aid]
        result = await ledger._finalize_voice_approval(aid, action_data, True)
        assert result is True
        assert ledger.pending_actions[aid]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_finalize_voice_approval_rejects_on_false(self, ledger):
        """_finalize_voice_approval rejects when approved=False."""
        aid = ledger.queue_action(
            "Weather", "get_weather", {"loc": "Lagos"}, risk_level="high",
        )
        action_data = ledger.pending_actions[aid]
        result = await ledger._finalize_voice_approval(aid, action_data, False)
        assert result is False
        assert ledger.pending_actions[aid]["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_run_voice_approval_loop_yes_returns_true(self, ledger):
        """_run_voice_approval_loop returns True on immediate 'yes'."""
        async def fake_listen(listener, timeout):
            return "yes"
        with patch.object(ledger, "_voice_listen_once", fake_listen):
            verdict = await ledger._run_voice_approval_loop(
                "aid-1", None, None, timeout=5, max_retries=2,
            )
        assert verdict is True

    @pytest.mark.asyncio
    async def test_run_voice_approval_loop_exhausts_retries(self, ledger):
        """Ambiguous responses exhaust retries → reject, listen max_retries+1 times."""
        call_count = [0]

        async def fake_listen(listener, timeout):
            call_count[0] += 1
            return "banana"

        with patch.object(ledger, "_voice_listen_once", fake_listen):
            verdict = await ledger._run_voice_approval_loop(
                "aid-1", None, None, timeout=5, max_retries=1,
            )
        assert verdict is False
        # max_retries=1 → 1 initial + 1 retry = 2 listens
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_wait_for_voice_approval_unknown_action_returns_false(self, ledger):
        """wait_for_voice_approval returns False for unknown action_id."""
        result = await ledger.wait_for_voice_approval("nonexistent-aid")
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_voice_approval_already_approved_returns_true(self, ledger):
        """wait_for_voice_approval returns True if action is already approved."""
        aid = ledger.queue_action(
            "Weather", "get_weather", {"loc": "Lagos"}, risk_level="low",
        )
        ledger.approve_action(aid)
        result = await ledger.wait_for_voice_approval(aid)
        assert result is True


# ===========================================================================
# 2. EngineeringOrg execute_task helpers
# ===========================================================================
class TestEngineeringOrgExecuteHelpers:
    """Verify execute_task() extracted helpers."""

    def test_helpers_exist_on_engineering_org(self):
        """All execute_task helpers must be defined on EngineeringOrg."""
        from core.engineering_org import EngineeringOrg
        for name in (
            "_run_work_function",
            "_run_validation",
            "_complete_or_fail",
        ):
            assert hasattr(EngineeringOrg, name), \
                f"EngineeringOrg missing {name}"

    @pytest.mark.asyncio
    async def test_run_work_function_returns_none_when_no_work_fn(self, org):
        """_run_work_function returns None when work_fn is None."""
        result = await org._run_work_function(None, MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_run_work_function_returns_none_on_success(self, org):
        """_run_work_function returns None when work_fn succeeds."""
        async def good_work(task):
            pass
        result = await org._run_work_function(good_work, MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_run_work_function_returns_error_on_failure(self, org):
        """_run_work_function returns the error string on failure."""
        async def bad_work(task):
            raise RuntimeError("intentional failure")
        result = await org._run_work_function(bad_work, MagicMock())
        assert result is not None
        assert "intentional failure" in result

    @pytest.mark.asyncio
    async def test_run_validation_returns_zero_when_skipped(self, org):
        """_run_validation returns (0, 0, None) when skip_validation=True."""
        tp, tf, report = await org._run_validation(skip_validation=True)
        assert (tp, tf, report) == (0, 0, None)

    @pytest.mark.asyncio
    async def test_run_validation_returns_counts_and_report_on_success(self, org):
        """_run_validation returns counts and report on success."""
        tp, tf, report = await org._run_validation(skip_validation=False)
        # FastPipeline returns empty checks list → both counts 0
        assert tp == 0
        assert tf == 0
        assert report is not None

    @pytest.mark.asyncio
    async def test_complete_or_fail_completes_on_success(self, org):
        """_complete_or_fail completes task when no errors."""
        from core.task_system import Task, TaskStatus, TaskPhase
        from core.engineering_org import Lead, LeadRole

        task = Task(
            title="t", description="d", phase=TaskPhase.QUALITY,
            assigned_to="testing_lead",
        )
        task.id = "test-cf-1"
        task.status = TaskStatus.IN_PROGRESS
        # Register the task with the queue so complete_task can find it.
        await org.task_queue.create_task(
            title="t", description="d", phase=TaskPhase.QUALITY,
            assigned_to="testing_lead",
        )
        # The create_task call above generated a task; we need its id.
        # Use the queue's internal storage to inject our task.
        org.task_queue._tasks[task.id] = task
        lead = Lead(
            role=LeadRole.TESTING_LEAD, name="Testing Lead",
            phases=[TaskPhase.VALIDATION, TaskPhase.QUALITY],
        )
        before = lead.tasks_completed
        receipt = await org._complete_or_fail(
            task, work_error=None, lead=lead, lead_name="Testing Lead",
            duration=0.5, tests_passed=0, tests_failed=0,
            validation_report=None,
        )
        assert receipt is not None
        assert receipt.task_id == task.id
        assert lead.tasks_completed == before + 1

    @pytest.mark.asyncio
    async def test_complete_or_fail_fails_on_work_error(self, org):
        """_complete_or_fail returns None on work_error."""
        from core.task_system import Task, TaskStatus, TaskPhase
        from core.engineering_org import Lead, LeadRole

        task = Task(
            title="t", description="d", phase=TaskPhase.QUALITY,
            assigned_to="testing_lead",
        )
        task.id = "test-cf-err"
        task.status = TaskStatus.IN_PROGRESS
        org.task_queue._tasks[task.id] = task
        lead = Lead(
            role=LeadRole.TESTING_LEAD, name="Testing Lead",
            phases=[TaskPhase.VALIDATION, TaskPhase.QUALITY],
        )
        before_failed = lead.tasks_failed
        receipt = await org._complete_or_fail(
            task, work_error="boom", lead=lead, lead_name="Testing Lead",
            duration=0.1,
        )
        assert receipt is None
        assert lead.tasks_failed == before_failed + 1

    @pytest.mark.asyncio
    async def test_complete_or_fail_fails_on_validation_failure(self, org):
        """_complete_or_fail returns None when validation_report.passed is False."""
        from core.task_system import Task, TaskStatus, TaskPhase
        from core.engineering_org import Lead, LeadRole

        task = Task(
            title="t", description="d", phase=TaskPhase.QUALITY,
            assigned_to="testing_lead",
        )
        task.id = "test-cf-vfail"
        task.status = TaskStatus.IN_PROGRESS
        org.task_queue._tasks[task.id] = task
        lead = Lead(
            role=LeadRole.TESTING_LEAD, name="Testing Lead",
            phases=[TaskPhase.VALIDATION, TaskPhase.QUALITY],
        )
        before_failed = lead.tasks_failed
        failing_report = SimpleNamespace(passed=False, summary="failed")
        receipt = await org._complete_or_fail(
            task, work_error=None, lead=lead, lead_name="Testing Lead",
            duration=0.1, validation_report=failing_report,
        )
        assert receipt is None
        assert lead.tasks_failed == before_failed + 1

    @pytest.mark.asyncio
    async def test_execute_task_returns_none_for_unknown_id(self, org):
        """execute_task returns None for an unknown task_id."""
        result = await org.execute_task("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_task_completes_with_work_fn(self, org):
        """execute_task runs work_fn, validates, completes with receipt."""
        from core.task_system import TaskPhase, TaskPriority
        task = await org.create_and_assign_task(
            title="Final complexity test",
            description="Smoke-test the refactored execute_task.",
            phase=TaskPhase.QUALITY,
            priority=TaskPriority.MEDIUM,
        )

        async def simple_work(t):
            pass

        receipt = await org.execute_task(task.id, work_fn=simple_work)
        assert receipt is not None
        assert receipt.task_id == task.id


# ===========================================================================
# 3. ProviderRouter _claude_stream_with_tools helpers
# ===========================================================================
class TestProviderRouterClaudeHelpers:
    """Verify _claude_stream_with_tools() extracted helpers."""

    def test_helpers_exist_on_provider_router(self):
        """All Claude-stream helpers must be defined on ProviderRouter."""
        from core.provider_router import ProviderRouter
        for name in (
            "_call_claude_with_tools",
            "_process_claude_tool_call",
            "_extract_tool_result",
            "_store_assistant_response",
        ):
            assert hasattr(ProviderRouter, name), \
                f"ProviderRouter missing {name}"

    def test_extract_tool_result_returns_block_for_tool_use(self):
        """_extract_tool_result returns the block when type is 'tool_use'."""
        from core.provider_router import ProviderRouter
        block = SimpleNamespace(type="tool_use", id="t1", name="foo", input={})
        result = ProviderRouter._extract_tool_result(block)
        assert result is block

    def test_extract_tool_result_returns_none_for_text(self):
        """_extract_tool_result returns None when type is not 'tool_use'."""
        from core.provider_router import ProviderRouter
        block = SimpleNamespace(type="text", text="hello")
        result = ProviderRouter._extract_tool_result(block)
        assert result is None

    @pytest.mark.asyncio
    async def test_process_claude_tool_call_returns_envelope(self):
        """_process_claude_tool_call returns a properly-shaped tool_result dict."""
        from core.provider_router import ProviderRouter

        tool_use = SimpleNamespace(name="search", id="tu-1", input={"q": "x"})
        brain = MagicMock()
        brain._execute_tool = AsyncMock(return_value={"hits": [1, 2, 3]})

        router = ProviderRouter()
        envelope = await router._process_claude_tool_call(tool_use, brain)

        assert envelope["role"] == "user"
        assert isinstance(envelope["content"], list)
        assert envelope["content"][0]["type"] == "tool_result"
        assert envelope["content"][0]["tool_use_id"] == "tu-1"
        assert envelope["content"][0]["content"] == {"hits": [1, 2, 3]}
        brain._execute_tool.assert_awaited_once_with("search", {"q": "x"})

    @pytest.mark.asyncio
    async def test_store_assistant_response_appends_to_history(self):
        """_store_assistant_response appends assistant message to history."""
        from core.provider_router import ProviderRouter

        brain = MagicMock()
        brain.conversation_history = []
        brain.memory = MagicMock()
        brain.memory.store_conversation = MagicMock()

        router = ProviderRouter()
        await router._store_assistant_response(brain, "hello world")

        assert len(brain.conversation_history) == 1
        assert brain.conversation_history[0]["role"] == "assistant"
        assert brain.conversation_history[0]["content"] == "hello world"
        brain.memory.store_conversation.assert_called_once_with(
            "assistant", "hello world",
        )

    @pytest.mark.asyncio
    async def test_store_assistant_response_swallows_memory_error(self):
        """_store_assistant_response does not raise when memory.store fails."""
        from core.provider_router import ProviderRouter

        brain = MagicMock()
        brain.conversation_history = []
        brain.memory = MagicMock()
        brain.memory.store_conversation = MagicMock(
            side_effect=RuntimeError("mem fail"),
        )

        router = ProviderRouter()
        # Must not raise.
        await router._store_assistant_response(brain, "hello")
        assert brain.conversation_history[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_store_assistant_response_no_memory(self):
        """_store_assistant_response still appends to history when brain.memory is None."""
        from core.provider_router import ProviderRouter

        brain = MagicMock()
        brain.conversation_history = []
        brain.memory = None

        router = ProviderRouter()
        await router._store_assistant_response(brain, "hi")
        assert brain.conversation_history == [{"role": "assistant", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_call_claude_with_tools_yields_chunks_and_appends_final_content(self):
        """_call_claude_with_tools yields text chunks and populates final_content_out."""
        from core.provider_router import ProviderRouter

        # Build a fake async context manager that yields events.
        class FakeDelta:
            def __init__(self, dtype, text):
                self.type = dtype
                self.text = text

        class FakeEvent:
            def __init__(self, etype, delta=None):
                self.type = etype
                self.delta = delta

        class FakeStream:
            def __init__(self):
                self._events = [
                    FakeEvent("content_block_delta", FakeDelta("text_delta", "Hel")),
                    FakeEvent("content_block_delta", FakeDelta("text_delta", "lo")),
                    FakeEvent("content_block_stop"),
                ]

            def __aiter__(self):
                self._idx = 0
                return self

            async def __anext__(self):
                if self._idx >= len(self._events):
                    raise StopAsyncIteration
                ev = self._events[self._idx]
                self._idx += 1
                return ev

            async def get_final_message(self):
                # Two content blocks: one text, one tool_use.
                return SimpleNamespace(content=[
                    SimpleNamespace(type="text", text="Hello"),
                    SimpleNamespace(type="tool_use", id="t1", name="foo", input={}),
                ])

        class FakeStreamCtx:
            def __init__(self, *args, **kwargs):
                self._stream = FakeStream()

            async def __aenter__(self):
                return self._stream

            async def __aexit__(self, *exc):
                return False

        brain = MagicMock()
        brain.claude_client = MagicMock()
        brain.claude_client.messages.stream = lambda *a, **kw: FakeStreamCtx(*a, **kw)
        brain.claude_model = "claude-test"

        router = ProviderRouter()
        holder = []
        chunks = []
        async for chunk in router._call_claude_with_tools(
            [], [], "sys", brain, holder,
        ):
            chunks.append(chunk)

        assert chunks == ["Hel", "lo"]
        assert len(holder) == 1
        final_content = holder[0]
        assert isinstance(final_content, list)
        assert len(final_content) == 2
        assert final_content[0].type == "text"
        assert final_content[1].type == "tool_use"

    @pytest.mark.asyncio
    async def test_claude_stream_with_tools_yields_error_when_no_client(self):
        """_claude_stream_with_tools yields an error message when brain.claude_client is None."""
        from core.provider_router import ProviderRouter

        brain = MagicMock()
        brain.claude_client = None
        brain.conversation_history = []
        brain.history_limit = 100
        brain._inject_rag_context = AsyncMock(return_value="")
        brain.summarizer = MagicMock()
        brain.summarizer.summarize_older_messages = AsyncMock(return_value=[])

        router = ProviderRouter()
        chunks = []
        async for chunk in router._claude_stream_with_tools(
            "hi", "system", [], brain,
        ):
            chunks.append(chunk)

        assert any("Claude client unavailable" in c for c in chunks)


# ===========================================================================
# 4. FutureSimulator _parse_simulation_paths helpers
# ===========================================================================
class TestSimulatorParseHelpers:
    """Verify _parse_simulation_paths() extracted helpers."""

    def test_helpers_exist_on_future_simulator(self):
        """All _parse_simulation_paths helpers must be defined on FutureSimulator."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from core.simulator import FutureSimulator
        for name in ("_parse_path_token", "_normalize_path"):
            assert hasattr(FutureSimulator, name), \
                f"FutureSimulator missing {name}"

    def test_parse_path_token_returns_none_for_empty(self):
        """_parse_path_token returns None for empty / whitespace-only lines."""
        from core.simulator import FutureSimulator
        assert FutureSimulator._parse_path_token("") is None
        assert FutureSimulator._parse_path_token("   ") is None

    def test_parse_path_token_returns_none_for_unknown_prefix(self):
        """_parse_path_token returns None for lines that don't match any known prefix."""
        from core.simulator import FutureSimulator
        assert FutureSimulator._parse_path_token("garbage line") is None
        assert FutureSimulator._parse_path_token("FOO: bar") is None

    def test_parse_path_token_returns_tuple_for_path_prefix(self):
        """_parse_path_token returns ('PATH', name) for PATH: ... lines."""
        from core.simulator import FutureSimulator
        result = FutureSimulator._parse_path_token("PATH: Optimistic")
        assert result == ("PATH", "Optimistic")

    def test_parse_path_token_returns_tuple_for_probability(self):
        """_parse_path_token recognises PROBABILITY prefix."""
        from core.simulator import FutureSimulator
        result = FutureSimulator._parse_path_token("PROBABILITY: 0.4")
        assert result == ("PROBABILITY", "0.4")

    def test_parse_path_token_case_insensitive(self):
        """_parse_path_token matches prefixes case-insensitively."""
        from core.simulator import FutureSimulator
        result = FutureSimulator._parse_path_token("risk: 0.7")
        assert result == ("RISK", "0.7")

    def test_parse_path_token_strips_whitespace(self):
        """_parse_path_token strips whitespace from token and value."""
        from core.simulator import FutureSimulator
        result = FutureSimulator._parse_path_token("  PATH:   Spaced Out  ")
        assert result == ("PATH", "Spaced Out")

    def test_normalize_path_applies_description(self):
        """_normalize_path sets path.description for DESCRIPTION key."""
        from core.simulator import FutureSimulator, DecisionPath
        path = DecisionPath(name="x", description="", probability=0.0, desirability=0.0, risk_level=0.0)
        FutureSimulator._normalize_path(path, "DESCRIPTION", "A best-case scenario")
        assert path.description == "A best-case scenario"

    def test_normalize_path_applies_probability(self):
        """_normalize_path sets path.probability for PROBABILITY key."""
        from core.simulator import FutureSimulator, DecisionPath
        path = DecisionPath(name="x", description="", probability=0.0, desirability=0.0, risk_level=0.0)
        FutureSimulator._normalize_path(path, "PROBABILITY", "0.4")
        assert path.probability == 0.4

    def test_normalize_path_probability_default_on_bad_value(self):
        """_normalize_path falls back to 0.5 on parse error for PROBABILITY."""
        from core.simulator import FutureSimulator, DecisionPath
        path = DecisionPath(name="x", description="", probability=0.0, desirability=0.0, risk_level=0.0)
        FutureSimulator._normalize_path(path, "PROBABILITY", "not-a-number")
        assert path.probability == 0.5

    def test_normalize_path_applies_outcomes_list(self):
        """_normalize_path splits OUTCOMES on ';' and strips whitespace."""
        from core.simulator import FutureSimulator, DecisionPath
        path = DecisionPath(name="x", description="", probability=0.0, desirability=0.0, risk_level=0.0)
        FutureSimulator._normalize_path(path, "OUTCOMES", "goal achieved; positive effects ; ")
        assert path.key_outcomes == ["goal achieved", "positive effects"]

    def test_normalize_path_applies_caveats_list(self):
        """_normalize_path splits CAVEATS on ';' and strips whitespace."""
        from core.simulator import FutureSimulator, DecisionPath
        path = DecisionPath(name="x", description="", probability=0.0, desirability=0.0, risk_level=0.0)
        FutureSimulator._normalize_path(path, "CAVEATS", "caveat1;caveat2")
        assert path.caveats == ["caveat1", "caveat2"]

    def test_parse_simulation_paths_end_to_end(self):
        """Full LLM-output parse produces the expected DecisionPath list."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from core.simulator import FutureSimulator

        fs = FutureSimulator()
        llm_output = (
            "PATH: Optimistic\n"
            "DESCRIPTION: Best case\n"
            "PROBABILITY: 0.4\n"
            "DESIRABILITY: 0.8\n"
            "RISK: 0.2\n"
            "OUTCOMES: goal; positive effects\n"
            "CAVEATS: assumes favorable\n\n"
            "PATH: Pessimistic\n"
            "DESCRIPTION: Worst case\n"
            "PROBABILITY: 0.3\n"
            "DESIRABILITY: 0.2\n"
            "RISK: 0.9\n"
            "OUTCOMES: goal fails; major losses\n"
            "CAVEATS: high uncertainty\n"
        )
        paths = fs._parse_simulation_paths(llm_output, num_paths=3)
        assert len(paths) == 2
        assert paths[0].name == "Optimistic"
        assert paths[0].probability == 0.4
        assert paths[0].desirability == 0.8
        assert paths[0].risk_level == 0.2
        assert paths[0].key_outcomes == ["goal", "positive effects"]
        assert paths[1].name == "Pessimistic"
        assert paths[1].risk_level == 0.9

    def test_parse_simulation_paths_respects_num_paths_limit(self):
        """_parse_simulation_paths truncates to num_paths."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from core.simulator import FutureSimulator

        fs = FutureSimulator()
        llm_output = (
            "PATH: A\nDESCRIPTION: a\n"
            "PATH: B\nDESCRIPTION: b\n"
            "PATH: C\nDESCRIPTION: c\n"
        )
        paths = fs._parse_simulation_paths(llm_output, num_paths=2)
        assert len(paths) == 2
        assert {p.name for p in paths} == {"A", "B"}


# ===========================================================================
# 5. mcp_server serve_stdio helpers
# ===========================================================================
class TestMcpServerServeStdioHelpers:
    """Verify serve_stdio() extracted helpers."""

    def test_helpers_exist_at_module_level(self):
        """_handle_mcp_request and _send_mcp_response are module-level functions."""
        import mcp_server
        assert hasattr(mcp_server, "_handle_mcp_request")
        assert hasattr(mcp_server, "_send_mcp_response")
        assert callable(mcp_server._handle_mcp_request)
        assert callable(mcp_server._send_mcp_response)

    @pytest.mark.asyncio
    async def test_handle_mcp_request_initialize(self):
        """_handle_mcp_request('initialize') returns result with protocolVersion."""
        from mcp_server import _handle_mcp_request
        server = MagicMock()
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        result = await _handle_mcp_request(server, request)
        assert "result" in result
        assert result["result"]["protocolVersion"] == "2024-11-05"
        assert "capabilities" in result["result"]
        assert result["result"]["serverInfo"]["name"] == "friday-mcp"

    @pytest.mark.asyncio
    async def test_handle_mcp_request_tools_list(self):
        """_handle_mcp_request('tools/list') returns the TOOLS list."""
        from mcp_server import _handle_mcp_request, TOOLS
        server = MagicMock()
        request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        result = await _handle_mcp_request(server, request)
        assert "result" in result
        assert result["result"]["tools"] == TOOLS

    @pytest.mark.asyncio
    async def test_handle_mcp_request_unknown_method_returns_error(self):
        """_handle_mcp_request for unknown method returns a -32601 error."""
        from mcp_server import _handle_mcp_request
        server = MagicMock()
        request = {"jsonrpc": "2.0", "id": 3, "method": "totally/unknown"}
        result = await _handle_mcp_request(server, request)
        assert "error" in result
        assert result["error"]["code"] == -32601
        assert "totally/unknown" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_handle_mcp_request_notifications_returns_none(self):
        """_handle_mcp_request for notifications/initialized returns None (no response)."""
        from mcp_server import _handle_mcp_request
        server = MagicMock()
        request = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        result = await _handle_mcp_request(server, request)
        assert result is None

    @pytest.mark.asyncio
    async def test_handle_mcp_request_authenticate_success(self):
        """_handle_mcp_request('friday/authenticate') with valid token returns authenticated."""
        from mcp_server import _handle_mcp_request
        server = MagicMock()
        server.authenticate = MagicMock(return_value=True)
        request = {
            "jsonrpc": "2.0", "id": 4, "method": "friday/authenticate",
            "params": {"token": "valid"},
        }
        result = await _handle_mcp_request(server, request)
        assert "result" in result
        assert result["result"]["authenticated"] is True

    @pytest.mark.asyncio
    async def test_handle_mcp_request_authenticate_failure(self):
        """_handle_mcp_request('friday/authenticate') with bad token returns -32001 error."""
        from mcp_server import _handle_mcp_request
        server = MagicMock()
        server.authenticate = MagicMock(return_value=False)
        request = {
            "jsonrpc": "2.0", "id": 5, "method": "friday/authenticate",
            "params": {"token": "bad"},
        }
        result = await _handle_mcp_request(server, request)
        assert "error" in result
        assert result["error"]["code"] == -32001

    @pytest.mark.asyncio
    async def test_handle_mcp_request_tools_call_gated_by_auth(self):
        """_handle_mcp_request('tools/call') returns auth error when not authenticated."""
        from mcp_server import _handle_mcp_request
        server = MagicMock()
        server._check_authenticated = MagicMock(return_value={
            "jsonrpc": "2.0",
            "error": {"code": -32001, "message": "Unauthorized"},
        })
        request = {
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "weather", "arguments": {}},
        }
        result = await _handle_mcp_request(server, request)
        assert "error" in result
        assert result["error"]["code"] == -32001

    @pytest.mark.asyncio
    async def test_handle_mcp_request_tools_call_dispatches_when_authed(self):
        """_handle_mcp_request('tools/call') dispatches when authenticated."""
        from mcp_server import _handle_mcp_request
        server = MagicMock()
        server._check_authenticated = MagicMock(return_value=None)
        server.dispatch = AsyncMock(return_value={"ok": True})
        request = {
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "weather", "arguments": {"city": "Lagos"}},
        }
        result = await _handle_mcp_request(server, request)
        assert "result" in result
        server.dispatch.assert_awaited_once_with("weather", {"city": "Lagos"})
        # The dispatch result is JSON-encoded in the content text.
        content_text = result["result"]["content"][0]["text"]
        assert json.loads(content_text) == {"ok": True}

    @pytest.mark.asyncio
    async def test_send_mcp_response_writes_envelope(self):
        """_send_mcp_response writes a JSON-RPC envelope line to the writer."""
        from mcp_server import _send_mcp_response
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        result_body = {"result": {"hello": "world"}}
        await _send_mcp_response(writer, "req-1", result_body)

        writer.write.assert_called_once()
        raw = writer.write.call_args[0][0]
        assert isinstance(raw, (bytes, bytearray))
        decoded = raw.decode("utf-8")
        # Must be a single newline-terminated JSON line.
        assert decoded.endswith("\n")
        envelope = json.loads(decoded.rstrip("\n"))
        assert envelope["jsonrpc"] == "2.0"
        assert envelope["id"] == "req-1"
        assert envelope["result"] == {"hello": "world"}
        writer.drain.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_mcp_response_supports_error_body(self):
        """_send_mcp_response correctly serialises an error envelope."""
        from mcp_server import _send_mcp_response
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        error_body = {"error": {"code": -32700, "message": "Parse error"}}
        await _send_mcp_response(writer, None, error_body)

        raw = writer.write.call_args[0][0]
        envelope = json.loads(raw.decode("utf-8").rstrip("\n"))
        assert envelope["id"] is None
        assert envelope["error"]["code"] == -32700
        assert envelope["error"]["message"] == "Parse error"
