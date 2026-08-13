"""FRIDAY Age V — Milestone 3: Living Memory RED-TEAM verification tests.

Independent red-team verification of the Living Memory system.
Each test attempts to break a specific security invariant. Tests are
named `test_vulnerability_*` when they DEMONSTRATE a vulnerability (the
test passes because the vulnerability exists), and `test_invariant_*`
when they CONFIRM an invariant holds (the test passes because the
system is secure on that axis).

Run with:
    python -m pytest tests/test_living_memory_redteam.py -q --tb=short
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import pytest

from core.living_memory import (
    # Base
    MemoryID, MemoryType, MemoryState, Memory, MemoryMetadata,
    ALLOWED_TRANSITIONS, validate_transition,
    AuthorizationError, LifecycleError, ValidationError, ImmuneRejection,
    ContradictionError, ResourceLimitError, PersistenceError,
    now_utc, new_correlation_id, payload_size_bytes, MAX_PAYLOAD_BYTES,
    # Provenance
    Provenance, ProvenanceEntry,
    # Authz
    AuthorizationContext, AuthorizationGate, MemoryCapability,
    REQUIRES_FOUNDER_APPROVAL,
    # Immune
    MemoryImmuneSystem, ImmuneReport,
    # Memory types
    EpisodicMemory, SemanticMemory, WorkingMemory, WorkingMemoryItem,
    ProceduralMemory, AssociationEdge, AssociationMemory, AssociationGraph,
    # Engines
    ConsolidationEngine, ConsolidationConfig,
    DecayEngine, DecayConfig,
    ContradictionDetector, ContradictionStatus, ContradictionRecord,
    # Persistence
    InMemoryPersistence, JSONFilePersistence, PersistenceAdapter,
    RecoveryReport,
    # Observability
    MemoryMetrics, MemoryEvent, ObservabilityHub,
    # Manager
    LivingMemoryManager, LivingMemoryConfig,
)
from core.living_memory.persistence import PersistenceAdapter


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


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
def worker_ctx():
    return AuthorizationContext(
        citizen_id="worker-00000001",
        rank_level=20,
        capabilities={
            MemoryCapability.MEMORY_READ.value,
            MemoryCapability.MEMORY_WRITE.value,
            MemoryCapability.MEMORY_REINFORCE.value,
            MemoryCapability.MEMORY_ASSOCIATE.value,
            MemoryCapability.MEMORY_CONSOLIDATE.value,
            MemoryCapability.MEMORY_ARCHIVE.value,
        },
        tenant_id="default",
    )


@pytest.fixture
def stranger_ctx():
    """A different citizen in the same tenant — no special access to founder data."""
    return AuthorizationContext(
        citizen_id="stranger-0000001",
        rank_level=20,
        capabilities={
            MemoryCapability.MEMORY_READ.value,
            MemoryCapability.MEMORY_WRITE.value,
        },
        tenant_id="default",
    )


@pytest.fixture
def manager():
    return LivingMemoryManager(persistence=InMemoryPersistence())


# ======================================================================
# V1 — TOCTOU race in remember(): ID collision check bypassable
# ======================================================================
#
# The immune system's _validate_existing_id check reads `existing_ids`
# (captured under the manager lock) but the actual write to
# tenant.memories happens LATER, after releasing the lock, running the
# immune scan, and calling `await self._persist(...)`. If the persistence
# adapter yields (e.g., async I/O), two concurrent remember() calls with
# the SAME caller-supplied MemoryID can both pass the ID-collision check
# and both write — the second silently overwrites the first.
#
# With InMemoryPersistence the race is latent (no yield between read and
# write). We use a SlowPersistence adapter that explicitly yields to
# expose the race.


class SlowPersistence(InMemoryPersistence):
    """Wraps InMemoryPersistence but yields before save to widen race window."""

    async def save(self, memory: Memory, provenance: Optional[Provenance] = None) -> None:
        await asyncio.sleep(0)  # yield — exposes TOCTOU window
        await asyncio.sleep(0)  # yield again to be sure
        await super().save(memory, provenance)


class TestV1TOCTOURaceIDCollision:
    """V1 (HIGH): TOCTOU race in remember() — ID collision check bypassable."""

    @pytest.mark.asyncio
    async def test_concurrent_same_id_both_pass_immune_check(self):
        """Two concurrent remembers with the SAME caller-supplied ID both
        pass the immune _validate_existing_id check when persistence yields.

        This demonstrates that the ID-collision protection is racy: the
        existing_ids snapshot is taken under lock but the write happens
        outside the lock, after an await.
        """
        mgr = LivingMemoryManager(persistence=SlowPersistence())
        await mgr.start()

        forged_id = MemoryID.from_string("forged-id-1234567890abcdef")

        async def try_remember(value: str) -> Tuple[str, Optional[str]]:
            mem = SemanticMemory.create(
                subject="x", predicate="p", value=value,
                owner_id="founder-00000001", source="t",
                tenant_id="default",
            )
            mem.id = MemoryID.from_string(forged_id.value)  # force same ID
            try:
                await mgr.remember(mem, founder_ctx_real, source="t")
                return ("ok", value)
            except ImmuneRejection as e:
                return ("rejected", str(e.reason))
            except Exception as e:
                return ("error", f"{type(e).__name__}: {e}")

        founder_ctx_real = AuthorizationContext(
            citizen_id="founder-00000001", rank_level=100,
            capabilities={c.value for c in MemoryCapability},
            tenant_id="default", is_founder=True,
        )

        # Patch the fixture reference
        results = await asyncio.gather(
            try_remember("value-A"),
            try_remember("value-B"),
        )

        # Count how many passed the immune check
        oks = [r for r in results if r[0] == "ok"]
        # If BOTH passed, the TOCTOU race was triggered
        # (the invariant we WANT is exactly 1 ok + 1 rejected)
        #
        # NOTE: this test DOCUMENTS the vulnerability. If both pass,
        # the race is real. The assertion below checks whether the
        # system is vulnerable.
        both_passed = len(oks) == 2
        print(f"\n  TOCTOU results: {results}")
        print(f"  Both passed immune check: {both_passed}")

        # Verify the ACTUAL data corruption: the second write should
        # have silently overwritten the first.
        tenant = mgr._get_or_create_tenant("default")
        stored_entry = tenant.memories.get(forged_id.value)
        if stored_entry:
            stored_value = stored_entry[0].payload.get("value")
            print(f"  Stored value at forged ID: {stored_value!r}")
            # If both passed, one of the values was silently lost.
            values_written = {r[1] for r in oks}
            if both_passed:
                assert stored_value in values_written
                lost_value = (values_written - {stored_value}).pop()
                print(f"  LOST value (silently overwritten): {lost_value!r}")

        await mgr.stop()

        # This assertion FAILS if the race is present (both pass).
        # We mark it xfail to document the vulnerability without breaking CI.
        if both_passed:
            pytest.fail(
                "VULNERABILITY V1 (HIGH): TOCTOU race in remember() — "
                "two concurrent remembers with the same caller-supplied ID "
                "both passed the immune _validate_existing_id check. "
                "The second write silently overwrote the first, causing "
                "silent data loss with no error reported to the caller."
            )
        else:
            # If only one passed, the race was not triggered (good).
            assert len(oks) == 1, f"Expected 1 ok, got {len(oks)}"


# ======================================================================
# V2 — Silent data loss on corrupted JSON persistence file
# ======================================================================


class TestV2SilentDataLossCorruptedJSON:
    """V2 (HIGH): JSONFilePersistence silently loses all data if the file
    is corrupted (invalid JSON). _load_sync catches JSONDecodeError and
    returns an empty cache with no error in the RecoveryReport. The next
    save() overwrites the file, permanently destroying all prior data.
    """

    @pytest.mark.asyncio
    async def test_corrupted_json_file_silent_data_loss(self, tmp_path):
        """Write a valid memory, corrupt the file, reload → 0 items, 0 errors."""
        path = str(tmp_path / "mem.json")
        adapter = JSONFilePersistence(path=path)

        # Save a real memory
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="important-data",
            owner_id="u", source="t", tenant_id="default",
        )
        prov = Provenance()
        prov.append(actor_id="u", action="create", source="t")
        await adapter.save(mem, prov)
        assert await adapter.count() == 1

        # Corrupt the file on disk
        with open(path, "w", encoding="utf-8") as f:
            f.write("{THIS IS NOT VALID JSON!!!\x00\x01\x02")

        # Create a NEW adapter instance (simulates restart)
        adapter2 = JSONFilePersistence(path=path)
        items, report = await adapter2.load()

        # VULNERABILITY: loaded_count=0, quarantined_count=0, errors=[]
        # The corruption is SILENTLY swallowed. No signal that data was lost.
        print(f"\n  Loaded: {report.loaded_count}, Quarantined: {report.quarantined_count}")
        print(f"  Errors: {report.errors}")
        assert report.loaded_count == 0, "Should load 0 items from corrupted file"
        # The vulnerability: NO error is reported
        assert report.quarantined_count == 0, "Should quarantine 0 (silent loss)"
        assert len(report.errors) == 0, "Should have NO errors (silent loss)"

        # Now save a new memory — this OVERWRITES the corrupted file,
        # permanently destroying any chance of recovering the old data
        mem2 = SemanticMemory.create(
            subject="new", predicate="p", value="v2",
            owner_id="u", source="t", tenant_id="default",
        )
        prov2 = Provenance()
        prov2.append(actor_id="u", action="create", source="t")
        await adapter2.save(mem2, prov2)

        # Read the file back — it now only has mem2. The original mem is GONE.
        adapter3 = JSONFilePersistence(path=path)
        items3, report3 = await adapter3.load()
        assert report3.loaded_count == 1
        ids = [m.id.value for m, _ in items3]
        assert mem.id.value not in ids, "Original memory ID should be GONE (silent data loss)"
        assert mem2.id.value in ids

    @pytest.mark.asyncio
    async def test_corrupted_single_record_silently_skipped(self, tmp_path):
        """Even if only ONE record is corrupt, it's silently skipped
        with no entry in the RecoveryReport."""
        path = str(tmp_path / "mem.json")
        adapter = JSONFilePersistence(path=path)

        # Save 3 valid memories
        for i in range(3):
            mem = SemanticMemory.create(
                subject=f"s{i}", predicate="p", value=i,
                owner_id="u", source="t", tenant_id="default",
            )
            prov = Provenance()
            prov.append(actor_id="u", action="create", source="t")
            await adapter.save(mem, prov)

        # Manually corrupt one record in the JSON file
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # Find a key and corrupt it
        keys = list(raw.keys())
        target_key = keys[1]
        raw[target_key] = {"memory": "CORRUPTED_NOT_A_DICT", "provenance": {}}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f)

        # Reload
        adapter2 = JSONFilePersistence(path=path)
        items, report = await adapter2.load()

        # The corrupt record is silently skipped — no error in report
        print(f"\n  Loaded: {report.loaded_count} (expected 2), Errors: {report.errors}")
        assert report.loaded_count == 2  # 3 - 1 corrupt = 2 valid
        # VULNERABILITY: the corrupt record is NOT in the error list
        assert len(report.errors) == 0, "Corrupt record should produce an error but doesn't"
        assert report.quarantined_count == 0


# ======================================================================
# V3 — Association graph degree cap bypass via bidirectional edges
# ======================================================================


class TestV3AssociationDegreeCapBypass:
    """V3 (MEDIUM — FIXED): AssociationGraph.add_edge now enforces degree
    cap on BOTH source and target nodes when bidirectional=True. Previously
    only the source was checked, allowing target-side degree blowup.
    """

    @pytest.mark.asyncio
    async def test_bidirectional_edges_enforce_target_degree_cap(self):
        """Adding bidirectional edges INTO a single target must stop once
        the target hits its max_degree cap (V3 regression test).
        """
        graph = AssociationGraph(
            tenant_id="default", max_degree=5, max_traversal_depth=3,
            max_edges=1000,
        )

        target = "target-mem-id-12345678"
        added = 0
        for i in range(10):
            source = f"src-{i:04d}-padding-pad"
            edge = AssociationEdge(
                source_id=source, target_id=target,
                relationship="related", weight=0.5,
                bidirectional=True, created_by="attacker",
            )
            try:
                await graph.add_edge(edge)
                added += 1
            except ResourceLimitError:
                break

        # FIX VERIFIED: target's out-degree stays at or below max_degree
        target_out_degree = await graph.degree(target, direction="out")
        assert added <= 5, (
            f"Target degree cap should be enforced: got {added} edges (cap=5)"
        )
        assert target_out_degree <= 5, (
            f"Target out-degree {target_out_degree} should be <= max_degree=5"
        )

    @pytest.mark.asyncio
    async def test_bidirectional_edges_exhaust_total_edge_cap_slowly(self):
        """The total edge cap (_edges_by_id) counts bidirectional edges
        as ONE entry, but they occupy TWO slots in _out. This means
        the actual memory usage can be ~2x the edge_count() report.

        Note: This is a known minor accounting quirk, not a security issue.
        The max_edges cap still bounds total entries in _edges_by_id.
        The max_total_memories_per_tenant cap on the manager bounds total
        memory growth.
        """
        graph = AssociationGraph(
            tenant_id="default", max_degree=1000, max_traversal_depth=3,
            max_edges=10,
        )
        target = "target-mem-id-12345678"
        for i in range(10):
            source = f"src-{i:04d}-padding-pad"
            edge = AssociationEdge(
                source_id=source, target_id=target,
                relationship="related", weight=0.5,
                bidirectional=True, created_by="attacker",
            )
            await graph.add_edge(edge)

        edge_count = await graph.edge_count()
        total_out_entries = sum(len(d) for d in graph._out.values())
        assert edge_count == 10  # cap enforced
        assert total_out_entries == 20  # each bidirectional edge = 2 _out slots


# ======================================================================
# V4 — Unbounded tenant creation (resource exhaustion DoS)
# ======================================================================


class TestV4UnboundedTenantCreation:
    """V4 (MEDIUM): _get_or_create_tenant has NO cap on the number of
    tenants. An attacker with memory.write capability can create unlimited
    tenants, each with its own AssociationGraph + memories dict +
    contradictions dict. This is a memory-exhaustion DoS vector.
    """

    @pytest.mark.asyncio
    async def test_unbounded_tenant_creation(self, founder_ctx):
        """Create 1000 tenants — no ResourceLimitError raised."""
        mgr = LivingMemoryManager(persistence=InMemoryPersistence())
        await mgr.start()

        for i in range(1000):
            mem = SemanticMemory.create(
                subject="x", predicate="p", value=i,
                owner_id="founder-00000001", source="t",
                tenant_id=f"tenant-{i:04d}",
            )
            # founder has all capabilities + cross-tenant access
            ctx = AuthorizationContext(
                citizen_id="founder-00000001", rank_level=100,
                capabilities={c.value for c in MemoryCapability},
                tenant_id=f"tenant-{i:04d}", is_founder=True,
            )
            await mgr.remember(mem, ctx, source="t")

        # 1000 tenants created — NO cap was enforced
        assert len(mgr._tenants) == 1000, (
            f"Expected 1000 tenants (no cap), got {len(mgr._tenants)}"
        )
        print(f"\n  Created {len(mgr._tenants)} tenants — NO ResourceLimitError raised")
        print(f"  Each tenant has its own AssociationGraph + memories dict")
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_unbounded_working_memory_creation(self, founder_ctx):
        """Similarly, _get_or_create_working has no cap on the number of
        WorkingMemory instances."""
        mgr = LivingMemoryManager(persistence=InMemoryPersistence())
        await mgr.start()

        for i in range(500):
            ctx = AuthorizationContext(
                citizen_id=f"citizen-{i:04d}-padding",
                rank_level=20,
                capabilities={MemoryCapability.MEMORY_WRITE.value},
                tenant_id="default",
            )
            await mgr.working_put(f"key-{i}", f"value-{i}", ctx)

        assert len(mgr._working_memories) == 500
        print(f"\n  Created {len(mgr._working_memories)} WorkingMemory instances — NO cap")
        await mgr.stop()


# ======================================================================
# V5 — traverse() missing authorization check
# ======================================================================


class TestV5TraverseMissingAuthorization:
    """V5 (MEDIUM — FIXED): traverse() now calls authorize_read on the source
    memory (or authorize_capability(memory.read) if the source doesn't exist
    in the caller's tenant). Banned users and zero-capability contexts are
    now denied traversal — preventing association-graph metadata leakage.
    """

    @pytest.mark.asyncio
    async def test_traverse_banned_user_blocked(self, founder_ctx):
        """Banned users cannot traverse the association graph (V5 regression)."""
        mgr = LivingMemoryManager(persistence=InMemoryPersistence())
        await mgr.start()

        mem1 = SemanticMemory.create(
            subject="secret1", predicate="key", value="CLASSIFIED_1",
            owner_id=founder_ctx.citizen_id, source="t", tenant_id="default",
        )
        mem2 = SemanticMemory.create(
            subject="secret2", predicate="key", value="CLASSIFIED_2",
            owner_id=founder_ctx.citizen_id, source="t", tenant_id="default",
        )
        await mgr.remember(mem1, founder_ctx, source="t")
        await mgr.remember(mem2, founder_ctx, source="t")

        await mgr.associate(
            mem1.id.value, mem2.id.value, founder_ctx,
            relationship="linked_to", weight=0.9, bidirectional=True,
        )

        banned_ctx = AuthorizationContext(
            citizen_id="banned-00000001",
            rank_level=100,
            capabilities={c.value for c in MemoryCapability},
            tenant_id="default",
            is_banned=True,
        )
        # FIX VERIFIED: banned user is now blocked from traversing
        with pytest.raises(AuthorizationError):
            await mgr.traverse(mem1.id.value, banned_ctx, max_depth=3)
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_traverse_no_capability_user_blocked(self, founder_ctx):
        """A user with ZERO capabilities is blocked from traversing (V5 regression)."""
        mgr = LivingMemoryManager(persistence=InMemoryPersistence())
        await mgr.start()

        mem1 = SemanticMemory.create(
            subject="s1", predicate="p", value="v1",
            owner_id=founder_ctx.citizen_id, source="t", tenant_id="default",
        )
        mem2 = SemanticMemory.create(
            subject="s2", predicate="p", value="v2",
            owner_id=founder_ctx.citizen_id, source="t", tenant_id="default",
        )
        await mgr.remember(mem1, founder_ctx, source="t")
        await mgr.remember(mem2, founder_ctx, source="t")
        await mgr.associate(
            mem1.id.value, mem2.id.value, founder_ctx,
            relationship="linked", weight=0.8, bidirectional=True,
        )

        no_caps_ctx = AuthorizationContext(
            citizen_id="nobody-00000001",
            rank_level=0,
            capabilities=set(),
            tenant_id="default",
        )
        # FIX VERIFIED: zero-capability user is blocked from traverse
        with pytest.raises(AuthorizationError):
            await mgr.traverse(mem1.id.value, no_caps_ctx, max_depth=3)
        await mgr.stop()


# ======================================================================
# V6 — inspect() leaks FORGOTTEN memory content
# ======================================================================


class TestV6InspectLeaksForgottenMemory:
    """V6 (MEDIUM — FIXED): inspect() now checks MemoryState.is_accessible()
    before returning memory content. FORGOTTEN, ARCHIVED, and QUARANTINED
    memories return None from inspect() — protecting erasure semantics.
    """

    @pytest.mark.asyncio
    async def test_inspect_forgotten_memory_returns_none(self, founder_ctx):
        """Founder creates a memory, forgets it, then inspects it.
        inspect() must return None (V6 regression test)."""
        mgr = LivingMemoryManager(persistence=InMemoryPersistence())
        await mgr.start()

        secret_value = "TOP_SECRET_VALUE_THAT_SHOULD_BE_FORGOTTEN"
        mem = SemanticMemory.create(
            subject="user_email", predicate="value", value=secret_value,
            owner_id=founder_ctx.citizen_id, source="t", tenant_id="default",
        )
        await mgr.remember(mem, founder_ctx, source="t")

        ok = await mgr.forget(mem.id.value, founder_ctx, notes="gdpr erasure")
        assert ok, "Forget should succeed for founder"

        # recall returns None (FORGOTTEN is not accessible)
        recalled = await mgr.recall(mem.id.value, founder_ctx)
        assert recalled is None, "FORGOTTEN memory should not be recallable"

        # FIX VERIFIED: inspect() also returns None for FORGOTTEN memory
        inspection = await mgr.inspect(mem.id.value, founder_ctx)
        assert inspection is None, (
            "inspect() must NOT leak FORGOTTEN memory content (V6 fix)"
        )
        await mgr.stop()


# ======================================================================
# V7 — Idempotency key tracked on failure prevents legitimate retry
# ======================================================================


class TestV7IdempotencyKeyTrackedOnFailure:
    """V7 (LOW-MEDIUM — FIXED): idempotency key is now tracked AFTER the
    contradiction check passes, not before. A ContradictionError no
    longer pollutes the idempotency cache — legitimate retries succeed.
    """

    @pytest.mark.asyncio
    async def test_idempotency_key_not_tracked_on_contradiction_failure(
        self, founder_ctx
    ):
        """A failed-contradiction remember() must NOT track the idempotency
        key. Retrying with the same key (and a corrected payload) must
        succeed (V7 regression test).
        """
        mgr = LivingMemoryManager(persistence=InMemoryPersistence())
        await mgr.start()

        # 1. Create a fact with idempotency key "k1"
        mem1 = SemanticMemory.create(
            subject="temp", predicate="value", value=42,
            owner_id=founder_ctx.citizen_id, source="sensor_a",
            tenant_id="default",
        )
        await mgr.remember(mem1, founder_ctx, source="sensor_a",
                           idempotency_key="k1")

        # 2. Attempt to write a CONFLICTING fact with idempotency key "k2"
        mem2 = SemanticMemory.create(
            subject="temp", predicate="value", value=99,
            owner_id=founder_ctx.citizen_id, source="sensor_b",
            tenant_id="default",
        )
        with pytest.raises(ContradictionError):
            await mgr.remember(mem2, founder_ctx, source="sensor_b",
                               idempotency_key="k2")

        # 3. Retry with key "k2" but a corrected (non-conflicting) subject.
        # FIX VERIFIED: the retry must succeed (key wasn't tracked).
        mem3 = SemanticMemory.create(
            subject="temp2", predicate="value", value=99,
            owner_id=founder_ctx.citizen_id, source="sensor_b",
            tenant_id="default",
        )
        stored = await mgr.remember(mem3, founder_ctx, source="sensor_b",
                                    idempotency_key="k2")
        assert stored is not None
        assert stored.state == MemoryState.ACTIVE
        await mgr.stop()


# ======================================================================
# V8 — Working memory operations don't check ANY capability
# ======================================================================


class TestV8WorkingMemoryNoCapabilityCheck:
    """V8 (LOW — FIXED): working_put / working_get / working_remove /
    working_snapshot now call authorize_capability. A zero-capability
    context is rejected (fail-closed, matching the architecture spec).
    """

    @pytest.mark.asyncio
    async def test_no_capability_ctx_blocked_from_working_memory(self):
        """A zero-capability context cannot put, get, remove, or snapshot
        working memory (V8 regression test).
        """
        mgr = LivingMemoryManager(persistence=InMemoryPersistence())
        await mgr.start()

        no_caps_ctx = AuthorizationContext(
            citizen_id="ghost-00000001",
            rank_level=0,
            capabilities=set(),
            tenant_id="default",
            is_banned=False,
            is_founder=False,
        )
        # FIX VERIFIED: all four working memory ops reject zero-cap ctx
        with pytest.raises(AuthorizationError):
            await mgr.working_put("key", "value", no_caps_ctx)
        with pytest.raises(AuthorizationError):
            await mgr.working_get("key", no_caps_ctx)
        with pytest.raises(AuthorizationError):
            await mgr.working_remove("key", no_caps_ctx)
        with pytest.raises(AuthorizationError):
            await mgr.working_snapshot(no_caps_ctx)
        await mgr.stop()


class TestV9ContradictionScanRaceCondition:
    """V9 (MEDIUM): _build_contradiction_candidates iterates
    tenant.memories WITHOUT holding self._lock. If another coroutine
    adds a memory concurrently (via remember()), Python raises
    RuntimeError: dictionary changed size during iteration.

    With InMemoryPersistence the race is latent (no yield between
    read and write). We use SlowPersistence to expose it.
    """

    @pytest.mark.asyncio
    async def test_contradiction_scan_dict_mutation_race(self):
        """One coroutine writes a semantic memory (triggers contradiction
        candidate scan). Another concurrently writes a DIFFERENT semantic
        memory (mutates tenant.memories during iteration).

        With a yielding persistence adapter, this raises RuntimeError."""
        mgr = LivingMemoryManager(persistence=SlowPersistence())
        await mgr.start()

        founder_ctx_real = AuthorizationContext(
            citizen_id="founder-00000001", rank_level=100,
            capabilities={c.value for c in MemoryCapability},
            tenant_id="default", is_founder=True,
        )

        # Pre-populate with some semantic memories (different predicates to avoid contradictions)
        for i in range(20):
            mem = SemanticMemory.create(
                subject=f"sub{i:04d}", predicate=f"pred{i:04d}", value=i,
                owner_id="founder-00000001", source="t", tenant_id="default",
            )
            await mgr.remember(mem, founder_ctx_real, source="t")

        # Now fire many concurrent writes — each triggers _build_contradiction_candidates
        # which iterates tenant.memories without the lock.
        errors = []

        async def write_one(i: int):
            try:
                mem = SemanticMemory.create(
                    subject=f"new{i:04d}", predicate=f"pp{i:04d}", value=i,
                    owner_id="founder-00000001", source="t", tenant_id="default",
                )
                await mgr.remember(mem, founder_ctx_real, source="t")
            except Exception as e:
                errors.append((i, type(e).__name__, str(e)))

        # 50 concurrent writes
        await asyncio.gather(*[write_one(i) for i in range(50)])

        runtime_errors = [e for e in errors if e[1] == "RuntimeError"]
        print(f"\n  Total errors: {len(errors)}")
        print(f"  RuntimeErrors (dict mutation): {len(runtime_errors)}")
        if runtime_errors:
            print(f"  Sample: {runtime_errors[0]}")

        # If we got RuntimeErrors, the race is confirmed.
        # If not, the race is latent (InMemoryPersistence doesn't yield enough).
        # We don't hard-fail here because the race is timing-dependent.
        # But we DOCUMENT it.
        if runtime_errors:
            pytest.fail(
                f"VULNERABILITY V9 (MEDIUM): RuntimeError during concurrent "
                f"contradiction scan — _build_contradiction_candidates iterates "
                f"tenant.memories without the lock. {len(runtime_errors)} errors."
            )
        else:
            # Race not triggered — note as latent issue
            print("  Race not triggered (latent — requires async persistence to surface)")


# ======================================================================
# V10 — Provenance chain O(n²) verify_chain performance
# ======================================================================


class TestV10ProvenanceChainQuadraticVerify:
    """V10 (LOW): Provenance.verify_chain() calls self.chain.index(entry)
    inside a for loop, which is O(n) per iteration, making the total
    O(n²). For a chain near the max (1000 entries), this is ~1M operations
    per scan. This is a DoS vector: an attacker who can build a long
    provenance chain (via repeated reinforces) can cause CPU exhaustion
    on every immune scan.
    """

    def test_verify_chain_is_quadratic(self):
        """Build a chain of 1000 entries and time verify_chain()."""
        import time
        prov = Provenance()
        for i in range(1000):
            prov.append(
                actor_id=f"actor-{i:04d}", action="reinforce",
                source="t", process="test",
            )

        start = time.perf_counter()
        ok = prov.verify_chain()
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"\n  verify_chain() on 1000-entry chain: {elapsed_ms:.1f} ms")
        assert ok, "Chain should verify"
        # Document the quadratic behavior — even 1000 entries should be fast
        # enough on modern hardware, but it grows quadratically.
        # The real issue is at scale (10000+ entries).
        # We just document it here, no hard fail.


# ======================================================================
# V11 — MemoryID accepts arbitrary strings (path traversal in Redis keys)
# ======================================================================


class TestV11MemoryIDFormatTooPermissive:
    """V11 (LOW — FIXED): MemoryID.from_string now rejects the pipe '|'
    character, which would collide with AssociationGraph's internal
    edge_key format: f'{source_id}|{target_id}|{relationship}'.
    Path separators are still accepted (the persistence layer does not
    use IDs as filenames, so this is not a traversal risk).
    """

    def test_memory_id_rejects_pipe_delimiter(self):
        """An ID containing '|' must be rejected (V11 regression test)."""
        with pytest.raises(ValidationError):
            MemoryID.from_string("evil|||target_id|related")

    def test_memory_id_accepts_normal_ids(self):
        """Normal IDs (UUIDs, alphanumeric) are still accepted."""
        ok = MemoryID.from_string("12345678-1234-1234-1234-123456789012")
        assert ok.value == "12345678-1234-1234-1234-123456789012"

    def test_association_edge_key_collision_via_pipe_in_id(self):
        """An attacker can craft a memory ID containing '|' to collide
        with the AssociationGraph's internal edge_key format, potentially
        causing edge confusion in _edges_by_id."""
        graph = AssociationGraph(
            tenant_id="default", max_degree=10, max_traversal_depth=3,
        )
        # Legitimate edge: source="aaaaaa1", target="bbbbbb2", rel="related"
        # edge_key = "aaaaaa1|bbbbbb2|related"
        edge1 = AssociationEdge(
            source_id="aaaaaa1", target_id="bbbbbb2",
            relationship="related", weight=0.5,
        )
        # Crafted edge: source="aaaaaa1", target="bbbbbb2|related|cccccc3"
        # edge_key = "aaaaaa1|bbbbbb2|related|cccccc3|related"
        # This is a DIFFERENT edge but the key format is ambiguous.
        edge2 = AssociationEdge(
            source_id="aaaaaa1", target_id="bbbbbb2|related|cccccc3",
            relationship="related", weight=0.5,
        )
        await_add1 = graph.add_edge(edge1)
        await_add2 = graph.add_edge(edge2)
        asyncio.get_event_loop().run_until_complete(asyncio.gather(await_add1, await_add2))

        # The edge_count is 2, but the _edges_by_id keys are:
        # "aaaaaa1|bbbbbb2|related"
        # "aaaaaa1|bbbbbb2|related|cccccc3|related"
        # These are different strings, so no collision. But the point
        # is that the ID format allows characters that make the key
        # format ambiguous and hard to parse.
        count = asyncio.get_event_loop().run_until_complete(graph.edge_count())
        assert count == 2


# ======================================================================
# V12 — Consolidation skips QUARANTINED check; can consolidate a memory
# that was just quarantined (state validation gap)
# ======================================================================


class TestV12ConsolidationStateValidationGap:
    """V12 (LOW): ConsolidationEngine.score_candidate checks state
    eligibility, but the manager's consolidate() method acquires the
    lock AFTER scoring. Between scoring and applying the transition,
    the memory's state could change (e.g., to QUARANTINED by a concurrent
    immune rejection on recovery). The validate_transition call would
    catch this, but the error is silently appended to result.errors
    rather than raising.
    """

    @pytest.mark.asyncio
    async def test_consolidation_of_quarantined_memory_silent_skip(
        self, founder_ctx
    ):
        """Manually set a memory to QUARANTINED, then run consolidate().
        The scoring engine correctly marks it ineligible, but if the
        state were changed AFTER scoring, the transition would fail
        silently."""
        mgr = LivingMemoryManager(persistence=InMemoryPersistence())
        await mgr.start()

        mem = SemanticMemory.create(
            subject="x", predicate="y", value=42,
            owner_id=founder_ctx.citizen_id, source="t", tenant_id="default",
            confidence=0.9, importance=0.9,
        )
        await mgr.remember(mem, founder_ctx, source="t")

        # Reinforce to boost score above consolidation threshold
        for _ in range(5):
            await mgr.reinforce(mem.id.value, founder_ctx, delta=0.1)

        # Manually quarantine the memory (simulating immune rejection on recovery)
        tenant = mgr._get_or_create_tenant("default")
        entry = tenant.memories.get(mem.id.value)
        assert entry is not None
        quarantined_mem, _ = entry
        quarantined_mem.state = MemoryState.QUARANTINED

        # Run consolidation — the QUARANTINED memory should be skipped
        result = await mgr.consolidate(founder_ctx)

        # The QUARANTINED memory should NOT be consolidated
        assert mem.id.value not in [
            c.memory_id for c in result.candidates if c.eligible
        ], "QUARANTINED memory should not be eligible for consolidation"
        # Good — this is handled correctly. Document as invariant.
        print(f"\n  QUARANTINED memory correctly skipped by consolidation")
        await mgr.stop()


# ======================================================================
# Summary test — runs all vulnerabilities and reports
# ======================================================================


class TestSummary:
    """Meta-test that documents all vulnerabilities found."""

    def test_vulnerability_summary(self):
        """Print a summary of all vulnerabilities identified by the red team."""
        summary = """
        ================================================================
        FRIDAY Age V Milestone 3 — Living Memory RED-TEAM SUMMARY
        ================================================================

        VULNERABILITIES FOUND:

        V1 (HIGH)   — TOCTOU race in remember() ID collision check
                      File: core/living_memory/manager.py:271-354
                      The existing_ids snapshot is taken under lock but
                      the write happens outside the lock after an await.
                      Exploitable with async persistence adapters.

        V2 (HIGH)   — Silent data loss on corrupted JSON persistence
                      File: core/living_memory/persistence.py:152-176
                      _load_sync catches JSONDecodeError and returns
                      empty cache with NO error in RecoveryReport.
                      Next save() overwrites the file, permanent loss.

        V3 (MEDIUM) — Association graph degree cap bypass (bidirectional)
                      File: core/living_memory/association.py:152-166
                      add_edge checks source degree but NOT target degree
                      when bidirectional=True. Target's degree is unbounded.

        V4 (MEDIUM) — Unbounded tenant creation (DoS)
                      File: core/living_memory/manager.py:1112-1118
                      _get_or_create_tenant has no cap on tenant count.
                      Each tenant allocates AssociationGraph + dicts.

        V5 (MEDIUM) — traverse() missing authorization check
                      File: core/living_memory/manager.py:992-1003
                      traverse() does not call authorize_read. Any
                      same-tenant caller can traverse associations of
                      any memory, leaking IDs + relationship metadata.

        V6 (MEDIUM) — inspect() leaks FORGOTTEN memory content
                      File: core/living_memory/manager.py:1037-1053
                      inspect() does not check is_accessible(). A
                      FORGOTTEN memory's full payload is still readable.
                      Breaks "forget = erasure" semantics.

        V7 (LOW-MED)— Idempotency key tracked on failure
                      File: core/living_memory/manager.py:305-311
                      Idempotency key is added to _seen_idempotency
                      BEFORE contradiction check. If ContradictionError
                      is raised, the key is already tracked. Legitimate
                      retry is rejected as replay.

        V8 (LOW)    — Working memory has no capability check
                      File: core/living_memory/manager.py:1009-1031
                      working_put/get/remove/snapshot don't call any
                      authorize_* method. Zero-capability contexts can
                      still read/write working memory. Fail-open.

        V9 (MEDIUM) — _build_contradiction_candidates dict mutation race
                      File: core/living_memory/manager.py:1138-1152
                      Iterates tenant.memories without holding self._lock.
                      Concurrent remember() can cause RuntimeError.
                      Latent with InMemoryPersistence, active with async I/O.

        V10 (LOW)   — Provenance.verify_chain() is O(n²)
                      File: core/living_memory/provenance.py:163-184
                      Uses self.chain.index(entry) inside a loop.
                      DoS vector at scale (1000+ entry chains).

        V11 (LOW)   — MemoryID.from_string too permissive
                      File: core/living_memory/base.py:96-107
                      Accepts any non-whitespace string >=8 chars,
                      including '|', '/', '\\'. Could cause key format
                      ambiguity in AssociationGraph._edges_by_id.

        ================================================================
        CERTIFICATION ASSESSMENT: DOES NOT MEET BAR
        ================================================================
        2 HIGH + 5 MEDIUM + 4 LOW vulnerabilities found.
        The system must fix V1, V2, V3, V4, V5, V6 before certification.
        ================================================================
        """
        print(summary)
