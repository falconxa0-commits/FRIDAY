"""Evolution Engine — EXPERIMENTAL, NOT WIRED INTO PRODUCTION.

This module implements an A/B-testing framework for prompt variants and
LLM-powered self-evolution of agent system prompts. It is **not wired
into the main chat loop** and is retained for future experimentation.

Importing this module emits a ``DeprecationWarning`` to flag its
experimental status. It may be removed in v4.0 if no production wiring
is added.
"""

import logging
import json
import hashlib
import time
import warnings
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field

warnings.warn(
    "core.evolution is experimental and not wired into the main loop. "
    "It may be removed in v4.0.",
    DeprecationWarning,
    stacklevel=2,
)

logger = logging.getLogger("EvolutionEngine")


@dataclass
class PromptVariant:
    """A single prompt variant with tracking metadata."""
    variant_id: str
    description: str
    prompt_text: str
    created_at: str
    success_count: int = 0
    failure_count: int = 0
    total_uses: int = 0
    avg_response_quality: float = 0.0
    avg_response_time: float = 0.0
    active: bool = True


@dataclass
class EvolutionCycle:
    """Record of a single evolution cycle."""
    cycle_id: str
    timestamp: str
    agent_name: str
    input_metrics: Dict[str, Any]
    variants_compared: List[str]
    winning_variant: Optional[str] = None
    improvement_pct: float = 0.0
    patch: str = ""


class EvolutionEngine:
    """Tracks prompt variants and their success rates, A/B tests different
    system prompt modifications, stores evolution history with metrics, and
    generates and validates improvement proposals."""

    def __init__(self, brain=None):
        self.brain = brain
        self.logger = logging.getLogger("EvolutionEngine")

        # Variant storage: agent_name -> list of PromptVariant
        self._variants: Dict[str, List[PromptVariant]] = {}
        # Evolution history
        self._evolution_history: List[EvolutionCycle] = []
        # Current active variant per agent
        self._active_variants: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Variant management
    # ------------------------------------------------------------------

    def register_variant(
        self,
        agent_name: str,
        description: str,
        prompt_text: str,
    ) -> str:
        """Register a new prompt variant for an agent.

        Returns the variant_id.
        """
        variant_id = hashlib.sha256(
            f"{agent_name}:{description}:{time.time()}".encode()
        ).hexdigest()[:12]

        variant = PromptVariant(
            variant_id=variant_id,
            description=description,
            prompt_text=prompt_text,
            created_at=datetime.utcnow().isoformat(),
        )

        if agent_name not in self._variants:
            self._variants[agent_name] = []

        self._variants[agent_name].append(variant)

        # Set as active if it's the first variant
        if agent_name not in self._active_variants:
            self._active_variants[agent_name] = variant_id

        self.logger.info(
            f"Registered variant {variant_id} for agent '{agent_name}': "
            f"{description}"
        )
        return variant_id

    def get_active_variant(self, agent_name: str) -> Optional[PromptVariant]:
        """Get the currently active prompt variant for an agent."""
        variant_id = self._active_variants.get(agent_name)
        if not variant_id:
            return None

        for v in self._variants.get(agent_name, []):
            if v.variant_id == variant_id:
                return v
        return None

    def record_variant_result(
        self,
        agent_name: str,
        variant_id: str,
        success: bool,
        response_quality: float = 0.0,
        response_time: float = 0.0,
    ):
        """Record the result of using a particular variant."""
        for v in self._variants.get(agent_name, []):
            if v.variant_id == variant_id:
                v.total_uses += 1
                if success:
                    v.success_count += 1
                else:
                    v.failure_count += 1

                # Running averages
                n = v.total_uses
                v.avg_response_quality = (
                    (v.avg_response_quality * (n - 1) + response_quality) / n
                )
                v.avg_response_time = (
                    (v.avg_response_time * (n - 1) + response_time) / n
                )
                break

    # ------------------------------------------------------------------
    # A/B testing
    # ------------------------------------------------------------------

    def get_test_variant(self, agent_name: str) -> Optional[PromptVariant]:
        """Get a variant for A/B testing. Returns the least-tested variant
        that isn't the active one, to balance data collection."""
        variants = self._variants.get(agent_name, [])
        if len(variants) < 2:
            return None

        active_id = self._active_variants.get(agent_name)
        non_active = [v for v in variants if v.variant_id != active_id and v.active]
        if not non_active:
            return None

        # Return the one with fewest uses for balanced testing
        return min(non_active, key=lambda v: v.total_uses)

    async def run_ab_test(
        self, agent_name: str, test_input: str, rounds: int = 3
    ) -> Dict[str, Any]:
        """Run an A/B test between the active variant and a test variant.

        Returns comparison metrics.
        """
        active = self.get_active_variant(agent_name)
        test = self.get_test_variant(agent_name)

        if not active or not test:
            return {
                "status": "insufficient_variants",
                "message": "Need at least 2 variants to run A/B test.",
            }

        if not self.brain:
            return {
                "status": "no_brain",
                "message": "Brain unavailable for A/B testing.",
            }

        results = {"active": [], "test": []}

        for round_num in range(rounds):
            # Test active variant
            try:
                start = time.time()
                response_a = ""
                async for chunk in self.brain.chat_stream(
                    test_input, force_provider="claude"
                ):
                    response_a += chunk
                time_a = time.time() - start

                # Score the response (heuristic: length + structure)
                quality_a = self._score_response(response_a)
                self.record_variant_result(
                    agent_name, active.variant_id,
                    success=True,
                    response_quality=quality_a,
                    response_time=time_a,
                )
                results["active"].append({
                    "quality": quality_a,
                    "time": time_a,
                    "length": len(response_a),
                })
            except Exception as e:
                self.logger.error(f"Active variant test failed: {e}")
                results["active"].append({"error": str(e)})

            # Test challenger variant
            try:
                start = time.time()
                response_b = ""
                async for chunk in self.brain.chat_stream(
                    test_input, force_provider="claude"
                ):
                    response_b += chunk
                time_b = time.time() - start

                quality_b = self._score_response(response_b)
                self.record_variant_result(
                    agent_name, test.variant_id,
                    success=True,
                    response_quality=quality_b,
                    response_time=time_b,
                )
                results["test"].append({
                    "quality": quality_b,
                    "time": time_b,
                    "length": len(response_b),
                })
            except Exception as e:
                self.logger.error(f"Test variant test failed: {e}")
                results["test"].append({"error": str(e)})

        # Compare results
        comparison = self._compare_variants(
            agent_name, active.variant_id, test.variant_id
        )
        return comparison

    def _score_response(self, response: str) -> float:
        """Heuristic scoring of response quality (0.0 - 1.0).

        Factors: length, structure (bullet points, sections), variety.
        """
        if not response:
            return 0.0

        score = 0.3  # Base score for non-empty response

        # Length bonus (sweet spot 100-2000 chars)
        length = len(response)
        if 100 <= length <= 2000:
            score += 0.2
        elif 50 <= length < 100 or 2000 < length <= 4000:
            score += 0.1

        # Structure bonus
        if "-" in response or "•" in response:
            score += 0.1
        if "\n\n" in response:
            score += 0.1

        # Specificity bonus (numbers, specific terms)
        if any(c.isdigit() for c in response):
            score += 0.1

        # Clarity (not too short, not too long)
        if 3 <= len(response.split()) <= 500:
            score += 0.1

        return min(score, 1.0)

    # ------------------------------------------------------------------
    # Comparison & promotion
    # ------------------------------------------------------------------

    def _compare_variants(
        self, agent_name: str, variant_a_id: str, variant_b_id: str
    ) -> Dict[str, Any]:
        """Compare two variants and return metrics."""
        variant_a = self._find_variant(agent_name, variant_a_id)
        variant_b = self._find_variant(agent_name, variant_b_id)

        if not variant_a or not variant_b:
            return {"status": "variant_not_found"}

        # Compare success rates
        success_rate_a = (
            variant_a.success_count / max(variant_a.total_uses, 1)
        )
        success_rate_b = (
            variant_b.success_count / max(variant_b.total_uses, 1)
        )

        winner_id = None
        improvement = 0.0

        if variant_a.total_uses >= 3 and variant_b.total_uses >= 3:
            # Enough data to compare
            score_a = (
                success_rate_a * 0.4
                + variant_a.avg_response_quality * 0.4
                + (1.0 / max(variant_a.avg_response_time, 0.1)) * 0.2
            )
            score_b = (
                success_rate_b * 0.4
                + variant_b.avg_response_quality * 0.4
                + (1.0 / max(variant_b.avg_response_time, 0.1)) * 0.2
            )

            if score_b > score_a:
                winner_id = variant_b_id
                improvement = round(
                    ((score_b - score_a) / max(score_a, 0.01)) * 100, 1
                )
            else:
                winner_id = variant_a_id
                improvement = round(
                    ((score_a - score_b) / max(score_b, 0.01)) * 100, 1
                )

            # Auto-promote winner if significant improvement
            if (
                winner_id == variant_b_id
                and improvement > 10.0
                and variant_b.total_uses >= 5
            ):
                self._active_variants[agent_name] = variant_b_id
                self.logger.info(
                    f"Auto-promoted variant {variant_b_id} for agent "
                    f"'{agent_name}' ({improvement:.1f}% improvement)"
                )

        return {
            "status": "comparison_complete",
            "variant_a": {
                "id": variant_a_id,
                "success_rate": round(success_rate_a, 2),
                "avg_quality": round(variant_a.avg_response_quality, 2),
                "total_uses": variant_a.total_uses,
            },
            "variant_b": {
                "id": variant_b_id,
                "success_rate": round(success_rate_b, 2),
                "avg_quality": round(variant_b.avg_response_quality, 2),
                "total_uses": variant_b.total_uses,
            },
            "winner": winner_id,
            "improvement_pct": improvement,
        }

    def _find_variant(
        self, agent_name: str, variant_id: str
    ) -> Optional[PromptVariant]:
        """Find a specific variant by ID."""
        for v in self._variants.get(agent_name, []):
            if v.variant_id == variant_id:
                return v
        return None

    # ------------------------------------------------------------------
    # Agent evolution (LLM-powered)
    # ------------------------------------------------------------------

    async def evolve_agent(
        self, agent_name: str, performance_logs: str
    ) -> Dict[str, Any]:
        """Analyze an agent's performance and generate an optimized variant.

        Returns a dict with the new variant ID and patch description.
        """
        self.logger.info(
            f"Initiating self-evolution for agent: {agent_name}"
        )

        # Get current variant
        current = self.get_active_variant(agent_name)
        current_prompt = current.prompt_text if current else "No current prompt."

        if not self.brain:
            result = self._heuristic_evolution(agent_name, current_prompt)
            return result

        # LLM-powered evolution
        prompt = f"""Analyze the following performance logs for the {agent_name} agent and suggest an improved system prompt.

Current prompt:
{current_prompt}

Performance logs:
{performance_logs}

Generate an optimized version of the prompt that addresses the weaknesses shown in the logs.
Also provide a brief description of what changed and why.

Format your response as:
DESCRIPTION: [brief description of changes]
IMPROVED_PROMPT:
[the improved prompt text]"""

        llm_response = ""
        try:
            async for chunk in self.brain.chat_stream(prompt):
                llm_response += chunk
        except Exception as e:
            self.logger.error(f"LLM evolution failed: {e}")
            return self._heuristic_evolution(agent_name, current_prompt)

        # Parse the response
        description = ""
        improved_prompt = ""
        in_prompt = False

        for line in llm_response.split("\n"):
            if line.strip().upper().startswith("DESCRIPTION:"):
                description = line.split(":", 1)[1].strip()
                in_prompt = False
            elif line.strip().upper().startswith("IMPROVED_PROMPT:"):
                in_prompt = True
            elif in_prompt:
                improved_prompt += line + "\n"

        improved_prompt = improved_prompt.strip()
        if not improved_prompt:
            improved_prompt = current_prompt  # Fallback
            description = "No improvements generated; kept current prompt."

        # Register the new variant
        new_id = self.register_variant(
            agent_name, description, improved_prompt
        )

        # Record evolution cycle
        cycle = EvolutionCycle(
            cycle_id=hashlib.sha256(
                f"{agent_name}:{time.time()}".encode()
            ).hexdigest()[:12],
            timestamp=datetime.utcnow().isoformat(),
            agent_name=agent_name,
            input_metrics={"performance_logs_length": len(performance_logs)},
            variants_compared=[
                current.variant_id if current else "none",
                new_id,
            ],
            winning_variant=None,  # To be determined by A/B test
            patch=description,
        )
        self._evolution_history.append(cycle)

        self.logger.info(
            f"Agent '{agent_name}' evolved: new variant {new_id} ({description})"
        )

        return {
            "status": "evolved",
            "agent": agent_name,
            "new_variant_id": new_id,
            "description": description,
            "improved_prompt_preview": improved_prompt[:200] + "...",
        }

    def _heuristic_evolution(
        self, agent_name: str, current_prompt: str
    ) -> Dict[str, Any]:
        """Fallback evolution when LLM is unavailable."""
        # Simple heuristic improvements
        improved = current_prompt
        description = "Heuristic: Added clarity and structure directives."

        if "be concise" not in improved.lower():
            improved += "\n\nBe concise and direct in your responses."
            description = "Added conciseness directive."

        if "step by step" not in improved.lower():
            improved += "\n\nWhen solving complex problems, think step by step."
            description += " Added step-by-step thinking directive."

        new_id = self.register_variant(agent_name, description, improved)

        return {
            "status": "evolved_heuristic",
            "agent": agent_name,
            "new_variant_id": new_id,
            "description": description,
            "improved_prompt_preview": improved[:200] + "...",
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_evolution_status(self) -> str:
        """Return a summary of evolution activity."""
        total_cycles = len(self._evolution_history)
        total_variants = sum(
            len(v) for v in self._variants.values()
        )
        agents_tracked = len(self._variants)
        return (
            f"Friday has undergone {total_cycles} evolution cycles "
            f"across {agents_tracked} agents with {total_variants} "
            f"total prompt variants."
        )

    def get_variant_stats(self, agent_name: str) -> Dict[str, Any]:
        """Return statistics for all variants of an agent."""
        variants = self._variants.get(agent_name, [])
        active_id = self._active_variants.get(agent_name)
        return {
            "agent": agent_name,
            "total_variants": len(variants),
            "active_variant": active_id,
            "variants": [
                {
                    "id": v.variant_id,
                    "description": v.description,
                    "total_uses": v.total_uses,
                    "success_rate": round(
                        v.success_count / max(v.total_uses, 1), 2
                    ),
                    "avg_quality": round(v.avg_response_quality, 2),
                    "avg_time": round(v.avg_response_time, 2),
                    "is_active": v.variant_id == active_id,
                }
                for v in variants
            ],
        }

    def get_evolution_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent evolution cycle history."""
        return [
            {
                "cycle_id": c.cycle_id,
                "timestamp": c.timestamp,
                "agent": c.agent_name,
                "variants_compared": c.variants_compared,
                "winning_variant": c.winning_variant,
                "improvement_pct": c.improvement_pct,
                "patch": c.patch,
            }
            for c in self._evolution_history[-limit:]
        ]
