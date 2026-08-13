"""Memory Immune System — defends memory against malicious/corrupted input.

The immune system runs every incoming memory through a series of validators
before it's allowed into the active store. Any validator failure raises
ImmuneRejection and the memory is quarantined (not silently dropped, so
we have an audit trail).

Defenses:
    - Payload size cap (MAX_PAYLOAD_BYTES)
    - Serialization integrity (must round-trip JSON)
    - Provenance required for persistent types
    - Owner/creator must be non-empty for persistent types
    - Replay attack protection (idempotency_key dedup)
    - Forged ID detection (reject caller-supplied IDs that already exist)
    - Forged provenance detection (chain must verify)
    - Malformed metadata (negative counts, NaN confidence, etc.)
    - Tenant isolation (payload tenant_id must match context tenant)
    - Schema version must be supported
"""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from .base import (
    ImmuneRejection, Memory, MemoryID, MemoryMetadata, MemoryState,
    MemoryType, MAX_PAYLOAD_BYTES, ValidationError, payload_size_bytes,
)
from .provenance import Provenance

logger = logging.getLogger("friday.living_memory.immune")


@dataclass
class ImmuneReport:
    """Result of an immune system scan."""
    accepted: bool
    reason: str = ""
    quarantined: bool = False
    validators_run: int = 0
    validators_failed: int = 0
    integrity_hash: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "quarantined": self.quarantined,
            "validators_run": self.validators_run,
            "validators_failed": self.validators_failed,
            "integrity_hash": self.integrity_hash,
            "timestamp": self.timestamp,
        }


class MemoryImmuneSystem:
    """Validates memories before they enter the store.

    Stateless w.r.t. the store: takes a memory + optional context
    (existing IDs, recent idempotency keys) and returns an ImmuneReport.
    """

    def __init__(
        self,
        max_payload_bytes: int = MAX_PAYLOAD_BYTES,
        max_provenance_chain_length: int = 1000,
        max_tags_count: int = 64,
    ):
        self._max_payload_bytes = max_payload_bytes
        self._max_provenance_chain_length = max_provenance_chain_length
        self._max_tags_count = max_tags_count

    def scan(
        self,
        memory: Memory,
        provenance: Optional[Provenance] = None,
        existing_ids: Optional[Set[str]] = None,
        seen_idempotency_keys: Optional[Set[str]] = None,
        idempotency_key: str = "",
        expected_tenant_id: str = "",
    ) -> ImmuneReport:
        """Scan a memory. Returns ImmuneReport (does NOT raise on rejection).

        On acceptance, integrity_hash is computed and stored on the memory.
        On rejection, the caller should quarantine the memory.
        """
        report = ImmuneReport(accepted=False)
        existing_ids = existing_ids or set()
        seen_idempotency_keys = seen_idempotency_keys or set()

        validators = [
            self._validate_id,
            self._validate_payload_size,
            self._validate_serialization,
            self._validate_state,
            self._validate_metadata,
            self._validate_provenance,
            self._validate_tenant,
            self._validate_replay,
            self._validate_existing_id,
            self._validate_schema_version,
        ]
        report.validators_run = len(validators)

        for v in validators:
            try:
                v(
                    memory=memory,
                    provenance=provenance,
                    existing_ids=existing_ids,
                    seen_idempotency_keys=seen_idempotency_keys,
                    idempotency_key=idempotency_key,
                    expected_tenant_id=expected_tenant_id,
                )
            except ImmuneRejection as e:
                report.validators_failed += 1
                report.accepted = False
                report.reason = e.reason
                report.quarantined = True
                logger.warning(
                    "Immune rejection: %s (memory_id=%s)",
                    e.reason, memory.id.value[:8] if memory.id else "<none>",
                )
                return report

        # All validators passed → compute integrity hash
        report.integrity_hash = self.compute_integrity_hash(memory, provenance)
        memory.integrity_hash = report.integrity_hash
        report.accepted = True
        return report

    def compute_integrity_hash(
        self, memory: Memory, provenance: Optional[Provenance] = None
    ) -> str:
        """SHA-256 over (id, type, state, payload, metadata.updated_at, head_hash)."""
        import json
        h = hashlib.sha256()
        h.update(memory.id.value.encode("utf-8"))
        h.update(b"|")
        h.update(memory.type.value.encode("utf-8"))
        h.update(b"|")
        h.update(memory.state.value.encode("utf-8"))
        h.update(b"|")
        h.update(
            json.dumps(memory.payload, sort_keys=True, default=str).encode("utf-8")
        )
        h.update(b"|")
        h.update(memory.metadata.updated_at.encode("utf-8"))
        h.update(b"|")
        head = provenance.head_hash if provenance else ""
        h.update(head.encode("utf-8"))
        return h.hexdigest()

    def verify_integrity(
        self, memory: Memory, provenance: Optional[Provenance] = None
    ) -> bool:
        """Verify a memory's stored integrity_hash matches recomputed value."""
        return memory.integrity_hash == self.compute_integrity_hash(memory, provenance)

    # ------------------------------------------------------------------
    # Individual validators
    # ------------------------------------------------------------------

    def _validate_id(self, memory: Memory, **kw) -> None:
        if not memory.id or not memory.id.value:
            raise ImmuneRejection("Memory has empty ID")
        try:
            MemoryID.from_string(memory.id.value)
        except ValidationError as e:
            raise ImmuneRejection(f"Memory ID malformed: {e}")

    def _validate_payload_size(self, memory: Memory, **kw) -> None:
        size = payload_size_bytes(memory.payload)
        if size > self._max_payload_bytes:
            raise ImmuneRejection(
                f"Payload exceeds size cap: {size} > {self._max_payload_bytes} bytes",
                payload={"size": size, "cap": self._max_payload_bytes},
            )

    def _validate_serialization(self, memory: Memory, **kw) -> None:
        """Memory payload must be JSON-serializable (for persistence).

        Uses default=str fallback for non-JSON-native types. Any exception
        (TypeError, ValueError, RuntimeError, etc.) results in rejection —
        the immune system must NEVER crash on bad input.
        """
        import json
        try:
            json.dumps(memory.payload, default=str)
        except Exception as e:
            raise ImmuneRejection(
                f"Payload not serializable: {type(e).__name__}: {e}",
                payload={"error_type": type(e).__name__},
            )

    def _validate_state(self, memory: Memory, **kw) -> None:
        # New memories must start in CREATED (or QUARANTINED for restoration)
        if memory.state not in (MemoryState.CREATED, MemoryState.QUARANTINED):
            # Allow ACTIVE for restoration paths (set explicitly by manager)
            if memory.state != MemoryState.ACTIVE:
                raise ImmuneRejection(
                    f"New memory has invalid initial state: {memory.state.value}"
                )

    def _validate_metadata(self, memory: Memory, **kw) -> None:
        md = memory.metadata
        if not isinstance(md.confidence, (int, float)) or math.isnan(md.confidence):
            raise ImmuneRejection("Confidence is NaN or non-numeric")
        if not 0.0 <= md.confidence <= 1.0:
            raise ImmuneRejection(
                f"Confidence out of range [0,1]: {md.confidence}",
                payload={"confidence": md.confidence},
            )
        if not isinstance(md.importance, (int, float)) or math.isnan(md.importance):
            raise ImmuneRejection("Importance is NaN or non-numeric")
        if not 0.0 <= md.importance <= 1.0:
            raise ImmuneRejection(
                f"Importance out of range [0,1]: {md.importance}"
            )
        if md.access_count < 0:
            raise ImmuneRejection("Access count negative")
        if md.reinforcement_count < 0:
            raise ImmuneRejection("Reinforcement count negative")
        if len(md.tags) > self._max_tags_count:
            raise ImmuneRejection(
                f"Too many tags: {len(md.tags)} > {self._max_tags_count}"
            )

    def _validate_provenance(
        self, memory: Memory, provenance: Optional[Provenance] = None, **kw
    ) -> None:
        """Persistent memory types require provenance chain."""
        if not MemoryType.is_persistent(memory.type):
            return
        if provenance is None or provenance.is_empty:
            raise ImmuneRejection(
                f"Persistent memory type {memory.type.value} requires provenance"
            )
        if len(provenance.chain) > self._max_provenance_chain_length:
            raise ImmuneRejection(
                f"Provenance chain too long: {len(provenance.chain)} > "
                f"{self._max_provenance_chain_length}"
            )
        if not provenance.verify_chain():
            raise ImmuneRejection(
                "Provenance chain verification failed — possible tampering"
            )
        # Creator must be non-empty
        if not provenance.creator:
            raise ImmuneRejection("Provenance creator is empty")

    def _validate_tenant(
        self, memory: Memory, expected_tenant_id: str = "", **kw
    ) -> None:
        if not memory.metadata.tenant_id:
            raise ImmuneRejection("Memory has no tenant_id")
        if expected_tenant_id and memory.metadata.tenant_id != expected_tenant_id:
            raise ImmuneRejection(
                f"Tenant mismatch: memory.tenant={memory.metadata.tenant_id} "
                f"expected={expected_tenant_id} (constitution Article 7)",
                payload={
                    "memory_tenant": memory.metadata.tenant_id,
                    "expected_tenant": expected_tenant_id,
                },
            )

    def _validate_replay(
        self,
        memory: Memory,
        seen_idempotency_keys: Optional[Set[str]] = None,
        idempotency_key: str = "",
        **kw,
    ) -> None:
        """If idempotency_key is provided, it must not have been seen recently."""
        if not idempotency_key:
            return
        if idempotency_key in (seen_idempotency_keys or set()):
            raise ImmuneRejection(
                f"Replay detected: idempotency_key {idempotency_key[:8]} already seen",
                payload={"idempotency_key": idempotency_key},
            )

    def _validate_existing_id(
        self, memory: Memory, existing_ids: Optional[Set[str]] = None, **kw
    ) -> None:
        """Caller-supplied ID must not collide with an existing memory."""
        existing = existing_ids or set()
        if memory.id.value in existing:
            raise ImmuneRejection(
                f"Forged/colliding ID: {memory.id.value[:8]} already exists",
                payload={"id": memory.id.value},
            )

    def _validate_schema_version(self, memory: Memory, **kw) -> None:
        if memory.schema_version < 1 or memory.schema_version > 1:
            raise ImmuneRejection(
                f"Unsupported schema_version: {memory.schema_version}",
                payload={"schema_version": memory.schema_version},
            )


__all__ = ["MemoryImmuneSystem", "ImmuneReport"]
