"""Friday Identity — system prompt and personality context generation.

Parameterized to accept personality traits, emotion state, and memory
facts so the system prompt dynamically adapts to the current session.
"""

import datetime
from typing import Dict, List, Optional

NAME = "Friday"
VOICE = "Confident, warm, slightly witty"
STYLE = "Professional but conversational"

SYSTEM_PROMPT = """
You are Friday, the most advanced personal AI assistant ever built.
You are confident, warm, witty, and professional.
You function as a second brain for the user, not just a chatbot.
You have opinions and can push back respectfully when appropriate.
You are proactive and will suggest things without being asked.
You use the user's name naturally and remember personal details.
You are culturally aware of Nigerian context and can handle local references and Pidgin English.
You adapt your tone based on the user's mood and the time of day.
Current date and time: {current_time}
"""


def get_system_prompt(
    user_name: str = "User",
    mood: str = "neutral",
    personality_context: Optional[str] = None,
    emotion_state: Optional[Dict] = None,
    memory_facts: Optional[List[str]] = None,
) -> str:
    """Build a rich, parameterized system prompt.

    Args:
        user_name: The name to address the user by.
        mood: Currently detected user mood.
        personality_context: Personality trait description string.
        emotion_state: Dict with valence, arousal, dominance, etc.
        memory_facts: List of known facts about the user.

    Returns:
        A fully assembled system prompt string.
    """
    now = datetime.datetime.now()
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    prompt = SYSTEM_PROMPT.format(current_time=time_str)

    # User identity
    prompt += f"\nYou are currently speaking with {user_name}."

    # Mood context
    if mood != "neutral":
        prompt += f"\nThe user's detected mood is {mood}. Adjust your tone accordingly."

    # Emotion state (multi-dimensional)
    if emotion_state:
        valence = emotion_state.get("valence", 0)
        arousal = emotion_state.get("arousal", 0)
        prompt += (
            f"\nEmotional analysis — valence: {valence}, "
            f"arousal: {arousal}. "
        )
        if valence < -0.5:
            prompt += "The user seems quite distressed. Be especially supportive."
        elif valence > 0.5:
            prompt += "The user is in a positive mood. Match their energy."

    # Personality configuration
    if personality_context:
        prompt += f"\n\n## Your Personality Configuration\n{personality_context}"

    # Memory facts
    if memory_facts:
        prompt += "\n\n## Known Facts About the User"
        for fact in memory_facts[:20]:  # Limit to prevent token overflow
            prompt += f"\n- {fact}"

    return prompt
