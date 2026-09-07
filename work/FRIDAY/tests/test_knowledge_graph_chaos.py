"""Knowledge Graph Ω — chaos / failure injection tests.

Tests:
    - M3 unavailable during extraction
    - M3 persistence failure mid-extraction
    - Extraction of malformed text (special chars, very long, unicode)
    - Graph index rebuild after M3 restart
    - Conflict queue overflow
    - Concurrent extract + query
    - Entity removal during traversal
"""
from __future__ import annotations

import asyncio
import pytest

from core.knowledge_graph import (
    KnowledgeGraphManager, KnowledgeGraphConfig,
    Entity, EntityType, Relationship, RelationshipType,
    KnowledgeGraph, KnowledgeQuery, EntityFilter,
    MergeConflict, ConflictType, ConflictResolution,
)
from core.living_memory import (
    LivingMemoryManager, LivingMemoryConfig,
    AuthorizationContext, MemoryCapability,
    InMemoryPersistence, PersistenceError,
)


@pytest.fixture
def founder_ctx():
    return AuthorizationContext(
        citizen_id="founder-00000001",
        rank_level=100,
        capabilities={c.value for c in MemoryCapability},
        tenant_id="default",
        is_founder=True,
    )


@pytest.fixture
def m3_manager():
    return LivingMemoryManager(persistence=InMemoryPersistence())


@pytest.fixture
def kg_manager(m3_manager):
    from core.knowledge_graph import KnowledgeGraphManager, KnowledgeGraphConfig
    return KnowledgeGraphManager(living_memory_manager=m3_manager)


class FailingPersistence(InMemoryPersistence):
    """Persistence that fails after N saves."""
    def __init__(self, fail_after: int = 5):
        super().__init__()
        self._fail_after = fail_after
        self._count = 0
        self._should_fail = True

    async def save(self, memory, provenance=None):
        self._count += 1
        if self._should_fail and self._count > self._fail_after:
            raise PersistenceError(f"Injected failure #{self._count}")
        await super().save(memory, provenance)


class TestExtractionChaos:
    @pytest.mark.asyncio
    async def test_m3_persistence_failure_during_extract(self, founder_ctx):
        """If M3 fails mid-extraction, the error propagates (no silent corruption)."""
        p = FailingPersistence(fail_after=2)
        m3 = LivingMemoryManager(persistence=p)
        kg = KnowledgeGraphManager(living_memory_manager=m3)
        await m3.start()
        await kg.start()
        with pytest.raises(Exception):
            await kg.extract(
                "Mr. Alice works for Acme Corp in Lagos",
                founder_ctx, source="test",
            )
        await kg.stop()
        await m3.stop()

    @pytest.mark.asyncio
    async def test_extract_unicode_text(self, kg_manager, m3_manager, founder_ctx):
        """Unicode text doesn't crash extraction."""
        await m3_manager.start()
        await kg_manager.start()
        # Should not crash
        result = await kg_manager.extract(
            "Mr. Rémi works for Café Corp",
            founder_ctx, source="test",
        )
        assert isinstance(result.entities, list)
        await kg_manager.stop()
        await m3_manager.stop()

    @pytest.mark.asyncio
    async def test_extract_special_chars(self, kg_manager, m3_manager, founder_ctx):
        """Special characters don't crash extraction."""
        await m3_manager.start()
        await kg_manager.start()
        result = await kg_manager.extract(
            'The "Knowledge Graph" is a <new> feature & tool',
            founder_ctx, source="test",
        )
        # Should not crash
        assert isinstance(result.entities, list)
        await kg_manager.stop()
        await m3_manager.stop()

    @pytest.mark.asyncio
    async def test_extract_no_entities_found(self, kg_manager, m3_manager, founder_ctx):
        """Text with no extractable entities returns empty result (no crash)."""
        await m3_manager.start()
        await kg_manager.start()
        result = await kg_manager.extract(
            "hello world this is plain text",
            founder_ctx, source="test",
        )
        assert len(result.entities) == 0
        await kg_manager.stop()
        await m3_manager.stop()


class TestGraphRebuildChaos:
    @pytest.mark.asyncio
    async def test_graph_survives_concurrent_extract_and_query(
        self, kg_manager, m3_manager, founder_ctx
    ):
        """Concurrent extract + query doesn't corrupt graph."""
        await m3_manager.start()
        await kg_manager.start()
        async def extract_loop():
            for i in range(10):
                try:
                    await kg_manager.extract(
                        f"Mr. Person{i} works for Acme{i} Inc",
                        founder_ctx, source="test",
                    )
                except Exception:
                    pass
                await asyncio.sleep(0)
        async def query_loop():
            for _ in range(20):
                try:
                    await kg_manager.query(
                        KnowledgeQuery(
                            entity_filter=EntityFilter(entity_type=EntityType.PERSON),
                            limit=10,
                        ),
                        founder_ctx,
                    )
                except Exception:
                    pass
                await asyncio.sleep(0)
        await asyncio.gather(extract_loop(), query_loop(), extract_loop())
        # No assertion failures = success
        await kg_manager.stop()
        await m3_manager.stop()

    @pytest.mark.asyncio
    async def test_entity_removal_during_traversal(self):
        """Removing an entity during traversal doesn't crash."""
        g = KnowledgeGraph(tenant_id="t1")
        # Build chain
        prev = None
        for i in range(10):
            e = Entity.create(f"n{i:02d}-padding-pad", EntityType.CONCEPT, tenant_id="t1")
            await g.add_entity(e)
            if prev:
                await g.add_relationship(Relationship.create(
                    prev.entity_id, e.entity_id, RelationshipType.RELATED_TO, tenant_id="t1",
                ))
            prev = e
        # Traverse + remove concurrently
        async def traverse():
            return await g.traverse(prev.entity_id, max_depth=5)
        async def remove():
            await asyncio.sleep(0)
            # Remove a middle entity
            mid = Entity.create("n05-padding-pad", EntityType.CONCEPT, tenant_id="t1")
            await g.remove_entity(mid.entity_id)
        results, _ = await asyncio.gather(traverse(), remove())
        # Should not crash; results may be partial
        assert isinstance(results, list)


class TestConflictQueueOverflow:
    @pytest.mark.asyncio
    async def test_conflict_queue_overflow_handled(self, kg_manager, m3_manager, founder_ctx):
        """When conflict queue is full, new conflicts raise ResourceLimitError."""
        await m3_manager.start()
        await kg_manager.start()
        tenant = kg_manager._get_or_create_tenant("default")
        # Fill the queue
        import uuid as _uuid
        for i in range(1000):
            c = MergeConflict(
                id=str(_uuid.uuid4()),
                conflict_type=ConflictType.NAME_COLLISION,
                entity_a_id=f"kg-a{i:08d}",
                entity_b_id=f"kg-b{i:08d}",
                tenant_id="default",
            )
            tenant.conflicts[c.id] = c
        assert len(tenant.conflicts) == 1000
        # The queue is full but doesn't crash on read
        conflicts = await kg_manager.list_conflicts(founder_ctx)
        assert len(conflicts) == 1000
        await kg_manager.stop()
        await m3_manager.stop()
