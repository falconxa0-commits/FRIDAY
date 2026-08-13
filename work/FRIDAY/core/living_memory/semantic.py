"""Semantic Memory — generalized facts, concepts, entities.

Semantic memories are:
    - Facts with predicate_key + value structure
    - Generalized knowledge (not tied to a specific episode)
    - Persistent (require provenance)
    - Subject to contradiction detection on write
    - Source-authority aware
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import Memory, MemoryID, MemoryMetadata, MemoryType, MemoryState, now_utc

logger = logging.getLogger("friday.living_memory.semantic")


@dataclass
class SemanticMemory(Memory):
    """A semantic fact / concept.

    Payload fields:
        subject:           str (e.g. "user", "python", "project_x")
        predicate:         str (e.g. "name", "language_version", "status")
        predicate_key:     str (canonical: f"{subject}.{predicate}")
        value:             Any (the fact value)
        value_type:        str ("str" | "int" | "float" | "bool" | "list" | "dict")
        source:            str (URI / agent / sensor that established this)
        source_authority:  float (0..1, higher = more authoritative)
        alternatives:      List[Dict] (rejected alternative values + reasons)
        superseded_by:     str (memory_id that supersedes this one, if any)
    """
    type: MemoryType = MemoryType.SEMANTIC

    @classmethod
    def create(
        cls,
        subject: str,
        predicate: str,
        value: Any,
        source: str = "",
        source_authority: float = 0.5,
        owner_id: str = "",
        tenant_id: str = "default",
        tags: Optional[List[str]] = None,
        confidence: float = 0.7,
        importance: float = 0.5,
        correlation_id: str = "",
        alternatives: Optional[List[Dict[str, Any]]] = None,
    ) -> "SemanticMemory":
        if not subject:
            raise ValueError("subject is required for semantic memory")
        if not predicate:
            raise ValueError("predicate is required for semantic memory")
        if not isinstance(source_authority, (int, float)) or not 0.0 <= source_authority <= 1.0:
            raise ValueError("source_authority must be in [0, 1]")
        predicate_key = f"{subject}.{predicate}"
        value_type = cls._infer_value_type(value)
        payload = {
            "subject": subject,
            "predicate": predicate,
            "predicate_key": predicate_key,
            "value": value,
            "value_type": value_type,
            "source": source,
            "source_authority": float(source_authority),
            "alternatives": list(alternatives or []),
            "superseded_by": "",
        }
        return cls(
            id=MemoryID(),
            type=MemoryType.SEMANTIC,
            state=MemoryState.CREATED,
            metadata=MemoryMetadata(
                owner_id=owner_id,
                created_by=owner_id,
                tenant_id=tenant_id,
                tags=list(tags or []),
                confidence=confidence,
                importance=importance,
                correlation_id=correlation_id,
            ),
            payload=payload,
        )

    @staticmethod
    def _infer_value_type(value: Any) -> str:
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "dict"
        return "str"

    @property
    def predicate_key(self) -> str:
        return self.payload.get("predicate_key", "")

    @property
    def subject(self) -> str:
        return self.payload.get("subject", "")

    @property
    def predicate(self) -> str:
        return self.payload.get("predicate", "")

    @property
    def value(self) -> Any:
        return self.payload.get("value")

    @property
    def source_authority(self) -> float:
        return float(self.payload.get("source_authority", 0.5))

    def supersede(self, new_memory_id: str) -> None:
        """Mark this fact as superseded by a newer memory."""
        self.payload["superseded_by"] = new_memory_id
        self.metadata.updated_at = now_utc()

    @property
    def is_superseded(self) -> bool:
        return bool(self.payload.get("superseded_by"))


__all__ = ["SemanticMemory"]
