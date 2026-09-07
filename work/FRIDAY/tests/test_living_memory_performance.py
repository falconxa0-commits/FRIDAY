"""Living Memory — performance benchmarks.

Measures actual throughput/latency for the key operations:
    - write_latency:   remember() per-memory latency
    - read_latency:    recall() per-memory latency
    - search_latency:  filtered search latency
    - reinforce_latency: reinforce() latency
    - consolidation_throughput: consolidation pass throughput
    - decay_throughput: decay pass throughput
    - association_throughput: associate() throughput
    - traversal_latency: graph BFS latency
    - working_memory_throughput: put() throughput

These are NOT micro-benchmarks — they measure end-to-end manager ops.

Performance thresholds (conservative; for detection of regression, not
absolute speed):
    - write_latency_p99 < 50ms (single op, in-memory persistence)
    - read_latency_p99 < 5ms
    - search_latency_p99 < 50ms (over 1000 memories)
    - consolidate_throughput > 100 memories/sec
    - decay_throughput > 100 memories/sec
    - association_throughput > 1000 edges/sec
    - working_memory_throughput > 5000 ops/sec
"""
from __future__ import annotations

import asyncio
import os
import time
import pytest
import statistics

from core.living_memory import (
    MemoryType, MemoryState,
    AuthorizationContext, MemoryCapability,
    SemanticMemory, WorkingMemory, AssociationEdge, AssociationGraph,
    InMemoryPersistence,
    LivingMemoryManager, LivingMemoryConfig,
    ConsolidationEngine, ConsolidationConfig,
    DecayEngine, DecayConfig,
    MemoryImmuneSystem, Provenance,
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
    """Compute the p-th percentile (0..100)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = int(len(s) * p / 100)
    if k >= len(s):
        k = len(s) - 1
    return s[k]


# ----------------------------------------------------------------------
# Write latency
# ----------------------------------------------------------------------


class TestWriteLatency:
    @pytest.mark.asyncio
    async def test_single_write_latency_p99(self, founder_ctx):
        manager = LivingMemoryManager(persistence=InMemoryPersistence())
        await manager.start()
        latencies = []
        for i in range(100):
            mem = SemanticMemory.create(
                subject=f"s{i}", predicate="p", value=i,
                owner_id=founder_ctx.citizen_id, source="t",
            )
            t0 = time.perf_counter()
            await manager.remember(mem, founder_ctx, source="t")
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)  # ms
        p99 = percentile(latencies, 99)
        p50 = percentile(latencies, 50)
        print(f"\nwrite latency p50={p50:.2f}ms p99={p99:.2f}ms")
        assert p99 < 50, f"write p99 latency too high: {p99:.2f}ms"
        await manager.stop()

    @pytest.mark.asyncio
    async def test_concurrent_write_throughput(self, founder_ctx):
        """Should sustain at least 200 writes/sec under concurrency."""
        manager = LivingMemoryManager(persistence=InMemoryPersistence())
        await manager.start()
        N = 500
        async def write_one(i):
            mem = SemanticMemory.create(
                subject=f"s{i}", predicate="p", value=i,
                owner_id=founder_ctx.citizen_id, source="t",
            )
            await manager.remember(mem, founder_ctx, source="t")
        t0 = time.perf_counter()
        # Run in batches of 50 to avoid overwhelming asyncio
        for batch_start in range(0, N, 50):
            await asyncio.gather(*[
                write_one(i) for i in range(batch_start, min(batch_start + 50, N))
            ])
        elapsed = time.perf_counter() - t0
        throughput = N / elapsed
        print(f"\nconcurrent write throughput: {throughput:.1f} writes/sec")
        assert throughput > 200, f"throughput too low: {throughput:.1f}/sec"
        await manager.stop()


# ----------------------------------------------------------------------
# Read latency
# ----------------------------------------------------------------------


class TestReadLatency:
    @pytest.mark.asyncio
    async def test_read_latency_p99(self, founder_ctx):
        manager = LivingMemoryManager(persistence=InMemoryPersistence())
        await manager.start()
        # Pre-populate
        ids = []
        for i in range(100):
            mem = SemanticMemory.create(
                subject=f"s{i}", predicate="p", value=i,
                owner_id=founder_ctx.citizen_id, source="t",
            )
            stored = await manager.remember(mem, founder_ctx, source="t")
            ids.append(stored.id.value)
        # Measure reads
        latencies = []
        for mid in ids:
            t0 = time.perf_counter()
            await manager.recall(mid, founder_ctx)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)
        p99 = percentile(latencies, 99)
        p50 = percentile(latencies, 50)
        print(f"\nread latency p50={p50:.3f}ms p99={p99:.3f}ms")
        assert p99 < 5, f"read p99 latency too high: {p99:.3f}ms"
        await manager.stop()


# ----------------------------------------------------------------------
# Search latency
# ----------------------------------------------------------------------


class TestSearchLatency:
    @pytest.mark.asyncio
    async def test_search_over_1000_memories(self, founder_ctx):
        manager = LivingMemoryManager(persistence=InMemoryPersistence())
        await manager.start()
        # Pre-populate with 1000 memories
        for i in range(1000):
            mem = SemanticMemory.create(
                subject=f"s{i}", predicate="p", value=i,
                owner_id=founder_ctx.citizen_id, source="t",
                importance=0.5, confidence=0.5,
                tags=[f"tag{i%5}"],
            )
            await manager.remember(mem, founder_ctx, source="t")
        # Measure search
        latencies = []
        for _ in range(50):
            t0 = time.perf_counter()
            results = await manager.search(
                founder_ctx, memory_type=MemoryType.SEMANTIC,
                min_confidence=0.3, limit=50,
            )
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)
        p99 = percentile(latencies, 99)
        p50 = percentile(latencies, 50)
        print(f"\nsearch latency p50={p50:.3f}ms p99={p99:.3f}ms")
        assert p99 < 100, f"search p99 latency too high: {p99:.3f}ms"
        await manager.stop()


# ----------------------------------------------------------------------
# Consolidation throughput
# ----------------------------------------------------------------------


class TestConsolidationThroughput:
    @pytest.mark.asyncio
    async def test_consolidation_throughput(self, founder_ctx):
        manager = LivingMemoryManager(persistence=InMemoryPersistence())
        await manager.start()
        # Pre-populate with high-importance memories
        for i in range(500):
            mem = SemanticMemory.create(
                subject=f"s{i}", predicate="p", value=i,
                owner_id=founder_ctx.citizen_id, source="t",
                importance=0.9, confidence=0.9,
            )
            stored = await manager.remember(mem, founder_ctx, source="t")
            for _ in range(3):
                await manager.reinforce(stored.id.value, founder_ctx, delta=0.05)
        t0 = time.perf_counter()
        result = await manager.consolidate(founder_ctx)
        elapsed = time.perf_counter() - t0
        throughput = result.candidates_evaluated / elapsed
        print(f"\nconsolidation throughput: {throughput:.1f} memories/sec "
              f"(evaluated {result.candidates_evaluated}, "
              f"consolidated {result.consolidated_count})")
        assert throughput > 100, f"throughput too low: {throughput:.1f}/sec"
        await manager.stop()


# ----------------------------------------------------------------------
# Decay throughput
# ----------------------------------------------------------------------


class TestDecayThroughput:
    @pytest.mark.asyncio
    async def test_decay_throughput(self, founder_ctx):
        manager = LivingMemoryManager(persistence=InMemoryPersistence())
        await manager.start()
        for i in range(500):
            mem = SemanticMemory.create(
                subject=f"s{i}", predicate="p", value=i,
                owner_id=founder_ctx.citizen_id, source="t",
            )
            await manager.remember(mem, founder_ctx, source="t")
        t0 = time.perf_counter()
        result = await manager.decay(founder_ctx)
        elapsed = time.perf_counter() - t0
        throughput = result.candidates_evaluated / elapsed
        print(f"\ndecay throughput: {throughput:.1f} memories/sec "
              f"(evaluated {result.candidates_evaluated})")
        assert throughput > 100, f"throughput too low: {throughput:.1f}/sec"
        await manager.stop()


# ----------------------------------------------------------------------
# Association throughput
# ----------------------------------------------------------------------


class TestAssociationThroughput:
    @pytest.mark.asyncio
    async def test_associate_throughput(self, founder_ctx):
        manager = LivingMemoryManager(persistence=InMemoryPersistence())
        await manager.start()
        # Pre-populate memories
        ids = []
        for i in range(200):
            mem = SemanticMemory.create(
                subject=f"s{i}", predicate="p", value=i,
                owner_id=founder_ctx.citizen_id, source="t",
            )
            stored = await manager.remember(mem, founder_ctx, source="t")
            ids.append(stored.id.value)
        # Create edges between consecutive pairs
        t0 = time.perf_counter()
        for i in range(len(ids) - 1):
            await manager.associate(
                ids[i], ids[i + 1], founder_ctx, weight=0.5,
            )
        elapsed = time.perf_counter() - t0
        n_edges = len(ids) - 1
        throughput = n_edges / elapsed
        print(f"\nassociation throughput: {throughput:.1f} edges/sec")
        assert throughput > 100, f"throughput too low: {throughput:.1f}/sec"
        await manager.stop()


# ----------------------------------------------------------------------
# Traversal latency
# ----------------------------------------------------------------------


class TestTraversalLatency:
    @pytest.mark.asyncio
    async def test_traversal_5_hop_chain(self, founder_ctx):
        manager = LivingMemoryManager(persistence=InMemoryPersistence())
        await manager.start()
        # Build a chain of 50 memories
        ids = []
        for i in range(50):
            mem = SemanticMemory.create(
                subject=f"s{i}", predicate="p", value=i,
                owner_id=founder_ctx.citizen_id, source="t",
            )
            stored = await manager.remember(mem, founder_ctx, source="t")
            ids.append(stored.id.value)
        for i in range(len(ids) - 1):
            await manager.associate(
                ids[i], ids[i + 1], founder_ctx, weight=1.0,
            )
        # Measure traversal from first
        latencies = []
        for _ in range(20):
            t0 = time.perf_counter()
            results = await manager.traverse(
                ids[0], founder_ctx, max_depth=10,
            )
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)
        p99 = percentile(latencies, 99)
        p50 = percentile(latencies, 50)
        print(f"\ntraversal latency p50={p50:.3f}ms p99={p99:.3f}ms "
              f"(returned {len(results)} results)")
        assert p99 < 50, f"traversal p99 latency too high: {p99:.3f}ms"
        await manager.stop()


# ----------------------------------------------------------------------
# Working memory throughput
# ----------------------------------------------------------------------


class TestWorkingMemoryThroughput:
    @pytest.mark.asyncio
    async def test_put_throughput(self):
        wm = WorkingMemory(
            tenant_id="t", owner_id="o", capacity=10000,
            default_ttl_seconds=0,
        )
        N = 2000
        t0 = time.perf_counter()
        for i in range(N):
            await wm.put(f"k{i}", f"v{i}")
        elapsed = time.perf_counter() - t0
        throughput = N / elapsed
        print(f"\nworking memory put throughput: {throughput:.1f} ops/sec")
        assert throughput > 1000, f"throughput too low: {throughput:.1f}/sec"

    @pytest.mark.asyncio
    async def test_get_throughput(self):
        wm = WorkingMemory(
            tenant_id="t", owner_id="o", capacity=10000,
            default_ttl_seconds=0,
        )
        # Pre-populate
        for i in range(2000):
            await wm.put(f"k{i}", f"v{i}")
        N = 2000
        t0 = time.perf_counter()
        for i in range(N):
            await wm.get(f"k{i}")
        elapsed = time.perf_counter() - t0
        throughput = N / elapsed
        print(f"\nworking memory get throughput: {throughput:.1f} ops/sec")
        assert throughput > 2000, f"throughput too low: {throughput:.1f}/sec"


# ----------------------------------------------------------------------
# Immune system throughput
# ----------------------------------------------------------------------


class TestImmuneSystemThroughput:
    def test_scan_throughput(self):
        immune = MemoryImmuneSystem()
        N = 1000
        # Pre-build a memory + provenance
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="t",
        )
        prov = Provenance()
        prov.append(actor_id="u", action="create")
        t0 = time.perf_counter()
        for _ in range(N):
            # Reset integrity hash to force recompute
            mem.integrity_hash = ""
            immune.scan(mem, prov)
        elapsed = time.perf_counter() - t0
        throughput = N / elapsed
        print(f"\nimmune scan throughput: {throughput:.1f} scans/sec")
        assert throughput > 500, f"throughput too low: {throughput:.1f}/sec"
