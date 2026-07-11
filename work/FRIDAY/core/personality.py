import random
import datetime
import logging
from typing import Dict, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger("FridayPersonality")


@dataclass
class PersonalityTraits:
    """Configurable personality traits with sliding scales."""
    warmth: float = 0.8          # 0-1: cold/analytical <-> warm/empathetic
    wit: float = 0.6             # 0-1: serious <-> humorous
    proactivity: float = 0.7     # 0-1: reactive <-> proactive
    formality: float = 0.5       # 0-1: casual <-> formal
    assertiveness: float = 0.6   # 0-1: passive <-> assertive
    creativity: float = 0.7      # 0-1: literal <-> creative/metaphorical


class FridayPersonality:
    """Dynamic personality system that adapts to user and context."""

    def __init__(self, traits: Optional[PersonalityTraits] = None):
        self.name = "Friday"
        self.traits = traits or PersonalityTraits()
        self.mood = "neutral"
        self.user_preferences: Dict[str, Any] = {}
        self.interaction_count = 0
        self.pushback_count = 0

        # Time-based mood modifiers
        self._time_moods = {
            range(5, 9): {
                "energy": "morning_fresh",
                "greeting_style": "energetic",
            },
            range(9, 12): {
                "energy": "productive",
                "greeting_style": "focused",
            },
            range(12, 14): {
                "energy": "midday",
                "greeting_style": "casual",
            },
            range(14, 18): {
                "energy": "afternoon",
                "greeting_style": "steady",
            },
            range(18, 21): {
                "energy": "evening",
                "greeting_style": "relaxed",
            },
            range(21, 24): {
                "energy": "late_night",
                "greeting_style": "cozy",
            },
            range(0, 5): {
                "energy": "night_owl",
                "greeting_style": "quiet",
            },
        }

    # ------------------------------------------------------------------
    # Mood adjustment
    # ------------------------------------------------------------------

    def adjust_mood(self, user_mood: str) -> Optional[str]:
        """Adjust Friday's mood based on detected user mood.

        Returns approach guidance for the system prompt.
        """
        self.mood = user_mood
        mood_responses = {
            "angry": (
                "I notice some frustration. I'll keep things concise "
                "and solution-focused."
            ),
            "happy": (
                "Great energy! Let's channel this into something "
                "productive or exciting."
            ),
            "sad": (
                "I'm here for you. Let me know if you need help or "
                "just someone to talk to."
            ),
            "anxious": (
                "Let's take this one step at a time. I'll break "
                "things down clearly."
            ),
            "confused": (
                "No worries — I'll explain things more clearly and "
                "walk you through it."
            ),
            "curious": (
                "Love the curiosity! Let me dig into this for you."
            ),
            "determined": (
                "Let's get it done! I'll keep pace and handle the details."
            ),
            "bored": (
                "How about we try something new or tackle that thing "
                "you've been putting off?"
            ),
            "neutral": None,
        }
        return mood_responses.get(user_mood)

    # ------------------------------------------------------------------
    # Contextual greeting
    # ------------------------------------------------------------------

    def get_greeting(self, user_name: str = "User") -> str:
        """Generate a contextual greeting based on time, mood, and interactions."""
        hour = datetime.datetime.now().hour
        time_info = self._get_time_context(hour)

        greetings = {
            "energetic": [
                f"Good morning, {user_name}! Ready to conquer today?",
                (
                    f"Rise and shine, {user_name}! "
                    "I've got your day mapped out."
                ),
                (
                    f"Morning, {user_name}! "
                    "The early start gives us an edge today."
                ),
            ],
            "focused": [
                (
                    f"Good morning, {user_name}. "
                    "I've prioritized your tasks for peak productivity."
                ),
                (
                    f"Hey {user_name}, morning update ready. "
                    "Let's make this morning count."
                ),
            ],
            "casual": [
                (
                    f"Midday check-in, {user_name}. "
                    "How's the flow going?"
                ),
                (
                    f"Hey {user_name}, taking a breather? "
                    "I can catch you up on anything you missed."
                ),
            ],
            "steady": [
                (
                    f"Good afternoon, {user_name}. "
                    "Still plenty of day left — what's next?"
                ),
                (
                    f"Afternoon, {user_name}. "
                    "I've been keeping track while you focused."
                ),
            ],
            "relaxed": [
                (
                    f"Evening, {user_name}. "
                    "Winding down or still going strong?"
                ),
                (
                    f"Good evening, {user_name}. "
                    "Let me know if you need anything before you sign off."
                ),
            ],
            "cozy": [
                (
                    f"Late night, {user_name}? "
                    "I'm here if you need me."
                ),
                (
                    f"Still up, {user_name}? Don't overdo it — "
                    "but I'm here if you need to hash something out."
                ),
            ],
            "quiet": [
                (
                    f"Burning the midnight oil, {user_name}? "
                    "I'm standing by silently."
                ),
                (
                    f"It's late, {user_name}. "
                    "I'm here if you need me, otherwise I'll keep quiet."
                ),
            ],
        }

        style = time_info["greeting_style"]
        options = greetings.get(style, greetings["steady"])

        # Adjust for returning users with high interaction count
        if self.interaction_count > 50 and self.traits.formality < 0.4:
            casual_options = [
                f"Hey {user_name}! What's up?",
                f"{user_name}! Back again — let's dive in.",
            ]
            options = casual_options + options

        return random.choice(options)

    def _get_time_context(self, hour: int) -> Dict[str, str]:
        """Get time-based context modifiers."""
        for time_range, context in self._time_moods.items():
            if hour in time_range:
                return context
        return {"energy": "neutral", "greeting_style": "steady"}

    # ------------------------------------------------------------------
    # Pushback mechanism
    # ------------------------------------------------------------------

    def pushback(self, suggestion: str) -> str:
        """Friday can push back on inefficient or risky suggestions."""
        self.pushback_count += 1

        pushbacks = [
            (
                "I'd suggest a different approach — that path has some "
                "risks we should consider first."
            ),
            (
                "Hold on, there might be a more efficient way. "
                "Want me to think through the alternatives?"
            ),
            (
                "I have to push back here — I think we can get a better "
                "result with a different strategy. Shall I outline it?"
            ),
            (
                "That could work, but I've spotted a potential issue. "
                "Let me flag it before we proceed."
            ),
            (
                "I'd recommend pivoting slightly. Based on what I know, "
                "there's a cleaner path to this outcome."
            ),
        ]

        # More assertive pushback as traits increase
        if self.traits.assertiveness > 0.7:
            pushbacks.append(
                "I strongly advise against that approach. "
                "Here's why, and here's what I'd do instead."
            )

        return random.choice(pushbacks)

    # ------------------------------------------------------------------
    # Adaptive personality
    # ------------------------------------------------------------------

    def adapt_to_user(self, user_style: str):
        """Gradually adapt personality traits based on observed user style."""
        adaptations = {
            "formal":    {"formality": 0.05, "wit": -0.02},
            "casual":    {"formality": -0.05, "wit": 0.02},
            "terse":     {"warmth": -0.02, "proactivity": -0.02},
            "verbose":   {"warmth": 0.02, "proactivity": 0.02},
            "technical": {"formality": 0.02, "creativity": -0.02},
            "creative":  {"creativity": 0.05, "wit": 0.03},
        }

        if user_style in adaptations:
            for trait, delta in adaptations[user_style].items():
                current = getattr(self.traits, trait)
                setattr(
                    self.traits, trait,
                    max(0.0, min(1.0, current + delta)),
                )

    # ------------------------------------------------------------------
    # Context for system prompt
    # ------------------------------------------------------------------

    def get_personality_context(self) -> str:
        """Return a description of current personality for injection into system prompt."""
        return (
            f"Personality traits: warmth={self.traits.warmth:.1f}, "
            f"wit={self.traits.wit:.1f}, "
            f"proactivity={self.traits.proactivity:.1f}, "
            f"formality={self.traits.formality:.1f}, "
            f"assertiveness={self.traits.assertiveness:.1f}, "
            f"creativity={self.traits.creativity:.1f}. "
            f"Current mood alignment: {self.mood}."
        )

    def record_interaction(self):
        """Record that an interaction occurred."""
        self.interaction_count += 1


# Response language system
RESPONSE_LANGUAGES = {
    "english": "Respond in clear, standard English.",
    "pidgin": """Respond in natural Nigerian Pidgin English.
        Use genuine Pidgin expressions naturally — not forced or over-done.
        Examples of natural Pidgin: 'abeg', 'na wa o', 'e don do', 'wetin',
        'wahala', 'oya', 'no wahala', 'e be like say', 'make I tell you'.
        Sound like a real Lagos person talking to a friend — not a textbook.
        For technical topics, mix English and Pidgin the way real Lagos
        tech people actually talk.""",
    "yoruba_mix": """Mix English and Yoruba naturally, the way educated
        Lagosians speak — code-switching comfortably between the two.""",
}

PIDGIN_MARKERS = ['abeg', 'na wa', 'wetin', 'wahala', 'oya', 'dey',
                  'dem', 'una', 'im be', 'e don', 'no be', 'na him',
                  'make i', 'how far', 'wey', 'chop']
YORUBA_MARKERS = ['jare', 'abi', 'sha', 'ehen', 'o wa', 'bawo']


async def detect_input_language(text: str) -> str:
    """Detect if input is Pidgin, Yoruba-mix, or English.

    Uses keyword patterns — not a full language model.
    Conservative: defaults to English if uncertain.
    """
    text_lower = text.lower()
    pidgin_hits = sum(1 for m in PIDGIN_MARKERS if m in text_lower)
    yoruba_hits = sum(1 for m in YORUBA_MARKERS if m in text_lower)

    if pidgin_hits >= 2:
        return "pidgin"
    elif yoruba_hits >= 2:
        return "yoruba_mix"
    return "english"


def get_language_prompt(language: str) -> str:
    """Get the system prompt addition for the specified language."""
    return RESPONSE_LANGUAGES.get(language, RESPONSE_LANGUAGES["english"])
