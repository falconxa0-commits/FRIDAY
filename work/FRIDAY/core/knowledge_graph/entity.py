"""Knowledge Graph — Entity types and Entity dataclass.

An Entity is a typed node in the knowledge graph. Each entity:
- Has a stable ID derived from (tenant_id, entity_type, canonical_name)
- Is anchored by an M3 SemanticMemory record (subject=entity_id)
- Has attributes stored as additional M3 SemanticMemory records
- Has aliases (alternative names)
- Has confidence (aggregated from underlying M3 memories)
- Inherits provenance, authz, immune validation from M3

This module is 100% additive — does NOT modify M3.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.knowledge_graph.entity")


class EntityType(str, Enum):
    """Standard entity types. Custom types allowed via string value."""
    PERSON = "person"
    ORGANIZATION = "organization"
    PLACE = "location"
    CONCEPT = "concept"
    EVENT = "event"
    DOCUMENT = "document"
    TOOL = "tool"
    PROJECT = "project"
    DATE = "date"
    MONEY = "money"
    PRODUCT = "product"
    OTHER = "other"

    @classmethod
    def from_string(cls, s: str) -> "EntityType":
        """Parse a string into an EntityType. Unknown → OTHER."""
        s = s.lower().strip()
        for member in cls:
            if member.value == s:
                return member
        # Allow custom types via the OTHER bucket with a tag
        return cls.OTHER


# Pattern for canonicalizing entity names
_NAME_NORMALIZE_RE = re.compile(r"\s+")


def canonicalize_name(name: str) -> str:
    """Normalize an entity name for stable ID derivation.

    - Lowercase
    - Collapse whitespace
    - Strip leading/trailing whitespace
    - Reject empty
    - Reject pipe character (collides with M3 AssociationGraph edge_key format)
    """
    if not isinstance(name, str):
        raise ValueError(f"Entity name must be string, got {type(name).__name__}")
    cleaned = _NAME_NORMALIZE_RE.sub(" ", name.strip().lower())
    if not cleaned:
        raise ValueError("Entity name cannot be empty after normalization")
    if len(cleaned) > 256:
        raise ValueError(f"Entity name too long (>256 chars): {cleaned[:50]}...")
    # Reject pipe character — would collide with M3 edge_key format
    if "|" in cleaned:
        raise ValueError("Entity name must not contain '|' character")
    return cleaned


def derive_entity_id(tenant_id: str, entity_type: EntityType, canonical_name: str) -> str:
    """Deterministic entity ID from (tenant, type, name).

    Format: kg-{first 16 chars of sha256}-...
    This makes entity IDs stable across restarts (same input → same ID).
    """
    h = hashlib.sha256()
    h.update(tenant_id.encode("utf-8"))
    h.update(b"|")
    h.update(entity_type.value.encode("utf-8"))
    h.update(b"|")
    h.update(canonical_name.encode("utf-8"))
    return f"kg-{h.hexdigest()[:24]}"


@dataclass
class Entity:
    """A knowledge graph node.

    Attributes:
        entity_id: Stable deterministic ID
        entity_type: EntityType enum
        canonical_name: Normalized name
        aliases: List of alternative names (also normalized)
        attributes: Dict of attr_name → attr_value (stored as M3 SemanticMemory)
        memory_id: ID of the M3 SemanticMemory that anchors this entity
        tenant_id: Tenant isolation
        confidence: 0..1, aggregated from underlying M3 memories
        created_at: ISO timestamp
        updated_at: ISO timestamp
    """
    entity_id: str
    entity_type: EntityType
    canonical_name: str
    aliases: List[str] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)
    memory_id: str = ""  # M3 SemanticMemory ID anchoring this entity
    tenant_id: str = "default"
    confidence: float = 0.5
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        if not self.entity_id:
            raise ValueError("Entity ID cannot be empty")
        if not isinstance(self.entity_type, EntityType):
            raise TypeError(f"entity_type must be EntityType, got {type(self.entity_type)}")
        if not self.canonical_name:
            raise ValueError("Canonical name cannot be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be in [0,1], got {self.confidence}")
        if len(self.aliases) > 32:
            raise ValueError(f"Too many aliases: {len(self.aliases)} > 32")

    @classmethod
    def create(
        cls,
        name: str,
        entity_type: EntityType,
        tenant_id: str = "default",
        aliases: Optional[List[str]] = None,
        attributes: Optional[Dict[str, Any]] = None,
        confidence: float = 0.5,
    ) -> "Entity":
        """Create a new Entity. Derives entity_id deterministically."""
        canonical = canonicalize_name(name)
        eid = derive_entity_id(tenant_id, entity_type, canonical)
        # Canonicalize aliases
        norm_aliases: List[str] = []
        seen = {canonical}
        for a in (aliases or []):
            try:
                na = canonicalize_name(a)
            except ValueError:
                continue
            if na not in seen:
                seen.add(na)
                norm_aliases.append(na)
        return cls(
            entity_id=eid,
            entity_type=entity_type,
            canonical_name=canonical,
            aliases=norm_aliases,
            attributes=dict(attributes or {}),
            tenant_id=tenant_id,
            confidence=confidence,
        )

    def add_alias(self, alias: str) -> bool:
        """Add an alias. Returns True if added, False if duplicate."""
        try:
            na = canonicalize_name(alias)
        except ValueError:
            return False
        if na == self.canonical_name or na in self.aliases:
            return False
        if len(self.aliases) >= 32:
            raise ValueError(f"Alias cap reached (32) for entity {self.entity_id}")
        self.aliases.append(na)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        return True

    def set_attribute(self, name: str, value: Any) -> None:
        """Set an attribute. Caps total attributes at 128."""
        if not name or not isinstance(name, str):
            raise ValueError("Attribute name must be non-empty string")
        if len(self.attributes) >= 128 and name not in self.attributes:
            raise ValueError(f"Attribute cap reached (128) for entity {self.entity_id}")
        self.attributes[name] = value
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def matches_name(self, query: str) -> bool:
        """Check if query matches canonical name or any alias (case-insensitive)."""
        try:
            q = canonicalize_name(query)
        except ValueError:
            return False
        return q == self.canonical_name or q in self.aliases

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type.value,
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "attributes": dict(self.attributes),
            "memory_id": self.memory_id,
            "tenant_id": self.tenant_id,
            "confidence": round(self.confidence, 6),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Entity":
        return cls(
            entity_id=d["entity_id"],
            entity_type=EntityType.from_string(d.get("entity_type", "other")),
            canonical_name=d["canonical_name"],
            aliases=list(d.get("aliases", [])),
            attributes=dict(d.get("attributes", {})),
            memory_id=d.get("memory_id", ""),
            tenant_id=d.get("tenant_id", "default"),
            confidence=float(d.get("confidence", 0.5)),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


__all__ = [
    "EntityType", "Entity", "canonicalize_name", "derive_entity_id",
]
