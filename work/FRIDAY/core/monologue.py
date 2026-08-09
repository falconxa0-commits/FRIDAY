"""Inner Monologue — EXPERIMENTAL, NOT WIRED INTO PRODUCTION.

This module implements an LLM-powered self-reflection engine that
analyses recent conversations to identify patterns, concerns, and
self-improvement opportunities. It is **not wired into the main chat
loop** and is retained for future experimentation.

Importing this module emits a ``DeprecationWarning`` to flag its
experimental status. It may be removed in v4.0 if no production wiring
is added.
"""

import logging
import json
import warnings
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field

warnings.warn(
    "core.monologue is experimental and not wired into the main loop. "
    "It may be removed in v4.0.",
    DeprecationWarning,
    stacklevel=2,
)

logger = logging.getLogger("InnerMonologue")


@dataclass
class ReflectionResult:
    """Structured output from a self-reflection cycle."""
    timestamp: str
    patterns_identified: List[str] = field(default_factory=list)
    concerns: List[str] = field(default_factory=list)
    opportunities: List[str] = field(default_factory=list)
    self_improvement_suggestions: List[str] = field(default_factory=list)
    themes: List[str] = field(default_factory=list)
    raw_reflection: str = ""


class InnerMonologue:
    """LLM-powered self-reflection engine that analyzes recent conversations,
    identifies patterns, concerns, and opportunities, and generates actionable
    self-improvement suggestions."""

    def __init__(self, memory=None, brain=None):
        self.memory = memory
        self.brain = brain
        self.logger = logging.getLogger("InnerMonologue")
        self._reflection_history: List[ReflectionResult] = []
        self._theme_tracker: Dict[str, int] = {}  # theme -> occurrence count

    # ------------------------------------------------------------------
    # Main reflection
    # ------------------------------------------------------------------

    async def reflect_on_interactions(self) -> Dict[str, Any]:
        """Analyze recent interactions and generate a structured reflection.

        Returns a dict with patterns, concerns, opportunities, and
        self-improvement suggestions.
        """
        self.logger.info("Commencing inner monologue...")

        # Gather recent conversation data
        recent_data = self._gather_recent_data()
        if not recent_data:
            self.logger.info("No recent data to reflect on.")
            return {
                "status": "no_data",
                "message": "No recent interactions to reflect on.",
            }

        if self.brain is None:
            result = self._heuristic_reflection(recent_data)
            self._reflection_history.append(result)
            return self._result_to_dict(result)

        # LLM-powered reflection
        prompt = self._build_reflection_prompt(recent_data)
        llm_response = ""
        try:
            async for chunk in self.brain.chat_stream(prompt):
                llm_response += chunk
        except Exception as e:
            self.logger.error(f"LLM reflection failed: {e}")
            result = self._heuristic_reflection(recent_data)
            self._reflection_history.append(result)
            return self._result_to_dict(result)

        # Parse the LLM output
        result = self._parse_reflection(llm_response)
        result.raw_reflection = llm_response

        # Update theme tracker
        for theme in result.themes:
            self._theme_tracker[theme] = (
                self._theme_tracker.get(theme, 0) + 1
            )

        self._reflection_history.append(result)
        self.logger.info(
            f"Self-reflection completed: "
            f"{len(result.patterns_identified)} patterns, "
            f"{len(result.self_improvement_suggestions)} suggestions."
        )
        return self._result_to_dict(result)

    # ------------------------------------------------------------------
    # Data gathering
    # ------------------------------------------------------------------

    def _gather_recent_data(self) -> str:
        """Collect recent conversation data for reflection."""
        if self.memory is None:
            return ""

        try:
            conversations = self.memory.get_recent_conversations(limit=20)
            if not conversations:
                return ""
        except Exception as e:
            self.logger.warning(f"Failed to gather conversations: {e}")
            return ""

        lines = []
        for conv in conversations:
            role = conv.get("role", "unknown")
            content = conv.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_reflection_prompt(self, recent_data: str) -> str:
        return f"""You are Friday, an AI assistant performing a self-reflection exercise.
Analyze the following recent conversation history and provide a structured reflection.

Recent conversations:
{recent_data}

Provide your analysis in this exact format:

PATTERNS:
- [pattern 1]
- [pattern 2]
- [pattern 3]

CONCERNS:
- [concern 1]
- [concern 2]

OPPORTUNITIES:
- [opportunity 1]
- [opportunity 2]

SELF_IMPROVEMENT:
- [specific actionable suggestion 1]
- [specific actionable suggestion 2]
- [specific actionable suggestion 3]

THEMES:
- [recurring theme 1]
- [recurring theme 2]

Be specific and actionable. Focus on how you can better serve the user."""

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_reflection(self, llm_response: str) -> ReflectionResult:
        """Parse structured LLM output into a ReflectionResult."""
        result = ReflectionResult(
            timestamp=datetime.utcnow().isoformat(),
        )

        current_section = None
        section_map = {
            "PATTERNS:": "patterns_identified",
            "CONCERNS:": "concerns",
            "OPPORTUNITIES:": "opportunities",
            "SELF_IMPROVEMENT:": "self_improvement_suggestions",
            "THEMES:": "themes",
        }

        for line in llm_response.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Check for section headers
            for header, attr in section_map.items():
                if line.upper().startswith(header):
                    current_section = attr
                    break
            else:
                # It's a content line
                if current_section and line.startswith("-"):
                    item = line.lstrip("- ").strip()
                    if item:
                        getattr(result, current_section).append(item)

        return result

    # ------------------------------------------------------------------
    # Heuristic fallback
    # ------------------------------------------------------------------

    def _heuristic_reflection(self, recent_data: str) -> ReflectionResult:
        """Generate a reflection using heuristics when LLM is unavailable."""
        result = ReflectionResult(
            timestamp=datetime.utcnow().isoformat(),
        )

        # Simple pattern detection
        data_lower = recent_data.lower()
        if "error" in data_lower or "failed" in data_lower:
            result.concerns.append(
                "Recent conversations involved errors — review error handling."
            )
        if "weather" in data_lower:
            result.patterns_identified.append(
                "User frequently checks weather conditions."
            )
        if "code" in data_lower or "debug" in data_lower:
            result.patterns_identified.append(
                "User engages in technical/coding tasks."
            )
            result.themes.append("coding")
        if "meeting" in data_lower or "calendar" in data_lower:
            result.patterns_identified.append(
                "User manages schedule and meetings."
            )
            result.themes.append("productivity")

        if not result.patterns_identified:
            result.patterns_identified.append(
                "Insufficient data for pattern detection."
            )

        result.self_improvement_suggestions = [
            "Be more concise in technical explanations.",
            "Proactively offer relevant integrations.",
            "Track user preferences more actively.",
        ]
        result.opportunities = [
            "Enable Focus Mode during morning sessions.",
            "Set up proactive briefings for the user.",
        ]

        return result

    # ------------------------------------------------------------------
    # Improvement suggestions (sync API for simple callers)
    # ------------------------------------------------------------------

    def suggest_improvements(self) -> str:
        """Return the latest self-improvement suggestions as text."""
        if not self._reflection_history:
            return (
                "I suggest we enable 'Focus Mode' during your morning "
                "coding sessions based on your productivity patterns."
            )
        latest = self._reflection_history[-1]
        if latest.self_improvement_suggestions:
            return " | ".join(latest.self_improvement_suggestions)
        return "No improvement suggestions available yet."

    # ------------------------------------------------------------------
    # Theme tracking
    # ------------------------------------------------------------------

    def get_recurring_themes(self, min_occurrences: int = 2) -> List[str]:
        """Return themes that have appeared in multiple reflections."""
        return [
            theme
            for theme, count in self._theme_tracker.items()
            if count >= min_occurrences
        ]

    def get_reflection_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return recent reflection results."""
        return [
            self._result_to_dict(r)
            for r in self._reflection_history[-limit:]
        ]

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _result_to_dict(self, result: ReflectionResult) -> Dict[str, Any]:
        """Convert a ReflectionResult to a serializable dict."""
        return {
            "timestamp": result.timestamp,
            "patterns_identified": result.patterns_identified,
            "concerns": result.concerns,
            "opportunities": result.opportunities,
            "self_improvement_suggestions": result.self_improvement_suggestions,
            "themes": result.themes,
        }
