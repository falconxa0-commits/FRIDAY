"""Tests for FridayBrain class."""

import asyncio
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Helpers – patch heavy imports so we can import the brain without real
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFridayBrainInit:
    """Test initialization of FridayBrain."""

    def test_init_default_provider(self, brain_cls, monkeypatch):
        """Default provider should be 'glm' (free-tier default)."""
        monkeypatch.delenv("BRAIN_PROVIDER", raising=False)
        brain = brain_cls()
        assert brain.provider == "glm"

    def test_init_custom_provider(self, brain_cls, monkeypatch):
        """Provider can be overridden via constructor."""
        brain = brain_cls(provider="ollama")
        assert brain.provider == "ollama"

    def test_init_no_api_key(self, brain_cls):
        """Claude client should be None when no API key is set."""
        brain = brain_cls()
        assert brain._claude_client is None

    def test_init_with_memory(self, brain_cls):
        """Memory subsystem should be stored."""
        mem = MagicMock()
        brain = brain_cls(memory=mem)
        assert brain.memory is mem

    def test_init_with_emotions(self, brain_cls):
        """Emotions subsystem should be stored."""
        emo = MagicMock()
        brain = brain_cls(emotions=emo)
        assert brain.emotions is emo

    def test_init_with_personality(self, brain_cls):
        """Personality subsystem should be stored."""
        per = MagicMock()
        brain = brain_cls(personality=per)
        assert brain.personality is per


class TestSkillDiscovery:
    """Test automatic skill discovery."""

    def test_skills_discovered_on_init(self, brain_cls):
        """Skills dict should be populated (even if empty on test machine)."""
        brain = brain_cls()
        assert isinstance(brain.skills, dict)

    def test_skills_are_base_skill_instances(self, brain_cls):
        """Each discovered skill should be a BaseSkill subclass instance."""
        from skills.base import BaseSkill
        brain = brain_cls()
        for name, skill in brain.skills.items():
            assert isinstance(skill, BaseSkill)


class TestToolBuilding:
    """Test tool definition generation."""

    def test_core_tools_present(self, brain_cls):
        """Core tools should always be present."""
        brain = brain_cls()
        tool_names = [t["name"] for t in brain.tools]
        assert "access_universal_service" in tool_names
        assert "search_web" in tool_names
        assert "remember" in tool_names
        assert "recall_memories" in tool_names

    def test_skill_tool_added_when_skills_exist(self, brain_cls):
        """run_skill tool should be added when skills are discovered."""
        brain = brain_cls()
        if brain.skills:
            tool_names = [t["name"] for t in brain.tools]
            assert "run_skill" in tool_names

    def test_tool_schemas_valid(self, brain_cls):
        """Each tool should have name, description, and input_schema."""
        brain = brain_cls()
        for tool in brain.tools:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            schema = tool["input_schema"]
            assert schema["type"] == "object"
            assert "properties" in schema


class TestSystemPrompt:
    """Test system prompt construction."""

    def test_basic_prompt_built(self, brain_cls):
        """_build_system_prompt should return a non-empty string."""
        brain = brain_cls()
        prompt = brain._build_system_prompt("Alice")
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "Alice" in prompt

    def test_emotion_context_injected(self, brain_cls):
        """When emotions detect a mood, it should appear in the prompt."""
        emo = MagicMock()
        emo.current_emotion = "happy"
        per = MagicMock()
        per.adjust_mood.return_value = "Great energy!"
        per.get_personality_context.return_value = "warmth=0.8, wit=0.6"

        brain = brain_cls(emotions=emo, personality=per)
        prompt = brain._build_system_prompt()
        assert isinstance(prompt, str)
        assert "happy" in prompt

    def test_personality_context_injected(self, brain_cls):
        """Personality context should be injected when personality exists."""
        per = MagicMock()
        per.get_personality_context.return_value = "warmth=0.8, wit=0.6"
        brain = brain_cls(personality=per)
        prompt = brain._build_system_prompt()
        assert "warmth=0.8" in prompt


class TestConversationHistory:
    """Test conversation history management."""

    def test_clear_context(self, brain_cls):
        """clear_context should empty history and summaries."""
        brain = brain_cls()
        brain.conversation_history = [{"role": "user", "content": "hi"}]
        brain.summarizer.summaries = ["old summary"]
        brain.clear_context()
        assert brain.conversation_history == []
        assert brain.summarizer.summaries == []

    def test_history_limit(self, brain_cls):
        """history_limit should default to 50."""
        brain = brain_cls()
        assert brain.history_limit == 50


class TestStats:
    """Test get_stats endpoint."""

    def test_stats_returns_dict(self, brain_cls):
        """get_stats should return a well-formed dict."""
        brain = brain_cls()
        stats = brain.get_stats()
        assert isinstance(stats, dict)
        assert "provider" in stats
        assert "model" in stats
        assert "history_length" in stats
        assert "skills_loaded" in stats
        assert "tools_available" in stats

    def test_stats_reflects_provider(self, brain_cls):
        """Stats should reflect the configured provider."""
        brain = brain_cls(provider="gemini")
        stats = brain.get_stats()
        assert stats["provider"] == "gemini"


class TestChatStream:
    """Test chat_stream with mocked Anthropic client."""

    @pytest.mark.asyncio
    async def test_chat_stream_no_provider_fallback(self, brain_cls):
        """When no API key and no local brain, should yield an error message."""
        brain = brain_cls()
        brain.local_brain = MagicMock()
        brain.local_brain.available = AsyncMock(return_value=False)
        with patch("core.brain.ANTHROPIC_API_KEY", None):
            chunks = []
            async for chunk in brain.chat_stream("hello"):
                chunks.append(chunk)
        assert len(chunks) > 0
        assert any("Error" in c or "No brain" in c for c in chunks)

    @pytest.mark.asyncio
    async def test_chat_stream_ollama_provider(self, brain_cls):
        """When provider is 'ollama', should delegate to local_brain."""
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
    async def test_chat_stream_with_memory(self, brain_cls):
        """chat_stream should store user message in memory."""
        mem = MagicMock()
        brain = brain_cls(provider="ollama", memory=mem)
        brain.local_brain = MagicMock()

        async def fake_stream(message):
            yield "Hi"

        brain.local_brain.chat_stream = fake_stream

        async for _ in brain.chat_stream("hello", force_provider="ollama"):
            pass

        mem.store_conversation.assert_called_once_with("user", "hello")

    @pytest.mark.asyncio
    async def test_chat_stream_detects_emotion(self, brain_cls):
        """chat_stream should call emotions.detect_emotion."""
        emo = MagicMock()
        brain = brain_cls(provider="ollama", emotions=emo)
        brain.local_brain = MagicMock()

        async def fake_stream(message):
            yield "Hi"

        brain.local_brain.chat_stream = fake_stream

        async for _ in brain.chat_stream("I'm happy", force_provider="ollama"):
            pass

        emo.detect_emotion.assert_called_once_with("I'm happy")
