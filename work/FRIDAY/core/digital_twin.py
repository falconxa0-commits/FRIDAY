"""Digital Twin — internal engineering knowledge graph.

Models the entire FRIDAY engineering platform as a graph of entities
and relationships. Every engineering action updates the graph.

Entities:
    - Module (Python file)
    - Package (directory)
    - Task
    - KnowledgeEntry
    - Benchmark
    - Release
    - ADR
    - Finding
    - Experiment

Relationships:
    - DEPENDS_ON (module → module)
    - OWNS (package → module)
    - IMPLEMENTS (module → task)
    - DOCUMENTED_BY (module → knowledge entry)
    - BENCHMARKED_BY (module → benchmark)
    - RELEASED_IN (task → release)
    - DECIDED_BY (module → ADR)
    - HAS_FINDING (module → finding)

Usage::

    twin = get_digital_twin()
    await twin.add_entity("module", "core.brain", {"loc": 866})
    await twin.add_relationship("core.brain", "core.ledger", "DEPENDS_ON")
    graph = await twin.get_subgraph("core.brain", depth=2)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("friday.digital_twin")

_TWIN_DIR = Path(os.environ.get(
    "FRIDAY_ENGINEERING_DIR",
    str(Path.cwd() / ".friday"),
)) / "digital_twin"
_TWIN_DIR.mkdir(parents=True, exist_ok=True)
_TWIN_PATH = _TWIN_DIR / "graph.json"


class EntityType(str, Enum):
    MODULE = "module"
    PACKAGE = "package"
    TASK = "task"
    KNOWLEDGE = "knowledge"
    BENCHMARK = "benchmark"
    RELEASE = "release"
    ADR = "adr"
    FINDING = "finding"
    EXPERIMENT = "experiment"
    TEST = "test"
    DEPENDENCY = "dependency"


class RelationType(str, Enum):
    DEPENDS_ON = "depends_on"
    OWNS = "owns"
    IMPLEMENTS = "implements"
    DOCUMENTED_BY = "documented_by"
    BENCHMARKED_BY = "benchmarked_by"
    RELEASED_IN = "released_in"
    DECIDED_BY = "decided_by"
    HAS_FINDING = "has_finding"
    TESTED_BY = "tested_by"
    RELATED_TO = "related_to"


@dataclass
class Entity:
    """A node in the digital twin graph."""
    id: str
    type: EntityType
    properties: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "properties": self.properties,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Relationship:
    """An edge in the digital twin graph."""
    source: str
    target: str
    type: RelationType
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type.value,
            "properties": self.properties,
        }


class DigitalTwin:
    """The engineering knowledge graph.

    Persists to ``.friday/digital_twin/graph.json``. Every engineering
    action (task completion, finding, release, ADR) updates the graph.
    """

    def __init__(self, persist_path: Optional[Path] = None):
        self.persist_path = persist_path or _TWIN_PATH
        self._lock = asyncio.Lock()
        self._entities: Dict[str, Entity] = {}
        self._relationships: List[Relationship] = []
        self._adjacency: Dict[str, List[Tuple[str, RelationType]]] = defaultdict(list)
        self._load()

    def _load(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            with open(self.persist_path) as f:
                data = json.load(f)
            for e_data in data.get("entities", []):
                e_data["type"] = EntityType(e_data["type"])
                entity = Entity(**e_data)
                self._entities[entity.id] = entity
            for r_data in data.get("relationships", []):
                r_data["type"] = RelationType(r_data["type"])
                rel = Relationship(**r_data)
                self._relationships.append(rel)
                self._adjacency[rel.source].append((rel.target, rel.type))
            logger.info(f"Digital twin loaded: {len(self._entities)} entities, {len(self._relationships)} relationships")
        except Exception as exc:
            logger.error(f"Failed to load digital twin: {exc}")

    def _persist(self) -> None:
        data = {
            "entities": [e.to_dict() for e in self._entities.values()],
            "relationships": [r.to_dict() for r in self._relationships],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(self.persist_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    async def add_entity(
        self, type: EntityType, id: str, properties: Optional[Dict] = None
    ) -> Entity:
        """Add or update an entity."""
        async with self._lock:
            if id in self._entities:
                entity = self._entities[id]
                entity.properties.update(properties or {})
                entity.updated_at = datetime.now(timezone.utc).isoformat()
            else:
                entity = Entity(id=id, type=type, properties=properties or {})
                self._entities[id] = entity
            self._persist()
            return entity

    async def add_relationship(
        self, source: str, target: str, type: RelationType, properties: Optional[Dict] = None
    ) -> Relationship:
        """Add a relationship between two entities."""
        async with self._lock:
            rel = Relationship(source=source, target=target, type=type, properties=properties or {})
            self._relationships.append(rel)
            self._adjacency[source].append((target, type))
            self._persist()
            return rel

    async def get_entity(self, id: str) -> Optional[Entity]:
        return self._entities.get(id)

    async def get_neighbors(self, id: str) -> List[Tuple[str, RelationType]]:
        """Get direct neighbors of an entity."""
        return list(self._adjacency.get(id, []))

    async def get_subgraph(self, id: str, depth: int = 2) -> Dict[str, Any]:
        """Get a subgraph rooted at the given entity, up to `depth` hops."""
        visited: Set[str] = set()
        queue: deque = deque([(id, 0)])
        entities: List[Entity] = []
        relationships: List[Relationship] = []

        while queue:
            current_id, current_depth = queue.popleft()
            if current_id in visited or current_depth > depth:
                continue
            visited.add(current_id)

            entity = self._entities.get(current_id)
            if entity:
                entities.append(entity)

            for neighbor_id, rel_type in self._adjacency.get(current_id, []):
                rel = Relationship(source=current_id, target=neighbor_id, type=rel_type)
                relationships.append(rel)
                if neighbor_id not in visited:
                    queue.append((neighbor_id, current_depth + 1))

        return {
            "root": id,
            "depth": depth,
            "entities": [e.to_dict() for e in entities],
            "relationships": [r.to_dict() for r in relationships],
        }

    async def find_path(self, source: str, target: str, max_depth: int = 5) -> Optional[List[str]]:
        """Find shortest path between two entities using BFS."""
        if source not in self._entities or target not in self._entities:
            return None
        visited: Set[str] = {source}
        queue: deque = deque([(source, [source])])

        while queue:
            current, path = queue.popleft()
            if len(path) > max_depth:
                continue
            if current == target:
                return path

            for neighbor_id, _ in self._adjacency.get(current, []):
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, path + [neighbor_id]))

        return None

    async def get_stats(self) -> Dict[str, Any]:
        """Get graph statistics."""
        by_type: Dict[str, int] = defaultdict(int)
        for e in self._entities.values():
            by_type[e.type.value] += 1

        by_rel: Dict[str, int] = defaultdict(int)
        for r in self._relationships:
            by_rel[r.type.value] += 1

        return {
            "total_entities": len(self._entities),
            "total_relationships": len(self._relationships),
            "entities_by_type": dict(by_type),
            "relationships_by_type": dict(by_rel),
        }

    async def sync_from_repository(self) -> None:
        """Synchronize the digital twin with the actual repository state.

        Scans all Python modules and their imports, creating entities
        and DEPENDS_ON relationships.
        """
        import ast
        from pathlib import Path

        project_root = Path(os.environ.get("FRIDAY_PROJECT_ROOT", str(Path.cwd())))

        # Collect all Python files
        py_files = []
        for path in project_root.rglob("*.py"):
            if any(part in path.parts for part in (".venv", "__pycache__", ".git", "node_modules", ".friday")):
                continue
            py_files.append(path)

        # Add module entities
        for filepath in py_files:
            rel = str(filepath.relative_to(project_root))
            module_id = rel.replace("/", ".").replace("\\", ".").replace(".py", "")
            loc = len(filepath.read_text(encoding="utf-8", errors="replace").split("\n"))
            await self.add_entity(EntityType.MODULE, module_id, {
                "path": rel,
                "loc": loc,
            })

        # Add dependency relationships
        for filepath in py_files:
            rel = str(filepath.relative_to(project_root))
            source_id = rel.replace("/", ".").replace("\\", ".").replace(".py", "")

            try:
                source = filepath.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source)
            except Exception:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target_id = alias.name
                        if target_id in self._entities:
                            await self.add_relationship(source_id, target_id, RelationType.DEPENDS_ON)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        target_id = node.module
                        if target_id in self._entities:
                            await self.add_relationship(source_id, target_id, RelationType.DEPENDS_ON)

        logger.info(f"Digital twin synced: {len(py_files)} modules")


# Singleton
_twin: Optional[DigitalTwin] = None


def get_digital_twin() -> DigitalTwin:
    global _twin
    if _twin is None:
        _twin = DigitalTwin()
    return _twin
