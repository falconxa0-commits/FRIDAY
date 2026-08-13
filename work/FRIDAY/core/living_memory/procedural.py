"""Procedural Memory — workflows, skills, learned procedures.

Procedural memories are:
    - Step-sequenced procedures with named inputs/outputs
    - Versioned (new versions supersede old, old retained for audit)
    - Track success/failure rates from execution history
    - Persistent (require provenance)
    - Subject to contradiction detection (same name, different steps)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import (
    Memory, MemoryID, MemoryMetadata, MemoryType, MemoryState, now_utc,
)

logger = logging.getLogger("friday.living_memory.procedural")


@dataclass
class ProceduralMemory(Memory):
    """A learned procedure / workflow.

    Payload fields:
        procedure_name:  str (unique within a tenant)
        version:         int (starts at 1, incremented on update)
        steps:           List[Dict[str, Any]] (ordered, each has name+params)
        inputs:          Dict[str, str] (param_name -> type)
        outputs:         Dict[str, str] (param_name -> type)
        execution_count: int
        success_count:   int
        failure_count:   int
        avg_duration_ms: float
        supersedes:      str (memory_id of previous version, if any)
        superseded_by:   str (memory_id of newer version, if any)
    """
    type: MemoryType = MemoryType.PROCEDURAL

    @classmethod
    def create(
        cls,
        procedure_name: str,
        steps: List[Dict[str, Any]],
        inputs: Optional[Dict[str, str]] = None,
        outputs: Optional[Dict[str, str]] = None,
        owner_id: str = "",
        tenant_id: str = "default",
        tags: Optional[List[str]] = None,
        confidence: float = 0.5,
        importance: float = 0.5,
        correlation_id: str = "",
        supersedes: str = "",
    ) -> "ProceduralMemory":
        if not procedure_name:
            raise ValueError("procedure_name is required")
        if not steps or not isinstance(steps, list):
            raise ValueError("steps must be a non-empty list")
        # Each step must have a name
        for i, s in enumerate(steps):
            if not isinstance(s, dict) or not s.get("name"):
                raise ValueError(f"Step {i} must be a dict with a 'name'")
        payload = {
            "procedure_name": procedure_name,
            "version": 1,
            "steps": list(steps),
            "inputs": dict(inputs or {}),
            "outputs": dict(outputs or {}),
            "execution_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "avg_duration_ms": 0.0,
            "supersedes": supersedes,
            "superseded_by": "",
        }
        return cls(
            id=MemoryID(),
            type=MemoryType.PROCEDURAL,
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

    @property
    def procedure_name(self) -> str:
        return self.payload.get("procedure_name", "")

    @property
    def version(self) -> int:
        return int(self.payload.get("version", 1))

    @property
    def steps(self) -> List[Dict[str, Any]]:
        return list(self.payload.get("steps", []))

    @property
    def success_rate(self) -> float:
        total = self.payload.get("execution_count", 0)
        if total == 0:
            return 0.0
        return float(self.payload.get("success_count", 0)) / total

    def record_execution(self, success: bool, duration_ms: float = 0.0) -> None:
        """Record an execution outcome. Updates success rate + avg duration."""
        self.payload["execution_count"] = int(self.payload.get("execution_count", 0)) + 1
        if success:
            self.payload["success_count"] = int(self.payload.get("success_count", 0)) + 1
        else:
            self.payload["failure_count"] = int(self.payload.get("failure_count", 0)) + 1
        # Running average
        prev_avg = float(self.payload.get("avg_duration_ms", 0.0))
        n = self.payload["execution_count"]
        self.payload["avg_duration_ms"] = round(
            (prev_avg * (n - 1) + duration_ms) / n, 3
        ) if n > 0 else 0.0
        # Reinforce on success, decay confidence on failure
        if success:
            self.metadata.reinforce(delta=0.05)
        else:
            self.metadata.confidence = max(0.0, self.metadata.confidence - 0.05)
            self.metadata.updated_at = now_utc()

    def new_version(
        self,
        steps: List[Dict[str, Any]],
        inputs: Optional[Dict[str, str]] = None,
        outputs: Optional[Dict[str, str]] = None,
    ) -> "ProceduralMemory":
        """Create a new version of this procedure (supersedes self)."""
        new_mem = ProceduralMemory.create(
            procedure_name=self.procedure_name,
            steps=steps,
            inputs=inputs or self.payload.get("inputs", {}),
            outputs=outputs or self.payload.get("outputs", {}),
            owner_id=self.metadata.owner_id,
            tenant_id=self.metadata.tenant_id,
            tags=self.metadata.tags,
            confidence=self.metadata.confidence,
            importance=self.metadata.importance,
            correlation_id=self.metadata.correlation_id,
            supersedes=self.id.value,
        )
        new_mem.payload["version"] = self.version + 1
        # Mark self as superseded
        self.payload["superseded_by"] = new_mem.id.value
        self.metadata.updated_at = now_utc()
        return new_mem

    @property
    def is_superseded(self) -> bool:
        return bool(self.payload.get("superseded_by"))


__all__ = ["ProceduralMemory"]
