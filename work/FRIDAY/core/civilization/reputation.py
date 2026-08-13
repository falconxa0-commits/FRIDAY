"""Reputation System — tracks citizen performance and adjusts trust.

Every citizen accumulates reputation based on:
    - Task success rate
    - Error frequency
    - Resource efficiency
    - Peer reviews
    - Founder feedback

Reputation affects:
    - Trust score (higher = more trusted)
    - Autonomy level (higher = less approval required)
    - Resource allocation (higher = more resources)
    - Task priority (higher = higher priority)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.civilization.citizen import Citizen

logger = logging.getLogger("friday.civilization.reputation")


@dataclass
class ReputationEvent:
    """A single reputation-affecting event."""
    citizen_id: str
    event_type: str  # "task_success", "task_failure", "error", "review", "founder_feedback"
    delta: int  # positive = good, negative = bad
    reason: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "citizen_id": self.citizen_id,
            "event_type": self.event_type,
            "delta": self.delta,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


class ReputationSystem:
    """Tracks and adjusts citizen reputation.

    Reputation is a 0-100 score that reflects the civilization's
    trust in a citizen. It affects autonomy, resource allocation,
    and task priority.

    Scoring:
        - Task success: +5
        - Task failure: -10
        - Error: -3
        - Positive review: +8
        - Negative review: -5
        - Founder praise: +15
        - Founder warning: -20

    Autonomy levels:
        - 0-20: Requires approval for all actions
        - 21-50: Auto-approve low-risk actions
        - 51-80: Auto-approve medium-risk actions
        - 81-100: Auto-approve high-risk actions (except critical)
    """

    SCORE_DELTAS = {
        "task_success": 5,
        "task_failure": -10,
        "error": -3,
        "review_positive": 8,
        "review_negative": -5,
        "founder_praise": 15,
        "founder_warning": -20,
    }

    def __init__(self):
        self._events: Dict[str, List[ReputationEvent]] = {}  # citizen_id → events
        self._scores: Dict[str, int] = {}  # citizen_id → score

    def record_event(
        self,
        citizen_id: str,
        event_type: str,
        reason: str = "",
        custom_delta: Optional[int] = None,
    ) -> int:
        """Record a reputation event and update the citizen's score.

        Args:
            citizen_id: The affected citizen.
            event_type: Type of event (see SCORE_DELTAS).
            reason: Human-readable reason.
            custom_delta: Override the default delta.

        Returns:
            The new reputation score.
        """
        delta = custom_delta if custom_delta is not None else self.SCORE_DELTAS.get(event_type, 0)

        event = ReputationEvent(
            citizen_id=citizen_id,
            event_type=event_type,
            delta=delta,
            reason=reason,
        )

        if citizen_id not in self._events:
            self._events[citizen_id] = []
        self._events[citizen_id].append(event)

        current = self._scores.get(citizen_id, 50)  # default trust = 50
        new_score = max(0, min(100, current + delta))
        self._scores[citizen_id] = new_score

        logger.info(
            f"Reputation: {citizen_id[:8]} {event_type} ({delta:+d}) → "
            f"score={new_score} — {reason}"
        )
        return new_score

    def get_score(self, citizen_id: str) -> int:
        """Get the current reputation score for a citizen."""
        return self._scores.get(citizen_id, 50)

    def get_autonomy_level(self, citizen_id: str) -> str:
        """Get the autonomy level for a citizen based on their score.

        Returns:
            "none" (0-20), "low" (21-50), "medium" (51-80), "high" (81-100)
        """
        score = self.get_score(citizen_id)
        if score <= 20:
            return "none"
        elif score <= 50:
            return "low"
        elif score <= 80:
            return "medium"
        else:
            return "high"

    def can_auto_approve(self, citizen_id: str, risk_level: str) -> bool:
        """Check if a citizen can auto-approve an action at the given risk level.

        Args:
            citizen_id: The citizen requesting approval.
            risk_level: "low", "medium", "high", "critical"

        Returns:
            True if auto-approval is allowed.
        """
        autonomy = self.get_autonomy_level(citizen_id)
        auto_approve_map = {
            "none": [],                          # No auto-approval
            "low": ["low"],                      # Auto-approve low risk only
            "medium": ["low", "medium"],         # Auto-approve low + medium
            "high": ["low", "medium", "high"],   # Auto-approve everything except critical
        }
        return risk_level in auto_approve_map.get(autonomy, [])

    def get_history(self, citizen_id: str, limit: int = 50) -> List[ReputationEvent]:
        """Get reputation history for a citizen."""
        events = self._events.get(citizen_id, [])
        return events[-limit:]

    def reset_score(self, citizen_id: str) -> None:
        """Reset a citizen's score to default (50)."""
        self._scores[citizen_id] = 50
        logger.info(f"Reset reputation score for {citizen_id[:8]} to 50")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_citizens_tracked": len(self._scores),
            "total_events": sum(len(e) for e in self._events.values()),
            "average_score": (
                sum(self._scores.values()) / len(self._scores)
                if self._scores else 50
            ),
            "high_trust": sum(1 for s in self._scores.values() if s > 80),
            "low_trust": sum(1 for s in self._scores.values() if s <= 20),
        }

    async def is_healthy(self) -> bool:
        return True

    async def stop(self) -> None:
        logger.info("Reputation system stopped")
