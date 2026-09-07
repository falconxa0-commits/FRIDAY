"""Knowledge Graph — per-tenant node + edge index.

The KnowledgeGraph is an in-memory index that maps entity_id → Entity and
relationship_id → Relationship, with adjacency lists for fast traversal.

Storage is delegated to M3 LivingMemoryManager (the source of truth). This
index is a cache that is rebuilt on startup from M3 records.

Bounded resources:
    - max_entities per tenant: 50,000
    - max_relationships_per_entity: 256 (degree cap)
    - max_query_result_size: 500
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .entity import Entity, EntityType
from .relationship import Relationship, RelationshipType

logger = logging.getLogger("friday.knowledge_graph.graph")


@dataclass
class GraphStats:
    """Snapshot statistics for a KnowledgeGraph."""
    entity_count: int = 0
    relationship_count: int = 0
    by_entity_type: Dict[str, int] = field(default_factory=dict)
    by_relationship_type: Dict[str, int] = field(default_factory=dict)
    avg_degree: float = 0.0
    max_degree: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_count": self.entity_count,
            "relationship_count": self.relationship_count,
            "by_entity_type": dict(self.by_entity_type),
            "by_relationship_type": dict(self.by_relationship_type),
            "avg_degree": round(self.avg_degree, 4),
            "max_degree": self.max_degree,
        }


class KnowledgeGraph:
    """Per-tenant in-memory knowledge graph index.

    Thread/async safe. Storage is delegated to M3 LivingMemoryManager.
    """

    def __init__(
        self,
        tenant_id: str = "default",
        max_entities: int = 50_000,
        max_relationships_per_entity: int = 256,
        max_query_results: int = 500,
        max_traversal_depth: int = 6,
    ):
        if max_entities < 1 or max_entities > 500_000:
            raise ValueError(f"max_entities out of range: {max_entities}")
        if max_relationships_per_entity < 1 or max_relationships_per_entity > 1024:
            raise ValueError(f"max_relationships_per_entity out of range")
        self._tenant_id = tenant_id
        self._max_entities = max_entities
        self._max_degree = max_relationships_per_entity
        self._max_query_results = max_query_results
        self._max_traversal_depth = max_traversal_depth

        # entity_id -> Entity
        self._entities: Dict[str, Entity] = {}
        # relationship_id -> Relationship
        self._relationships: Dict[str, Relationship] = {}
        # entity_id -> set of relationship_ids (outgoing + incoming)
        self._adjacency: Dict[str, Set[str]] = defaultdict(set)
        # name index: canonical_name + aliases -> entity_id (for fast lookup)
        self._name_index: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def max_entities(self) -> int:
        return self._max_entities

    @property
    def max_degree(self) -> int:
        return self._max_degree

    # ------------------------------------------------------------------
    # Entity operations
    # ------------------------------------------------------------------

    async def add_entity(self, entity: Entity) -> bool:
        """Add an entity. Returns True if added, False if already exists.

        V5 fix: detect name-index collisions. If two entities of different types
        share a canonical name or alias, the second add raises ValueError
        (prevents hijacking lookups for the first entity).
        """
        if entity.tenant_id != self._tenant_id:
            raise ValueError(f"Entity tenant {entity.tenant_id} != graph tenant {self._tenant_id}")
        async with self._lock:
            if entity.entity_id in self._entities:
                return False
            if len(self._entities) >= self._max_entities:
                raise ValueError(
                    f"Entity cap reached: {self._max_entities} (tenant={self._tenant_id})"
                )
            # V5 fix: check for name-index collisions (same name, different entity)
            for name in [entity.canonical_name] + entity.aliases:
                existing_eid = self._name_index.get(name)
                if existing_eid and existing_eid != entity.entity_id:
                    existing_entity = self._entities.get(existing_eid)
                    if existing_entity and existing_entity.entity_type != entity.entity_type:
                        raise ValueError(
                            f"Name collision: {name!r} already indexed to "
                            f"{existing_entity.entity_type.value} entity "
                            f"(cannot add as {entity.entity_type.value})"
                        )
            self._entities[entity.entity_id] = entity
            # Index names — only set if not already pointing to a different entity
            for name in [entity.canonical_name] + entity.aliases:
                if name not in self._name_index:
                    self._name_index[name] = entity.entity_id
            return True

    async def get_entity(self, entity_id: str) -> Optional[Entity]:
        async with self._lock:
            return self._entities.get(entity_id)

    async def find_entity_by_name(self, name: str) -> Optional[Entity]:
        """Find entity by canonical name or alias."""
        from .entity import canonicalize_name
        try:
            canonical = canonicalize_name(name)
        except ValueError:
            return None
        async with self._lock:
            eid = self._name_index.get(canonical)
            if not eid:
                return None
            return self._entities.get(eid)

    async def update_entity(self, entity: Entity) -> bool:
        """Update an existing entity (e.g. add alias, set attribute)."""
        async with self._lock:
            if entity.entity_id not in self._entities:
                return False
            self._entities[entity.entity_id] = entity
            # Rebuild name index for this entity
            self._name_index[entity.canonical_name] = entity.entity_id
            for alias in entity.aliases:
                if alias not in self._name_index or self._name_index[alias] == entity.entity_id:
                    self._name_index[alias] = entity.entity_id
            return True

    async def remove_entity(self, entity_id: str) -> bool:
        """Remove an entity + all its relationships."""
        async with self._lock:
            if entity_id not in self._entities:
                return False
            entity = self._entities[entity_id]
            # Remove from name index
            self._name_index.pop(entity.canonical_name, None)
            for alias in entity.aliases:
                # Only remove if it points to this entity
                if self._name_index.get(alias) == entity_id:
                    del self._name_index[alias]
            # Remove entity
            del self._entities[entity_id]
            # Remove all relationships involving this entity
            rel_ids = list(self._adjacency.get(entity_id, set()))
            for rid in rel_ids:
                self._relationships.pop(rid, None)
                # Remove from other endpoints' adjacency
                rel = None
                # We need to find the rel to know the other endpoint
                # (we already popped it, so look it up via the relationship_id)
                # Actually we should look it up BEFORE popping
            # Rebuild adjacency for affected entities
            self._rebuild_adjacency_for(entity_id)
            return True

    async def list_entities(
        self,
        entity_type: Optional[EntityType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Entity]:
        """List entities, optionally filtered by type."""
        if limit < 1 or limit > 50_000:
            raise ValueError("limit must be 1..50000")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        async with self._lock:
            entities = list(self._entities.values())
        if entity_type:
            entities = [e for e in entities if e.entity_type == entity_type]
        entities = entities[offset:offset + limit]
        return entities

    # ------------------------------------------------------------------
    # Relationship operations
    # ------------------------------------------------------------------

    async def add_relationship(self, rel: Relationship) -> bool:
        """Add a relationship. Returns True if added, False if duplicate.

        V4 fix: enforce degree cap on BOTH source (out-degree) AND target
        (in-degree). Previously only source was checked, allowing target-side
        degree blowup via directional edges.
        """
        if rel.tenant_id != self._tenant_id:
            raise ValueError(f"Relationship tenant mismatch")
        async with self._lock:
            if rel.relationship_id in self._relationships:
                return False
            # Check degree cap on SOURCE (out-degree)
            out_count = sum(
                1 for rid in self._adjacency.get(rel.source_entity_id, set())
                if rid in self._relationships and
                self._relationships[rid].source_entity_id == rel.source_entity_id
            )
            if out_count >= self._max_degree:
                raise ValueError(
                    f"Out-degree cap reached for entity {rel.source_entity_id[:12]}: {out_count}"
                )
            # V4 fix: Check degree cap on TARGET (in-degree) for directional edges
            if not rel.bidirectional:
                in_count = sum(
                    1 for rid in self._adjacency.get(rel.target_entity_id, set())
                    if rid in self._relationships and
                    self._relationships[rid].target_entity_id == rel.target_entity_id
                )
                if in_count >= self._max_degree:
                    raise ValueError(
                        f"In-degree cap reached for entity {rel.target_entity_id[:12]}: {in_count}"
                    )
            # Check both endpoints exist
            if rel.source_entity_id not in self._entities:
                raise ValueError(f"Source entity {rel.source_entity_id[:12]} not in graph")
            if rel.target_entity_id not in self._entities:
                raise ValueError(f"Target entity {rel.target_entity_id[:12]} not in graph")
            self._relationships[rel.relationship_id] = rel
            self._adjacency[rel.source_entity_id].add(rel.relationship_id)
            self._adjacency[rel.target_entity_id].add(rel.relationship_id)
            return True

    async def get_relationship(self, relationship_id: str) -> Optional[Relationship]:
        async with self._lock:
            return self._relationships.get(relationship_id)

    async def find_relationship(
        self,
        source_id: str,
        target_id: str,
        rel_type: RelationshipType,
    ) -> Optional[Relationship]:
        """Find a specific relationship by endpoints + type."""
        from .relationship import derive_relationship_id
        rid = derive_relationship_id(source_id, target_id, rel_type)
        async with self._lock:
            return self._relationships.get(rid)

    async def remove_relationship(self, relationship_id: str) -> bool:
        async with self._lock:
            if relationship_id not in self._relationships:
                return False
            rel = self._relationships.pop(relationship_id)
            self._adjacency[rel.source_entity_id].discard(relationship_id)
            self._adjacency[rel.target_entity_id].discard(relationship_id)
            return True

    async def neighbors(
        self,
        entity_id: str,
        rel_type: Optional[RelationshipType] = None,
        min_weight: float = 0.0,
        direction: str = "both",  # "out" | "in" | "both"
    ) -> List[Tuple[Relationship, str]]:
        """Get neighbors of an entity. Returns (relationship, other_entity_id) tuples.

        V12 fix: direction="in" for non-bidirectional edges now correctly
        returns the source entity as the "other" endpoint (previously returned
        nothing, masking the in-degree).
        """
        if direction not in ("out", "in", "both"):
            raise ValueError("direction must be out/in/both")
        if not 0.0 <= min_weight <= 1.0:
            raise ValueError("min_weight must be in [0,1]")
        async with self._lock:
            results: List[Tuple[Relationship, str]] = []
            for rid in self._adjacency.get(entity_id, set()):
                rel = self._relationships.get(rid)
                if not rel:
                    continue
                if rel_type and rel.relationship_type != rel_type:
                    continue
                if rel.weight < min_weight:
                    continue
                # Determine the other endpoint based on direction
                if rel.source_entity_id == entity_id:
                    # entity_id is the source → "out" direction
                    if direction == "in":
                        continue
                    other = rel.target_entity_id
                elif rel.target_entity_id == entity_id:
                    # entity_id is the target → "in" direction
                    if direction == "out":
                        continue
                    # V12 fix: for non-bidirectional edges, "in" direction
                    # returns the source as the other endpoint
                    other = rel.source_entity_id
                else:
                    continue
                results.append((rel, other))
            return results

    async def traverse(
        self,
        start_entity_id: str,
        max_depth: Optional[int] = None,
        rel_type: Optional[RelationshipType] = None,
        min_weight: float = 0.0,
        max_results: int = 100,
    ) -> List[Tuple[str, int, float]]:
        """BFS traversal. Returns (entity_id, depth, accumulated_weight) tuples."""
        depth = min(max_depth or self._max_traversal_depth, self._max_traversal_depth)
        if max_results > self._max_query_results:
            max_results = self._max_query_results
        async with self._lock:
            visited: Set[str] = {start_entity_id}
            results: List[Tuple[str, int, float]] = []
            queue: List[Tuple[str, int, float]] = [(start_entity_id, 0, 1.0)]
            while queue:
                cur, d, total_w = queue.pop(0)
                if d >= depth:
                    continue
                if len(results) >= max_results:
                    break
                for rid in self._adjacency.get(cur, set()):
                    rel = self._relationships.get(rid)
                    if not rel:
                        continue
                    if rel_type and rel.relationship_type != rel_type:
                        continue
                    if rel.weight < min_weight:
                        continue
                    # Determine other endpoint
                    if rel.source_entity_id == cur:
                        other = rel.target_entity_id
                    elif rel.target_entity_id == cur:
                        # V12 fix: traverse can follow incoming edges too
                        # (the relationship exists in both endpoints' adjacency).
                        # For non-bidirectional, this means "walking backwards"
                        # along the edge — which is valid for traversal.
                        other = rel.source_entity_id
                    else:
                        continue
                    if other in visited:
                        continue
                    visited.add(other)
                    new_w = total_w * rel.weight
                    results.append((other, d + 1, new_w))
                    queue.append((other, d + 1, new_w))
            return results

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def stats(self) -> GraphStats:
        async with self._lock:
            stats = GraphStats()
            stats.entity_count = len(self._entities)
            stats.relationship_count = len(self._relationships)
            for e in self._entities.values():
                t = e.entity_type.value
                stats.by_entity_type[t] = stats.by_entity_type.get(t, 0) + 1
            for r in self._relationships.values():
                t = r.relationship_type.value
                stats.by_relationship_type[t] = stats.by_relationship_type.get(t, 0) + 1
            # Degree stats (out-degree)
            degrees = []
            for eid, rels in self._adjacency.items():
                out_count = sum(
                    1 for rid in rels
                    if rid in self._relationships and
                    self._relationships[rid].source_entity_id == eid
                )
                degrees.append(out_count)
            if degrees:
                stats.avg_degree = sum(degrees) / len(degrees)
                stats.max_degree = max(degrees)
            return stats

    async def entity_count(self) -> int:
        async with self._lock:
            return len(self._entities)

    async def relationship_count(self) -> int:
        async with self._lock:
            return len(self._relationships)

    def _rebuild_adjacency_for(self, entity_id: str) -> None:
        """Rebuild adjacency entries for an entity (after removal)."""
        # Remove the entity's adjacency entry
        self._adjacency.pop(entity_id, None)
        # Remove any dangling relationship IDs from other entities' adjacency
        for eid, rels in list(self._adjacency.items()):
            rels_to_remove = [rid for rid in rels if rid not in self._relationships]
            for rid in rels_to_remove:
                rels.discard(rid)


__all__ = ["KnowledgeGraph", "GraphStats"]
