"""Associative Memory — weighted graph of memory links.

Allows memories to reference related memories. Edges are:
    - Directed (or bidirectional if requested)
    - Typed (relationship type)
    - Weighted (0..1, confidence in the association)
    - Bounded: max edges per node + max traversal depth

Prevents infinite traversal via depth cap.
Prevents uncontrolled graph growth via degree cap.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .base import (
    Memory, MemoryID, MemoryType, MemoryState, MemoryMetadata,
    now_utc, ResourceLimitError, ValidationError,
)

logger = logging.getLogger("friday.living_memory.association")


@dataclass
class AssociationEdge:
    """A directed edge between two memories."""
    source_id: str
    target_id: str
    relationship: str = "related"  # e.g. "causes", "similar_to", "supersedes"
    weight: float = 0.5  # 0..1
    bidirectional: bool = False
    created_at: str = field(default_factory=now_utc)
    created_by: str = ""
    correlation_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relationship": self.relationship,
            "weight": round(self.weight, 6),
            "bidirectional": self.bidirectional,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "correlation_id": self.correlation_id,
        }


@dataclass
class AssociationMemory(Memory):
    """An association edge stored as a memory record (so it has provenance)."""
    type: MemoryType = MemoryType.ASSOCIATIVE

    @classmethod
    def create(
        cls,
        source_id: str,
        target_id: str,
        relationship: str = "related",
        weight: float = 0.5,
        bidirectional: bool = False,
        owner_id: str = "",
        tenant_id: str = "default",
        confidence: float = 0.7,
        importance: float = 0.5,
        correlation_id: str = "",
    ) -> "AssociationMemory":
        if not source_id or not target_id:
            raise ValidationError("source_id and target_id required")
        if source_id == target_id:
            raise ValidationError("self-association not allowed")
        if not 0.0 <= weight <= 1.0:
            raise ValidationError("weight must be in [0, 1]")
        if not relationship:
            raise ValidationError("relationship required")
        payload = {
            "source_id": source_id,
            "target_id": target_id,
            "relationship": relationship,
            "weight": float(weight),
            "bidirectional": bool(bidirectional),
        }
        return cls(
            id=MemoryID(),
            type=MemoryType.ASSOCIATIVE,
            state=MemoryState.CREATED,
            metadata=MemoryMetadata(
                owner_id=owner_id,
                created_by=owner_id,
                tenant_id=tenant_id,
                confidence=confidence,
                importance=importance,
                correlation_id=correlation_id,
            ),
            payload=payload,
        )


class AssociationGraph:
    """In-memory association graph with bounded degree + traversal depth.

    One graph per tenant. Thread/async safe.
    """

    def __init__(
        self,
        tenant_id: str = "default",
        max_degree: int = 64,
        max_traversal_depth: int = 8,
        max_edges: int = 100_000,
    ):
        if max_degree < 1:
            raise ValidationError("max_degree must be >= 1")
        if max_traversal_depth < 1:
            raise ValidationError("max_traversal_depth must be >= 1")
        self._tenant_id = tenant_id
        self._max_degree = max_degree
        self._max_traversal_depth = max_traversal_depth
        self._max_edges = max_edges
        # Outgoing edges: source_id -> {target_id: edge}
        self._out: Dict[str, Dict[str, AssociationEdge]] = {}
        # Incoming edges: target_id -> set of source_ids (for reverse lookup)
        self._in: Dict[str, Set[str]] = {}
        # Edge memory_id -> edge (for management)
        self._edges_by_id: Dict[str, AssociationEdge] = {}  # note: edge id not stored; use (s,t,key) tuple
        self._lock = asyncio.Lock()

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    async def add_edge(self, edge: AssociationEdge) -> None:
        """Add an edge. Raises ResourceLimitError if degree or total cap exceeded."""
        async with self._lock:
            # Dedup: if same source+target+relationship exists, replace
            existing = self._out.get(edge.source_id, {}).get(edge.target_id)
            if existing and existing.relationship == edge.relationship:
                # Replace (update weight, bidirectional, etc.)
                self._out[edge.source_id][edge.target_id] = edge
                if edge.bidirectional:
                    self._out.setdefault(edge.target_id, {})[edge.source_id] = edge
                return
            # Check total edge cap
            if len(self._edges_by_id) >= self._max_edges:
                raise ResourceLimitError(
                    f"Association graph at edge cap: {self._max_edges}"
                )
            # Check degree cap on SOURCE
            out_degree = len(self._out.get(edge.source_id, {}))
            if out_degree >= self._max_degree:
                raise ResourceLimitError(
                    f"Node {edge.source_id[:8]} at max out-degree {self._max_degree}"
                )
            # V3: For bidirectional edges, also enforce degree cap on TARGET.
            # The target gets a reverse edge in self._out, so its degree grows too.
            if edge.bidirectional:
                target_degree = len(self._out.get(edge.target_id, {}))
                if target_degree >= self._max_degree:
                    raise ResourceLimitError(
                        f"Node {edge.target_id[:8]} at max out-degree "
                        f"{self._max_degree} (bidirectional target)"
                    )
            # Add edge
            self._out.setdefault(edge.source_id, {})[edge.target_id] = edge
            self._in.setdefault(edge.target_id, set()).add(edge.source_id)
            if edge.bidirectional:
                # For bidirectional, ensure reverse index exists
                self._in.setdefault(edge.source_id, set()).add(edge.target_id)
                self._out.setdefault(edge.target_id, {}).setdefault(edge.source_id, edge)
            # Edge id tracking (use source|target|rel as key since edges don't have IDs)
            edge_key = f"{edge.source_id}|{edge.target_id}|{edge.relationship}"
            self._edges_by_id[edge_key] = edge

    async def remove_edge(
        self, source_id: str, target_id: str, relationship: str = ""
    ) -> bool:
        async with self._lock:
            out_map = self._out.get(source_id, {})
            edge = out_map.get(target_id)
            if not edge:
                return False
            if relationship and edge.relationship != relationship:
                return False
            del out_map[target_id]
            if not out_map:
                del self._out[source_id]
            in_set = self._in.get(target_id, set())
            in_set.discard(source_id)
            if not in_set:
                del self._in[target_id]
            edge_key = f"{source_id}|{target_id}|{edge.relationship}"
            self._edges_by_id.pop(edge_key, None)
            if edge.bidirectional:
                # Also remove reverse
                rev_map = self._out.get(target_id, {})
                if source_id in rev_map:
                    del rev_map[source_id]
                    if not rev_map:
                        del self._out[target_id]
                rev_in = self._in.get(source_id, set())
                rev_in.discard(target_id)
                if not rev_in:
                    del self._in[source_id]
            return True

    async def neighbors(
        self,
        memory_id: str,
        relationship: str = "",
        min_weight: float = 0.0,
        direction: str = "out",  # "out" | "in" | "both"
    ) -> List[AssociationEdge]:
        """Get direct neighbors of a memory (1-hop)."""
        async with self._lock:
            edges: List[AssociationEdge] = []
            if direction in ("out", "both"):
                for target, edge in self._out.get(memory_id, {}).items():
                    if relationship and edge.relationship != relationship:
                        continue
                    if edge.weight < min_weight:
                        continue
                    edges.append(edge)
            if direction in ("in", "both"):
                for source in self._in.get(memory_id, set()):
                    edge = self._out.get(source, {}).get(memory_id)
                    if not edge:
                        continue
                    if relationship and edge.relationship != relationship:
                        continue
                    if edge.weight < min_weight:
                        continue
                    # Don't double-count bidirectional edges
                    if direction == "both" and edge.bidirectional and edge in edges:
                        continue
                    edges.append(edge)
            return edges

    async def traverse(
        self,
        start_id: str,
        max_depth: Optional[int] = None,
        min_weight: float = 0.0,
        relationship: str = "",
        max_results: int = 100,
    ) -> List[Tuple[str, int, float]]:
        """BFS traversal. Returns list of (memory_id, depth, total_weight).

        The traversal is capped at max_traversal_depth (configurable) to
        prevent infinite walks. max_results caps the response size.
        """
        depth = min(max_depth or self._max_traversal_depth, self._max_traversal_depth)
        async with self._lock:
            visited: Set[str] = {start_id}
            results: List[Tuple[str, int, float]] = []
            queue: List[Tuple[str, int, float]] = [(start_id, 0, 1.0)]
            while queue:
                cur, d, total_w = queue.pop(0)
                if d >= depth:
                    continue
                if len(results) >= max_results:
                    break
                for target, edge in self._out.get(cur, {}).items():
                    if edge.weight < min_weight:
                        continue
                    if relationship and edge.relationship != relationship:
                        continue
                    if target in visited:
                        continue
                    visited.add(target)
                    new_total = total_w * edge.weight
                    results.append((target, d + 1, new_total))
                    queue.append((target, d + 1, new_total))
                # Also traverse incoming if bidirectional edges exist
                for source in self._in.get(cur, set()):
                    edge = self._out.get(source, {}).get(cur)
                    if not edge or not edge.bidirectional:
                        continue
                    if edge.weight < min_weight:
                        continue
                    if relationship and edge.relationship != relationship:
                        continue
                    if source in visited:
                        continue
                    visited.add(source)
                    new_total = total_w * edge.weight
                    results.append((source, d + 1, new_total))
                    queue.append((source, d + 1, new_total))
            return results

    async def degree(self, memory_id: str, direction: str = "out") -> int:
        async with self._lock:
            if direction == "out":
                return len(self._out.get(memory_id, {}))
            elif direction == "in":
                return len(self._in.get(memory_id, set()))
            else:
                return len(self._out.get(memory_id, {})) + len(self._in.get(memory_id, set()))

    async def edge_count(self) -> int:
        async with self._lock:
            return len(self._edges_by_id)

    async def remove_all_for(self, memory_id: str) -> int:
        """Remove all edges involving this memory (used when a memory is forgotten)."""
        async with self._lock:
            removed = 0
            # Outgoing
            for target in list(self._out.get(memory_id, {}).keys()):
                edge = self._out[memory_id][target]
                edge_key = f"{memory_id}|{target}|{edge.relationship}"
                self._edges_by_id.pop(edge_key, None)
                self._in.get(target, set()).discard(memory_id)
                removed += 1
                if edge.bidirectional:
                    rev = self._out.get(target, {})
                    if memory_id in rev:
                        del rev[memory_id]
            if memory_id in self._out:
                del self._out[memory_id]
            # Incoming
            for source in list(self._in.get(memory_id, set())):
                edge = self._out.get(source, {}).get(memory_id)
                if edge:
                    edge_key = f"{source}|{memory_id}|{edge.relationship}"
                    self._edges_by_id.pop(edge_key, None)
                    self._out.get(source, {}).pop(memory_id, None)
                    removed += 1
                    if edge.bidirectional:
                        rev_in = self._in.get(memory_id, set())
                        # nothing extra to do
                        pass
            if memory_id in self._in:
                del self._in[memory_id]
            return removed

    async def snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            return {
                "tenant_id": self._tenant_id,
                "node_count": len(set(list(self._out.keys()) + list(self._in.keys()))),
                "edge_count": len(self._edges_by_id),
                "max_degree": self._max_degree,
                "max_traversal_depth": self._max_traversal_depth,
                "max_edges": self._max_edges,
            }


__all__ = ["AssociationEdge", "AssociationMemory", "AssociationGraph"]
