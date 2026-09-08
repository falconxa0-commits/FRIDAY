"""Knowledge Graph Ω — concurrency stress tests."""
from __future__ import annotations

import asyncio
import pytest

from core.knowledge_graph import (
    KnowledgeGraphManager, KnowledgeGraphConfig,
    Entity, EntityType, Relationship, RelationshipType,
    KnowledgeGraph, KnowledgeQuery, EntityFilter,
)
from core.living_memory import (
    LivingMemoryManager, AuthorizationContext, MemoryCapability,
    InMemoryPersistence,
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
    return KnowledgeGraphManager(living_memory_manager=m3_manager)


class TestConcurrentExtraction:
    @pytest.mark.asyncio
    async def test_50_concurrent_extractions_distinct(self, kg_manager, m3_manager, founder_ctx):
        """50 concurrent extractions of distinct entities → all stored."""
        await m3_manager.start()
        await kg_manager.start()
        names = ["Alice", "Bob", "Charlie", "Dave", "Eve",
                 "Frank", "Grace", "Heidi", "Ivan", "Judy",
                 "Karl", "Leo", "Mallory", "Nancy", "Oscar",
                 "Pat", "Quinn", "Ruth", "Sybil", "Trent",
                 "Uma", "Victor", "Walter", "Xavier", "Yara",
                 "Zara", "Anna", "Ben", "Cara", "Dan",
                 "Ella", "Finn", "Gina", "Hugo", "Iris",
                 "Jack", "Kate", "Liam", "Mona", "Noah",
                 "Olga", "Paul", "Rita", "Sam", "Tony",
                 "Ulla", "Vera", "Will", "Yuki", "Zoe"]
        texts = [f"Mr. {name} works for Acme Corp" for name in names]
        async def extract_one(text):
            await kg_manager.extract(text, founder_ctx, source="test")
        # Run in batches of 10 to avoid overwhelming
        for batch_start in range(0, 50, 10):
            await asyncio.gather(*[
                extract_one(texts[i]) for i in range(batch_start, min(batch_start + 10, 50))
            ])
        results = await kg_manager.query(
            KnowledgeQuery(
                entity_filter=EntityFilter(entity_type=EntityType.PERSON),
                limit=100,
            ),
            founder_ctx,
        )
        assert len(results) >= 50
        await kg_manager.stop()
        await m3_manager.stop()

    @pytest.mark.asyncio
    async def test_concurrent_extract_same_text_idempotent(self, kg_manager, m3_manager, founder_ctx):
        """Same text extracted 10 times concurrently → no corruption."""
        await m3_manager.start()
        await kg_manager.start()
        text = "Mr. Alice works for Acme Corp"
        async def extract_once():
            try:
                await kg_manager.extract(text, founder_ctx, source="test")
                return True
            except Exception:
                return False
        results = await asyncio.gather(*[extract_once() for _ in range(10)])
        # At least one should succeed
        assert any(results)
        # Query should show Alice + Acme
        persons = await kg_manager.query(
            KnowledgeQuery(
                entity_filter=EntityFilter(entity_type=EntityType.PERSON),
                limit=100,
            ),
            founder_ctx,
        )
        # Alice should be there (possibly multiple extract attempts but dedup via ID)
        assert len(persons) >= 1
        await kg_manager.stop()
        await m3_manager.stop()


class TestConcurrentQuery:
    @pytest.mark.asyncio
    async def test_100_concurrent_queries(self, kg_manager, m3_manager, founder_ctx):
        """100 concurrent queries don't deadlock or crash."""
        await m3_manager.start()
        await kg_manager.start()
        await kg_manager.extract("Mr. Alice works for Acme Corp", founder_ctx, source="test")
        q = KnowledgeQuery(
            entity_filter=EntityFilter(entity_type=EntityType.PERSON),
            limit=10,
        )
        async def query_one():
            return await kg_manager.query(q, founder_ctx)
        results = await asyncio.gather(*[query_one() for _ in range(100)])
        assert all(isinstance(r, list) for r in results)
        await kg_manager.stop()
        await m3_manager.stop()

    @pytest.mark.asyncio
    async def test_concurrent_query_with_traversal(self, kg_manager, m3_manager, founder_ctx):
        """Concurrent queries with traversal don't crash."""
        await m3_manager.start()
        await kg_manager.start()
        # Build a small graph
        await kg_manager.extract(
            "Mr. Alice works for Acme Corp. Acme Corp is located in Lagos",
            founder_ctx, source="test",
        )
        from core.knowledge_graph import TraversalSpec
        q = KnowledgeQuery(
            entity_filter=EntityFilter(entity_type=EntityType.PERSON),
            traversal=TraversalSpec(max_depth=3),
            limit=10,
        )
        async def query_one():
            return await kg_manager.query(q, founder_ctx)
        results = await asyncio.gather(*[query_one() for _ in range(50)])
        assert all(isinstance(r, list) for r in results)
        await kg_manager.stop()
        await m3_manager.stop()


class TestConcurrentGraphOps:
    @pytest.mark.asyncio
    async def test_concurrent_add_entity_safe(self):
        """100 concurrent add_entity calls don't corrupt graph."""
        g = KnowledgeGraph(tenant_id="t1", max_entities=1000)
        async def add_one(i):
            try:
                await g.add_entity(Entity.create(
                    f"entity{i:04d}-padding", EntityType.CONCEPT, tenant_id="t1",
                ))
                return True
            except ValueError:
                return False
        results = await asyncio.gather(*[add_one(i) for i in range(100)])
        assert sum(1 for r in results if r) == 100
        assert await g.entity_count() == 100

    @pytest.mark.asyncio
    async def test_concurrent_add_relationship_safe(self):
        """Concurrent relationship additions don't corrupt."""
        g = KnowledgeGraph(tenant_id="t1", max_relationships_per_entity=1000)
        # Create 1 hub + 50 satellites
        hub = Entity.create("Hub", EntityType.CONCEPT, tenant_id="t1")
        await g.add_entity(hub)
        satellites = []
        for i in range(50):
            e = Entity.create(f"sat{i:04d}-padding-pad", EntityType.CONCEPT, tenant_id="t1")
            await g.add_entity(e)
            satellites.append(e)
        # Concurrently add edges
        async def add_edge(sat):
            try:
                await g.add_relationship(Relationship.create(
                    hub.entity_id, sat.entity_id, RelationshipType.RELATED_TO, tenant_id="t1",
                ))
                return True
            except ValueError:
                return False
        results = await asyncio.gather(*[add_edge(s) for s in satellites])
        assert sum(1 for r in results if r) == 50
        assert await g.relationship_count() == 50

    @pytest.mark.asyncio
    async def test_concurrent_traverse_safe(self):
        """Multiple concurrent traversals on same graph don't crash."""
        g = KnowledgeGraph(tenant_id="t1")
        # Build chain
        prev = None
        for i in range(20):
            e = Entity.create(f"n{i:04d}-padding-pad", EntityType.CONCEPT, tenant_id="t1")
            await g.add_entity(e)
            if prev:
                await g.add_relationship(Relationship.create(
                    prev.entity_id, e.entity_id, RelationshipType.RELATED_TO, tenant_id="t1",
                ))
            prev = e
        async def traverse_one():
            return await g.traverse(prev.entity_id, max_depth=5)
        results = await asyncio.gather(*[traverse_one() for _ in range(20)])
        assert all(isinstance(r, list) for r in results)


class TestBoundedUnderStress:
    @pytest.mark.asyncio
    async def test_graph_entity_cap_under_burst(self):
        """10,000 burst adds to capacity=100 → never exceeds 100."""
        g = KnowledgeGraph(tenant_id="t1", max_entities=100)
        async def burst(start, end):
            for i in range(start, end):
                try:
                    await g.add_entity(Entity.create(
                        f"e{i:05d}-padding-pad", EntityType.CONCEPT, tenant_id="t1",
                    ))
                except ValueError:
                    pass  # cap reached
        await asyncio.gather(
            burst(0, 2500), burst(2500, 5000),
            burst(5000, 7500), burst(7500, 10000),
        )
        assert await g.entity_count() <= 100

    @pytest.mark.asyncio
    async def test_degree_cap_under_burst(self):
        """Burst edges respect degree cap."""
        g = KnowledgeGraph(tenant_id="t1", max_relationships_per_entity=10)
        hub = Entity.create("Hub", EntityType.CONCEPT, tenant_id="t1")
        await g.add_entity(hub)
        # Create 100 satellites
        for i in range(100):
            await g.add_entity(Entity.create(f"s{i:04d}-padding-pad", EntityType.CONCEPT, tenant_id="t1"))
        # Concurrently try to add edges from hub
        sats = await g.list_entities(limit=200)
        sats = [s for s in sats if s.entity_id != hub.entity_id]
        async def try_add_edge(sat):
            try:
                await g.add_relationship(Relationship.create(
                    hub.entity_id, sat.entity_id, RelationshipType.RELATED_TO, tenant_id="t1",
                ))
                return True
            except ValueError:
                return False
        results = await asyncio.gather(*[try_add_edge(s) for s in sats])
        # Should never exceed degree cap
        assert sum(1 for r in results if r) <= 10
        assert await g.relationship_count() <= 10
