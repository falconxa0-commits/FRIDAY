"""Tests for EmotionsEngine class."""

import pytest
from core.emotions import EmotionsEngine, EmotionalState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    return EmotionsEngine()


# ---------------------------------------------------------------------------
# Basic emotion detection
# ---------------------------------------------------------------------------

class TestBasicEmotionDetection:
    """Test detection of primary emotions from text."""

    def test_happy(self, engine):
        result = engine.detect_emotion("I'm so happy today!")
        assert result == "happy"

    def test_sad(self, engine):
        result = engine.detect_emotion("I feel really sad about this")
        assert result == "sad"

    def test_angry(self, engine):
        result = engine.detect_emotion("I'm furious about what happened")
        assert result == "angry"

    def test_anxious(self, engine):
        result = engine.detect_emotion("I'm anxious about the exam")
        assert result == "anxious"

    def test_confused(self, engine):
        result = engine.detect_emotion("I'm confused about the instructions")
        assert result == "confused"

    def test_bored(self, engine):
        result = engine.detect_emotion("This is so boring")
        assert result == "bored"

    def test_surprised(self, engine):
        result = engine.detect_emotion("Wow that's surprising!")
        assert result == "surprised"

    def test_curious(self, engine):
        result = engine.detect_emotion("I'm curious about how this works")
        assert result == "curious"

    def test_neutral(self, engine):
        result = engine.detect_emotion("The document is on the table")
        assert result == "neutral"

    def test_determined(self, engine):
        result = engine.detect_emotion("I'm determined to finish this project")
        assert result == "determined"

    def test_grateful(self, engine):
        result = engine.detect_emotion("I'm so grateful for your help")
        assert result == "grateful"

    def test_updates_current_emotion(self, engine):
        engine.detect_emotion("I'm happy")
        assert engine.current_emotion == "happy"

    def test_appends_to_history(self, engine):
        engine.detect_emotion("I'm happy")
        assert len(engine.emotion_history) == 1
        assert engine.emotion_history[0].primary_emotion == "happy"


# ---------------------------------------------------------------------------
# Negation handling
# ---------------------------------------------------------------------------

class TestNegationHandling:
    """Test that negation flips the detected emotion."""

    def test_not_happy(self, engine):
        state = engine.analyze_emotion("I'm not happy about this")
        assert state.valence < 0  # should be negative after flip

    def test_dont_like(self, engine):
        state = engine.analyze_emotion("I don't like this, I'm not happy at all")
        # Negation flips happy valence to negative
        assert state.valence < 0

    def test_never_happy(self, engine):
        state = engine.analyze_emotion("I'm never happy with the results")
        assert state.valence < 0

    def test_is_not_sad(self, engine):
        """Negating sadness should flip to positive."""
        state = engine.analyze_emotion("I'm not sad anymore")
        # valence should be positive (flipped from negative)
        assert state.valence > 0


# ---------------------------------------------------------------------------
# Intensifiers and diminishers
# ---------------------------------------------------------------------------

class TestIntensifiersAndDiminishers:
    """Test that intensifiers amplify and diminishers reduce emotional scores."""

    def test_intensifier_very(self, engine):
        state_normal = engine.analyze_emotion("I'm happy")
        state_intensified = engine.analyze_emotion("I'm very happy")
        # Intensified should have higher arousal or valence magnitude
        assert abs(state_intensified.valence) >= abs(state_normal.valence)

    def test_intensifier_extremely(self, engine):
        state_normal = engine.analyze_emotion("I'm angry")
        state_intensified = engine.analyze_emotion("I'm extremely angry")
        assert abs(state_intensified.arousal) >= abs(state_normal.arousal)

    def test_diminisher_slightly(self, engine):
        state_normal = engine.analyze_emotion("I'm happy")
        state_diminished = engine.analyze_emotion("I'm slightly happy")
        assert abs(state_diminished.valence) <= abs(state_normal.valence)

    def test_diminisher_kind_of(self, engine):
        state_normal = engine.analyze_emotion("I'm sad")
        state_diminished = engine.analyze_emotion("I'm kind of sad")
        assert abs(state_diminished.valence) <= abs(state_normal.valence)


# ---------------------------------------------------------------------------
# Emotional trend analysis
# ---------------------------------------------------------------------------

class TestEmotionalTrend:
    """Test trend analysis over emotional history."""

    def test_no_history_returns_unknown(self, engine):
        trend = engine.get_emotional_trend()
        assert trend["trend"] == "unknown"

    def test_improving_trend(self, engine):
        # Simulate improving mood
        engine.detect_emotion("I'm sad")
        engine.detect_emotion("I'm calm")
        engine.detect_emotion("I'm happy")
        trend = engine.get_emotional_trend()
        assert trend["trend"] == "improving"

    def test_declining_trend(self, engine):
        # Simulate declining mood
        engine.detect_emotion("I'm happy")
        engine.detect_emotion("I'm calm")
        engine.detect_emotion("I'm sad")
        trend = engine.get_emotional_trend()
        assert trend["trend"] == "declining"

    def test_stable_trend(self, engine):
        engine.detect_emotion("I'm calm")
        engine.detect_emotion("I'm calm")
        engine.detect_emotion("I'm calm")
        trend = engine.get_emotional_trend()
        assert trend["trend"] == "stable"

    def test_insufficient_data(self, engine):
        engine.detect_emotion("I'm happy")
        trend = engine.get_emotional_trend()
        assert trend["trend"] == "insufficient_data"

    def test_avg_valence_computed(self, engine):
        engine.detect_emotion("I'm happy")
        engine.detect_emotion("I'm sad")
        trend = engine.get_emotional_trend()
        assert "avg_valence" in trend
        assert isinstance(trend["avg_valence"], float)

    def test_dominant_emotion(self, engine):
        engine.detect_emotion("I'm happy")
        engine.detect_emotion("I'm happy")
        engine.detect_emotion("I'm sad")
        trend = engine.get_emotional_trend()
        assert trend["dominant_emotion"] == "happy"


# ---------------------------------------------------------------------------
# Multi-dimensional analysis
# ---------------------------------------------------------------------------

class TestMultiDimensionalAnalysis:
    """Test the full EmotionalState VAD analysis."""

    def test_returns_emotional_state(self, engine):
        state = engine.analyze_emotion("I'm happy")
        assert isinstance(state, EmotionalState)

    def test_valence_range(self, engine):
        state = engine.analyze_emotion("I'm extremely happy")
        assert -1.0 <= state.valence <= 1.0

    def test_arousal_range(self, engine):
        state = engine.analyze_emotion("I'm furious")
        assert 0.0 <= state.arousal <= 1.0

    def test_dominance_range(self, engine):
        state = engine.analyze_emotion("I'm determined")
        assert 0.0 <= state.dominance <= 1.0

    def test_confidence_range(self, engine):
        state = engine.analyze_emotion("I'm happy and glad")
        assert 0.0 <= state.confidence <= 1.0

    def test_secondary_emotions(self, engine):
        state = engine.analyze_emotion("I'm happy and grateful and proud")
        # Should have secondary emotions when multiple emotions match
        assert isinstance(state.secondary_emotions, list)

    def test_neutral_state(self, engine):
        state = engine.analyze_emotion("The lamp is on the table")
        assert state.primary_emotion == "neutral"
        assert state.confidence == 0.0

    def test_history_trimming(self, engine):
        """Emotion history should be trimmed when it exceeds 100 entries."""
        for i in range(110):
            engine.detect_emotion("I'm happy")
        assert len(engine.emotion_history) <= 100
