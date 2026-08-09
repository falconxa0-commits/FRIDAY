"""Tests for the WAVE2-ARCH brain.py decomposition.

Verifies that the extracted modules (``ProviderRouter``, ``ContextManager``,
``CreativeRouter``) work both in isolation and through the ``FridayBrain``
façade — and that the public API of ``FridayBrain`` is unchanged.

Coverage
--------
1. ProviderRouter delegation to GLM / Claude / fallback
2. ContextManager append / branch / switch
3. CreativeRouter detection (positive + negative)
4. FridayBrain backward-compat (existing API still works through the
   new collaborator objects)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures — same patching strategy as tests/test_brain.py so we can import
# FridayBrain without real Anthropic / Gemini / Tavily credentials.
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


# ===========================================================================
# ProviderRouter
# ===========================================================================


def _make_mock_brain_for_router(glm_available=True, claude_client=None,
                                local_available=False, gemini_brain=None,
                                memory=None):
    """Construct a minimal mock brain satisfying ProviderRouter's needs."""
    brain = MagicMock()
    brain.logger = MagicMock()
    brain.memory = memory
    brain.tools = []  # no tools → simple streaming paths
    brain.history_limit = 50
    brain.claude_model = "claude-sonnet-4-20250514"
    brain.conversation_history = []
    brain.claude_client = claude_client
    brain.gemini_brain = gemini_brain

    # GLM brain
    brain.glm_brain = MagicMock()
    brain.glm_brain.available.return_value = glm_available

    async def _glm_chat_stream(msg, system_prompt=None):
        yield "glm-chunk-1 "
        yield "glm-chunk-2"

    brain.glm_brain.chat_stream = _glm_chat_stream

    # Local brain
    brain.local_brain = MagicMock()
    brain.local_brain.available = AsyncMock(return_value=local_available)

    async def _local_chat_stream(msg):
        yield "local-chunk"

    brain.local_brain.chat_stream = _local_chat_stream

    # RAG injection returns empty string (no memories)
    brain._inject_rag_context = AsyncMock(return_value="")

    # GLM tool-calling loop (unused when tools=[] but referenced)
    async def _glm_stream_with_tools(messages, tools, system_prompt):
        yield "glm-tools-chunk"
        return

    brain._glm_stream_with_tools = _glm_stream_with_tools

    # Claude tool executor (unused when no tool calls)
    brain._execute_tool = AsyncMock(return_value='{"status": "ok"}')

    # Summarizer mock
    summarizer = MagicMock()
    summarizer.summarize_older_messages = AsyncMock(side_effect=lambda msgs: msgs)
    brain.summarizer = summarizer

    return brain


class TestProviderRouterGLM:
    """ProviderRouter.route() should delegate to GLM when provider='glm'."""

    @pytest.mark.asyncio
    async def test_route_glm_delegates_to_glm_brain(self):
        from core.provider_router import ProviderRouter

        brain = _make_mock_brain_for_router(glm_available=True)
        router = ProviderRouter()

        chunks = []
        async for chunk in router.route("glm", "hello", "system-prompt", [], brain):
            chunks.append(chunk)

        # GLM's chat_stream yields "glm-chunk-1 " and "glm-chunk-2"
        assert "glm-chunk-1" in "".join(chunks)
        assert "glm-chunk-2" in "".join(chunks)

        # User message should be persisted to conversation_history
        assert brain.conversation_history[0] == {"role": "user", "content": "hello"}

    @pytest.mark.asyncio
    async def test_route_glm_appends_assistant_response_to_history(self):
        from core.provider_router import ProviderRouter

        brain = _make_mock_brain_for_router(glm_available=True)
        router = ProviderRouter()

        async for _ in router.route("glm", "hello", "sys", [], brain):
            pass

        # Should have both user + assistant messages in history
        roles = [m["role"] for m in brain.conversation_history]
        assert "user" in roles
        assert "assistant" in roles


class TestProviderRouterClaude:
    """ProviderRouter.route() should delegate to Claude when provider='claude'."""

    @pytest.mark.asyncio
    async def test_route_claude_delegates_to_claude_client(self):
        from core.provider_router import ProviderRouter

        # Build a mock Claude streaming client
        claude_client = MagicMock()

        class _FakeStream:
            def __init__(self):
                self._events = [
                    MagicMock(type="content_block_delta",
                              delta=MagicMock(type="text_delta", text="Hello ")),
                    MagicMock(type="content_block_delta",
                              delta=MagicMock(type="text_delta", text="world!")),
                ]
                self._idx = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._events):
                    raise StopAsyncIteration
                ev = self._events[self._idx]
                self._idx += 1
                return ev

            async def get_final_message(self):
                final = MagicMock()
                final.content = []  # no tool calls
                return final

        claude_client.messages.stream = MagicMock(return_value=_FakeStream())

        brain = _make_mock_brain_for_router(
            glm_available=False,
            claude_client=claude_client,
        )

        # Patch ANTHROPIC_API_KEY at the module level so the fallback
        # branch is not triggered.
        with patch("core.provider_router.ANTHROPIC_API_KEY", "fake-key"):
            router = ProviderRouter()
            chunks = []
            async for chunk in router.route("claude", "hi", "sys", [], brain):
                chunks.append(chunk)

        assert "Hello " in "".join(chunks)
        assert "world!" in "".join(chunks)
        claude_client.messages.stream.assert_called_once()


class TestProviderRouterFallback:
    """ProviderRouter.route() should fall back to GLM when Claude unavailable."""

    @pytest.mark.asyncio
    async def test_route_falls_back_to_glm_when_claude_unavailable(self):
        """When provider='claude' but ANTHROPIC_API_KEY is unset and GLM is up, use GLM."""
        from core.provider_router import ProviderRouter

        brain = _make_mock_brain_for_router(
            glm_available=True,
            claude_client=None,  # No Claude client
        )

        # ANTHROPIC_API_KEY defaults to None in the isolated env
        router = ProviderRouter()
        chunks = []
        async for chunk in router.route("claude", "hi", "sys", [], brain):
            chunks.append(chunk)

        # Should have routed to GLM (not yielded an error)
        full = "".join(chunks)
        assert "glm-chunk" in full
        assert "Error" not in full


# ===========================================================================
# ContextManager
# ===========================================================================


class TestContextManagerHistory:
    """ContextManager.append() + get_history() roundtrip."""

    def test_append_single_message(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        cm.append("user", "hello")

        history = cm.get_history()
        assert history == [{"role": "user", "content": "hello"}]

    def test_append_multiple_messages_preserves_order(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        cm.append("user", "first")
        cm.append("assistant", "second")
        cm.append("user", "third")

        history = cm.get_history()
        assert len(history) == 3
        assert history[0]["content"] == "first"
        assert history[1]["content"] == "second"
        assert history[2]["content"] == "third"

    def test_clear_empties_history_and_summaries(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        cm.append("user", "msg")
        cm.summarizer.summaries = ["old summary"]
        assert len(cm.get_history()) == 1

        cm.clear()
        assert cm.get_history() == []
        assert cm.summarizer.summaries == []


class TestContextManagerBranching:
    """ContextManager.branch() + switch_branch()."""

    @pytest.mark.asyncio
    async def test_branch_creates_new_branch_with_copied_history(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        cm.append("user", "msg-1")
        cm.append("assistant", "resp-1")
        cm.append("user", "msg-2")
        cm.append("assistant", "resp-2")

        # Branch at index 1 (copies messages 0 and 1)
        branch_id = await cm.branch(1)

        assert branch_id.startswith("branch_")
        assert branch_id in cm._branches

        branch_history = cm._branches[branch_id]["history"]
        assert len(branch_history) == 2
        assert branch_history[0]["content"] == "msg-1"
        assert branch_history[1]["content"] == "resp-1"

        # Active history should now BE the branch (switched on creation)
        assert cm._active_branch_id == branch_id
        assert len(cm.history) == 2

    @pytest.mark.asyncio
    async def test_switch_branch_changes_active_history(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        cm.append("user", "orig-1")
        cm.append("assistant", "orig-2")

        # Create branch at index 0 (copies 1 message)
        branch_id = await cm.branch(0)

        # On branch: 1 message
        assert len(cm.history) == 1

        # Append to branch
        cm.append("user", "branch-extra")
        assert len(cm.history) == 2
        assert cm.history[1]["content"] == "branch-extra"

        # Switch back to main
        ok = cm.switch_branch("main")
        assert ok is True
        assert cm._active_branch_id is None
        assert len(cm.history) == 2  # original 2 messages
        assert cm.history[0]["content"] == "orig-1"

        # Switch back to branch — should have 2 messages (orig + extra)
        ok = cm.switch_branch(branch_id)
        assert ok is True
        assert len(cm.history) == 2
        assert cm.history[1]["content"] == "branch-extra"

    @pytest.mark.asyncio
    async def test_switch_branch_returns_false_for_unknown(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        assert cm.switch_branch("nonexistent") is False

    @pytest.mark.asyncio
    async def test_delete_branch_removes_it(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        cm.append("user", "msg")
        branch_id = await cm.branch(0)

        ok = cm.delete_branch(branch_id)
        assert ok is True
        assert branch_id not in cm._branches

    @pytest.mark.asyncio
    async def test_get_branches_includes_main(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        cm.append("user", "msg")
        branch_id = await cm.branch(0)

        branches = cm.get_branches()
        branch_ids = [b["branch_id"] for b in branches]
        assert branch_id in branch_ids
        assert "main" in branch_ids


# ===========================================================================
# CreativeRouter
# ===========================================================================


class TestCreativeRouterDetect:
    """CreativeRouter.detect() pattern matching."""

    def test_detect_image_request(self):
        from core.creative_router import CreativeRouter

        router = CreativeRouter(universal_connector=MagicMock())
        route = router.detect("generate an image of the Lagos skyline at sunset")

        assert route is not None
        assert route["type"] == "image"
        assert "lagos" in route["prompt"].lower()
        assert "sunset" in route["prompt"].lower()

    def test_detect_video_request(self):
        from core.creative_router import CreativeRouter

        router = CreativeRouter(universal_connector=MagicMock())
        route = router.detect("create a video of a drone shot over Victoria Island")

        assert route is not None
        assert route["type"] == "video"
        assert "drone" in route["prompt"].lower() or "victoria" in route["prompt"].lower()

    def test_detect_returns_none_for_normal_chat(self):
        from core.creative_router import CreativeRouter

        router = CreativeRouter(universal_connector=MagicMock())
        assert router.detect("what is the weather in Lagos?") is None
        assert router.detect("hello there") is None
        assert router.detect("explain how transformers work") is None

    def test_detect_different_prompts_produce_different_routes(self):
        from core.creative_router import CreativeRouter

        router = CreativeRouter(universal_connector=MagicMock())
        r1 = router.detect("generate an image of a cat")
        r2 = router.detect("generate an image of a dog")
        assert r1["prompt"] != r2["prompt"]


class TestCreativeRouterHandle:
    """CreativeRouter.handle() dispatch to UniversalConnector."""

    @pytest.mark.asyncio
    async def test_handle_image_routes_to_image_gen(self):
        captured = []

        connector = MagicMock()

        async def _exec(service, action, params, **kw):
            captured.append({"service": service, "action": action, "params": params})
            return {
                "status": "success",
                "receipt": {"image_url": "https://example.com/cat.png"},
            }

        connector.execute_action = _exec

        from core.creative_router import CreativeRouter
        router = CreativeRouter(universal_connector=connector)

        chunks = []
        async for chunk in router.handle({"type": "image", "prompt": "a cat"}):
            chunks.append(chunk)

        full = "".join(chunks)
        assert "https://example.com/cat.png" in full
        assert len(captured) == 1
        assert captured[0]["service"] == "image_gen"
        assert captured[0]["action"] == "generate_image"

    @pytest.mark.asyncio
    async def test_handle_failure_yields_error(self):
        connector = MagicMock()

        async def _exec(service, action, params, **kw):
            return {"status": "error", "message": "model offline"}

        connector.execute_action = _exec

        from core.creative_router import CreativeRouter
        router = CreativeRouter(universal_connector=connector)

        chunks = []
        async for chunk in router.handle({"type": "image", "prompt": "a cat"}):
            chunks.append(chunk)

        full = "".join(chunks)
        assert "failed" in full.lower() or "error" in full.lower()


# ===========================================================================
# Backward compatibility — FridayBrain public API
# ===========================================================================


class TestFridayBrainBackwardCompat:
    """FridayBrain public API still works through the new collaborators."""

    def test_brain_exposes_context_manager(self, brain_cls):
        brain = brain_cls()
        from core.context_manager import ContextManager
        assert isinstance(brain.context, ContextManager)

    def test_brain_exposes_provider_router(self, brain_cls):
        brain = brain_cls()
        from core.provider_router import ProviderRouter
        assert isinstance(brain.router, ProviderRouter)

    def test_brain_exposes_creative_router(self, brain_cls):
        brain = brain_cls()
        from core.creative_router import CreativeRouter
        assert isinstance(brain.creative_router, CreativeRouter)

    def test_conversation_history_delegates_to_context(self, brain_cls):
        """brain.conversation_history should be the same list as context.history."""
        brain = brain_cls()
        brain.conversation_history.append({"role": "user", "content": "hi"})
        assert brain.context.history[-1] == {"role": "user", "content": "hi"}
        # And vice versa
        brain.context.append("assistant", "hello")
        assert brain.conversation_history[-1] == {"role": "assistant", "content": "hello"}

    def test_summarizer_delegates_to_context(self, brain_cls):
        """brain.summarizer should be the same object as context.summarizer."""
        brain = brain_cls()
        brain.summarizer.summaries = ["test-summary"]
        assert brain.context.summarizer.summaries == ["test-summary"]

    def test_detect_creative_route_still_works(self, brain_cls):
        """brain._detect_creative_route should still detect image/video requests."""
        brain = brain_cls()
        route = brain._detect_creative_route("generate an image of a sunset")
        assert route is not None
        assert route["type"] == "image"

        # Non-creative messages return None
        assert brain._detect_creative_route("what's the weather?") is None

    @pytest.mark.asyncio
    async def test_branch_conversation_still_works(self, brain_cls):
        """brain.branch_conversation should still create branches via context."""
        brain = brain_cls()
        brain.conversation_history = [
            {"role": "user", "content": "msg-1"},
            {"role": "assistant", "content": "resp-1"},
        ]

        result = await brain.branch_conversation("0")
        assert result["branch_id"].startswith("branch_")
        assert result["message_count"] == 1
        assert result["branch_id"] in brain._branches

    @pytest.mark.asyncio
    async def test_clear_context_still_works(self, brain_cls):
        """brain.clear_context should empty history + summaries."""
        brain = brain_cls()
        brain.conversation_history = [{"role": "user", "content": "hi"}]
        brain.summarizer.summaries = ["old summary"]

        brain.clear_context()
        assert brain.conversation_history == []
        assert brain.summarizer.summaries == []

    @pytest.mark.asyncio
    async def test_chat_stream_ollama_provider_still_works(self, brain_cls):
        """chat_stream with provider='ollama' should still delegate to local_brain."""
        brain = brain_cls(provider="ollama")
        brain.local_brain = MagicMock()

        async def fake_stream(message):
            yield "Hello from local"

        brain.local_brain.chat_stream = fake_stream

        chunks = []
        async for chunk in brain.chat_stream("hello", force_provider="ollama"):
            chunks.append(chunk)

        assert "Hello from local" in "".join(chunks)

    @pytest.mark.asyncio
    async def test_chat_stream_creative_route_short_circuits(self, brain_cls):
        """A creative request should route to the creative router, not the LLM."""
        brain = brain_cls(provider="ollama")

        # Mock the connector so creative_router.handle returns a deterministic chunk
        captured = []

        async def _exec(service, action, params, **kw):
            captured.append({"service": service, "action": action, "params": params})
            return {
                "status": "success",
                "receipt": {"image_url": "https://example.com/x.png"},
            }

        brain.connector.execute_action = _exec

        chunks = []
        async for chunk in brain.chat_stream("generate an image of a cat"):
            chunks.append(chunk)

        full = "".join(chunks)
        assert "https://example.com/x.png" in full
        assert len(captured) == 1
        assert captured[0]["service"] == "image_gen"

    def test_get_stats_still_works(self, brain_cls):
        """get_stats should still return the expected dict."""
        brain = brain_cls()
        stats = brain.get_stats()
        assert "provider" in stats
        assert "history_length" in stats
        assert "summary_count" in stats
        assert "skills_loaded" in stats
        assert "tools_available" in stats


# ===========================================================================
# Decomposition invariants
# ===========================================================================


class TestDecompositionInvariants:
    """The decomposition should not duplicate state or break contracts."""

    def test_brain_router_uses_brain_glm_brain_reference(self, brain_cls):
        """ProviderRouter should reference the same glm_brain as the brain."""
        brain = brain_cls()
        # The router stores the glm_brain passed at construction time
        assert brain.router.glm_brain is brain.glm_brain

    def test_brain_creative_router_uses_brain_connector(self, brain_cls):
        """CreativeRouter should use the brain's UniversalConnector."""
        brain = brain_cls()
        assert brain.creative_router.connector is brain.connector

    def test_context_manager_history_is_brain_conversation_history(self, brain_cls):
        """Mutating brain.conversation_history should mutate context.history."""
        brain = brain_cls()
        # Initially the same list reference
        assert brain.conversation_history is brain.context.history
