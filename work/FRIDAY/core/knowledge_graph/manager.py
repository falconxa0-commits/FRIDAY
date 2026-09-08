"""Knowledge Graph — KnowledgeGraphManager facade.

Composes with M3 LivingMemoryManager (does NOT duplicate it):
    - Entity anchors stored as M3 SemanticMemory records
    - Relationships stored as M3 AssociationMemory records (via manager.associate)
    - Evidence stored as M3 EpisodicMemory records
    - Provenance, authz, immune validation inherited from M3

The KnowledgeGraphManager:
    1. Extracts entities + relationships from text (via EntityExtractor + RelationshipDetector)
    2. Stores them in M3 LivingMemory (anchored as SemanticMemory records)
    3. Maintains a per-tenant in-memory KnowledgeGraph index for fast queries
    4. Detects merge conflicts + resolves them via M3 governance
    5. Provides a SPARQL-inspired query API

All operations require an AuthorizationContext (from M3).
All mutations emit events via M3's ObservabilityHub (when configured).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# M3 imports
# M3 imports (authz gate added to fix V1/V2/V3 red-team findings)
from core.living_memory import (
    AuthorizationContext, AuthorizationError, MemoryCapability,
    SemanticMemory, EpisodicMemory, LivingMemoryManager,
    MemoryType, MemoryState, Provenance,
    ContradictionError, ImmuneRejection, ResourceLimitError,
    now_utc, new_correlation_id,
    AuthorizationGate,
)

# M4 imports
from .entity import Entity, EntityType, canonicalize_name, derive_entity_id
from .extractor import EntityExtractor, ExtractionResult, ExtractedMention
from .graph import KnowledgeGraph, GraphStats
from .merge import (
    MergeConflict, MergeResolver, ConflictType, ConflictResolution,
    HIGH_STAKES_TYPES,
)
from .query import KnowledgeQuery, QueryEngine, QueryResult
from .relationship import (
    Relationship, RelationshipType, BIDIRECTIONAL_TYPES,
    derive_relationship_id,
)
from .relationship_detector import RelationshipDetector, DetectionResult

# M3 PersistenceError import
from core.living_memory.base import PersistenceError

logger = logging.getLogger("friday.knowledge_graph.manager")


@dataclass
class KnowledgeGraphConfig:
    """Configuration for the KnowledgeGraphManager."""
    max_entities_per_tenant: int = 50_000
    max_relationships_per_entity: int = 256
    max_query_results: int = 500
    max_traversal_depth: int = 6
    max_conflicts_per_tenant: int = 1000
    max_extraction_text_size: int = 10_000
    co_occurrence_window: int = 5
    max_relationships_per_text: int = 50
    max_tenants: int = 1_000  # V6 fix: cap total tenants (DoS protection)


@dataclass
class TenantKGState:
    """Per-tenant KnowledgeGraph state."""
    tenant_id: str
    graph: KnowledgeGraph
    conflicts: Dict[str, MergeConflict] = field(default_factory=dict)
    extraction_count: int = 0
    last_extraction_at: str = ""


class KnowledgeGraphManager:
    """Unified facade for the Knowledge Graph.

    Usage::

        kg = KnowledgeGraphManager(living_memory_manager=m3_manager)
        await kg.start()

        # Extract entities from text
        result = await kg.extract("Alice works for Acme Corp in Lagos", ctx)

        # Query
        results = await kg.query(KnowledgeQuery.from_dict({
            "entity_filter": {"type": "person"}
        }), ctx)

        await kg.stop()
    """

    def __init__(
        self,
        living_memory_manager: LivingMemoryManager,
        config: Optional[KnowledgeGraphConfig] = None,
        extractor: Optional[EntityExtractor] = None,
        detector: Optional[RelationshipDetector] = None,
    ):
        self._m3 = living_memory_manager
        self._config = config or KnowledgeGraphConfig()
        self._extractor = extractor or EntityExtractor()
        self._detector = detector or RelationshipDetector(
            co_occurrence_window=self._config.co_occurrence_window,
            max_relationships_per_text=self._config.max_relationships_per_text,
        )
        self._resolver = MergeResolver(
            max_conflicts_per_tenant=self._config.max_conflicts_per_tenant,
        )
        # V1/V2/V3 fix: dedicated authz gate for read APIs
        self._authz = AuthorizationGate()
        self._tenants: Dict[str, TenantKGState] = {}
        self._lock = asyncio.Lock()
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        async with self._lock:
            self._started = True
            logger.info("KnowledgeGraphManager started")

    async def stop(self) -> None:
        if not self._started:
            return
        async with self._lock:
            self._started = False
            logger.info("KnowledgeGraphManager stopped")

    # ------------------------------------------------------------------
    # Tenant state
    # ------------------------------------------------------------------

    def _get_or_create_tenant(self, tenant_id: str) -> TenantKGState:
        if tenant_id not in self._tenants:
            # V6 fix: enforce tenant cap to prevent DoS
            if len(self._tenants) >= self._config.max_tenants:
                raise ResourceLimitError(
                    f"Tenant cap reached: {self._config.max_tenants} (tenant_id={tenant_id})"
                )
            self._tenants[tenant_id] = TenantKGState(
                tenant_id=tenant_id,
                graph=KnowledgeGraph(
                    tenant_id=tenant_id,
                    max_entities=self._config.max_entities_per_tenant,
                    max_relationships_per_entity=self._config.max_relationships_per_entity,
                    max_query_results=self._config.max_query_results,
                    max_traversal_depth=self._config.max_traversal_depth,
                ),
            )
        return self._tenants[tenant_id]

    def _authorize_read(self, ctx: AuthorizationContext, tenant_id: str = "") -> None:
        """V1/V2/V3 fix: enforce read authorization on all read APIs.

        Rejects:
            - Missing citizen_id (unauthenticated)
            - Banned citizens
            - Cross-tenant reads (unless Founder)
            - Missing memory.read capability (unless Founder)
        """
        tid = tenant_id or ctx.tenant_id
        # Use a synthetic owner_id for the authz check (the gate's read path
        # checks tenant + capability + ban; owner_id matters only for
        # cross-tenant non-Founder reads, which we want to block).
        # Founder can read any tenant; others must match ctx.tenant_id.
        if ctx.is_founder:
            # Founder: just check authenticated + not banned + has read cap
            self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_READ)
            return
        if tid != ctx.tenant_id:
            raise AuthorizationError(
                f"Cross-tenant read denied: ctx.tenant={ctx.tenant_id} "
                f"target_tenant={tid} (constitution Article 7)"
            )
        self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_READ)

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    async def extract(
        self,
        text: str,
        ctx: AuthorizationContext,
        source_memory_id: str = "",
        source: str = "",
    ) -> ExtractionResult:
        """Extract entities + relationships from text.

        Requires: memory.write capability (creates M3 SemanticMemory anchors)
        """
        if not self._started:
            raise RuntimeError("KnowledgeGraphManager not started")
        if not text or not isinstance(text, str):
            raise ValueError("text must be non-empty string")
        if len(text) > self._config.max_extraction_text_size:
            raise ValueError(
                f"Text too large: {len(text)} > {self._config.max_extraction_text_size}"
            )

        # V7 fix: probe authz BEFORE doing any work, even if no entities will
        # be extracted. This prevents banned/zero-capability callers from
        # triggering tenant state creation + counter increments.
        # We do this by checking the authz gate directly.
        self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_WRITE)

        # Authorize: extraction creates memories, so require write
        # Use the M3 manager's authz gate
        # We need to call authorize_write on m3's authz, but it's private.
        # Instead, we'll attempt to remember the anchor memory and let M3 authz handle it.
        # If the caller isn't authorized, M3 will raise AuthorizationError.

        # Step 1: Extract entities
        result = self._extractor.extract_entities(
            text=text,
            tenant_id=ctx.tenant_id,
            source_memory_id=source_memory_id,
        )

        # Step 2: Store each entity as an M3 SemanticMemory anchor
        # NOTE: We attempt the FIRST memory write here to surface authz errors
        # before doing any in-memory graph mutation. If the caller is unauthorized,
        # M3 will raise AuthorizationError and we propagate it immediately.
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        if result.entities:
            # Probe authorization with the first entity
            first = result.entities[0]
            probe_mem = SemanticMemory.create(
                subject=first.entity_id,
                predicate="entity_type",
                value=first.entity_type.value,
                source=source or "extraction",
                source_authority=0.7,
                owner_id=ctx.citizen_id,
                tenant_id=ctx.tenant_id,
                tags=["kg_entity", first.entity_type.value],
                confidence=first.confidence,
                importance=0.6,
                correlation_id=result.extracted_at,
            )
            # This will raise AuthorizationError if ctx lacks memory.write
            stored = await self._m3.remember(probe_mem, ctx, source=source or "extraction")
            first.memory_id = stored.id.value

            # Store canonical_name for the first entity too (the loop path
            # does this for entities[1:], but the probe path skipped it)
            name_mem_first = SemanticMemory.create(
                subject=first.entity_id,
                predicate="canonical_name",
                value=first.canonical_name,
                source=source or "extraction",
                source_authority=0.9,
                owner_id=ctx.citizen_id,
                tenant_id=ctx.tenant_id,
                tags=["kg_canonical_name"],
                confidence=0.95,
                correlation_id=result.extracted_at,
            )
            try:
                await self._m3.remember(name_mem_first, ctx, source=source or "extraction")
            except ContradictionError:
                pass  # name already stored

            await tenant.graph.add_entity(first)

            # Process remaining entities
            for entity in result.entities[1:]:
                try:
                    # Check for merge conflicts first
                    existing_entities = await tenant.graph.list_entities(limit=1000)
                    conflicts = self._resolver.detect_name_collisions(existing_entities, entity)
                    for c in conflicts:
                        if len(tenant.conflicts) >= self._config.max_conflicts_per_tenant:
                            raise ResourceLimitError("Conflict queue full")
                        tenant.conflicts[c.id] = c
                        logger.warning(f"Merge conflict detected: {c.description}")

                    # Store anchor memory in M3
                    anchor_mem = SemanticMemory.create(
                        subject=entity.entity_id,
                        predicate="entity_type",
                        value=entity.entity_type.value,
                        source=source or "extraction",
                        source_authority=0.7,
                        owner_id=ctx.citizen_id,
                        tenant_id=ctx.tenant_id,
                        tags=["kg_entity", entity.entity_type.value],
                        confidence=entity.confidence,
                        importance=0.6,
                        correlation_id=result.extracted_at,
                    )
                    stored = await self._m3.remember(anchor_mem, ctx, source=source or "extraction")
                    entity.memory_id = stored.id.value

                    # Store each attribute as a separate SemanticMemory
                    for attr_name, attr_value in entity.attributes.items():
                        attr_mem = SemanticMemory.create(
                            subject=entity.entity_id,
                            predicate=attr_name,
                            value=attr_value,
                            source=source or "extraction",
                            source_authority=0.7,
                            owner_id=ctx.citizen_id,
                            tenant_id=ctx.tenant_id,
                            tags=["kg_attribute", attr_name],
                            confidence=entity.confidence,
                            correlation_id=result.extracted_at,
                        )
                        try:
                            await self._m3.remember(attr_mem, ctx, source=source or "extraction")
                        except ContradictionError:
                            attr_conflicts = self._resolver.detect_attribute_contradictions(
                                entity, {attr_name: attr_value},
                            )
                            for c in attr_conflicts:
                                tenant.conflicts[c.id] = c

                    # Store canonical name as attribute
                    name_mem = SemanticMemory.create(
                        subject=entity.entity_id,
                        predicate="canonical_name",
                        value=entity.canonical_name,
                        source=source or "extraction",
                        source_authority=0.9,
                        owner_id=ctx.citizen_id,
                        tenant_id=ctx.tenant_id,
                        tags=["kg_canonical_name"],
                        confidence=0.95,
                        correlation_id=result.extracted_at,
                    )
                    try:
                        await self._m3.remember(name_mem, ctx, source=source or "extraction")
                    except ContradictionError:
                        pass  # name already stored

                    # Add to in-memory graph index
                    await tenant.graph.add_entity(entity)

                except (AuthorizationError, ImmuneRejection, ResourceLimitError):
                    raise
                except PersistenceError:
                    # Propagate persistence failures (don't swallow)
                    raise
                except Exception as e:
                    logger.error(f"Failed to store entity {entity.entity_id}: {e}")

        # Step 3: Detect + store relationships
        if result.entities:
            detection = self._detector.detect(
                text=text,
                entities=result.entities,
                mentions=result.mentions,
                tenant_id=ctx.tenant_id,
            )
            for rel in detection.relationships:
                try:
                    # Both endpoints must exist in graph
                    src = await tenant.graph.get_entity(rel.source_entity_id)
                    tgt = await tenant.graph.get_entity(rel.target_entity_id)
                    if not src or not tgt:
                        continue
                    # Store relationship as M3 AssociationMemory (via manager.associate)
                    if src.memory_id and tgt.memory_id:
                        try:
                            await self._m3.associate(
                                src.memory_id, tgt.memory_id, ctx,
                                relationship=rel.relationship_type.value,
                                weight=rel.weight,
                                bidirectional=rel.bidirectional,
                            )
                        except (AuthorizationError, ImmuneRejection, ResourceLimitError):
                            # Skip but keep in in-memory graph
                            pass
                        except Exception as e:
                            logger.debug(f"M3 associate failed: {e}")
                    # Add to in-memory graph
                    await tenant.graph.add_relationship(rel)
                except ValueError as e:
                    logger.debug(f"Skipping relationship: {e}")

        tenant.extraction_count += 1
        tenant.last_extraction_at = now_utc()
        return result

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def query(
        self,
        query: KnowledgeQuery,
        ctx: AuthorizationContext,
    ) -> List[QueryResult]:
        """Execute a knowledge graph query. Requires memory.read capability.

        V1 fix: explicit authz check — banned users and zero-capability contexts
        are rejected.
        """
        if not self._started:
            raise RuntimeError("KnowledgeGraphManager not started")
        self._authorize_read(ctx)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        engine = QueryEngine(tenant.graph)
        return await engine.execute(query)

    async def get_entity(
        self, entity_id: str, ctx: AuthorizationContext
    ) -> Optional[Entity]:
        """Get an entity by ID. V1 fix: requires memory.read."""
        self._authorize_read(ctx)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        return await tenant.graph.get_entity(entity_id)

    async def find_entity(
        self, name: str, ctx: AuthorizationContext
    ) -> Optional[Entity]:
        """Find an entity by name. V1 fix: requires memory.read."""
        self._authorize_read(ctx)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        return await tenant.graph.find_entity_by_name(name)

    async def neighbors(
        self, entity_id: str, ctx: AuthorizationContext,
        rel_type: Optional[RelationshipType] = None,
        min_weight: float = 0.0,
        direction: str = "both",
    ) -> List[Tuple[Relationship, str]]:
        """Get neighbors of an entity. V1 fix: requires memory.read."""
        self._authorize_read(ctx)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        return await tenant.graph.neighbors(
            entity_id, rel_type=rel_type, min_weight=min_weight, direction=direction,
        )

    async def traverse(
        self, entity_id: str, ctx: AuthorizationContext,
        max_depth: Optional[int] = None,
        rel_type: Optional[RelationshipType] = None,
        min_weight: float = 0.0,
        max_results: int = 100,
    ) -> List[Tuple[str, int, float]]:
        """Traverse the graph. V1 fix: requires memory.read."""
        self._authorize_read(ctx)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        return await tenant.graph.traverse(
            entity_id, max_depth=max_depth, rel_type=rel_type,
            min_weight=min_weight, max_results=max_results,
        )

    # ------------------------------------------------------------------
    # Conflict management
    # ------------------------------------------------------------------

    async def list_conflicts(
        self, ctx: AuthorizationContext,
        status: Optional[ConflictResolution] = None,
    ) -> List[MergeConflict]:
        """V1 fix: requires memory.read."""
        self._authorize_read(ctx)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        if status:
            return [c for c in tenant.conflicts.values() if c.status == status]
        return list(tenant.conflicts.values())

    async def resolve_conflict(
        self,
        conflict_id: str,
        ctx: AuthorizationContext,
        resolution: ConflictResolution,
        approval_id: Optional[str] = None,
        notes: str = "",
    ) -> bool:
        """Resolve a merge conflict. Requires Founder approval for high-stakes."""
        if resolution == ConflictResolution.PENDING:
            raise ValueError("Cannot resolve to PENDING")

        tenant = self._get_or_create_tenant(ctx.tenant_id)
        conflict = tenant.conflicts.get(conflict_id)
        if not conflict:
            return False
        if conflict.status != ConflictResolution.PENDING:
            return False

        # Check if Founder approval required
        if conflict.requires_founder_approval and not ctx.is_founder:
            # If approval gate is configured on M3, check it
            if self._m3._approval_gate is not None:
                if not approval_id:
                    raise AuthorizationError(
                        "High-stakes conflict requires approval_id"
                    )
                req = self._m3._approval_gate.get_request(approval_id)
                if not req:
                    raise AuthorizationError("Approval not found")
                from core.living_memory.governance_compat import is_approval_approved
                if not is_approval_approved(req):
                    raise AuthorizationError("Approval not approved")
            else:
                raise AuthorizationError(
                    "High-stakes conflict requires Founder rank or approval gate"
                )

        conflict.status = resolution
        conflict.resolved_at = now_utc()
        conflict.resolved_by = ctx.citizen_id
        conflict.resolution_notes = notes

        # Apply resolution side-effects
        if resolution == ConflictResolution.KEEP_A:
            # Remove entity_b from graph
            await tenant.graph.remove_entity(conflict.entity_b_id)
        elif resolution == ConflictResolution.KEEP_B:
            await tenant.graph.remove_entity(conflict.entity_a_id)
        elif resolution == ConflictResolution.MERGE:
            # Merge B into A: copy aliases + attributes
            entity_a = await tenant.graph.get_entity(conflict.entity_a_id)
            entity_b = await tenant.graph.get_entity(conflict.entity_b_id)
            if entity_a and entity_b:
                for alias in entity_b.aliases:
                    try:
                        entity_a.add_alias(alias)
                    except ValueError:
                        pass
                for k, v in entity_b.attributes.items():
                    if k not in entity_a.attributes:
                        entity_a.set_attribute(k, v)
                await tenant.graph.update_entity(entity_a)
                await tenant.graph.remove_entity(entity_b.entity_id)
        elif resolution == ConflictResolution.SPLIT:
            # Keep both — caller should rename one to disambiguate
            pass  # no-op

        return True

    # ------------------------------------------------------------------
    # Stats / Health
    # ------------------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        """Health check is UNAUTHORIZED (intentional — used by ops/monitoring).
        Returns only aggregate counts, no entity data."""
        return {
            "started": self._started,
            "tenants": {
                tid: {
                    "entity_count": await t.graph.entity_count(),
                    "relationship_count": await t.graph.relationship_count(),
                    "conflict_count": len(t.conflicts),
                    "extraction_count": t.extraction_count,
                    "last_extraction_at": t.last_extraction_at,
                }
                for tid, t in self._tenants.items()
            },
        }

    async def get_stats(
        self, ctx: AuthorizationContext, tenant_id: Optional[str] = None,
    ) -> GraphStats:
        """V3 fix: requires AuthorizationContext + memory.read.
        Cross-tenant stats require Founder rank."""
        tid = tenant_id or ctx.tenant_id
        self._authorize_read(ctx, tenant_id=tid)
        tenant = self._get_or_create_tenant(tid)
        return await tenant.graph.stats()

    async def export_graph(
        self, ctx: AuthorizationContext, tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Export the knowledge graph as a dict.

        V2 fix: tenant_id parameter removed from cross-tenant access path.
        Caller can only export their OWN tenant unless they are Founder
        (and Founder must still have memory.read capability).
        """
        tid = tenant_id or ctx.tenant_id
        self._authorize_read(ctx, tenant_id=tid)
        tenant = self._get_or_create_tenant(tid)
        entities = await tenant.graph.list_entities(limit=10000)
        graph_stats = await tenant.graph.stats()
        return {
            "tenant_id": tid,
            "entities": [e.to_dict() for e in entities],
            "stats": graph_stats.to_dict(),
            "conflicts": [c.to_dict() for c in tenant.conflicts.values()],
            "exported_at": now_utc(),
            "exported_by": ctx.citizen_id,
        }


__all__ = [
    "KnowledgeGraphManager", "KnowledgeGraphConfig", "TenantKGState",
]
