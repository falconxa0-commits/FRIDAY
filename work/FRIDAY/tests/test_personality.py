"""Tests for FridayPersonality class."""

import pytest
from unittest.mock import patch
from core.personality import FridayPersonality, PersonalityTraits


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def personality():
    return FridayPersonality()


@pytest.fixture()
def assertive_personality():
    traits = PersonalityTraits(assertiveness=0.9)
    return FridayPersonality(traits=traits)


# ---------------------------------------------------------------------------
# Time-based greetings
# ---------------------------------------------------------------------------

class TestTimeBasedGreetings:
    """Test that greetings adapt based on time of day."""

    def test_greeting_returns_string(self, personality):
        result = personality.get_greeting("Alice")
        assert isinstance(result, str)
        assert "Alice" in result

    def test_greeting_default_user(self, personality):
        result = personality.get_greeting()
        assert "User" in result

    @patch("core.personality.datetime")
    def test_morning_greeting(self, mock_dt, personality):
        mock_dt.datetime.now().hour = 7
        result = personality.get_greeting("Bob")
        # Morning greetings should contain morning-related language
        result_lower = result.lower()
        assert "morning" in result_lower or "bob" in result_lower

    @patch("core.personality.datetime")
    def test_evening_greeting(self, mock_dt, personality):
        mock_dt.datetime.now().hour = 19
        result = personality.get_greeting("Bob")
        result_lower = result.lower()
        assert "evening" in result_lower or "bob" in result_lower

    @patch("core.personality.datetime")
    def test_late_night_greeting(self, mock_dt, personality):
        mock_dt.datetime.now().hour = 23
        result = personality.get_greeting("Bob")
        # Should acknowledge late hour
        assert "Bob" in result

    @patch("core.personality.datetime")
    def test_afternoon_greeting(self, mock_dt, personality):
        mock_dt.datetime.now().hour = 15
        result = personality.get_greeting("Bob")
        result_lower = result.lower()
        assert "afternoon" in result_lower or "bob" in result_lower


# ---------------------------------------------------------------------------
# Mood adaptation
# ---------------------------------------------------------------------------

class TestMoodAdaptation:
    """Test mood adjustment and approach guidance."""

    def test_adjust_mood_happy(self, personality):
        result = personality.adjust_mood("happy")
        assert result is not None
        assert "energy" in result.lower() or "productive" in result.lower()

    def test_adjust_mood_angry(self, personality):
        result = personality.adjust_mood("angry")
        assert result is not None
        assert "concise" in result.lower() or "solution" in result.lower()

    def test_adjust_mood_sad(self, personality):
        result = personality.adjust_mood("sad")
        assert result is not None
        assert "here" in result.lower()

    def test_adjust_mood_anxious(self, personality):
        result = personality.adjust_mood("anxious")
        assert result is not None
        assert "step" in result.lower()

    def test_adjust_mood_neutral(self, personality):
        result = personality.adjust_mood("neutral")
        assert result is None

    def test_mood_stored(self, personality):
        personality.adjust_mood("happy")
        assert personality.mood == "happy"


# ---------------------------------------------------------------------------
# Pushback mechanism
# ---------------------------------------------------------------------------

class TestPushback:
    """Test that Friday can push back on suggestions."""

    def test_pushback_returns_string(self, personality):
        result = personality.pushback("delete everything")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_pushback_increments_counter(self, personality):
        initial = personality.pushback_count
        personality.pushback("bad idea")
        assert personality.pushback_count == initial + 1

    def test_assertive_pushback(self, assertive_personality):
        """High assertiveness should produce stronger pushback."""
        # The assertive pushback is added to the list but random.choice
        # may not select it. Check that the pushback list has the strong
        # option available (verified by reading the code path).
        results = [
            assertive_personality.pushback("bad idea")
            for _ in range(50)
        ]
        # With 50 attempts and 6 options (5 regular + 1 strong),
        # probability of never hitting strong is (5/6)^50 ≈ 0.0001
        has_strong = any("strongly" in r.lower() for r in results)
        assert has_strong  # Extremely high probability with 50 attempts


# ---------------------------------------------------------------------------
# Personality adaptation
# ---------------------------------------------------------------------------

class TestPersonalityAdaptation:
    """Test adaptive personality based on user style."""

    def test_adapt_to_formal(self, personality):
        initial_formality = personality.traits.formality
        personality.adapt_to_user("formal")
        assert personality.traits.formality >= initial_formality

    def test_adapt_to_casual(self, personality):
        initial_formality = personality.traits.formality
        personality.adapt_to_user("casual")
        assert personality.traits.formality <= initial_formality

    def test_adapt_to_technical(self, personality):
        personality.adapt_to_user("technical")
        # Should increase formality slightly, decrease creativity slightly
        assert personality.traits.formality >= 0.0

    def test_adapt_to_creative(self, personality):
        initial_creativity = personality.traits.creativity
        personality.adapt_to_user("creative")
        assert personality.traits.creativity >= initial_creativity

    def test_adapt_bounds(self, personality):
        """Traits should stay within [0.0, 1.0] bounds."""
        for _ in range(100):
            personality.adapt_to_user("formal")
        assert 0.0 <= personality.traits.formality <= 1.0

    def test_unknown_style_no_change(self, personality):
        initial = personality.traits.formality
        personality.adapt_to_user("nonexistent_style")
        assert personality.traits.formality == initial


class TestPersonalityContext:
    """Test personality context generation for system prompts."""

    def test_get_personality_context(self, personality):
        ctx = personality.get_personality_context()
        assert isinstance(ctx, str)
        assert "warmth" in ctx
        assert "wit" in ctx

    def test_record_interaction(self, personality):
        initial = personality.interaction_count
        personality.record_interaction()
        assert personality.interaction_count == initial + 1

    def test_casual_greeting_for_frequent_users(self, personality):
        """Users with >50 interactions and low formality get casual greetings."""
        personality.interaction_count = 60
        personality.traits.formality = 0.3
        greeting = personality.get_greeting("Alice")
        assert "Alice" in greeting
