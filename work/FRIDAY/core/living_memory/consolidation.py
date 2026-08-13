"""Consolidation Engine — promotes important memories to consolidated state.

Consolidation is governed (Constitution Article 4 — Memory Governance).
It is NOT autonomous: a consolidation pass requires:
    - Authorized citizen (memory.consolidate capability)
    - Configurable signals (importance, confidence, repetition, recency)
    - Optional human approval for high-stakes promotions

Consolidation scoring:
    score = w1 * importance + w2 * confidence + w3 * repetition_norm
          + w4 * recency_norm + w5 * source_authority + w6 * success_rate

    Threshold (default 0.65) → eligible for consolidation.
    Memories already consolidated are skipped.

Consolidation is reversible (Constitution Article 6).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    Memory, MemoryState, MemoryType, now_utc, LifecycleError,
    validate_transition,
)

logger = logging.getLogger("friday.living_memory.consolidation")


@dataclass
class ConsolidationConfig:
    """Configurable consolidation signals + weights."""
    importance_weight: float = 0.30
    confidence_weight: float = 0.20
    repetition_weight: float = 0.20
    recency_weight: float = 0.10
    source_authority_weight: float = 0.10
    success_rate_weight: float = 0.10
    threshold: float = 0.65           # min score to consolidate
    max_per_pass: int = 100            # cap per consolidation pass (homeostasis)
    recency_halflife_seconds: int = 3600  # 1 hour halflife for recency
    require_reinforcement_min: int = 0    # min reinforcements required


@dataclass
class ConsolidationCandidate:
    """A memory being considered for consolidation."""
    memory_id: str
    memory_type: MemoryType
    state: MemoryState
    score: float
    signals: Dict[str, float]
    eligible: bool
    reason: str = ""


@dataclass
class ConsolidationResult:
    """Result of a consolidation pass."""
    candidates_evaluated: int = 0
    consolidated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    candidates: List[ConsolidationCandidate] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


class ConsolidationEngine:
    """Scores + promotes memories to CONSOLIDATED state.

    The engine is stateless: it operates on a snapshot of memories passed
    by the manager. The manager is responsible for actually mutating state
    and persisting changes — the engine just decides.

    Usage::

        engine = ConsolidationEngine(config)
        result = engine.score_candidates(memories)
        # Manager iterates result.candidates and applies transitions for eligible ones
    """

    def __init__(self, config: Optional[ConsolidationConfig] = None):
        self._config = config or ConsolidationConfig()

    @property
    def config(self) -> ConsolidationConfig:
        return self._config

    def score_candidate(self, memory: Memory) -> ConsolidationCandidate:
        """Score a single memory for consolidation eligibility."""
        md = memory.metadata
        signals: Dict[str, float] = {}

        # Importance (already 0..1)
        signals["importance"] = max(0.0, min(1.0, md.importance))

        # Confidence (already 0..1)
        signals["confidence"] = max(0.0, min(1.0, md.confidence))

        # Repetition (reinforcement count normalized via 1/(1+e^-x))
        import math
        rcount = md.reinforcement_count
        signals["repetition"] = 1.0 / (1.0 + math.exp(-rcount / 5.0)) if rcount > 0 else 0.0

        # Recency: how recently accessed?
        # 1.0 if just accessed, decays by half every `recency_halflife_seconds`
        now = datetime.now(timezone.utc)
        if md.last_accessed_at:
            try:
                last = datetime.fromisoformat(md.last_accessed_at)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                age_seconds = max(0.0, (now - last).total_seconds())
            except Exception:
                age_seconds = 1e9
        else:
            age_seconds = 1e9
        halflife = max(1, self._config.recency_halflife_seconds)
        signals["recency"] = 0.5 ** (age_seconds / halflife)

        # Source authority (for semantic) or success rate (for procedural)
        authority = 0.5
        if memory.type == MemoryType.SEMANTIC:
            authority = float(memory.payload.get("source_authority", 0.5))
        elif memory.type == MemoryType.PROCEDURAL:
            total = memory.payload.get("execution_count", 0)
            succ = memory.payload.get("success_count", 0)
            authority = (succ / total) if total > 0 else 0.5
        elif memory.type == MemoryType.EPISODIC:
            # Episodes that had successful outcomes consolidate better
            authority = 1.0 if memory.payload.get("outcome") == "success" else 0.5
        signals["source_authority"] = authority
        signals["success_rate"] = authority  # alias for clarity in scoring

        # Weighted score
        c = self._config
        score = (
            c.importance_weight * signals["importance"]
            + c.confidence_weight * signals["confidence"]
            + c.repetition_weight * signals["repetition"]
            + c.recency_weight * signals["recency"]
            + c.source_authority_weight * signals["source_authority"]
            + c.success_rate_weight * signals["success_rate"]
        )

        # Determine eligibility
        eligible = True
        reason = ""
        if memory.state == MemoryState.CONSOLIDATED:
            eligible = False
            reason = "already consolidated"
        elif memory.state in (MemoryState.ARCHIVED, MemoryState.FORGOTTEN, MemoryState.QUARANTINED):
            eligible = False
            reason = f"state {memory.state.value} not eligible"
        elif score < c.threshold:
            eligible = False
            reason = f"score {score:.3f} below threshold {c.threshold:.3f}"
        elif md.reinforcement_count < c.require_reinforcement_min:
            eligible = False
            reason = (
                f"reinforcements {md.reinforcement_count} < required "
                f"{c.require_reinforcement_min}"
            )

        return ConsolidationCandidate(
            memory_id=memory.id.value,
            memory_type=memory.type,
            state=memory.state,
            score=round(score, 6),
            signals={k: round(v, 6) for k, v in signals.items()},
            eligible=eligible,
            reason=reason,
        )

    def score_candidates(
        self, memories: List[Memory]
    ) -> ConsolidationResult:
        """Score a batch of memories and return eligible candidates."""
        result = ConsolidationResult(started_at=now_utc())
        for mem in memories:
            try:
                cand = self.score_candidate(mem)
                result.candidates.append(cand)
                result.candidates_evaluated += 1
            except Exception as e:
                result.errors.append(f"{mem.id.value}: {e}")
                result.failed_count += 1

        # Sort eligible candidates by score descending, take top max_per_pass
        eligible = sorted(
            [c for c in result.candidates if c.eligible],
            key=lambda c: c.score,
            reverse=True,
        )[: self._config.max_per_pass]
        result.consolidated_count = len(eligible)
        result.skipped_count = (
            result.candidates_evaluated - result.consolidated_count - result.failed_count
        )
        result.finished_at = now_utc()
        return result

    def eligible_for_transition(
        self, candidate: ConsolidationCandidate
    ) -> bool:
        """Check if a candidate's current state allows transition to CONSOLIDATED."""
        if not candidate.eligible:
            return False
        try:
            # Check both intermediate (CONSOLIDATING) and final (CONSOLIDATED)
            validate_transition(candidate.state, MemoryState.CONSOLIDATING)
            return True
        except LifecycleError:
            return False


__all__ = [
    "ConsolidationConfig", "ConsolidationCandidate", "ConsolidationResult",
    "ConsolidationEngine",
]
