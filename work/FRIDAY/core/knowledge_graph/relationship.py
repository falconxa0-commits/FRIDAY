"""Knowledge Graph — Relationship types and Relationship dataclass.

A Relationship is a typed, weighted, directed edge between two Entities.
Each relationship:
- Has a stable ID derived from (source, target, type)
- Is stored as an M3 AssociationMemory (which goes through M3 immune + provenance)
- Has evidence (list of M3 EpisodicMemory IDs that observed it)
- Has temporal metadata (first_seen, last_reinforced)
- Inherits authz + tenant isolation from M3
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.knowledge_graph.relationship")


class RelationshipType(str, Enum):
    """Standard relationship types. Custom types allowed via string value."""
    WORKS_FOR = "works_for"
    LOCATED_IN = "located_in"
    CREATED_BY = "created_by"
    DEPENDS_ON = "depends_on"
    SIMILAR_TO = "similar_to"
    MENTIONS = "mentions"
    MEMBER_OF = "member_of"
    OWNS = "owns"
    PART_OF = "part_of"
    RELATED_TO = "related_to"
    DERIVED_FROM = "derived_from"
    CONTRADICTS = "contradicts"  # M4-specific: relationship that conflicts
    SUPERSEDES = "supersedes"

    @classmethod
    def from_string(cls, s: str) -> Optional["RelationshipType"]:
        s = s.lower().strip()
        for member in cls:
            if member.value == s:
                return member
        return None  # Unknown type → caller may allow custom


# Relationship types that are inherently bidirectional
BIDIRECTIONAL_TYPES = {
    RelationshipType.SIMILAR_TO,
    RelationshipType.RELATED_TO,
    RelationshipType.CONTRADICTS,
}


def derive_relationship_id(
    source_entity_id: str,
    target_entity_id: str,
    rel_type: RelationshipType,
) -> str:
    """Deterministic relationship ID."""
    h = hashlib.sha256()
    h.update(source_entity_id.encode("utf-8"))
    h.update(b"|")
    h.update(target_entity_id.encode("utf-8"))
    h.update(b"|")
    h.update(rel_type.value.encode("utf-8"))
    return f"kgr-{h.hexdigest()[:24]}"


@dataclass
class Relationship:
    """A knowledge graph edge.

    Attributes:
        relationship_id: Stable deterministic ID
        source_entity_id: Source Entity ID
        target_entity_id: Target Entity ID
        relationship_type: RelationshipType enum
        weight: 0..1 (confidence in the relationship)
        bidirectional: If True, edge is symmetric
        evidence: List of M3 EpisodicMemory IDs that observed this relationship
        memory_id: ID of the M3 AssociationMemory anchoring this relationship
        tenant_id: Tenant isolation
        first_seen_at: ISO timestamp
        last_reinforced_at: ISO timestamp
        reinforcement_count: How many times this relationship has been observed
    """
    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: RelationshipType
    weight: float = 0.5
    bidirectional: bool = False
    evidence: List[str] = field(default_factory=list)
    memory_id: str = ""
    tenant_id: str = "default"
    first_seen_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_reinforced_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reinforcement_count: int = 0

    def __post_init__(self):
        if not self.relationship_id:
            raise ValueError("Relationship ID cannot be empty")
        if not self.source_entity_id or not self.target_entity_id:
            raise ValueError("Source and target entity IDs required")
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("Self-relationship not allowed")
        if not isinstance(self.relationship_type, RelationshipType):
            raise TypeError("relationship_type must be RelationshipType")
        if not 0.0 <= self.weight <= 1.0:
            raise ValueError(f"Weight must be in [0,1], got {self.weight}")
        if self.reinforcement_count < 0:
            raise ValueError("Reinforcement count cannot be negative")
        if len(self.evidence) > 64:
            raise ValueError(f"Too many evidence entries: {len(self.evidence)} > 64")
        # Auto-set bidirectional for inherently symmetric types
        if self.relationship_type in BIDIRECTIONAL_TYPES:
            self.bidirectional = True

    @classmethod
    def create(
        cls,
        source_entity_id: str,
        target_entity_id: str,
        rel_type: RelationshipType,
        weight: float = 0.5,
        bidirectional: Optional[bool] = None,
        tenant_id: str = "default",
        evidence: Optional[List[str]] = None,
    ) -> "Relationship":
        """Create a new Relationship. Derives ID deterministically."""
        if source_entity_id == target_entity_id:
            raise ValueError("Self-relationship not allowed")
        rid = derive_relationship_id(source_entity_id, target_entity_id, rel_type)
        # If bidirectional not specified, infer from type
        if bidirectional is None:
            bidirectional = rel_type in BIDIRECTIONAL_TYPES
        return cls(
            relationship_id=rid,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            relationship_type=rel_type,
            weight=weight,
            bidirectional=bidirectional,
            tenant_id=tenant_id,
            evidence=list(evidence or []),
        )

    def add_evidence(self, episode_memory_id: str) -> bool:
        """Add evidence (an M3 EpisodicMemory ID). Returns True if added."""
        if not episode_memory_id:
            return False
        if episode_memory_id in self.evidence:
            return False
        if len(self.evidence) >= 64:
            raise ValueError(f"Evidence cap reached (64) for relationship {self.relationship_id}")
        self.evidence.append(episode_memory_id)
        self.reinforcement_count += 1
        self.last_reinforced_at = datetime.now(timezone.utc).isoformat()
        return True

    def reinforce(self, delta: float = 0.05) -> None:
        """Reinforce this relationship (bump weight + count).

        V10 fix: reject negative delta (would violate weight ∈ [0,1] invariant).
        """
        if delta < 0:
            raise ValueError(f"reinforce delta must be non-negative, got {delta}")
        if delta > 1.0:
            raise ValueError(f"reinforce delta must be <= 1.0, got {delta}")
        self.weight = min(1.0, self.weight + delta)
        self.reinforcement_count += 1
        self.last_reinforced_at = datetime.now(timezone.utc).isoformat()

    def involves(self, entity_id: str) -> bool:
        """Check if this relationship involves the given entity."""
        if self.bidirectional:
            return entity_id in (self.source_entity_id, self.target_entity_id)
        return entity_id == self.source_entity_id or entity_id == self.target_entity_id

    def other_endpoint(self, entity_id: str) -> Optional[str]:
        """Get the other endpoint of the relationship given one endpoint."""
        if entity_id == self.source_entity_id:
            return self.target_entity_id
        if entity_id == self.target_entity_id:
            return self.source_entity_id if self.bidirectional else None
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "relationship_type": self.relationship_type.value,
            "weight": round(self.weight, 6),
            "bidirectional": self.bidirectional,
            "evidence": list(self.evidence),
            "memory_id": self.memory_id,
            "tenant_id": self.tenant_id,
            "first_seen_at": self.first_seen_at,
            "last_reinforced_at": self.last_reinforced_at,
            "reinforcement_count": self.reinforcement_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Relationship":
        rt = RelationshipType.from_string(d.get("relationship_type", "related_to"))
        if rt is None:
            rt = RelationshipType.RELATED_TO
        return cls(
            relationship_id=d["relationship_id"],
            source_entity_id=d["source_entity_id"],
            target_entity_id=d["target_entity_id"],
            relationship_type=rt,
            weight=float(d.get("weight", 0.5)),
            bidirectional=bool(d.get("bidirectional", False)),
            evidence=list(d.get("evidence", [])),
            memory_id=d.get("memory_id", ""),
            tenant_id=d.get("tenant_id", "default"),
            first_seen_at=d.get("first_seen_at", ""),
            last_reinforced_at=d.get("last_reinforced_at", ""),
            reinforcement_count=int(d.get("reinforcement_count", 0)),
        )


__all__ = [
    "RelationshipType", "Relationship", "BIDIRECTIONAL_TYPES",
    "derive_relationship_id",
]
