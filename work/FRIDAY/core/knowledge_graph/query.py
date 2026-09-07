"""Knowledge Graph — Query Engine.

SPARQL-inspired but simplified query language for the knowledge graph.

Query structure:
    {
        "entity_filter": {
            "type": "person",                          # optional
            "name_pattern": "alice*",                  # optional (glob)
            "attributes": {"role": "engineer"},        # optional
            "min_confidence": 0.5,                     # optional
        },
        "relationship_filter": {
            "type": "works_for",                       # optional
            "direction": "out",                        # out/in/both
            "min_weight": 0.5,                         # optional
        },
        "traversal": {
            "max_depth": 3,
            "relationship_types": ["works_for", "member_of"],
            "min_weight": 0.3,
        },
        "limit": 100,
        "offset": 0,
    }

Returns: List[QueryResult] where each QueryResult contains:
    - entity: the matched Entity
    - score: float (relevance score)
    - path: List of (relationship_id, entity_id) tuples showing how we got here
    - matched_attributes: Dict of attributes that matched the filter
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .entity import Entity, EntityType
from .graph import KnowledgeGraph
from .relationship import Relationship, RelationshipType

logger = logging.getLogger("friday.knowledge_graph.query")


@dataclass
class EntityFilter:
    """Filter for entity selection."""
    entity_type: Optional[EntityType] = None
    name_pattern: Optional[str] = None  # glob pattern
    attributes: Dict[str, Any] = field(default_factory=dict)
    min_confidence: float = 0.0
    tags: List[str] = field(default_factory=list)  # not used yet; reserved

    def matches(self, entity: Entity) -> bool:
        if self.entity_type and entity.entity_type != self.entity_type:
            return False
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0,1]")
        if entity.confidence < self.min_confidence:
            return False
        if self.name_pattern:
            # Match against canonical name + aliases
            names = [entity.canonical_name] + entity.aliases
            if not any(fnmatch.fnmatch(n, self.name_pattern) for n in names):
                return False
        for k, v in self.attributes.items():
            if entity.attributes.get(k) != v:
                return False
        return True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EntityFilter":
        et = None
        if d.get("type"):
            et = EntityType.from_string(d["type"])
        return cls(
            entity_type=et,
            name_pattern=d.get("name_pattern"),
            attributes=dict(d.get("attributes", {})),
            min_confidence=float(d.get("min_confidence", 0.0)),
            tags=list(d.get("tags", [])),
        )


@dataclass
class RelationshipFilter:
    """Filter for relationship traversal."""
    relationship_type: Optional[RelationshipType] = None
    direction: str = "both"  # out/in/both
    min_weight: float = 0.0

    def __post_init__(self):
        if self.direction not in ("out", "in", "both"):
            raise ValueError(f"direction must be out/in/both, got {self.direction}")
        if not 0.0 <= self.min_weight <= 1.0:
            raise ValueError("min_weight must be in [0,1]")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RelationshipFilter":
        rt = None
        if d.get("type"):
            from .relationship import RelationshipType as RT
            rt = RT.from_string(d["type"])
        return cls(
            relationship_type=rt,
            direction=d.get("direction", "both"),
            min_weight=float(d.get("min_weight", 0.0)),
        )


@dataclass
class TraversalSpec:
    """Specification for graph traversal."""
    max_depth: int = 3
    relationship_types: List[RelationshipType] = field(default_factory=list)
    min_weight: float = 0.0
    max_results: int = 100

    def __post_init__(self):
        if not 1 <= self.max_depth <= 8:
            raise ValueError("max_depth must be 1..8")
        if not 0.0 <= self.min_weight <= 1.0:
            raise ValueError("min_weight must be in [0,1]")
        if not 1 <= self.max_results <= 500:
            raise ValueError("max_results must be 1..500")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TraversalSpec":
        from .relationship import RelationshipType as RT
        rt_list = []
        for t in d.get("relationship_types", []):
            rt = RT.from_string(t)
            if rt:
                rt_list.append(rt)
        return cls(
            max_depth=int(d.get("max_depth", 3)),
            relationship_types=rt_list,
            min_weight=float(d.get("min_weight", 0.0)),
            max_results=int(d.get("max_results", 100)),
        )


@dataclass
class KnowledgeQuery:
    """A complete knowledge graph query."""
    entity_filter: Optional[EntityFilter] = None
    relationship_filter: Optional[RelationshipFilter] = None
    traversal: Optional[TraversalSpec] = None
    limit: int = 100
    offset: int = 0

    def __post_init__(self):
        if not 1 <= self.limit <= 500:
            raise ValueError("limit must be 1..500")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KnowledgeQuery":
        ef = EntityFilter.from_dict(d["entity_filter"]) if d.get("entity_filter") else None
        rf = RelationshipFilter.from_dict(d["relationship_filter"]) if d.get("relationship_filter") else None
        tv = TraversalSpec.from_dict(d["traversal"]) if d.get("traversal") else None
        return cls(
            entity_filter=ef,
            relationship_filter=rf,
            traversal=tv,
            limit=int(d.get("limit", 100)),
            offset=int(d.get("offset", 0)),
        )


@dataclass
class QueryResult:
    """A single query result."""
    entity: Entity
    score: float
    path: List[Tuple[str, str]] = field(default_factory=list)  # (rel_id, entity_id)
    matched_attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity": self.entity.to_dict(),
            "score": round(self.score, 6),
            "path": [list(p) for p in self.path],
            "matched_attributes": dict(self.matched_attributes),
        }


class QueryEngine:
    """Executes KnowledgeQuery against a KnowledgeGraph."""

    def __init__(self, graph: KnowledgeGraph):
        self._graph = graph

    async def execute(self, query: KnowledgeQuery) -> List[QueryResult]:
        """Execute a query. Returns list of QueryResult."""
        # Step 1: find matching entities
        if query.entity_filter:
            entities = await self._graph.list_entities(
                entity_type=query.entity_filter.entity_type,
                limit=1000,  # cap pre-filter
            )
            matching = [e for e in entities if query.entity_filter.matches(e)]
        else:
            entities = await self._graph.list_entities(limit=1000)
            matching = entities

        # Apply offset
        matching = matching[query.offset:query.offset + query.limit]

        if not matching:
            return []

        results: List[QueryResult] = []
        for entity in matching:
            score = entity.confidence
            matched_attrs = {}
            if query.entity_filter and query.entity_filter.attributes:
                for k, v in query.entity_filter.attributes.items():
                    if entity.attributes.get(k) == v:
                        matched_attrs[k] = v

            # Step 2: optional traversal
            path: List[Tuple[str, str]] = []
            if query.traversal and query.traversal.max_depth > 0:
                # Use the first relationship type if specified
                rel_type = None
                if query.traversal.relationship_types:
                    rel_type = query.traversal.relationship_types[0]
                traversal_results = await self._graph.traverse(
                    entity.entity_id,
                    max_depth=query.traversal.max_depth,
                    rel_type=rel_type,
                    min_weight=query.traversal.min_weight,
                    max_results=query.traversal.max_results,
                )
                # Boost score if traversal found related entities
                if traversal_results:
                    score = min(1.0, score + 0.1)
                    path = [(r[0], r[0]) for r in traversal_results[:5]]  # simplified path

            results.append(QueryResult(
                entity=entity,
                score=score,
                path=path,
                matched_attributes=matched_attrs,
            ))

        # Sort by score desc
        results.sort(key=lambda r: -r.score)
        return results[:query.limit]


__all__ = [
    "EntityFilter", "RelationshipFilter", "TraversalSpec",
    "KnowledgeQuery", "QueryResult", "QueryEngine",
]
