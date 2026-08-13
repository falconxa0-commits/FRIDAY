"""Contradiction detection — never silently overwrite conflicting information.

When a new semantic/procedural fact conflicts with an existing one, the
contradiction engine:
    - Detects the conflict
    - Creates a ContradictionRecord linking both memories
    - Flags both memories (does NOT delete either)
    - Emits a "memory.contradiction_detected" event
    - Awaits governance resolution (Founder or council)

Conflict heuristics (any one triggers):
    - Same predicate (subject+verb+object key) but different value
    - Same procedure name but different step sequence
    - Negation conflict (fact A says "X is Y", fact B says "X is not Y")
    - Numerical conflict with delta > tolerance
    - Source authority mismatch (lower-authority source updates a higher-authority fact)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .base import now_utc

logger = logging.getLogger("friday.living_memory.contradiction")


class ContradictionType(str, Enum):
    PREDICATE_CONFLICT = "predicate_conflict"          # same predicate, different value
    NEGATION_CONFLICT = "negation_conflict"            # X is Y vs X is not Y
    NUMERICAL_CONFLICT = "numerical_conflict"          # same numeric field, different value
    PROCEDURE_CONFLICT = "procedure_conflict"          # same procedure name, different steps
    SOURCE_AUTHORITY_CONFLICT = "source_authority_conflict"
    DUPLICATE = "duplicate"                            # exact duplicate (not a real conflict)


class ContradictionStatus(str, Enum):
    DETECTED = "detected"
    RESOLVED_KEEP_OLD = "resolved_keep_old"
    RESOLVED_KEEP_NEW = "resolved_keep_new"
    RESOLVED_MERGE = "resolved_merge"
    RESOLVED_INVALID = "resolved_invalid"              # false alarm


@dataclass
class ContradictionRecord:
    """A detected contradiction between two memories."""
    id: str
    memory_a_id: str
    memory_b_id: str
    conflict_type: ContradictionType
    description: str = ""
    status: ContradictionStatus = ContradictionStatus.DETECTED
    detected_at: str = field(default_factory=now_utc)
    resolved_at: str = ""
    resolved_by: str = ""
    resolution_notes: str = ""
    predicate_key: str = ""    # the conflicting key, if applicable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "memory_a_id": self.memory_a_id,
            "memory_b_id": self.memory_b_id,
            "conflict_type": self.conflict_type.value,
            "description": self.description,
            "status": self.status.value,
            "detected_at": self.detected_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "resolution_notes": self.resolution_notes,
            "predicate_key": self.predicate_key,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ContradictionRecord":
        return cls(
            id=d["id"],
            memory_a_id=d["memory_a_id"],
            memory_b_id=d["memory_b_id"],
            conflict_type=ContradictionType(d.get("conflict_type", "predicate_conflict")),
            description=d.get("description", ""),
            status=ContradictionStatus(d.get("status", "detected")),
            detected_at=d.get("detected_at", ""),
            resolved_at=d.get("resolved_at", ""),
            resolved_by=d.get("resolved_by", ""),
            resolution_notes=d.get("resolution_notes", ""),
            predicate_key=d.get("predicate_key", ""),
        )


class ContradictionDetector:
    """Detects contradictions between a new memory and existing ones.

    Stateless: takes a list of candidate existing memories + the new memory
    and returns a list of ContradictionRecords (if any).

    The detector uses simple, deterministic heuristics:
        - Same predicate_key (subject+predicate) → compare values
        - Negation pattern detection (contains "not ")
        - Numeric tolerance (default 1e-9)
        - Procedure step-sequence equality
    """

    def __init__(self, numerical_tolerance: float = 1e-9):
        self._tolerance = numerical_tolerance

    def detect(
        self,
        new_memory_id: str,
        new_payload: Dict[str, Any],
        new_type: str,
        candidates: List[tuple],  # list of (memory_id, payload, type, source_authority)
    ) -> List[ContradictionRecord]:
        """Detect contradictions. Returns list of records (empty if none).

        Args:
            new_memory_id: ID of the incoming memory.
            new_payload: payload of the incoming memory.
            new_type: type of the incoming memory ("semantic"|"procedural"|...)
            candidates: list of (existing_memory_id, existing_payload, existing_type, source_authority)
        """
        records: List[ContradictionRecord] = []
        import uuid as _uuid

        # Semantic fact contradiction: same predicate key
        if new_type == "semantic":
            new_pk = new_payload.get("predicate_key", "")
            new_val = new_payload.get("value")
            for (eid, epayload, etype, auth) in candidates:
                if etype != "semantic":
                    continue
                epk = epayload.get("predicate_key", "")
                if not new_pk or not epk or new_pk != epk:
                    continue
                eval_ = epayload.get("value")
                if new_val == eval_:
                    # Exact duplicate — not a real conflict
                    records.append(ContradictionRecord(
                        id=str(_uuid.uuid4()),
                        memory_a_id=eid,
                        memory_b_id=new_memory_id,
                        conflict_type=ContradictionType.DUPLICATE,
                        description=f"Exact duplicate predicate: {new_pk}",
                        predicate_key=new_pk,
                    ))
                    continue
                # Negation conflict: one says "X", other says "not X"
                if isinstance(new_val, str) and isinstance(eval_, str):
                    if (new_val.startswith("not ") and eval_ == new_val[4:]) or \
                       (eval_.startswith("not ") and new_val == eval_[4:]):
                        records.append(ContradictionRecord(
                            id=str(_uuid.uuid4()),
                            memory_a_id=eid,
                            memory_b_id=new_memory_id,
                            conflict_type=ContradictionType.NEGATION_CONFLICT,
                            description=f"Negation conflict on {new_pk}: "
                                        f"old={eval_!r} new={new_val!r}",
                            predicate_key=new_pk,
                        ))
                        continue
                # Numerical conflict
                if isinstance(new_val, (int, float)) and isinstance(eval_, (int, float)):
                    if abs(new_val - eval_) > self._tolerance:
                        records.append(ContradictionRecord(
                            id=str(_uuid.uuid4()),
                            memory_a_id=eid,
                            memory_b_id=new_memory_id,
                            conflict_type=ContradictionType.NUMERICAL_CONFLICT,
                            description=f"Numerical conflict on {new_pk}: "
                                        f"old={eval_} new={new_val} delta={abs(new_val-eval_)}",
                            predicate_key=new_pk,
                        ))
                        continue
                # Generic predicate conflict (different values)
                records.append(ContradictionRecord(
                    id=str(_uuid.uuid4()),
                    memory_a_id=eid,
                    memory_b_id=new_memory_id,
                    conflict_type=ContradictionType.PREDICATE_CONFLICT,
                    description=f"Predicate {new_pk} has conflicting values: "
                                f"old={eval_!r} new={new_val!r}",
                    predicate_key=new_pk,
                ))
                # Source authority: if new memory's source has lower authority
                # than existing memory's source, flag as authority conflict
                new_auth = new_payload.get("source_authority", 0)
                if isinstance(new_auth, (int, float)) and isinstance(auth, (int, float)):
                    if new_auth < auth:
                        records.append(ContradictionRecord(
                            id=str(_uuid.uuid4()),
                            memory_a_id=eid,
                            memory_b_id=new_memory_id,
                            conflict_type=ContradictionType.SOURCE_AUTHORITY_CONFLICT,
                            description=f"Lower-authority source updating higher-authority fact: "
                                        f"existing_auth={auth} new_auth={new_auth}",
                            predicate_key=new_pk,
                        ))

        # Procedural conflict: same procedure name, different steps
        elif new_type == "procedural":
            new_name = new_payload.get("procedure_name", "")
            new_steps = new_payload.get("steps", [])
            for (eid, epayload, etype, auth) in candidates:
                if etype != "procedural":
                    continue
                ename = epayload.get("procedure_name", "")
                if not new_name or not ename or new_name != ename:
                    continue
                esteps = epayload.get("steps", [])
                if new_steps != esteps:
                    records.append(ContradictionRecord(
                        id=str(_uuid.uuid4()),
                        memory_a_id=eid,
                        memory_b_id=new_memory_id,
                        conflict_type=ContradictionType.PROCEDURE_CONFLICT,
                        description=f"Procedure {new_name} has conflicting step sequences",
                        predicate_key=new_name,
                    ))

        return records


__all__ = [
    "ContradictionType", "ContradictionStatus", "ContradictionRecord",
    "ContradictionDetector",
]
