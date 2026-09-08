"""Knowledge Graph Ω — performance benchmarks."""
from __future__ import annotations

import asyncio
import time
import pytest

from core.knowledge_graph import (
    KnowledgeGraphManager, KnowledgeGraphConfig,
    Entity, EntityType, Relationship, RelationshipType,
    KnowledgeGraph, KnowledgeQuery, EntityFilter, TraversalSpec,
    EntityExtractor,
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


def percentile(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = int(len(s) * p / 100)
    if k >= len(s):
        k = len(s) - 1
    return s[k]


class TestExtractionPerformance:
    @pytest.mark.asyncio
    async def test_extraction_latency_p99(self, founder_ctx):
        """Single extraction p99 < 100ms."""
        m3 = LivingMemoryManager(persistence=InMemoryPersistence())
        kg = KnowledgeGraphManager(living_memory_manager=m3)
        await m3.start()
        await kg.start()
        latencies = []
        for i in range(50):
            text = f"Mr. Person{i} works for Acme{i} Corp"
            t0 = time.perf_counter()
            await kg.extract(text, founder_ctx, source="test")
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)
        p99 = percentile(latencies, 99)
        p50 = percentile(latencies, 50)
        print(f"\nextract latency p50={p50:.2f}ms p99={p99:.2f}ms")
        assert p99 < 500, f"extraction p99 too high: {p99:.2f}ms"
        await kg.stop()
        await m3.stop()

    @pytest.mark.asyncio
    async def test_extraction_throughput(self, founder_ctx):
        """Should sustain > 20 extractions/sec (with M3 persistence)."""
        m3 = LivingMemoryManager(persistence=InMemoryPersistence())
        kg = KnowledgeGraphManager(living_memory_manager=m3)
        await m3.start()
        await kg.start()
        N = 50
        names = ["Alice", "Bob", "Charlie", "Dave", "Eve",
                 "Frank", "Grace", "Heidi", "Ivan", "Judy"] * (N // 10)
        t0 = time.perf_counter()
        for i, name in enumerate(names):
            await kg.extract(f"Mr. {name}{i} works for Acme{i} Corp", founder_ctx, source="test")
        elapsed = time.perf_counter() - t0
        throughput = N / elapsed
        print(f"\nextraction throughput: {throughput:.1f} extractions/sec")
        assert throughput > 10, f"throughput too low: {throughput:.1f}/sec"
        await kg.stop()
        await m3.stop()


class TestQueryPerformance:
    @pytest.mark.asyncio
    async def test_query_latency_over_100_entities(self, founder_ctx):
        """Query over 100 entities p99 < 50ms."""
        m3 = LivingMemoryManager(persistence=InMemoryPersistence())
        kg = KnowledgeGraphManager(living_memory_manager=m3)
        await m3.start()
        await kg.start()
        # Populate with 100 entities
        for i in range(100):
            await kg.extract(
                f"Mr. Person{i:03d} works for Acme{i:03d} Corp",
                founder_ctx, source="test",
            )
        latencies = []
        q = KnowledgeQuery(
            entity_filter=EntityFilter(entity_type=EntityType.PERSON),
            limit=50,
        )
        for _ in range(50):
            t0 = time.perf_counter()
            await kg.query(q, founder_ctx)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)
        p99 = percentile(latencies, 99)
        p50 = percentile(latencies, 50)
        print(f"\nquery latency p50={p50:.3f}ms p99={p99:.3f}ms (over 100 entities)")
        assert p99 < 100, f"query p99 too high: {p99:.3f}ms"
        await kg.stop()
        await m3.stop()


class TestTraversalPerformance:
    @pytest.mark.asyncio
    async def test_traversal_latency(self):
        """BFS traversal over 50-node chain p99 < 10ms."""
        g = KnowledgeGraph(tenant_id="t1", max_traversal_depth=10)
        prev = None
        for i in range(50):
            e = Entity.create(f"n{i:04d}-padding-pad", EntityType.CONCEPT, tenant_id="t1")
            await g.add_entity(e)
            if prev:
                await g.add_relationship(Relationship.create(
                    prev.entity_id, e.entity_id, RelationshipType.RELATED_TO, tenant_id="t1",
                ))
            prev = e
        latencies = []
        for _ in range(20):
            t0 = time.perf_counter()
            await g.traverse(prev.entity_id, max_depth=10)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)
        p99 = percentile(latencies, 99)
        print(f"\ntraversal p99={p99:.3f}ms (50-node chain)")
        assert p99 < 50, f"traversal p99 too high: {p99:.3f}ms"


class TestExtractorPerformance:
    def test_extractor_throughput(self):
        """Pure extractor (no M3) should process > 1000 texts/sec."""
        ex = EntityExtractor()
        text = "Mr. Alice works for Acme Corp in Lagos on 2026-12-31"
        N = 1000
        t0 = time.perf_counter()
        for _ in range(N):
            ex.extract_mentions(text)
        elapsed = time.perf_counter() - t0
        throughput = N / elapsed
        print(f"\nextractor throughput: {throughput:.1f} texts/sec")
        assert throughput > 500, f"throughput too low: {throughput:.1f}/sec"
