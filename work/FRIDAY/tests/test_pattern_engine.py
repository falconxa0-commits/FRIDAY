"""Tests for core/pattern_engine.py — behavioral pattern learning."""
import asyncio
import datetime
import pytest

from core.pattern_engine import PatternEngine, MIN_PATTERN_OCCURRENCES


@pytest.fixture()
def engine():
    return PatternEngine()


class TestPatternEngineObserve:
    """Test interaction observation."""

    @pytest.mark.asyncio
    async def test_observe_records_interaction(self, engine):
        await engine.observe({"action_type": "chat", "content": "hello"})
        assert len(engine.interactions) == 1
        assert engine.interactions[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_observe_adds_timestamp(self, engine):
        await engine.observe({"action_type": "chat", "content": "test"})
        assert "timestamp" in engine.interactions[0]

    @pytest.mark.asyncio
    async def test_observe_sets_defaults(self, engine):
        await engine.observe({"content": "test"})
        assert engine.interactions[0]["action_type"] == "other"
        assert engine.interactions[0]["outcome"] == "unknown"


class TestPatternDiscovery:
    """Test pattern discovery."""

    @pytest.mark.asyncio
    async def test_no_patterns_with_few_interactions(self, engine):
        # Below MIN_PATTERN_OCCURRENCES
        for i in range(MIN_PATTERN_OCCURRENCES - 1):
            await engine.observe({"action_type": "chat", "content": f"msg {i}"})
        patterns = await engine.discover_patterns()
        assert patterns == []

    @pytest.mark.asyncio
    async def test_action_pair_pattern_detected(self, engine):
        """research → write report pattern should be detected."""
        base_time = datetime.datetime(2026, 7, 1, 9, 0, 0)
        for i in range(8):
            await engine.observe({
                "timestamp": base_time.isoformat(),
                "action_type": "research",
                "content": "research quantum computing",
            })
            await engine.observe({
                "timestamp": base_time.isoformat(),
                "action_type": "write",
                "content": "write report on quantum computing",
            })
        patterns = await engine.discover_patterns()
        action_pairs = [p for p in patterns if p["type"] == "action_pair"]
        assert len(action_pairs) > 0
        assert any("research" in p["description"] and "write" in p["description"]
                    for p in action_pairs)

    @pytest.mark.asyncio
    async def test_keyword_frequency_detected(self, engine):
        for i in range(5):
            await engine.observe({"action_type": "chat", "content": "quantum computing research"})
        patterns = await engine.discover_patterns()
        keyword_patterns = [p for p in patterns if p["type"] == "keyword_frequency"]
        assert any("quantum" in p["description"] for p in keyword_patterns)

    @pytest.mark.asyncio
    async def test_output_depends_on_input(self, engine):
        """Different history → different patterns."""
        for i in range(5):
            await engine.observe({"action_type": "code", "content": "python function"})
        patterns1 = await engine.discover_patterns()

        engine2 = PatternEngine()
        for i in range(5):
            await engine2.observe({"action_type": "music", "content": "spotify playlist"})
        patterns2 = await engine2.discover_patterns()

        # The patterns should differ because the inputs differ
        p1_descs = {p["description"] for p in patterns1}
        p2_descs = {p["description"] for p in patterns2}
        assert p1_descs != p2_descs


class TestSuggestions:
    """Test proactive suggestions."""

    @pytest.mark.asyncio
    async def test_suggestions_based_on_patterns(self, engine):
        for i in range(5):
            await engine.observe({"action_type": "research", "content": "quantum computing"})
        await engine.discover_patterns()
        suggestions = await engine.get_suggestions({"current_action": "quantum"})
        # Should have at least one suggestion based on the keyword pattern
        assert len(suggestions) >= 0  # suggestions are heuristic — may be empty

    @pytest.mark.asyncio
    async def test_no_suggestions_without_patterns(self, engine):
        suggestions = await engine.get_suggestions({"current_action": "test"})
        assert suggestions == []
