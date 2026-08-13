"""Civilization Manager — coordinates the civilization hierarchy.

Manages:
    - Citizen hierarchy (Founder → Governors → Specialists → Workers)
    - Department organization
    - Citizen lifecycle coordination
    - Communication routing
    - Resource allocation

Uses Age IV EventBus (if available) for civilization-wide events.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.civilization.citizen import (
    Citizen, CitizenID, CitizenRank, CitizenStatus, CitizenRegistry,
)
from core.civilization.reputation import ReputationSystem

logger = logging.getLogger("friday.civilization.manager")


@dataclass
class Department:
    """A department in the civilization."""
    name: str
    governor_id: str = ""
    member_ids: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "governor_id": self.governor_id,
            "member_count": len(self.member_ids),
            "capabilities": self.capabilities,
            "created_at": self.created_at,
        }


class CivilizationManager:
    """Manages the FRIDAY civilization.

    The CivilizationManager is the top-level coordinator for Age V.
    It manages the citizen registry, departments, and the civilization
    lifecycle.

    It uses (but does NOT modify) Age IV services:
        - EventBus (for civilization events)
        - PolicyEngine (for governance decisions)
        - CapabilityRegistry (for service discovery)
    """

    def __init__(
        self,
        registry: Optional[CitizenRegistry] = None,
        reputation: Optional[ReputationSystem] = None,
        event_bus=None,
    ):
        self.registry = registry or CitizenRegistry(event_bus=event_bus)
        self.reputation = reputation or ReputationSystem()
        self._event_bus = event_bus
        self._departments: Dict[str, Department] = {}
        self._initialized = False

    async def initialize(self, founder_name: str = "Founder") -> Citizen:
        """Initialize the civilization with a Founder.

        This creates the Founder citizen and sets up the initial
        civilization structure.

        Args:
            founder_name: Name of the human Founder.

        Returns:
            The Founder Citizen.
        """
        if self._initialized:
            return await self.registry.get_founder()

        # Register the Founder (sovereign)
        founder = await self.registry.register(
            name=founder_name,
            rank=CitizenRank.FOUNDER,
            capabilities={"*"},  # all capabilities
            metadata={"type": "human"},
        )
        founder.status = CitizenStatus.ACTIVE
        founder.trust_score = 100
        self.reputation._scores[founder.id.id] = 100

        # Create default departments
        default_departments = [
            ("Runtime", ["runtime.manage", "runtime.observe"]),
            ("Memory", ["memory.read", "memory.write", "memory.govern"]),
            ("Knowledge", ["knowledge.read", "knowledge.write", "knowledge.query"]),
            ("Security", ["security.audit", "security.policy", "security.scan"]),
            ("Planning", ["planning.create", "planning.execute", "planning.review"]),
            ("Engineering", ["engineering.code", "engineering.review", "engineering.deploy"]),
            ("Communication", ["comm.send", "comm.receive", "comm.broadcast"]),
        ]

        for dept_name, caps in default_departments:
            dept = Department(name=dept_name, capabilities=caps)
            self._departments[dept_name] = dept

        self._initialized = True

        if self._event_bus:
            await self._event_bus.publish(
                "civilization.initialized",
                {"founder": founder.to_dict(), "departments": len(self._departments)},
                source="civilization_manager",
            )

        logger.info(f"Civilization initialized — Founder: {founder_name}, "
                     f"{len(self._departments)} departments")

        return founder

    async def spawn_citizen(
        self,
        name: str,
        rank: CitizenRank = CitizenRank.WORKER,
        parent_id: Optional[str] = None,
        capabilities: Optional[set] = None,
        department: Optional[str] = None,
    ) -> Citizen:
        """Spawn a new citizen in the civilization.

        Args:
            name: Citizen name.
            rank: Authority level.
            parent_id: ID of the spawning citizen.
            capabilities: Set of capabilities.
            department: Department to assign to.

        Returns:
            The new Citizen.
        """
        citizen = await self.registry.register(
            name=name,
            rank=rank,
            capabilities=capabilities or set(),
            parent_id=parent_id,
        )
        citizen.status = CitizenStatus.ACTIVE

        if department and department in self._departments:
            self._departments[department].member_ids.append(citizen.id.id)

        if self._event_bus:
            await self._event_bus.publish(
                "civilization.citizen_spawned",
                citizen.to_dict(),
                source="civilization_manager",
            )

        logger.info(f"Spawned citizen: {name} ({rank.value}) in {department or 'general'}")
        return citizen

    async def assign_to_department(self, citizen_id: str, department: str) -> bool:
        """Assign a citizen to a department."""
        if department not in self._departments:
            return False
        if citizen_id not in self._departments[department].member_ids:
            self._departments[department].member_ids.append(citizen_id)
        return True

    async def appoint_governor(self, citizen_id: str, department: str) -> bool:
        """Appoint a citizen as governor of a department."""
        if department not in self._departments:
            return False
        citizen = await self.registry.get(citizen_id)
        if not citizen:
            return False
        self._departments[department].governor_id = citizen_id
        citizen.rank = CitizenRank.GOVERNOR
        logger.info(f"Appointed {citizen.name} as Governor of {department}")
        return True

    async def retire_citizen(self, citizen_id: str) -> bool:
        """Gracefully retire a citizen."""
        result = await self.registry.retire(citizen_id)
        if result and self._event_bus:
            await self._event_bus.publish(
                "civilization.citizen_retired",
                {"citizen_id": citizen_id},
                source="civilization_manager",
            )
        return result

    async def get_civilization_status(self) -> Dict[str, Any]:
        """Get full civilization status."""
        stats = self.registry.get_stats()
        return {
            "initialized": self._initialized,
            "total_citizens": stats["total_citizens"],
            "by_rank": stats["by_rank"],
            "by_status": stats["by_status"],
            "departments": {n: d.to_dict() for n, d in self._departments.items()},
            "reputation_stats": self.reputation.get_stats(),
            "has_founder": stats["has_founder"],
        }

    def get_department(self, name: str) -> Optional[Department]:
        """Get a department by name."""
        return self._departments.get(name)

    def list_departments(self) -> List[Department]:
        """List all departments."""
        return list(self._departments.values())

    async def is_healthy(self) -> bool:
        """Check if the civilization is healthy."""
        if not self._initialized:
            return False
        founder = await self.registry.get_founder()
        return founder is not None and founder.is_active

    async def stop(self) -> None:
        """Shut down the civilization."""
        if self._event_bus:
            await self._event_bus.publish(
                "civilization.shutdown",
                {},
                source="civilization_manager",
            )
        await self.registry.stop()
        await self.reputation.stop()
        self._initialized = False
        logger.info("Civilization shut down")
