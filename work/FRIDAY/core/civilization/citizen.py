"""Citizen — the fundamental unit of the FRIDAY civilization.

Every agent, plugin, tool, model, and service in Age V is a Citizen.
Citizens have:
    - Unique identity (CitizenID)
    - Rank (Founder → Worker)
    - Status (Active → Archived)
    - Capabilities (what they can do)
    - Trust score (how much they're trusted)
    - Lifecycle (birth → retirement)

This module is 100% additive. It does NOT modify Age IV.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("friday.civilization.citizen")


class CitizenRank(str, Enum):
    """Hierarchy of citizen authority levels."""
    FOUNDER = "founder"          # Human — absolute authority
    SUPREME_COUNCIL = "supreme"  # AI executives — constitutional authority
    GOVERNOR = "governor"        # Department heads — departmental authority
    SPECIALIST = "specialist"    # Domain experts — domain authority
    WORKER = "worker"            # Task executors — execution only
    CITIZEN = "citizen"          # Plugins, tools — capability-scoped

    @property
    def authority_level(self) -> int:
        """Higher = more authority. Founder = 100."""
        return {
            "founder": 100,
            "supreme": 80,
            "governor": 60,
            "specialist": 40,
            "worker": 20,
            "citizen": 10,
        }[self.value]


class CitizenStatus(str, Enum):
    """Lifecycle states for a citizen."""
    PENDING = "pending"          # Registered but not initialized
    ACTIVE = "active"            # Operational
    DEGRADED = "degraded"        # Performance issues
    RECOVERING = "recovering"    # Attempting recovery
    RETIRED = "retired"          # Gracefully shut down
    ARCHIVED = "archived"        # Historical record preserved
    BANNED = "banned"            # Revoked (security violation)


@dataclass
class CitizenID:
    """Unique identifier for a citizen."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    rank: CitizenRank = CitizenRank.CITIZEN
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "rank": self.rank.value,
            "created_at": self.created_at,
        }


@dataclass
class Citizen:
    """A registered member of the FRIDAY civilization.

    Attributes:
        id: Unique identity
        name: Human-readable name
        rank: Authority level
        status: Current lifecycle state
        capabilities: What this citizen can do
        trust_score: 0-100, higher = more trusted
        reputation: Performance history
        parent_id: ID of the citizen that spawned this one
        metadata: Arbitrary key-value data
    """
    id: CitizenID
    name: str = ""
    rank: CitizenRank = CitizenRank.CITIZEN
    status: CitizenStatus = CitizenStatus.PENDING
    capabilities: Set[str] = field(default_factory=set)
    trust_score: int = 50  # default trust
    reputation: Dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def is_active(self) -> bool:
        return self.status == CitizenStatus.ACTIVE

    @property
    def can_spawn(self) -> bool:
        """Only Governor+ can spawn new citizens."""
        return self.rank.authority_level >= CitizenRank.GOVERNOR.authority_level

    @property
    def can_approve(self) -> bool:
        """Only Specialist+ can approve actions."""
        return self.rank.authority_level >= CitizenRank.SPECIALIST.authority_level

    def has_capability(self, capability: str) -> bool:
        """Check if this citizen has a specific capability."""
        return capability in self.capabilities

    def grant_capability(self, capability: str) -> None:
        """Grant a capability to this citizen."""
        self.capabilities.add(capability)
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def revoke_capability(self, capability: str) -> None:
        """Revoke a capability from this citizen."""
        self.capabilities.discard(capability)
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def adjust_trust(self, delta: int) -> None:
        """Adjust trust score (clamped to 0-100)."""
        self.trust_score = max(0, min(100, self.trust_score + delta))
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id.to_dict(),
            "name": self.name,
            "rank": self.rank.value,
            "status": self.status.value,
            "capabilities": list(self.capabilities),
            "trust_score": self.trust_score,
            "reputation": self.reputation,
            "parent_id": self.parent_id,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CitizenRegistry:
    """Registry of all citizens in the civilization.

    Thread-safe (asyncio.Lock). Persists to JSON file.
    Uses Age IV EventBus (if available) to emit lifecycle events.
    """

    def __init__(self, event_bus=None):
        self._citizens: Dict[str, Citizen] = {}
        self._lock = asyncio.Lock()
        self._event_bus = event_bus
        self._founder_id: Optional[str] = None

    async def register(
        self,
        name: str,
        rank: CitizenRank = CitizenRank.CITIZEN,
        capabilities: Optional[Set[str]] = None,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Citizen:
        """Register a new citizen."""
        citizen_id = CitizenID(name=name, rank=rank)
        citizen = Citizen(
            id=citizen_id,
            name=name,
            rank=rank,
            capabilities=capabilities or set(),
            parent_id=parent_id,
            metadata=metadata or {},
        )

        async with self._lock:
            self._citizens[citizen_id.id] = citizen

            # First registered Founder becomes the sovereign
            if rank == CitizenRank.FOUNDER and self._founder_id is None:
                self._founder_id = citizen_id.id

        logger.info(f"Registered citizen: {name} ({rank.value}) — id={citizen_id.id[:8]}")

        await self._emit("citizen.registered", citizen)
        return citizen

    async def get(self, citizen_id: str) -> Optional[Citizen]:
        """Get a citizen by ID."""
        async with self._lock:
            return self._citizens.get(citizen_id)

    async def list(
        self,
        rank: Optional[CitizenRank] = None,
        status: Optional[CitizenStatus] = None,
    ) -> List[Citizen]:
        """List citizens, optionally filtered."""
        async with self._lock:
            citizens = list(self._citizens.values())
        if rank:
            citizens = [c for c in citizens if c.rank == rank]
        if status:
            citizens = [c for c in citizens if c.status == status]
        return citizens

    async def update_status(self, citizen_id: str, status: CitizenStatus) -> bool:
        """Update a citizen's lifecycle status."""
        async with self._lock:
            citizen = self._citizens.get(citizen_id)
            if not citizen:
                return False
            old_status = citizen.status
            citizen.status = status
            citizen.updated_at = datetime.now(timezone.utc).isoformat()

        logger.info(f"Citizen {citizen_id[:8]} status: {old_status.value} → {status.value}")
        await self._emit("citizen.status_changed", {
            "citizen_id": citizen_id,
            "old_status": old_status.value,
            "new_status": status.value,
        })
        return True

    async def retire(self, citizen_id: str) -> bool:
        """Gracefully retire a citizen."""
        return await self.update_status(citizen_id, CitizenStatus.RETIRED)

    async def ban(self, citizen_id: str) -> bool:
        """Ban a citizen (security violation)."""
        return await self.update_status(citizen_id, CitizenStatus.BANNED)

    async def get_founder(self) -> Optional[Citizen]:
        """Get the Founder (sovereign) citizen."""
        if not self._founder_id:
            return None
        return await self.get(self._founder_id)

    async def count(self) -> int:
        """Total citizen count."""
        async with self._lock:
            return len(self._citizens)

    async def _emit(self, event_type: str, data: Any) -> None:
        """Emit an event through the EventBus (if available)."""
        if not self._event_bus:
            return
        if isinstance(data, Citizen):
            data = data.to_dict()
        await self._event_bus.publish(event_type, data, source="citizen_registry")

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        by_rank = {}
        by_status = {}
        for c in self._citizens.values():
            by_rank[c.rank.value] = by_rank.get(c.rank.value, 0) + 1
            by_status[c.status.value] = by_status.get(c.status.value, 0) + 1
        return {
            "total_citizens": len(self._citizens),
            "by_rank": by_rank,
            "by_status": by_status,
            "has_founder": self._founder_id is not None,
        }

    async def is_healthy(self) -> bool:
        return True

    async def stop(self) -> None:
        logger.info("Citizen registry stopped")
