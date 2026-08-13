"""Decay Engine — bounded memory with configurable decay policies.

Decay is governed (Constitution Article 4). Memories do NOT decay
autonomously: a decay pass is invoked by the manager on a schedule.

Decay policy:
    - Compute a "decay priority" for each memory.
    - Memories below the archival threshold → ARCHIVED.
    - Memories below the forget threshold → FORGOTTEN (requires approval).
    - Reinforced memories resist decay (reinforcement_count bonus).
    - Consolidated memories resist decay (state bonus).

Bounded resource homeostasis:
    - max_total_memories: hard cap; if exceeded, lowest-priority memories archived
    - max_age_seconds: memories older than this become eligible for archival
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    Memory, MemoryState, MemoryType, now_utc, LifecycleError,
    validate_transition,
)

logger = logging.getLogger("friday.living_memory.decay")


@dataclass
class DecayConfig:
    """Configurable decay policy."""
    # Decay rate (higher = faster decay). Each access counts as recency.
    base_decay_rate: float = 0.01  # per day
    recency_halflife_seconds: int = 86400  # 1 day halflife
    reinforcement_resistance: float = 0.1  # each reinforcement slows decay by 10%
    consolidation_resistance: float = 0.5  # consolidated state resists decay by 50%
    archival_threshold: float = 0.2  # below → ARCHIVED
    forget_threshold: float = 0.05  # below → FORGOTTEN (requires approval)
    max_total_memories: int = 100_000
    max_age_seconds: int = 30 * 86400  # 30 days
    max_per_pass: int = 500  # cap per decay pass (homeostasis)


@dataclass
class DecayCandidate:
    """A memory being evaluated for decay."""
    memory_id: str
    memory_type: MemoryType
    state: MemoryState
    decay_score: float  # 0..1, lower = more decayed
    age_seconds: float
    recommendation: str  # "keep" | "archive" | "forget"
    reason: str = ""


@dataclass
class DecayResult:
    """Result of a decay pass."""
    candidates_evaluated: int = 0
    archive_count: int = 0
    forget_count: int = 0
    keep_count: int = 0
    forced_archive_count: int = 0  # exceeded max_total_memories
    candidates: List[DecayCandidate] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


class DecayEngine:
    """Computes decay scores + recommendations.

    The engine is stateless. Manager invokes `score_candidates`, then
    applies transitions for the recommended ones (with approval for forget).
    """

    def __init__(self, config: Optional[DecayConfig] = None):
        self._config = config or DecayConfig()

    @property
    def config(self) -> DecayConfig:
        return self._config

    def _age_seconds(self, memory: Memory) -> float:
        """Seconds since last update."""
        now = datetime.now(timezone.utc)
        if not memory.metadata.updated_at:
            return 1e9
        try:
            last = datetime.fromisoformat(memory.metadata.updated_at)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            return max(0.0, (now - last).total_seconds())
        except Exception:
            return 1e9

    def score_candidate(self, memory: Memory) -> DecayCandidate:
        """Compute decay score for a single memory.

        Score formula (0..1, higher = more decayed):
            base = base_decay_rate * (age_days)
            resistance = reinforcement_resistance * reinforcement_count
                       + consolidation_resistance (if consolidated)
            decay_score = base * exp(-resistance)
            final_score = 1 - exp(-decay_score)
        """
        age_s = self._age_seconds(memory)
        age_days = age_s / 86400.0
        c = self._config

        # Base decay
        base = c.base_decay_rate * age_days

        # Resistance from reinforcement count
        rcount = memory.metadata.reinforcement_count
        resistance = c.reinforcement_resistance * rcount
        if memory.state == MemoryState.CONSOLIDATED:
            resistance += c.consolidation_resistance
        if memory.state == MemoryState.REINFORCED:
            resistance += c.consolidation_resistance * 0.5

        # Decay score (0..1)
        decay_score = base * math.exp(-resistance)
        final_score = 1.0 - math.exp(-decay_score)
        final_score = max(0.0, min(1.0, final_score))

        # Recommendation
        recommendation = "keep"
        reason = ""
        if memory.state in (MemoryState.FORGOTTEN, MemoryState.QUARANTINED):
            recommendation = "keep"
            reason = f"terminal/quarantine state {memory.state.value}"
        elif memory.state == MemoryState.ARCHIVED:
            # Already archived — leave (will be forgotten via separate policy)
            recommendation = "keep"
            reason = "already archived"
        elif final_score >= (1.0 - c.forget_threshold):
            recommendation = "forget"
            reason = (
                f"decay_score {final_score:.3f} >= forget_threshold "
                f"{1.0 - c.forget_threshold:.3f}"
            )
        elif final_score >= (1.0 - c.archival_threshold):
            recommendation = "archive"
            reason = (
                f"decay_score {final_score:.3f} >= archival_threshold "
                f"{1.0 - c.archival_threshold:.3f}"
            )

        # Age override
        if age_s > c.max_age_seconds and recommendation == "keep":
            recommendation = "archive"
            reason = f"age {age_s:.0f}s exceeds max_age {c.max_age_seconds}s"

        return DecayCandidate(
            memory_id=memory.id.value,
            memory_type=memory.type,
            state=memory.state,
            decay_score=round(final_score, 6),
            age_seconds=age_s,
            recommendation=recommendation,
            reason=reason,
        )

    def score_candidates(self, memories: List[Memory]) -> DecayResult:
        """Score a batch of memories for decay."""
        result = DecayResult(started_at=now_utc())
        for mem in memories:
            try:
                cand = self.score_candidate(mem)
                result.candidates.append(cand)
                result.candidates_evaluated += 1
                if cand.recommendation == "archive":
                    result.archive_count += 1
                elif cand.recommendation == "forget":
                    result.forget_count += 1
                else:
                    result.keep_count += 1
            except Exception as e:
                result.errors.append(f"{mem.id.value}: {e}")
        # Cap per pass (homeostasis)
        result.candidates = result.candidates[: self._config.max_per_pass]
        result.finished_at = now_utc()
        return result

    def select_forced_archive(
        self, memories: List[Memory], excess: int
    ) -> List[str]:
        """When total memory exceeds cap, select `excess` lowest-priority for archival.

        Priority = importance * 0.4 + confidence * 0.3 + reinforcement_norm * 0.3
        Lowest priority → archived first.
        """
        if excess <= 0:
            return []

        def priority(mem: Memory) -> float:
            rcount = mem.metadata.reinforcement_count
            rep_norm = 1.0 / (1.0 + math.exp(-rcount / 5.0)) if rcount > 0 else 0.0
            return (
                0.4 * mem.metadata.importance
                + 0.3 * mem.metadata.confidence
                + 0.3 * rep_norm
            )

        # Don't touch FORGOTTEN/QUARANTINED
        eligible = [m for m in memories if m.state not in (
            MemoryState.FORGOTTEN, MemoryState.QUARANTINED, MemoryState.ARCHIVED,
        )]
        eligible.sort(key=priority)  # ascending = lowest priority first
        return [m.id.value for m in eligible[:excess]]


__all__ = [
    "DecayConfig", "DecayCandidate", "DecayResult", "DecayEngine",
]
