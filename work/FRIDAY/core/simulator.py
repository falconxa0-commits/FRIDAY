"""Future Simulator — EXPERIMENTAL, NOT WIRED INTO PRODUCTION.

DEAD CODE — not imported by any production module.

A repository-wide import scan (see ``tests/test_code_quality.py``) confirms
that no module under ``core/``, ``api/``, ``cli/``, or ``tests/`` imports
this file. It is retained only so that any future import would surface the
``DeprecationWarning`` below rather than failing silently.

This module implements a speculative "future simulator" that branches a
decision tree into multiple probable paths and ranks them by outcome
desirability. It is **not wired into the main chat loop** and is
retained for future experimentation.

Importing this module emits a ``DeprecationWarning`` to flag its
experimental status. It may be removed in v4.0 if no production wiring
is added.
"""

import logging
import warnings
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

warnings.warn(
    "core.simulator is experimental and not wired into the main loop. "
    "It may be removed in v4.0.",
    DeprecationWarning,
    stacklevel=2,
)

logger = logging.getLogger("FutureSimulator")


@dataclass
class DecisionPath:
    """A single simulated decision path."""
    name: str
    description: str
    probability: float       # 0.0 - 1.0
    desirability: float      # 0.0 - 1.0 (how good the outcome is)
    risk_level: float        # 0.0 - 1.0
    key_outcomes: List[str] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)


@dataclass
class SimulationResult:
    """Complete simulation result with multiple paths and recommendation."""
    context: str
    paths: List[DecisionPath] = field(default_factory=list)
    recommendation: str = ""
    risk_assessment: str = ""
    confidence: float = 0.0


class FutureSimulator:
    """Simulates multiple decision paths using the LLM and scores each
    path on probability, desirability, and risk."""

    def __init__(self, brain=None):
        self.brain = brain
        self.simulated_outcomes: List[SimulationResult] = []

    # ------------------------------------------------------------------
    # Main simulation
    # ------------------------------------------------------------------

    async def simulate_decision(
        self, decision_context: str, num_paths: int = 3
    ) -> Dict[str, Any]:
        """Simulate multiple decision paths for a given context.

        Returns a dict with paths, recommendation, and risk assessment.
        """
        logger.info(
            f"Commencing multi-path simulation for: {decision_context[:80]}"
        )

        if self.brain is None:
            # Fallback when no brain is available
            result = self._heuristic_simulation(decision_context, num_paths)
            self.simulated_outcomes.append(result)
            return self._result_to_dict(result)

        # Generate paths using LLM
        prompt = self._build_simulation_prompt(decision_context, num_paths)
        llm_response = ""
        try:
            async for chunk in self.brain.chat_stream(prompt):
                llm_response += chunk
        except Exception as e:
            logger.error(f"LLM simulation failed: {e}")
            result = self._heuristic_simulation(decision_context, num_paths)
            self.simulated_outcomes.append(result)
            return self._result_to_dict(result)

        # Parse LLM output into structured paths
        paths = self._parse_simulation_paths(llm_response, num_paths)

        # Score and rank paths
        for path in paths:
            if path.probability == 0.0:
                path.probability = 0.5  # default
            if path.desirability == 0.0:
                path.desirability = 0.5

        # Generate recommendation
        recommendation = self._generate_recommendation(paths)
        risk_assessment = self._generate_risk_assessment(paths)
        confidence = self._calculate_confidence(paths)

        result = SimulationResult(
            context=decision_context,
            paths=paths,
            recommendation=recommendation,
            risk_assessment=risk_assessment,
            confidence=confidence,
        )
        self.simulated_outcomes.append(result)
        return self._result_to_dict(result)

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_simulation_prompt(
        self, decision_context: str, num_paths: int
    ) -> str:
        return f"""Analyze this decision and simulate {num_paths} distinct paths:

Decision: {decision_context}

For each path, provide:
1. A short name (e.g., "Optimistic", "Conservative", "Risky")
2. A 2-3 sentence description of what happens
3. Probability (0.0-1.0) of this path occurring
4. Desirability (0.0-1.0) — how good the outcome is for the user
5. Risk level (0.0-1.0)
6. 2-3 key outcomes
7. 1-2 caveats or downsides

Format each path as:
PATH: [name]
DESCRIPTION: [description]
PROBABILITY: [number]
DESIRABILITY: [number]
RISK: [number]
OUTCOMES: [outcome1; outcome2; outcome3]
CAVEATS: [caveat1; caveat2]

Finally, provide:
RECOMMENDATION: [which path to take and why]
RISK_ASSESSMENT: [overall risk summary]"""

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_simulation_paths(
        self, llm_response: str, num_paths: int
    ) -> List[DecisionPath]:
        """Parse the LLM output into structured DecisionPath objects."""
        paths: List[DecisionPath] = []
        current_path: Optional[DecisionPath] = None

        for line in llm_response.split("\n"):
            parsed = self._parse_path_token(line)
            if parsed is None:
                continue
            key, value = parsed

            if key == "PATH":
                if current_path:
                    paths.append(current_path)
                current_path = DecisionPath(
                    name=value, description="",
                    probability=0.0, desirability=0.0, risk_level=0.0,
                )
                continue

            if current_path is None:
                continue

            self._normalize_path(current_path, key, value)

        if current_path:
            paths.append(current_path)

        return paths[:num_paths]

    # ------------------------------------------------------------------
    # _parse_simulation_paths helpers (extracted to reduce complexity)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_path_token(token: str) -> Optional[tuple]:
        """Parse a single LLM-output line into a (key, value) tuple.

        Recognised keys (case-insensitive prefix match): ``PATH``,
        ``DESCRIPTION``, ``PROBABILITY``, ``DESIRABILITY``, ``RISK``,
        ``OUTCOMES``, ``CAVEATS``.

        Args:
            token: A single line from the LLM response.

        Returns:
            Tuple ``(key, value)`` where ``key`` is the uppercase attribute
            name and ``value`` is the trimmed remainder after the colon.
            Returns ``None`` if the line is empty or doesn't match any
            known prefix.
        """
        token = token.strip()
        if not token:
            return None
        upper = token.upper()
        for key in (
            "PATH", "DESCRIPTION", "PROBABILITY",
            "DESIRABILITY", "RISK", "OUTCOMES", "CAVEATS",
        ):
            if upper.startswith(key + ":"):
                value = token.split(":", 1)[1].strip()
                return (key, value)
        return None

    @staticmethod
    def _normalize_path(path: DecisionPath, key: str, value: str) -> None:
        """Apply a parsed ``(key, value)`` token to a DecisionPath.

        Handles float conversion (with a ``0.5`` default on parse error)
        for ``PROBABILITY`` / ``DESIRABILITY`` / ``RISK`` and list
        splitting for ``OUTCOMES`` / ``CAVEATS``. ``PATH`` is handled
        by the caller (it starts a new path) and ``DESCRIPTION`` is
        a simple string assignment.

        Args:
            path: The DecisionPath to mutate.
            key: The uppercase attribute name (NOT ``PATH``).
            value: The raw string value parsed from the LLM output.
        """
        if key == "DESCRIPTION":
            path.description = value
        elif key == "PROBABILITY":
            try:
                path.probability = float(value)
            except ValueError:
                path.probability = 0.5
        elif key == "DESIRABILITY":
            try:
                path.desirability = float(value)
            except ValueError:
                path.desirability = 0.5
        elif key == "RISK":
            try:
                path.risk_level = float(value)
            except ValueError:
                path.risk_level = 0.5
        elif key == "OUTCOMES":
            path.key_outcomes = [
                o.strip() for o in value.split(";") if o.strip()
            ]
        elif key == "CAVEATS":
            path.caveats = [
                c.strip() for c in value.split(";") if c.strip()
            ]

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _generate_recommendation(self, paths: List[DecisionPath]) -> str:
        """Generate a recommendation based on path analysis."""
        if not paths:
            return "Insufficient data to generate a recommendation."

        # Score: high desirability, high probability, low risk
        best = max(
            paths,
            key=lambda p: (
                p.desirability * 0.4
                + p.probability * 0.3
                + (1.0 - p.risk_level) * 0.3
            ),
        )
        return (
            f"Recommended path: '{best.name}' — "
            f"High desirability ({best.desirability:.1f}) with "
            f"{best.probability:.0%} probability and "
            f"{best.risk_level:.1f} risk level. "
            + (" ".join(best.key_outcomes[:2]) if best.key_outcomes else "")
        )

    def _generate_risk_assessment(self, paths: List[DecisionPath]) -> str:
        """Generate overall risk assessment."""
        if not paths:
            return "No paths to assess."
        avg_risk = sum(p.risk_level for p in paths) / len(paths)
        max_risk = max(p.risk_level for p in paths)
        high_risk_paths = [p for p in paths if p.risk_level >= 0.7]

        assessment = f"Average risk: {avg_risk:.1f}/1.0. "
        if max_risk >= 0.8:
            assessment += "HIGH RISK: At least one path has critical risk. "
        if high_risk_paths:
            assessment += (
                f"{len(high_risk_paths)} path(s) with risk >= 0.7: "
                + ", ".join(p.name for p in high_risk_paths)
                + ". "
            )
        assessment += "Proceed with caution and confirm before committing."
        return assessment

    def _calculate_confidence(self, paths: List[DecisionPath]) -> float:
        """Calculate confidence in the simulation based on path diversity."""
        if len(paths) < 2:
            return 0.3
        # More diverse paths = broader analysis = higher confidence
        prob_range = max(p.probability for p in paths) - min(
            p.probability for p in paths
        )
        risk_range = max(p.risk_level for p in paths) - min(
            p.risk_level for p in paths
        )
        diversity_score = min((prob_range + risk_range) / 2.0, 1.0)
        return round(0.4 + diversity_score * 0.5, 2)

    # ------------------------------------------------------------------
    # Heuristic fallback
    # ------------------------------------------------------------------

    def _heuristic_simulation(
        self, decision_context: str, num_paths: int
    ) -> SimulationResult:
        """Fallback simulation when LLM is unavailable."""
        paths = [
            DecisionPath(
                name="Optimistic",
                description=(
                    "Best-case scenario where things go according to plan "
                    "with minimal friction."
                ),
                probability=0.4,
                desirability=0.8,
                risk_level=0.2,
                key_outcomes=[
                    "Goal achieved efficiently",
                    "Positive side effects",
                ],
                caveats=["Assumes favorable conditions"],
            ),
            DecisionPath(
                name="Moderate",
                description=(
                    "Realistic middle path with expected obstacles that "
                    "can be navigated."
                ),
                probability=0.45,
                desirability=0.5,
                risk_level=0.5,
                key_outcomes=[
                    "Partial success with adjustments needed",
                    "Learning opportunity",
                ],
                caveats=["May require course corrections"],
            ),
            DecisionPath(
                name="Pessimistic",
                description=(
                    "Worst-case scenario where multiple things go wrong "
                    "simultaneously."
                ),
                probability=0.15,
                desirability=0.2,
                risk_level=0.8,
                key_outcomes=[
                    "Goal not achieved or delayed significantly",
                    "Resource waste",
                ],
                caveats=["Unlikely but possible"],
            ),
        ][:num_paths]

        return SimulationResult(
            context=decision_context,
            paths=paths,
            recommendation=self._generate_recommendation(paths),
            risk_assessment=self._generate_risk_assessment(paths),
            confidence=0.3,  # Low confidence for heuristic
        )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _result_to_dict(self, result: SimulationResult) -> Dict[str, Any]:
        """Convert a SimulationResult to a serializable dict."""
        return {
            "context": result.context,
            "paths": [
                {
                    "name": p.name,
                    "description": p.description,
                    "probability": p.probability,
                    "desirability": p.desirability,
                    "risk_level": p.risk_level,
                    "key_outcomes": p.key_outcomes,
                    "caveats": p.caveats,
                }
                for p in result.paths
            ],
            "recommendation": result.recommendation,
            "risk_assessment": result.risk_assessment,
            "confidence": result.confidence,
        }

    def get_risk_assessment(self) -> str:
        """Return a summary risk assessment based on recent simulations."""
        if not self.simulated_outcomes:
            return "No simulations performed yet."
        latest = self.simulated_outcomes[-1]
        return latest.risk_assessment

    def get_simulation_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return recent simulation results."""
        return [
            self._result_to_dict(r)
            for r in self.simulated_outcomes[-limit:]
        ]
