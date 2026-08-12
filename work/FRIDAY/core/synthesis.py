import logging
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger("LifeSynthesisEngine")


@dataclass
class SynthesisInput:
    """Structured input from various integrations for synthesis."""
    weather: Optional[Dict] = None
    calendar: Optional[Dict] = None
    tasks: Optional[Dict] = None
    health: Optional[Dict] = None
    email: Optional[Dict] = None
    finance: Optional[Dict] = None
    smart_home: Optional[Dict] = None
    custom: Optional[Dict] = None


@dataclass
class ActionRecommendation:
    """A single prioritized action recommendation."""
    title: str
    description: str
    priority: str          # "high", "medium", "low"
    category: str          # "work", "health", "personal", "finance", etc.
    source_integration: str
    reasoning: str
    time_sensitivity: str  # "immediate", "today", "this_week", "flexible"


@dataclass
class SynthesisResult:
    """Complete synthesis result with holistic advice."""
    timestamp: str
    context_summary: str
    holistic_advice: str
    recommendations: List[ActionRecommendation] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    opportunities: List[str] = field(default_factory=list)


class LifeSynthesisEngine:
    """Combines data from multiple integrations (weather, calendar, tasks,
    health, etc.) to generate holistic contextual advice and prioritized
    action recommendations."""

    def __init__(self, brain=None, connector=None):
        self.brain = brain
        self.connector = connector
        self._synthesis_history: List[SynthesisResult] = []

    # ------------------------------------------------------------------
    # Main synthesis
    # ------------------------------------------------------------------

    async def generate_holistic_advice(
        self, user_context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Generate holistic advice by synthesizing data from all integrations.

        Args:
            user_context: Optional dict with user-specific context
                (name, preferences, timezone, etc.)

        Returns:
            Dict with context_summary, holistic_advice,
            recommendations, risk_factors, opportunities.
        """
        # Gather data from all available integrations
        synthesis_input = await self._gather_integration_data()

        # Build time context
        time_context = self._get_time_context()

        # Combine all context
        full_context = self._assemble_context(
            synthesis_input, time_context, user_context or {}
        )

        if self.brain is None:
            result = self._heuristic_synthesis(full_context, time_context)
            self._synthesis_history.append(result)
            return self._result_to_dict(result)

        # LLM-powered synthesis
        prompt = self._build_synthesis_prompt(full_context, time_context)
        llm_response = ""
        try:
            async for chunk in self.brain.chat_stream(prompt):
                llm_response += chunk
        except Exception as e:
            logger.error(f"LLM synthesis failed: {e}")
            result = self._heuristic_synthesis(full_context, time_context)
            self._synthesis_history.append(result)
            return self._result_to_dict(result)

        # Parse LLM output
        result = self._parse_synthesis(llm_response, full_context)
        self._synthesis_history.append(result)
        return self._result_to_dict(result)

    # ------------------------------------------------------------------
    # Data gathering
    # ------------------------------------------------------------------

    async def _gather_integration_data(self) -> SynthesisInput:
        """Collect data from all available integrations."""
        data = SynthesisInput()

        if self.connector is None:
            return data

        integration_calls = [
            ("Weather", "get_weather", {}, "weather"),
            ("Calendar", "get_todays_events", {}, "calendar"),
            ("Gmail", "get_unread_summary", {}, "email"),
            ("Finance", "get_portfolio_summary", {}, "finance"),
            ("SmartHome", "get_device_status", {}, "smart_home"),
        ]

        for service, action, params, attr_name in integration_calls:
            try:
                result = await self.connector.execute_action(
                    service, action, params
                )
                setattr(data, attr_name, result)
            except Exception as e:
                logger.debug(
                    f"Integration {service} unavailable: {e}"
                )

        return data

    # ------------------------------------------------------------------
    # Context assembly
    # ------------------------------------------------------------------

    def _get_time_context(self) -> Dict[str, Any]:
        """Generate time-based context."""
        now = datetime.now()
        hour = now.hour
        if 5 <= hour < 9:
            period = "early_morning"
            energy = "high"
        elif 9 <= hour < 12:
            period = "morning"
            energy = "peak"
        elif 12 <= hour < 14:
            period = "midday"
            energy = "moderate"
        elif 14 <= hour < 18:
            period = "afternoon"
            energy = "declining"
        elif 18 <= hour < 21:
            period = "evening"
            energy = "winding_down"
        elif 21 <= hour < 24:
            period = "late_evening"
            energy = "low"
        else:
            period = "night"
            energy = "minimal"

        return {
            "hour": hour,
            "day_of_week": now.strftime("%A"),
            "period": period,
            "energy_level": energy,
            "is_weekend": now.weekday() >= 5,
        }

    def _assemble_context(
        self,
        data: SynthesisInput,
        time_ctx: Dict,
        user_ctx: Dict,
    ) -> str:
        """Assemble all data sources into a single context string."""
        parts = [
            f"Time: {time_ctx['period']} ({time_ctx['day_of_week']})",
            f"Energy level: {time_ctx['energy_level']}",
        ]

        if data.weather:
            parts.append(f"Weather: {json.dumps(data.weather, default=str)}")
        if data.calendar:
            parts.append(f"Calendar: {json.dumps(data.calendar, default=str)}")
        if data.email:
            parts.append(f"Email: {json.dumps(data.email, default=str)}")
        if data.finance:
            parts.append(f"Finance: {json.dumps(data.finance, default=str)}")
        if data.smart_home:
            parts.append(f"Smart Home: {json.dumps(data.smart_home, default=str)}")
        if data.health:
            parts.append(f"Health: {json.dumps(data.health, default=str)}")
        if data.tasks:
            parts.append(f"Tasks: {json.dumps(data.tasks, default=str)}")
        if user_ctx:
            parts.append(f"User context: {json.dumps(user_ctx, default=str)}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_synthesis_prompt(
        self, full_context: str, time_ctx: Dict
    ) -> str:
        return f"""You are Friday, synthesizing data from multiple sources to provide holistic life advice.

Available data:
{full_context}

Based on ALL the data above, provide:

1. CONTEXT_SUMMARY: A 2-3 sentence overview of the current situation
2. HOLISTIC_ADVICE: Thoughtful, personalized advice considering the full picture (weather + schedule + energy + commitments)
3. RECOMMENDATIONS: 3-5 prioritized action items, each with:
   RECOMMENDATION: [title] | [priority: high/medium/low] | [category] | [time_sensitivity: immediate/today/this_week/flexible] | [reasoning]
4. RISK_FACTORS: Things to watch out for
5. OPPORTUNITIES: Things to capitalize on

Factor in the time of day and energy level when making recommendations.
Be specific and actionable, not generic."""

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_synthesis(
        self, llm_response: str, context: str
    ) -> SynthesisResult:
        """Parse LLM synthesis output into structured result."""
        result = SynthesisResult(
            timestamp=datetime.utcnow().isoformat(),
            context_summary="",
            holistic_advice="",
        )

        current_section = None
        for line in llm_response.split("\n"):
            line = line.strip()
            if not line:
                continue
            current_section = self._parse_synthesis_line(line, result, current_section)

        # Fallback if parsing didn't work well
        if not result.holistic_advice:
            result.holistic_advice = llm_response[:500]
            result.context_summary = "Synthesis generated from multi-source data."

        return result

    @staticmethod
    def _parse_synthesis_line(line: str, result, current_section: str) -> str:
        """Parse a single line of synthesis output. Returns the new current_section."""
        if line.upper().startswith("CONTEXT_SUMMARY:"):
            result.context_summary = line.split(":", 1)[1].strip()
            return "context_summary"
        elif line.upper().startswith("HOLISTIC_ADVICE:"):
            result.holistic_advice = line.split(":", 1)[1].strip()
            return "holistic_advice"
        elif line.upper().startswith("RECOMMENDATION:"):
            SynthesisEngine._parse_recommendation_line(line, result)
            return "recommendation"
        elif line.upper().startswith("RISK_FACTORS:"):
            return "risk_factors"
        elif line.upper().startswith("OPPORTUNITIES:"):
            return "opportunities"
        elif line.startswith("-") or line.startswith("•"):
            SynthesisEngine._parse_list_item(line, current_section, result)
            return current_section
        return current_section

    @staticmethod
    def _parse_recommendation_line(line: str, result) -> None:
        """Parse a recommendation line and append to result."""
        parts = line.split("|")
        if len(parts) >= 5:
            result.recommendations.append(ActionRecommendation(
                title=parts[0].split(":", 1)[1].strip(),
                priority=parts[1].strip(),
                category=parts[2].strip(),
                time_sensitivity=parts[3].strip(),
                reasoning=parts[4].strip(),
                source_integration="synthesis",
                description=parts[4].strip(),
            ))

    @staticmethod
    def _parse_list_item(line: str, current_section: str, result) -> None:
        """Parse a list item (bullet point) and append to the appropriate section."""
        item = line.lstrip("- •").strip()
        if current_section == "risk_factors" and item:
            result.risk_factors.append(item)
        elif current_section == "opportunities" and item:
            result.opportunities.append(item)

    # ------------------------------------------------------------------
    # Heuristic fallback
    # ------------------------------------------------------------------

    def _heuristic_synthesis(
        self, full_context: str, time_ctx: Dict
    ) -> SynthesisResult:
        """Fallback synthesis when LLM is unavailable."""
        period = time_ctx.get("period", "morning")
        energy = time_ctx.get("energy_level", "moderate")

        # Time-based advice
        advice_map = {
            "early_morning": (
                "Good morning! Start with your most important task while "
                "your energy is high. Check your calendar for any early meetings."
            ),
            "morning": (
                "Peak productivity hours — focus on deep work. "
                "Batch process emails after your main task."
            ),
            "midday": (
                "Midday recharge — take a brief break, then tackle "
                "collaborative tasks or meetings."
            ),
            "afternoon": (
                "Energy is declining — switch to lighter tasks, "
                "code reviews, or planning for tomorrow."
            ),
            "evening": (
                "Winding down — wrap up remaining tasks, "
                "review what you accomplished, and plan tomorrow."
            ),
            "late_evening": (
                "Late evening — avoid starting new complex tasks. "
                "Consider reading or light planning."
            ),
            "night": (
                "It's late — rest is important for tomorrow's productivity."
            ),
        }

        recommendations = []
        if energy in ("high", "peak"):
            recommendations.append(ActionRecommendation(
                title="Tackle highest-priority task",
                description="Use peak energy for deep work",
                priority="high",
                category="work",
                source_integration="synthesis",
                reasoning="Energy levels are optimal for focus-intensive work",
                time_sensitivity="immediate",
            ))
        if period in ("afternoon", "evening"):
            recommendations.append(ActionRecommendation(
                title="Plan tomorrow's priorities",
                description="End-of-day planning for tomorrow",
                priority="medium",
                category="productivity",
                source_integration="synthesis",
                reasoning="Planning while context is fresh improves next-day start",
                time_sensitivity="today",
            ))

        return SynthesisResult(
            timestamp=datetime.utcnow().isoformat(),
            context_summary=f"Time period: {period}, Energy: {energy}",
            holistic_advice=advice_map.get(period, "Stay focused and productive."),
            recommendations=recommendations,
            risk_factors=[
                "Energy levels may not match task difficulty" if energy in (
                    "declining", "low", "minimal"
                ) else "",
            ],
            opportunities=[
                "Optimal focus window — capitalize on it" if energy in (
                    "high", "peak"
                ) else "",
            ],
        )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _result_to_dict(self, result: SynthesisResult) -> Dict[str, Any]:
        """Convert a SynthesisResult to a serializable dict."""
        return {
            "timestamp": result.timestamp,
            "context_summary": result.context_summary,
            "holistic_advice": result.holistic_advice,
            "recommendations": [
                {
                    "title": r.title,
                    "description": r.description,
                    "priority": r.priority,
                    "category": r.category,
                    "time_sensitivity": r.time_sensitivity,
                    "reasoning": r.reasoning,
                }
                for r in result.recommendations
            ],
            "risk_factors": [r for r in result.risk_factors if r],
            "opportunities": [o for o in result.opportunities if o],
        }

    def get_synthesis_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return recent synthesis results."""
        return [
            self._result_to_dict(r)
            for r in self._synthesis_history[-limit:]
        ]
