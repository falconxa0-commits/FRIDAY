"""Knowledge Graph — Merge Conflict + Resolver.

When two extraction passes produce conflicting information about the same
entity, a MergeConflict is created. Resolution requires governance.

Conflict types:
    - NAME_COLLISION: two entities have the same canonical name but different IDs
    - ATTRIBUTE_CONTRADICTION: same entity has conflicting attribute values
    - RELATIONSHIP_CONFLICT: two relationships with same endpoints but different types

Resolution strategies (governed):
    - KEEP_A: keep entity_a, archive entity_b
    - KEEP_B: keep entity_b, archive entity_a
    - MERGE: merge attributes + aliases of both into entity_a
    - SPLIT: keep both, but disambiguate (rename one)

High-stakes conflicts (e.g. merging PERSON entities) require Founder approval.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.knowledge_graph.merge")


class ConflictType(str, Enum):
    NAME_COLLISION = "name_collision"
    ATTRIBUTE_CONTRADICTION = "attribute_contradiction"
    RELATIONSHIP_CONFLICT = "relationship_conflict"


class ConflictResolution(str, Enum):
    PENDING = "pending"
    KEEP_A = "keep_a"
    KEEP_B = "keep_b"
    MERGE = "merge"
    SPLIT = "split"
    INVALID = "invalid"  # false alarm


# Entity types where merging requires Founder approval (high-stakes)
HIGH_STAKES_TYPES = {"person", "organization"}


@dataclass
class MergeConflict:
    """A merge conflict between two entities or relationships."""
    id: str
    conflict_type: ConflictType
    entity_a_id: str
    entity_b_id: str
    description: str = ""
    evidence_a: List[str] = field(default_factory=list)  # memory IDs supporting A
    evidence_b: List[str] = field(default_factory=list)  # memory IDs supporting B
    status: ConflictResolution = ConflictResolution.PENDING
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: str = ""
    resolved_by: str = ""
    resolution_notes: str = ""
    requires_founder_approval: bool = False
    tenant_id: str = "default"
    attribute_name: str = ""  # for ATTRIBUTE_CONTRADICTION
    attribute_value_a: Any = None
    attribute_value_b: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "conflict_type": self.conflict_type.value,
            "entity_a_id": self.entity_a_id,
            "entity_b_id": self.entity_b_id,
            "description": self.description,
            "evidence_a": list(self.evidence_a),
            "evidence_b": list(self.evidence_b),
            "status": self.status.value,
            "detected_at": self.detected_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "resolution_notes": self.resolution_notes,
            "requires_founder_approval": self.requires_founder_approval,
            "tenant_id": self.tenant_id,
            "attribute_name": self.attribute_name,
            "attribute_value_a": self.attribute_value_a,
            "attribute_value_b": self.attribute_value_b,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MergeConflict":
        return cls(
            id=d["id"],
            conflict_type=ConflictType(d.get("conflict_type", "name_collision")),
            entity_a_id=d["entity_a_id"],
            entity_b_id=d["entity_b_id"],
            description=d.get("description", ""),
            evidence_a=list(d.get("evidence_a", [])),
            evidence_b=list(d.get("evidence_b", [])),
            status=ConflictResolution(d.get("status", "pending")),
            detected_at=d.get("detected_at", ""),
            resolved_at=d.get("resolved_at", ""),
            resolved_by=d.get("resolved_by", ""),
            resolution_notes=d.get("resolution_notes", ""),
            requires_founder_approval=bool(d.get("requires_founder_approval", False)),
            tenant_id=d.get("tenant_id", "default"),
            attribute_name=d.get("attribute_name", ""),
            attribute_value_a=d.get("attribute_value_a"),
            attribute_value_b=d.get("attribute_value_b"),
        )


class MergeResolver:
    """Detects + resolves merge conflicts.

    The resolver is stateless: it takes a list of entities + relationships
    and returns detected conflicts. Resolution is done via the manager
    (which composes with M3 governance for Founder approval).
    """

    def __init__(self, max_conflicts_per_tenant: int = 1000):
        if max_conflicts_per_tenant < 1 or max_conflicts_per_tenant > 10000:
            raise ValueError("max_conflicts_per_tenant must be 1..10000")
        self._max = max_conflicts_per_tenant

    def detect_name_collisions(
        self,
        existing_entities: List[Any],
        new_entity: Any,
    ) -> List[MergeConflict]:
        """Detect name collisions between a new entity and existing ones."""
        import uuid as _uuid
        conflicts: List[MergeConflict] = []
        new_names = {new_entity.canonical_name} | set(new_entity.aliases)
        for existing in existing_entities:
            if existing.entity_id == new_entity.entity_id:
                continue
            existing_names = {existing.canonical_name} | set(existing.aliases)
            overlap = new_names & existing_names
            if not overlap:
                continue
            # If types differ, it's a real conflict (e.g. "Apple" the fruit vs "Apple" the company)
            # If types match, it's likely the same entity — MERGE candidate
            requires_approval = (
                existing.entity_type.value in HIGH_STAKES_TYPES or
                new_entity.entity_type.value in HIGH_STAKES_TYPES
            )
            conflicts.append(MergeConflict(
                id=str(_uuid.uuid4()),
                conflict_type=ConflictType.NAME_COLLISION,
                entity_a_id=existing.entity_id,
                entity_b_id=new_entity.entity_id,
                description=(
                    f"Name collision: {overlap} shared between "
                    f"{existing.entity_type.value} and {new_entity.entity_type.value}"
                ),
                requires_founder_approval=requires_approval,
                tenant_id=new_entity.tenant_id,
            ))
            if len(conflicts) >= self._max:
                break
        return conflicts

    def detect_attribute_contradictions(
        self,
        existing_entity: Any,
        new_attributes: Dict[str, Any],
        evidence: Optional[List[str]] = None,
    ) -> List[MergeConflict]:
        """Detect attribute contradictions on an existing entity."""
        import uuid as _uuid
        conflicts: List[MergeConflict] = []
        for attr_name, new_value in new_attributes.items():
            existing_value = existing_entity.attributes.get(attr_name)
            if existing_value is None:
                continue  # no conflict, attribute is new
            if existing_value == new_value:
                continue  # same value, no conflict
            # Different value → contradiction
            requires_approval = existing_entity.entity_type.value in HIGH_STAKES_TYPES
            conflicts.append(MergeConflict(
                id=str(_uuid.uuid4()),
                conflict_type=ConflictType.ATTRIBUTE_CONTRADICTION,
                entity_a_id=existing_entity.entity_id,
                entity_b_id=existing_entity.entity_id,  # same entity
                description=(
                    f"Attribute '{attr_name}' contradiction: "
                    f"existing={existing_value!r} new={new_value!r}"
                ),
                evidence_a=[],  # filled by caller
                evidence_b=list(evidence or []),
                requires_founder_approval=requires_approval,
                tenant_id=existing_entity.tenant_id,
                attribute_name=attr_name,
                attribute_value_a=existing_value,
                attribute_value_b=new_value,
            ))
        return conflicts


__all__ = [
    "ConflictType", "ConflictResolution", "MergeConflict", "MergeResolver",
    "HIGH_STAKES_TYPES",
]
