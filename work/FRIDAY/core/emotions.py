import logging
import re
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("EmotionsEngine")


@dataclass
class EmotionalState:
    """Multi-dimensional emotional state representation."""
    valence: float = 0.0      # -1.0 (negative) to +1.0 (positive)
    arousal: float = 0.0      # 0.0 (calm) to 1.0 (excited)
    dominance: float = 0.5    # 0.0 (submissive) to 1.0 (dominant)
    primary_emotion: str = "neutral"
    confidence: float = 0.0
    secondary_emotions: List[str] = field(default_factory=list)


class EmotionsEngine:
    """Advanced emotion detection with multi-dimensional analysis."""

    def __init__(self):
        self.current_emotion = "neutral"
        self.emotional_state = EmotionalState()
        self.emotion_history: List[EmotionalState] = []

        # Extended emotion lexicon with valence/arousal/dominance values
        self.emotion_lexicon = {
            # Positive emotions
            "happy": {
                "valence": 0.8, "arousal": 0.6, "dominance": 0.7,
                "words": [
                    "happy", "glad", "pleased", "delighted", "joyful",
                    "cheerful", "great", "awesome", "wonderful", "amazing",
                    "fantastic", "excellent", "perfect", "love", "excited",
                    "thrilled",
                ],
            },
            "grateful": {
                "valence": 0.7, "arousal": 0.4, "dominance": 0.6,
                "words": [
                    "grateful", "thankful", "appreciate", "blessed", "thank",
                ],
            },
            "proud": {
                "valence": 0.8, "arousal": 0.5, "dominance": 0.8,
                "words": [
                    "proud", "accomplished", "achieved", "success", "nailed",
                ],
            },
            "relieved": {
                "valence": 0.5, "arousal": 0.2, "dominance": 0.5,
                "words": ["relieved", "finally", "phew", "glad it's over"],
            },
            "calm": {
                "valence": 0.4, "arousal": 0.1, "dominance": 0.5,
                "words": [
                    "calm", "peaceful", "relaxed", "serene", "chill", "zen",
                ],
            },
            # Negative emotions
            "sad": {
                "valence": -0.7, "arousal": 0.2, "dominance": 0.3,
                "words": [
                    "sad", "unhappy", "depressed", "down", "blue",
                    "miserable", "heartbroken", "disappointed", "devastated",
                ],
            },
            "angry": {
                "valence": -0.8, "arousal": 0.9, "dominance": 0.8,
                "words": [
                    "angry", "mad", "furious", "rage", "annoyed",
                    "frustrated", "irritated", "pissed", "hate", "livid",
                ],
            },
            "anxious": {
                "valence": -0.5, "arousal": 0.7, "dominance": 0.3,
                "words": [
                    "anxious", "worried", "nervous", "stressed",
                    "panicking", "scared", "afraid", "terrified",
                    "overwhelmed",
                ],
            },
            "confused": {
                "valence": -0.3, "arousal": 0.4, "dominance": 0.3,
                "words": [
                    "confused", "lost", "unsure", "uncertain", "what",
                    "don't understand", "bewildered",
                ],
            },
            "bored": {
                "valence": -0.2, "arousal": 0.1, "dominance": 0.4,
                "words": [
                    "bored", "boring", "dull", "tedious", "meh", "whatever",
                ],
            },
            "guilty": {
                "valence": -0.6, "arousal": 0.4, "dominance": 0.2,
                "words": [
                    "guilty", "sorry", "regret", "ashamed", "my fault",
                ],
            },
            # Mixed/complex
            "surprised": {
                "valence": 0.2, "arousal": 0.8, "dominance": 0.5,
                "words": [
                    "surprised", "shocked", "wow", "unexpected", "whoa",
                    "unbelievable",
                ],
            },
            "curious": {
                "valence": 0.3, "arousal": 0.5, "dominance": 0.6,
                "words": [
                    "curious", "wondering", "interested", "tell me",
                    "how does", "why",
                ],
            },
            "determined": {
                "valence": 0.5, "arousal": 0.7, "dominance": 0.9,
                "words": [
                    "determined", "committed", "will do", "let's go",
                    "ready", "focus",
                ],
            },
            "hopeful": {
                "valence": 0.6, "arousal": 0.4, "dominance": 0.6,
                "words": [
                    "hopeful", "optimistic", "looking forward",
                    "fingers crossed", "hopefully",
                ],
            },
        }

        # Negation patterns
        self.negation_patterns = [
            r"\bnot\b", r"\bnever\b", r"\bno\b", r"\bdon'?t\b",
            r"\bwasn'?t\b", r"\bcan'?t\b", r"\bwon'?t\b",
            r"\bisn'?t\b", r"\baren'?t\b", r"\bdidn'?t\b",
            r"\bhardly\b", r"\bbarely\b",
        ]

        # Intensifiers & diminishers
        self.intensifiers = [
            "very", "really", "extremely", "so", "incredibly",
            "absolutely", "totally", "super",
        ]
        self.diminishers = [
            "a bit", "slightly", "kind of", "sort of", "somewhat",
            "a little",
        ]

    # ------------------------------------------------------------------
    # Emotion detection
    # ------------------------------------------------------------------

    def detect_emotion(self, text: str) -> str:
        """Detect primary emotion from text with nuanced analysis."""
        state = self.analyze_emotion(text)
        self.current_emotion = state.primary_emotion
        self.emotional_state = state
        self.emotion_history.append(state)

        # Keep history manageable
        if len(self.emotion_history) > 100:
            self.emotion_history = self.emotion_history[-50:]

        return state.primary_emotion

    def analyze_emotion(self, text: str) -> EmotionalState:
        """Full multi-dimensional emotion analysis."""
        text_lower = text.lower()

        # Detect negation
        is_negated = any(
            re.search(p, text_lower) for p in self.negation_patterns
        )

        # Detect intensifiers/diminishers
        intensity_modifier = 1.0
        for word in self.intensifiers:
            if word in text_lower:
                intensity_modifier = 1.3
                break
        for phrase in self.diminishers:
            if phrase in text_lower:
                intensity_modifier = 0.7
                break

        # Score each emotion
        emotion_scores: Dict[str, float] = {}
        word_matches: Dict[str, List[str]] = {}
        for emotion, config in self.emotion_lexicon.items():
            matches = [w for w in config["words"] if w in text_lower]
            if matches:
                emotion_scores[emotion] = len(matches) * intensity_modifier
                word_matches[emotion] = matches

        if not emotion_scores:
            return EmotionalState(
                primary_emotion="neutral", confidence=0.0
            )

        # Find primary and secondary emotions
        sorted_emotions = sorted(
            emotion_scores.items(), key=lambda x: x[1], reverse=True
        )
        primary = sorted_emotions[0]
        secondary = [
            e for e, s in sorted_emotions[1:3]
        ] if len(sorted_emotions) > 1 else []

        primary_name = primary[0]
        primary_config = self.emotion_lexicon[primary_name]

        # Calculate VAD values
        valence = (
            primary_config["valence"]
            * min(primary[1] / 2, 1.0)
            * intensity_modifier
        )
        arousal = (
            primary_config["arousal"]
            * min(primary[1] / 2, 1.0)
            * intensity_modifier
        )
        dominance = primary_config["dominance"]

        # Flip valence if negated
        if is_negated:
            valence = -valence
            opposites = {
                "happy": "sad", "sad": "happy", "angry": "calm",
                "anxious": "calm", "calm": "anxious",
            }
            primary_name = opposites.get(primary_name, primary_name)

        confidence = min(primary[1] / 3.0, 1.0)

        return EmotionalState(
            valence=round(valence, 2),
            arousal=round(arousal, 2),
            dominance=round(dominance, 2),
            primary_emotion=primary_name,
            confidence=round(confidence, 2),
            secondary_emotions=secondary,
        )

    # ------------------------------------------------------------------
    # Trend analysis
    # ------------------------------------------------------------------

    def get_emotional_trend(self, window: int = 10) -> Dict[str, float]:
        """Analyze emotional trend over recent interactions."""
        if not self.emotion_history:
            return {
                "trend": "unknown",
                "avg_valence": 0.0,
                "avg_arousal": 0.0,
            }

        recent = self.emotion_history[-window:]
        avg_valence = sum(s.valence for s in recent) / len(recent)
        avg_arousal = sum(s.arousal for s in recent) / len(recent)

        # Determine trend
        if len(recent) >= 3:
            half = len(recent) // 2
            early = sum(s.valence for s in recent[:half]) / half
            late = (
                sum(s.valence for s in recent[half:])
                / (len(recent) - half)
            )
            if late > early:
                trend = "improving"
            elif late < early:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        dominant_emotion = "neutral"
        if recent:
            emotion_counts: Dict[str, int] = {}
            for s in recent:
                emotion_counts[s.primary_emotion] = (
                    emotion_counts.get(s.primary_emotion, 0) + 1
                )
            dominant_emotion = max(
                emotion_counts, key=emotion_counts.get  # type: ignore[arg-type]
            )

        return {
            "trend": trend,
            "avg_valence": round(avg_valence, 2),
            "avg_arousal": round(avg_arousal, 2),
            "dominant_emotion": dominant_emotion,
        }
