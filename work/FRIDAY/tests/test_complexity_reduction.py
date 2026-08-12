"""Tests for the COUNCIL-DELTA-V10 complexity-reduction refactor.

Verifies that the extracted helper methods exist on the right classes
and behave correctly in isolation. Each test documents the helper it
guards so future regressions are immediately obvious.

Coverage
--------
1. ``GLMBrain`` web_search helpers
   - :meth:`_parse_web_search_results`
   - :meth:`_parse_tool_call_results`
   - :meth:`_create_synthesized_result`
   - :meth:`_is_search_tool_call`
   - :meth:`_extract_tool_call_items`

2. ``ProviderRouter`` route helpers
   - :meth:`_route_glm`
   - :meth:`_route_ollama`
   - :meth:`_route_claude`
   - :meth:`_route_fallback_no_anthropic`
   - :meth:`_should_route_gemini`

3. ``ActionLedger`` voice-approval helpers
   - :meth:`_build_voice_description`
   - :meth:`_voice_speak`
   - :meth:`_voice_listen_once`
   - :meth:`_run_voice_approval_loop`
   - :meth:`_process_voice_response`
   - :meth:`_handle_voice_timeout`
   - :meth:`_finalize_voice_approval`

4. ``SecretScanner`` scan_file helpers
   - :meth:`_relative_path_str`
   - :meth:`_is_test_or_fixture_file`
   - :meth:`_is_placeholder_text`
   - :meth:`_is_placeholder_line`
   - :meth:`_should_skip_line`
   - :meth:`_scan_line_for_secrets`

5. ``DependencyAnalyzer`` helpers
   - :meth:`_collect_python_files`
   - :meth:`_build_module_info`
   - :meth:`_build_reverse_imports`
   - :meth:`_detect_circular_dependencies`

6. Complexity invariant — every refactored function below cyclomatic 15.
"""
from __future__ import annotations

import ast
import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — patch heavy imports so we can import the brain without real
# Anthropic / Gemini / Supabase credentials.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Ensure no real API calls leak during tests."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAIN_PROVIDER", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)


@pytest.fixture()
def brain_cls():
    """Return the FridayBrain class with dangerous imports patched."""
    with patch.dict("sys.modules", {
        "anthropic": MagicMock(),
        "google.generativeai": MagicMock(),
        "google.genai": MagicMock(),
        "tavily": MagicMock(),
    }):
        with patch("config.settings.ANTHROPIC_API_KEY", None), \
             patch("config.settings.GEMINI_API_KEY", None), \
             patch("config.settings.TAVILY_API_KEY", None):
            from core.brain import FridayBrain
            return FridayBrain


def _compute_complexity(source: str, function_name: str) -> int:
    """Compute cyclomatic complexity of ``function_name`` in ``source``.

    Mirrors the ComplexityAnalyzer formula used by EngineeringIntelligence:
        +1 base path
        +1 per If/While/For/AsyncFor
        +1 per ExceptHandler
        +(n-1) per BoolOp with n values
        +1 per comprehension / generator expression
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == function_name:
            complexity = 1
            for child in ast.walk(node):
                if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor)):
                    complexity += 1
                elif isinstance(child, ast.ExceptHandler):
                    complexity += 1
                elif isinstance(child, ast.BoolOp):
                    complexity += len(child.values) - 1
                elif isinstance(child, (ast.ListComp, ast.SetComp,
                                        ast.DictComp, ast.GeneratorExp)):
                    complexity += 1
            return complexity
    raise ValueError(f"function {function_name!r} not found in source")


# ===========================================================================
# 1. GLMBrain web_search helpers
# ===========================================================================


class TestGLMBrainWebSearchHelpers:
    """Each helper extracted from ``web_search()`` must exist and work."""

    def test_helpers_exist_on_glm_brain(self):
        from core.glm_brain import GLMBrain
        for name in (
            "_parse_web_search_results",
            "_parse_tool_call_results",
            "_create_synthesized_result",
            "_is_search_tool_call",
            "_extract_tool_call_items",
        ):
            assert hasattr(GLMBrain, name), f"GLMBrain missing {name}"

    def test_parse_web_search_results_extracts_real_results(self):
        from core.glm_brain import GLMBrain
        item = {"title": "Lagos Weather", "link": "https://example.com",
                "content": "Sunny, 28C"}
        response = SimpleNamespace(web_search=[item])
        results = GLMBrain._parse_web_search_results(response, 5)
        assert len(results) == 1
        assert results[0]["title"] == "Lagos Weather"
        assert results[0]["url"] == "https://example.com"
        assert results[0]["source"] == "zhipuai_web_search"

    def test_parse_web_search_results_handles_missing_field(self):
        from core.glm_brain import GLMBrain
        response = SimpleNamespace()  # no web_search attribute
        results = GLMBrain._parse_web_search_results(response, 5)
        assert results == []

    def test_parse_web_search_results_skips_non_dict_items(self):
        from core.glm_brain import GLMBrain
        response = SimpleNamespace(web_search=["not-a-dict", 42, None])
        results = GLMBrain._parse_web_search_results(response, 5)
        assert results == []

    def test_is_search_tool_call_detects_search_name(self):
        from core.glm_brain import GLMBrain
        tc = SimpleNamespace(function=SimpleNamespace(name="web_search"))
        assert GLMBrain._is_search_tool_call(tc) is True

    def test_is_search_tool_call_rejects_other_names(self):
        from core.glm_brain import GLMBrain
        tc = SimpleNamespace(function=SimpleNamespace(name="calculator"))
        assert GLMBrain._is_search_tool_call(tc) is False

    def test_is_search_tool_call_returns_false_when_no_function(self):
        from core.glm_brain import GLMBrain
        assert GLMBrain._is_search_tool_call(SimpleNamespace()) is False

    def test_extract_tool_call_items_handles_list(self):
        from core.glm_brain import GLMBrain
        data = [{"title": "a"}, {"title": "b"}, "skip-me"]
        items = GLMBrain._extract_tool_call_items(data)
        assert len(items) == 2  # non-dict item filtered out

    def test_extract_tool_call_items_handles_results_dict(self):
        from core.glm_brain import GLMBrain
        data = {"results": [{"title": "a"}]}
        items = GLMBrain._extract_tool_call_items(data)
        assert len(items) == 1

    def test_extract_tool_call_items_empty_for_other_shapes(self):
        from core.glm_brain import GLMBrain
        assert GLMBrain._extract_tool_call_items({"foo": "bar"}) == []
        assert GLMBrain._extract_tool_call_items(42) == []

    def test_parse_tool_call_results_returns_empty_for_no_calls(self):
        from core.glm_brain import GLMBrain
        assert GLMBrain._parse_tool_call_results(None, 5) == []
        assert GLMBrain._parse_tool_call_results([], 5) == []

    def test_parse_tool_call_results_extracts_from_list_payload(self):
        from core.glm_brain import GLMBrain
        tc = SimpleNamespace(function=SimpleNamespace(
            name="web_search",
            arguments='[{"title": "T", "url": "https://x"}]',
        ))
        results = GLMBrain._parse_tool_call_results([tc], 5)
        assert len(results) == 1
        assert results[0]["title"] == "T"
        assert results[0]["source"] == "zhipuai_tool_call"

    def test_parse_tool_call_results_extracts_from_dict_with_results_key(self):
        from core.glm_brain import GLMBrain
        tc = SimpleNamespace(function=SimpleNamespace(
            name="web_search",
            arguments='{"results": [{"title": "Y", "link": "https://y"}]}',
        ))
        results = GLMBrain._parse_tool_call_results([tc], 5)
        assert len(results) == 1
        assert results[0]["url"] == "https://y"

    def test_parse_tool_call_results_skips_non_search_tools(self):
        from core.glm_brain import GLMBrain
        tc = SimpleNamespace(function=SimpleNamespace(
            name="calculator",
            arguments='[{"answer": "42"}]',
        ))
        assert GLMBrain._parse_tool_call_results([tc], 5) == []

    def test_create_synthesized_result_labels_as_llm_output(self):
        from core.glm_brain import GLMBrain
        results = GLMBrain._create_synthesized_result("Some synthesised text")
        assert len(results) == 1
        assert results[0]["source"] == "llm_synthesised"
        assert "warning" in results[0]
        assert "NOT a real search result" in results[0]["title"]

    def test_create_synthesized_result_empty_for_empty_content(self):
        from core.glm_brain import GLMBrain
        assert GLMBrain._create_synthesized_result("") == []
        assert GLMBrain._create_synthesized_result(None) == []


# ===========================================================================
# 2. ProviderRouter route helpers
# ===========================================================================


class TestProviderRouterRouteHelpers:
    """Each per-provider dispatch helper must exist and behave correctly."""

    def test_helpers_exist_on_provider_router(self):
        from core.provider_router import ProviderRouter
        for name in (
            "_route_glm",
            "_route_ollama",
            "_route_claude",
            "_route_fallback_no_anthropic",
            "_should_route_gemini",
        ):
            assert hasattr(ProviderRouter, name), \
                f"ProviderRouter missing {name}"

    def test_should_route_gemini_for_explicit_provider(self):
        from core.provider_router import ProviderRouter
        router = ProviderRouter()
        brain = MagicMock()
        brain.gemini_brain = MagicMock()  # truthy
        assert router._should_route_gemini("gemini", "short", brain) is True

    def test_should_route_gemini_for_long_message(self):
        from core.provider_router import ProviderRouter
        router = ProviderRouter()
        brain = MagicMock()
        brain.gemini_brain = MagicMock()
        long_msg = "x" * 5001
        assert router._should_route_gemini("claude", long_msg, brain) is True

    def test_should_route_gemini_false_when_no_brain(self):
        from core.provider_router import ProviderRouter
        router = ProviderRouter()
        brain = MagicMock()
        brain.gemini_brain = None
        # Router has no gemini_brain either
        router.gemini_brain = None
        assert router._should_route_gemini("gemini", "msg", brain) is False

    def test_should_route_gemini_false_for_short_normal_message(self):
        from core.provider_router import ProviderRouter
        router = ProviderRouter()
        brain = MagicMock()
        brain.gemini_brain = MagicMock()
        assert router._should_route_gemini("claude", "short msg", brain) is False

    @pytest.mark.asyncio
    async def test_route_ollama_delegates_to_local_brain(self):
        from core.provider_router import ProviderRouter
        router = ProviderRouter()
        brain = MagicMock()

        async def _local_stream(msg):
            yield "local-chunk"

        brain.local_brain = MagicMock()
        brain.local_brain.chat_stream = _local_stream

        chunks = []
        async for c in router._route_ollama("hi", brain):
            chunks.append(c)
        assert chunks == ["local-chunk"]

    @pytest.mark.asyncio
    async def test_route_claude_delegates_to_claude_stream(self):
        from core.provider_router import ProviderRouter
        router = ProviderRouter()

        async def _fake_claude(msg, sp, tools, brain):
            yield "claude-chunk"

        with patch.object(router, "_claude_stream_with_tools", _fake_claude):
            chunks = []
            async for c in router._route_claude("hi", "sys", [], MagicMock()):
                chunks.append(c)
            assert chunks == ["claude-chunk"]

    @pytest.mark.asyncio
    async def test_route_fallback_yields_error_when_no_brain(self):
        from core.provider_router import ProviderRouter
        router = ProviderRouter()
        brain = MagicMock()
        brain.glm_brain = MagicMock()
        brain.glm_brain.available.return_value = False
        brain.local_brain = MagicMock()
        brain.local_brain.available = AsyncMock(return_value=False)
        router.glm_brain = None
        router.local_brain = None

        chunks = []
        async for c in router._route_fallback_no_anthropic("msg", brain):
            chunks.append(c)
        assert "Error" in "".join(chunks)
        assert "No brain providers available" in "".join(chunks)


# ===========================================================================
# 3. ActionLedger voice-approval helpers
# ===========================================================================


class TestActionLedgerVoiceApprovalHelpers:
    """Each voice-approval helper must exist and behave correctly."""

    @pytest.fixture()
    def ledger(self):
        """Fresh ActionLedger with temp persistence files."""
        old_secret = os.environ.get("FRIDAY_LEDGER_HMAC_SECRET")
        os.environ["FRIDAY_LEDGER_HMAC_SECRET"] = "test-hmac-secret-for-complexity-tests"
        from core.ledger import ActionLedger
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

    def test_helpers_exist_on_action_ledger(self):
        from core.ledger import ActionLedger
        for name in (
            "_build_voice_description",
            "_voice_speak",
            "_voice_listen_once",
            "_run_voice_approval_loop",
            "_process_voice_response",
            "_handle_voice_timeout",
            "_finalize_voice_approval",
        ):
            assert hasattr(ActionLedger, name), \
                f"ActionLedger missing {name}"

    def test_build_voice_description_includes_component_and_action(self):
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

    def test_build_voice_description_uses_unknown_when_missing(self):
        from core.ledger import ActionLedger
        desc = ActionLedger._build_voice_description({})
        assert "unknown" in desc

    @pytest.mark.asyncio
    async def test_voice_speak_prints_when_no_speaker(self, capsys):
        from core.ledger import ActionLedger
        await ActionLedger._voice_speak(None, "hello")
        captured = capsys.readouterr()
        assert "[VOICE APPROVAL] hello" in captured.out

    @pytest.mark.asyncio
    async def test_voice_speak_uses_async_speak_when_available(self):
        from core.ledger import ActionLedger
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        await ActionLedger._voice_speak(speaker, "hello")
        speaker.speak_async.assert_awaited_once_with("hello")
        speaker.speak.assert_not_called()

    @pytest.mark.asyncio
    async def test_voice_speak_falls_back_to_sync_on_async_failure(self):
        from core.ledger import ActionLedger
        speaker = MagicMock()
        speaker.speak_async = AsyncMock(side_effect=Exception("async fail"))
        speaker.speak = MagicMock()
        await ActionLedger._voice_speak(speaker, "hello")
        speaker.speak.assert_called_once_with("hello")

    @pytest.mark.asyncio
    async def test_voice_listen_once_returns_empty_when_no_listener(self):
        from core.ledger import ActionLedger
        result = await ActionLedger._voice_listen_once(None, 5)
        assert result == ""

    @pytest.mark.asyncio
    async def test_voice_listen_once_returns_empty_when_listener_missing_record_audio(self):
        from core.ledger import ActionLedger
        listener = MagicMock()  # no record_audio attribute
        result = await ActionLedger._voice_listen_once(listener, 5)
        assert result == ""

    @pytest.mark.asyncio
    async def test_handle_voice_timeout_speaks_on_first_attempt(self):
        from core.ledger import ActionLedger
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        result = await ActionLedger._handle_voice_timeout("aid-1", 1, speaker)
        assert result is False
        speaker.speak_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_voice_timeout_silent_on_subsequent_attempts(self):
        from core.ledger import ActionLedger
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        result = await ActionLedger._handle_voice_timeout("aid-1", 2, speaker)
        assert result is False
        speaker.speak_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_voice_response_returns_true_for_yes(self, ledger):
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        verdict = await ledger._process_voice_response(
            "yes please", "aid-1", speaker, attempts=1, max_retries=1,
        )
        assert verdict is True

    @pytest.mark.asyncio
    async def test_process_voice_response_returns_false_for_no(self, ledger):
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        verdict = await ledger._process_voice_response(
            "no way", "aid-1", speaker, attempts=1, max_retries=1,
        )
        assert verdict is False

    @pytest.mark.asyncio
    async def test_process_voice_response_returns_none_for_ambiguous_with_retries_left(self, ledger):
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        verdict = await ledger._process_voice_response(
            "banana", "aid-1", speaker, attempts=1, max_retries=2,
        )
        assert verdict is None  # caller should re-ask

    @pytest.mark.asyncio
    async def test_process_voice_response_returns_false_when_ambiguous_and_no_retries_left(self, ledger):
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        verdict = await ledger._process_voice_response(
            "banana", "aid-1", speaker, attempts=2, max_retries=1,
        )
        assert verdict is False

    @pytest.mark.asyncio
    async def test_process_voice_response_routes_timeout_to_handler(self, ledger):
        speaker = MagicMock()
        speaker.speak_async = AsyncMock()
        speaker.speak = MagicMock()
        verdict = await ledger._process_voice_response(
            "", "aid-1", speaker, attempts=1, max_retries=1,
        )
        assert verdict is False
        speaker.speak_async.assert_awaited_once()  # timeout message

    @pytest.mark.asyncio
    async def test_finalize_voice_approval_approves_when_approved_true(self, ledger):
        aid = ledger.queue_action("Weather", "get_weather", {"loc": "Lagos"}, risk_level="high")
        action_data = ledger.pending_actions[aid]
        await ledger._finalize_voice_approval(aid, action_data, True)
        assert ledger.pending_actions[aid]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_finalize_voice_approval_rejects_when_approved_false(self, ledger):
        aid = ledger.queue_action("Weather", "get_weather", {"loc": "Lagos"}, risk_level="high")
        action_data = ledger.pending_actions[aid]
        await ledger._finalize_voice_approval(aid, action_data, False)
        assert ledger.pending_actions[aid]["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_finalize_voice_approval_rejects_when_approved_none(self, ledger):
        aid = ledger.queue_action("Weather", "get_weather", {"loc": "Lagos"}, risk_level="high")
        action_data = ledger.pending_actions[aid]
        await ledger._finalize_voice_approval(aid, action_data, None)
        assert ledger.pending_actions[aid]["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_run_voice_approval_loop_returns_true_on_immediate_yes(self, ledger):
        # Mock _voice_listen_once to return "yes" on the first call
        async def fake_listen(listener, timeout):
            return "yes"
        with patch.object(ledger, "_voice_listen_once", fake_listen):
            verdict = await ledger._run_voice_approval_loop(
                "aid-1", None, None, timeout=5, max_retries=2,
            )
        assert verdict is True

    @pytest.mark.asyncio
    async def test_run_voice_approval_loop_rejects_on_loop_exhaustion(self, ledger):
        """If the loop exhausts max_retries without resolution, default to False."""
        call_count = [0]

        async def fake_listen(listener, timeout):
            call_count[0] += 1
            return "banana"  # always ambiguous

        with patch.object(ledger, "_voice_listen_once", fake_listen):
            verdict = await ledger._run_voice_approval_loop(
                "aid-1", None, None, timeout=5, max_retries=1,
            )
        assert verdict is False
        # Should have listened 2 times (max_retries=1 → 1 initial + 1 retry)
        assert call_count[0] == 2


# ===========================================================================
# 4. SecretScanner scan_file helpers
# ===========================================================================


class TestSecretScannerHelpers:
    """Each scan_file helper must exist and behave correctly."""

    def test_helpers_exist_on_secret_scanner(self):
        from core.security_ops import SecretScanner
        for name in (
            "_relative_path_str",
            "_is_test_or_fixture_file",
            "_is_placeholder_text",
            "_is_placeholder_line",
            "_should_skip_line",
            "_scan_line_for_secrets",
        ):
            assert hasattr(SecretScanner, name), \
                f"SecretScanner missing {name}"

    def test_relative_path_str_returns_relative_when_under_root(self, tmp_path):
        from core.security_ops import SecretScanner
        filepath = tmp_path / "core" / "x.py"
        filepath.parent.mkdir(parents=True)
        filepath.write_text("")
        rel = SecretScanner._relative_path_str(filepath, tmp_path)
        assert rel == "core/x.py"

    def test_relative_path_str_falls_back_to_absolute(self, tmp_path):
        from core.security_ops import SecretScanner
        # tmp_path/dir2/x.py is not relative to tmp_path/dir1
        root = tmp_path / "dir1"
        root.mkdir()
        filepath = tmp_path / "dir2" / "x.py"
        filepath.parent.mkdir(parents=True)
        filepath.write_text("")
        rel = SecretScanner._relative_path_str(filepath, root)
        # Should fall back to the absolute path string
        assert rel == str(filepath)

    def test_is_test_or_fixture_file_detects_tests_dir(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._is_test_or_fixture_file(
            "tests/test_x.py", Path("tests/test_x.py")
        ) is True

    def test_is_test_or_fixture_file_detects_benchmarks(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._is_test_or_fixture_file(
            "benchmarks/run.py", Path("benchmarks/run.py")
        ) is True

    def test_is_test_or_fixture_file_detects_verify_scripts(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._is_test_or_fixture_file(
            "scripts/verify_x.py", Path("scripts/verify_x.py")
        ) is True

    def test_is_test_or_fixture_file_detects_conftest(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._is_test_or_fixture_file(
            "tests/conftest.py", Path("tests/conftest.py")
        ) is True

    def test_is_test_or_fixture_file_returns_false_for_source(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._is_test_or_fixture_file(
            "core/glm_brain.py", Path("core/glm_brain.py")
        ) is False

    def test_is_placeholder_text_detects_your_prefix(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._is_placeholder_text("your_api_key_here") is True

    def test_is_placeholder_text_detects_sk_test(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._is_placeholder_text("sk_test_abc123") is True

    def test_is_placeholder_text_returns_false_for_real_secret(self):
        from core.security_ops import SecretScanner
        # 32-char alphanumeric, not a placeholder
        assert SecretScanner._is_placeholder_text("a" * 32) is False

    def test_is_placeholder_line_detects_example(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._is_placeholder_line(
            'API_KEY = "your-key-here"  # example'
        ) is True

    def test_should_skip_line_skips_comments(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._should_skip_line(
            "# this is a comment", False, "core/x.py"
        ) is True

    def test_should_skip_line_skips_slash_comments(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._should_skip_line(
            "// this is a comment", False, "core/x.py"
        ) is True

    def test_should_skip_line_skips_test_file_lines(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._should_skip_line(
            'API_KEY = "sk-realsecret"', True, "tests/test_x.py"
        ) is True

    def test_should_skip_line_returns_false_for_real_secret(self):
        from core.security_ops import SecretScanner
        assert SecretScanner._should_skip_line(
            'API_KEY = "sk-realsecret1234567890abcdef1234567890"',
            False, "core/config.py",
        ) is False

    def test_scan_line_for_secrets_returns_empty_for_skipped_line(self):
        from core.security_ops import SecretScanner
        findings = SecretScanner._scan_line_for_secrets(
            "# comment", 1, "core/x.py", False,
        )
        assert findings == []

    def test_scan_line_for_secrets_detects_real_secret(self):
        from core.security_ops import SecretScanner
        line = 'API_KEY = "sk-1234567890abcdef1234567890abcdef"'
        findings = SecretScanner._scan_line_for_secrets(
            line, 1, "core/config.py", False,
        )
        assert len(findings) >= 1
        assert findings[0].type.value == "hardcoded_secret"

    def test_scan_line_for_secrets_skips_placeholder(self):
        from core.security_ops import SecretScanner
        line = 'API_KEY = "your_api_key_here"'
        findings = SecretScanner._scan_line_for_secrets(
            line, 1, "core/config.py", False,
        )
        assert findings == []

    def test_scan_file_returns_empty_for_skipped_extension(self, tmp_path):
        from core.security_ops import SecretScanner
        filepath = tmp_path / "data.png"
        filepath.write_bytes(b"\x89PNG")
        assert SecretScanner.scan_file(filepath) == []


# ===========================================================================
# 5. DependencyAnalyzer helpers
# ===========================================================================


class TestDependencyAnalyzerHelpers:
    """Each DependencyAnalyzer helper must exist and behave correctly."""

    @pytest.fixture()
    def project(self, tmp_path):
        """Create a tiny project with two modules that import each other."""
        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "__init__.py").write_text("")
        (tmp_path / "core" / "a.py").write_text(
            "import core.b\n"
            "def foo():\n"
            "    return 1\n"
        )
        (tmp_path / "core" / "b.py").write_text(
            "from core.a import foo\n"
            "class Bar:\n"
            "    pass\n"
        )
        return tmp_path

    def test_helpers_exist_on_dependency_analyzer(self):
        from core.engineering_intelligence import DependencyAnalyzer
        for name in (
            "_collect_python_files",
            "_build_module_info",
            "_build_reverse_imports",
            "_detect_circular_dependencies",
        ):
            assert hasattr(DependencyAnalyzer, name), \
                f"DependencyAnalyzer missing {name}"

    def test_collect_python_files_skips_venv(self, project):
        from core.engineering_intelligence import DependencyAnalyzer
        # Add a .venv directory with a .py file — must be skipped
        venv_dir = project / ".venv"
        venv_dir.mkdir()
        (venv_dir / "skip.py").write_text("# should be skipped")
        files = DependencyAnalyzer._collect_python_files(project)
        names = [f.name for f in files]
        assert "a.py" in names
        assert "b.py" in names
        assert "skip.py" not in names

    def test_build_module_info_parses_imports_classes_functions(self, project):
        from core.engineering_intelligence import DependencyAnalyzer
        mod_info = DependencyAnalyzer._build_module_info(
            project / "core" / "b.py", project
        )
        assert mod_info is not None
        assert mod_info.classes == 1
        assert mod_info.functions == 0
        assert "core.a" in mod_info.imports

    def test_build_module_info_returns_none_on_unparseable_file(self, tmp_path):
        from core.engineering_intelligence import DependencyAnalyzer
        # Write a file that isn't valid Python
        bad = tmp_path / "bad.py"
        bad.write_text("def broken(:\n    pass")
        assert DependencyAnalyzer._build_module_info(bad, tmp_path) is None

    def test_build_reverse_imports_populates_imported_by(self, project):
        from core.engineering_intelligence import (
            DependencyAnalyzer, ModuleInfo,
        )
        modules = {
            "core.a": ModuleInfo(
                path="core/a.py", loc=3, classes=0, functions=1,
                imports=["core.b"], imported_by=[], complexity=0.0, finding_count=0,
            ),
            "core.b": ModuleInfo(
                path="core/b.py", loc=3, classes=1, functions=0,
                imports=["core.a"], imported_by=[], complexity=0.0, finding_count=0,
            ),
        }
        DependencyAnalyzer._build_reverse_imports(modules)
        assert "core.b" in modules["core.a"].imported_by
        assert "core.a" in modules["core.b"].imported_by

    def test_detect_circular_dependencies_finds_cycle(self, project):
        from core.engineering_intelligence import (
            DependencyAnalyzer, FindingType, ModuleInfo,
        )
        modules = {
            "core.a": ModuleInfo(
                path="core/a.py", loc=3, classes=0, functions=1,
                imports=["core.b"], imported_by=[], complexity=0.0, finding_count=0,
            ),
            "core.b": ModuleInfo(
                path="core/b.py", loc=3, classes=1, functions=0,
                imports=["core.a"], imported_by=[], complexity=0.0, finding_count=0,
            ),
        }
        DependencyAnalyzer._build_reverse_imports(modules)
        findings = DependencyAnalyzer._detect_circular_dependencies(modules)
        assert len(findings) >= 1
        assert all(f.type == FindingType.CIRCULAR_DEP for f in findings)

    def test_detect_circular_dependencies_returns_empty_when_none(self, project):
        from core.engineering_intelligence import (
            DependencyAnalyzer, ModuleInfo,
        )
        modules = {
            "core.a": ModuleInfo(
                path="core/a.py", loc=3, classes=0, functions=1,
                imports=[], imported_by=[], complexity=0.0, finding_count=0,
            ),
        }
        findings = DependencyAnalyzer._detect_circular_dependencies(modules)
        assert findings == []

    def test_analyze_end_to_end_returns_modules_and_findings(self, project):
        from core.engineering_intelligence import DependencyAnalyzer
        modules, findings = DependencyAnalyzer.analyze(project)
        assert "core.a" in modules
        assert "core.b" in modules
        # The a ↔ b cycle is a real cycle, so we expect ≥1 finding
        assert len(findings) >= 1


# ===========================================================================
# 6. Complexity invariant — every refactored function below cyclomatic 15
# ===========================================================================


class TestComplexityInvariants:
    """Refactored functions must have cyclomatic complexity < 15."""

    ROOT = Path(__file__).resolve().parent.parent

    @pytest.mark.parametrize(
        "file_rel,function_name",
        [
            ("core/glm_brain.py", "web_search"),
            ("core/glm_brain.py", "_parse_web_search_results"),
            ("core/glm_brain.py", "_parse_tool_call_results"),
            ("core/glm_brain.py", "_create_synthesized_result"),
            ("core/glm_brain.py", "_is_search_tool_call"),
            ("core/glm_brain.py", "_extract_tool_call_items"),
            ("core/provider_router.py", "route"),
            ("core/provider_router.py", "_route_glm"),
            ("core/provider_router.py", "_route_ollama"),
            ("core/provider_router.py", "_route_claude"),
            ("core/provider_router.py", "_route_fallback_no_anthropic"),
            ("core/provider_router.py", "_should_route_gemini"),
            ("core/ledger.py", "wait_for_voice_approval"),
            ("core/ledger.py", "_build_voice_description"),
            ("core/ledger.py", "_voice_speak"),
            ("core/ledger.py", "_voice_listen_once"),
            ("core/ledger.py", "_run_voice_approval_loop"),
            ("core/ledger.py", "_process_voice_response"),
            ("core/ledger.py", "_handle_voice_timeout"),
            ("core/ledger.py", "_finalize_voice_approval"),
            ("core/security_ops.py", "scan_file"),
            ("core/security_ops.py", "_scan_line_for_secrets"),
            ("core/security_ops.py", "_is_test_or_fixture_file"),
            ("core/security_ops.py", "_is_placeholder_text"),
            ("core/security_ops.py", "_is_placeholder_line"),
            ("core/security_ops.py", "_should_skip_line"),
            ("core/security_ops.py", "_relative_path_str"),
            ("core/engineering_intelligence.py", "analyze"),  # DependencyAnalyzer.analyze
        ],
    )
    def test_function_complexity_below_15(self, file_rel, function_name):
        source = (self.ROOT / file_rel).read_text()
        # analyze() appears 3 times — pick the DependencyAnalyzer one (line 287)
        # by checking it's the highest-complexity analyze() in the file.
        if function_name == "analyze":
            # Find the analyze() that's at module-level (not nested in
            # ArchitectureSmellDetector or EngineeringIntelligence — those
            # are complexity 5, the DependencyAnalyzer one is the
            # originally-flagged one).
            complexities = []
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and node.name == "analyze":
                    c = 1
                    for child in ast.walk(node):
                        if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor)):
                            c += 1
                        elif isinstance(child, ast.ExceptHandler):
                            c += 1
                        elif isinstance(child, ast.BoolOp):
                            c += len(child.values) - 1
                        elif isinstance(child, (ast.ListComp, ast.SetComp,
                                                ast.DictComp, ast.GeneratorExp)):
                            c += 1
                    complexities.append(c)
            # All analyze() implementations must be below 15
            for c in complexities:
                assert c < 15, (
                    f"analyze() in {file_rel} has complexity {c} >= 15"
                )
        else:
            c = _compute_complexity(source, function_name)
            assert c < 15, (
                f"{function_name}() in {file_rel} has complexity {c} >= 15. "
                "Refactor target violated."
            )

    def test_refactored_top_5_no_longer_high_or_critical(self):
        """Run EngineeringIntelligence and assert the top 5 refactored
        functions are no longer flagged at high/critical complexity.

        Before COUNCIL-DELTA-V10 these were:
            - core/glm_brain.py web_search() complexity 38
            - core/provider_router.py route() complexity 30
            - core/security_ops.py scan_file() complexity 26
            - core/ledger.py wait_for_voice_approval() complexity 23
            - core/engineering_intelligence.py DependencyAnalyzer.analyze() complexity 19
        After refactor all five must be either < 15 or not flagged at
        high/critical severity.
        """
        import sys
        sys.path.insert(0, str(self.ROOT))
        from core.engineering_intelligence import EngineeringIntelligence
        report = EngineeringIntelligence(project_root=self.ROOT).analyze()

        # Map (file, function) → expected max complexity
        targets = [
            ("core/glm_brain.py", "web_search", 15),
            ("core/provider_router.py", "route", 15),
            ("core/security_ops.py", "scan_file", 15),
            ("core/ledger.py", "wait_for_voice_approval", 15),
            ("core/engineering_intelligence.py", "analyze", 15),
        ]
        # Build a lookup of (file, function) → max complexity in report
        flagged = {}
        for f in report.findings:
            if f.type.value != "complexity":
                continue
            key = (f.file, f.metadata.get("function"))
            flagged[key] = max(
                flagged.get(key, 0),
                f.metadata.get("complexity", 0),
            )
        for file_rel, func, limit in targets:
            actual = flagged.get((file_rel, func), 0)
            assert actual < limit, (
                f"{file_rel}::{func}() has complexity {actual} "
                f">= {limit} (must be < {limit} after refactor)"
            )
