"""Episodic Memory — records experiences/events and their context.

Episodic memories are:
    - Unique events with timestamps, actors, context, outcomes
    - Linked to related episodes (chronological / causal)
    - Persistent (require provenance)
    - Subject to decay but resist decay if reinforced (retrieved frequently)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import Memory, MemoryID, MemoryMetadata, MemoryType, MemoryState, now_utc
from .provenance import Provenance

logger = logging.getLogger("friday.living_memory.episodic")


@dataclass
class EpisodicMemory(Memory):
    """A single episode / experience.

    Payload fields:
        event_type:    str (e.g. "user.conversation", "task.completed")
        actor_id:      str (who/what performed the event)
        participants:  List[str]
        context:       Dict[str, Any] (arbitrary context)
        outcome:       str ("success" | "failure" | "neutral")
        outcome_data:  Dict[str, Any]
        duration_ms:    int
        location:      str (logical or physical)
        related_episode_ids: List[str] (causal/temporal links)
    """
    type: MemoryType = MemoryType.EPISODIC

    @classmethod
    def create(
        cls,
        event_type: str,
        actor_id: str,
        context: Optional[Dict[str, Any]] = None,
        outcome: str = "neutral",
        participants: Optional[List[str]] = None,
        outcome_data: Optional[Dict[str, Any]] = None,
        duration_ms: int = 0,
        location: str = "",
        owner_id: str = "",
        tenant_id: str = "default",
        tags: Optional[List[str]] = None,
        confidence: float = 0.7,
        importance: float = 0.5,
        correlation_id: str = "",
        related_episode_ids: Optional[List[str]] = None,
    ) -> "EpisodicMemory":
        if not event_type:
            raise ValueError("event_type is required for episodic memory")
        if not actor_id:
            raise ValueError("actor_id is required for episodic memory")
        if outcome not in ("success", "failure", "neutral"):
            raise ValueError(f"Invalid outcome: {outcome}")
        payload = {
            "event_type": event_type,
            "actor_id": actor_id,
            "participants": list(participants or []),
            "context": dict(context or {}),
            "outcome": outcome,
            "outcome_data": dict(outcome_data or {}),
            "duration_ms": int(duration_ms),
            "location": location,
            "related_episode_ids": list(related_episode_ids or []),
        }
        return cls(
            id=MemoryID(),
            type=MemoryType.EPISODIC,
            state=MemoryState.CREATED,
            metadata=MemoryMetadata(
                owner_id=owner_id or actor_id,
                created_by=actor_id,
                tenant_id=tenant_id,
                tags=list(tags or []),
                confidence=confidence,
                importance=importance,
                correlation_id=correlation_id,
            ),
            payload=payload,
        )

    @property
    def event_type(self) -> str:
        return self.payload.get("event_type", "")

    @property
    def actor_id(self) -> str:
        return self.payload.get("actor_id", "")

    @property
    def outcome(self) -> str:
        return self.payload.get("outcome", "neutral")

    @property
    def related_episode_ids(self) -> List[str]:
        return list(self.payload.get("related_episode_ids", []))

    def link_related(self, other_episode_id: str) -> None:
        """Link this episode to another (causal/temporal)."""
        if other_episode_id and other_episode_id not in self.payload["related_episode_ids"]:
            self.payload["related_episode_ids"].append(other_episode_id)
            self.metadata.updated_at = now_utc()

    def matches_context(self, **context_keys) -> bool:
        """Check if this episode's context contains all the given key/value pairs."""
        ctx = self.payload.get("context", {})
        return all(ctx.get(k) == v for k, v in context_keys.items())


__all__ = ["EpisodicMemory"]
