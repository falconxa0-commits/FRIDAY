"""Living Memory — base types, identity, lifecycle, errors.

This module defines the foundational types shared across all living memory
subsystems. It is purely additive — does NOT modify Age IV's `core/memory.py`.

Design principles:
    - Strong typing via dataclasses + enums
    - Every memory has a stable identity (MemoryID)
    - Every persistent memory has provenance
    - Every mutation is auditable
    - Lifecycle transitions are explicit and validated
    - All operations fail closed on authorization failure
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.living_memory.base")


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


class LivingMemoryError(Exception):
    """Base error for living memory subsystems."""


class AuthorizationError(LivingMemoryError):
    """Caller is not authorized to perform the operation."""


class LifecycleError(LivingMemoryError):
    """Invalid lifecycle transition attempted."""


class ValidationError(LivingMemoryError):
    """Input failed validation (oversized, malformed, etc.)."""


class ContradictionError(LivingMemoryError):
    """A contradiction was detected and not resolved."""


class ImmuneRejection(LivingMemoryError):
    """Memory was rejected by the immune system."""

    def __init__(self, reason: str, payload: Optional[Dict[str, Any]] = None):
        super().__init__(reason)
        self.reason = reason
        self.payload = payload or {}


class PersistenceError(LivingMemoryError):
    """Persistence layer failure."""


class ResourceLimitError(LivingMemoryError):
    """A bounded resource was exceeded."""


# ----------------------------------------------------------------------
# Memory identity
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryID:
    """Stable, globally-unique identifier for a memory.

    IDs are versioned (schema v1). The version field lets us evolve the ID
    format without breaking deserialization of older records.
    """
    value: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: int = 1

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryID":
        return cls(
            value=data.get("value", str(uuid.uuid4())),
            schema_version=data.get("schema_version", 1),
        )

    @classmethod
    def from_string(cls, s: str) -> "MemoryID":
        """Parse an ID from a string. Rejects empty / non-str / non-UUID-ish.

        V11: Rejects the pipe character '|' since AssociationGraph uses
        source|target|rel as its edge_key format — a memory ID containing
        a pipe would create ambiguous edge keys.
        """
        if not isinstance(s, str) or not s:
            raise ValidationError("MemoryID string must be non-empty")
        # Accept canonical UUIDs or any non-whitespace printable string >=8 chars
        if len(s) < 8:
            raise ValidationError("MemoryID string too short (min 8 chars)")
        if any(c.isspace() for c in s):
            raise ValidationError("MemoryID string must not contain whitespace")
        # V11: Reject pipe character (used as edge-key separator in AssociationGraph)
        if "|" in s:
            raise ValidationError("MemoryID string must not contain '|' character")
        return cls(value=s, schema_version=1)


# ----------------------------------------------------------------------
# Memory type + lifecycle
# ----------------------------------------------------------------------


class MemoryType(str, Enum):
    """The five biological memory classes."""
    WORKING = "working"          # active context, bounded, TTL
    EPISODIC = "episodic"        # experiences, events, context
    SEMANTIC = "semantic"        # facts, concepts, generalized knowledge
    PROCEDURAL = "procedural"    # workflows, skills, learned procedures
    ASSOCIATIVE = "associative"  # links between memories

    @classmethod
    def is_persistent(cls, mt: "MemoryType") -> bool:
        """Persistent memory types require provenance."""
        return mt in (cls.EPISODIC, cls.SEMANTIC, cls.PROCEDURAL, cls.ASSOCIATIVE)


class MemoryState(str, Enum):
    """Lifecycle states. Transitions validated via `ALLOWED_TRANSITIONS`."""
    CREATED = "created"
    ACTIVE = "active"
    REINFORCED = "reinforced"
    CONSOLIDATING = "consolidating"
    CONSOLIDATED = "consolidated"
    DECAYING = "decaying"
    ARCHIVED = "archived"
    FORGOTTEN = "forgotten"
    QUARANTINED = "quarantined"  # immune system hold

    @classmethod
    def is_terminal(cls, state: "MemoryState") -> bool:
        """Only FORGOTTEN is truly terminal. ARCHIVED is restorable."""
        return state == cls.FORGOTTEN

    @classmethod
    def is_accessible(cls, state: "MemoryState") -> bool:
        """States in which a memory can be retrieved."""
        return state in (
            cls.ACTIVE, cls.REINFORCED, cls.CONSOLIDATING,
            cls.CONSOLIDATED, cls.DECAYING,
        )


# Lifecycle transition table. Source state -> set of allowed target states.
# Anything not listed here raises LifecycleError.
ALLOWED_TRANSITIONS: Dict[MemoryState, set] = {
    MemoryState.CREATED: {
        MemoryState.ACTIVE, MemoryState.QUARANTINED, MemoryState.FORGOTTEN,
    },
    MemoryState.ACTIVE: {
        MemoryState.REINFORCED, MemoryState.CONSOLIDATING,
        MemoryState.DECAYING, MemoryState.QUARANTINED, MemoryState.FORGOTTEN,
        MemoryState.ARCHIVED,
    },
    MemoryState.REINFORCED: {
        MemoryState.REINFORCED,  # re-reinforcement allowed
        MemoryState.CONSOLIDATING, MemoryState.DECAYING,
        MemoryState.QUARANTINED, MemoryState.FORGOTTEN,
        MemoryState.ARCHIVED,
    },
    MemoryState.CONSOLIDATING: {
        MemoryState.CONSOLIDATED, MemoryState.ACTIVE,  # failure → fall back
        MemoryState.QUARANTINED,
    },
    MemoryState.CONSOLIDATED: {
        MemoryState.REINFORCED, MemoryState.DECAYING,
        MemoryState.QUARANTINED, MemoryState.ARCHIVED, MemoryState.FORGOTTEN,
    },
    MemoryState.DECAYING: {
        MemoryState.REINFORCED,  # access reinforces
        MemoryState.ARCHIVED, MemoryState.FORGOTTEN, MemoryState.QUARANTINED,
    },
    MemoryState.ARCHIVED: {
        MemoryState.ACTIVE,  # restoration
        MemoryState.FORGOTTEN,
    },
    MemoryState.QUARANTINED: {
        MemoryState.ACTIVE,  # cleared
        MemoryState.FORGOTTEN,
    },
    MemoryState.FORGOTTEN: set(),  # terminal
}


def validate_transition(src: MemoryState, dst: MemoryState) -> None:
    """Raise LifecycleError if src→dst is not allowed."""
    allowed = ALLOWED_TRANSITIONS.get(src, set())
    if dst not in allowed:
        raise LifecycleError(
            f"Invalid memory lifecycle transition: {src.value} → {dst.value}"
        )


# ----------------------------------------------------------------------
# Memory metadata + base record
# ----------------------------------------------------------------------


@dataclass
class MemoryMetadata:
    """Per-memory metadata. All persistent memories must populate provenance."""
    owner_id: str = ""                       # Citizen ID of owner
    created_by: str = ""                     # Citizen ID of creator (may differ)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_accessed_at: str = ""
    access_count: int = 0
    reinforcement_count: int = 0
    confidence: float = 0.5                   # 0.0..1.0
    importance: float = 0.5                   # 0.0..1.0
    tags: List[str] = field(default_factory=list)
    tenant_id: str = "default"
    correlation_id: str = ""                  # tracing

    def touch(self) -> None:
        """Update last_accessed_at and access_count on retrieval."""
        self.last_accessed_at = datetime.now(timezone.utc).isoformat()
        self.access_count += 1

    def reinforce(self, delta: float = 0.1) -> None:
        """Reinforce: bump counts and confidence (clamped 0..1)."""
        self.reinforcement_count += 1
        self.confidence = min(1.0, self.confidence + delta)
        self.importance = min(1.0, self.importance + delta * 0.5)
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "owner_id": self.owner_id,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
            "reinforcement_count": self.reinforcement_count,
            "confidence": round(self.confidence, 6),
            "importance": round(self.importance, 6),
            "tags": list(self.tags),
            "tenant_id": self.tenant_id,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryMetadata":
        return cls(
            owner_id=d.get("owner_id", ""),
            created_by=d.get("created_by", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            last_accessed_at=d.get("last_accessed_at", ""),
            access_count=int(d.get("access_count", 0)),
            reinforcement_count=int(d.get("reinforcement_count", 0)),
            confidence=float(d.get("confidence", 0.5)),
            importance=float(d.get("importance", 0.5)),
            tags=list(d.get("tags", [])),
            tenant_id=d.get("tenant_id", "default"),
            correlation_id=d.get("correlation_id", ""),
        )


@dataclass
class Memory:
    """Base memory record.

    All five memory classes compose this base record. Specific subclasses
    (EpisodicMemory, SemanticMemory, etc.) extend it with type-specific
    payload fields.
    """
    id: MemoryID = field(default_factory=MemoryID)
    type: MemoryType = MemoryType.SEMANTIC
    state: MemoryState = MemoryState.CREATED
    metadata: MemoryMetadata = field(default_factory=MemoryMetadata)
    payload: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    # Integrity: HMAC over (id, type, state, payload, updated_at)
    # Set by immune system on validation. Empty until first validated.
    integrity_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id.to_dict(),
            "type": self.type.value,
            "state": self.state.value,
            "metadata": self.metadata.to_dict(),
            "payload": self.payload,
            "schema_version": self.schema_version,
            "integrity_hash": self.integrity_hash,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Memory":
        return cls(
            id=MemoryID.from_dict(d.get("id", {})),
            type=MemoryType(d.get("type", "semantic")),
            state=MemoryState(d.get("state", "created")),
            metadata=MemoryMetadata.from_dict(d.get("metadata", {})),
            payload=dict(d.get("payload", {})),
            schema_version=int(d.get("schema_version", 1)),
            integrity_hash=d.get("integrity_hash", ""),
        )


# ----------------------------------------------------------------------
# Health state (for procedural + working memory)
# ----------------------------------------------------------------------


class HealthState(str, Enum):
    """Operational health of a memory-bearing component."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    RECOVERING = "recovering"


# ----------------------------------------------------------------------
# Utility: now_utc, correlation_id, size_of
# ----------------------------------------------------------------------


def now_utc() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def new_correlation_id() -> str:
    return str(uuid.uuid4())


MAX_PAYLOAD_BYTES = 256 * 1024  # 256 KiB hard cap per memory payload


def payload_size_bytes(payload: Any) -> int:
    """Estimate serialized payload size in bytes.

    Uses JSON serialization for accuracy when possible; falls back to
    repr-length for non-serializable payloads. NEVER raises — the immune
    system relies on this to detect oversized payloads without crashing.
    """
    try:
        import json
        return len(json.dumps(payload, default=str, sort_keys=True).encode("utf-8"))
    except (TypeError, ValueError, RuntimeError):
        # Non-serializable payload — estimate via repr, but guard against
        # repr() raising too
        try:
            return len(repr(payload).encode("utf-8"))
        except Exception:
            return 0


__all__ = [
    "LivingMemoryError", "AuthorizationError", "LifecycleError",
    "ValidationError", "ContradictionError", "ImmuneRejection",
    "PersistenceError", "ResourceLimitError",
    "MemoryID", "MemoryType", "MemoryState", "ALLOWED_TRANSITIONS",
    "validate_transition", "MemoryMetadata", "Memory", "HealthState",
    "now_utc", "new_correlation_id", "payload_size_bytes", "MAX_PAYLOAD_BYTES",
]
