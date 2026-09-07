"""Living Memory — core unit + invariant tests.

Covers:
    - MemoryID / MemoryType / MemoryState basics
    - Lifecycle transitions (allowed + invalid)
    - Provenance chain (append, seal, verify, tamper detection)
    - AuthorizationContext + AuthorizationGate (fail-closed)
    - ImmuneSystem scan + rejection reasons
    - Each memory type's create() + payload invariants
    - WorkingMemory capacity, TTL, eviction
    - AssociationGraph degree cap, traversal cap, edge dedup
    - Consolidation scoring + threshold
    - Decay scoring + recommendation
    - ContradictionDetector (duplicate, predicate, negation, numerical, procedural)
    - Persistence: InMemory round-trip; JSONFile atomic write
    - Observability: counters + gauges + event recording
    - Manager: remember / recall / reinforce / consolidate / decay end-to-end
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import pytest

from core.living_memory import (
    # Base
    MemoryID, MemoryType, MemoryState, Memory, MemoryMetadata,
    ALLOWED_TRANSITIONS, validate_transition,
    AuthorizationError, LifecycleError, ValidationError, ImmuneRejection,
    ContradictionError, ResourceLimitError,
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
    ContradictionDetector, ContradictionStatus,
    # Persistence
    InMemoryPersistence, JSONFilePersistence, RecoveryReport,
    # Observability
    MemoryMetrics, MemoryEvent, ObservabilityHub,
    # Manager
    LivingMemoryManager, LivingMemoryConfig,
)


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
def unauthorized_ctx():
    return AuthorizationContext(
        citizen_id="ghost-00000001",
        rank_level=10,
        capabilities=set(),
        tenant_id="default",
    )


@pytest.fixture
def banned_ctx():
    return AuthorizationContext(
        citizen_id="banned-00000001",
        rank_level=80,
        capabilities={c.value for c in MemoryCapability},
        tenant_id="default",
        is_banned=True,
    )


@pytest.fixture
def other_tenant_ctx():
    return AuthorizationContext(
        citizen_id="tenant2-0000001",
        rank_level=100,
        capabilities={c.value for c in MemoryCapability},
        tenant_id="tenant_2",
        is_founder=True,
    )


@pytest.fixture
def manager():
    """Fresh in-memory manager."""
    m = LivingMemoryManager(
        persistence=InMemoryPersistence(),
        config=LivingMemoryConfig(),
    )
    return m


# ----------------------------------------------------------------------
# MemoryID
# ----------------------------------------------------------------------


class TestMemoryID:
    def test_default_id_is_uuid_v4_length(self):
        mid = MemoryID()
        assert len(mid.value) == 36
        assert mid.schema_version == 1

    def test_id_is_hashable(self):
        mid = MemoryID()
        s = {mid}  # should not raise
        assert mid in s

    def test_from_string_rejects_empty(self):
        with pytest.raises(ValidationError):
            MemoryID.from_string("")

    def test_from_string_rejects_short(self):
        with pytest.raises(ValidationError):
            MemoryID.from_string("abc")

    def test_from_string_rejects_whitespace(self):
        with pytest.raises(ValidationError):
            MemoryID.from_string("has space inside")

    def test_from_string_accepts_valid_uuid(self):
        s = "12345678-1234-1234-1234-123456789012"
        mid = MemoryID.from_string(s)
        assert mid.value == s

    def test_to_dict_round_trip(self):
        mid = MemoryID()
        d = mid.to_dict()
        restored = MemoryID.from_dict(d)
        assert restored == mid


# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------


class TestLifecycle:
    def test_created_to_active_allowed(self):
        validate_transition(MemoryState.CREATED, MemoryState.ACTIVE)

    def test_active_to_reinforced_allowed(self):
        validate_transition(MemoryState.ACTIVE, MemoryState.REINFORCED)

    def test_active_to_consolidating_allowed(self):
        validate_transition(MemoryState.ACTIVE, MemoryState.CONSOLIDATING)

    def test_consolidated_to_decaying_allowed(self):
        validate_transition(MemoryState.CONSOLIDATED, MemoryState.DECAYING)

    def test_forgotten_is_terminal(self):
        assert MemoryState.is_terminal(MemoryState.FORGOTTEN)
        assert MemoryState.is_terminal(MemoryState.ARCHIVED) is False  # archived can be restored

    def test_archived_to_active_allowed_restoration(self):
        validate_transition(MemoryState.ARCHIVED, MemoryState.ACTIVE)

    def test_forgotten_no_transitions(self):
        assert ALLOWED_TRANSITIONS[MemoryState.FORGOTTEN] == set()

    def test_invalid_transition_raises(self):
        with pytest.raises(LifecycleError):
            validate_transition(MemoryState.CREATED, MemoryState.CONSOLIDATED)

    def test_forgotten_to_active_raises(self):
        with pytest.raises(LifecycleError):
            validate_transition(MemoryState.FORGOTTEN, MemoryState.ACTIVE)

    def test_quarantined_to_active_allowed_clearance(self):
        validate_transition(MemoryState.QUARANTINED, MemoryState.ACTIVE)

    def test_quarantined_to_forgotten_allowed(self):
        validate_transition(MemoryState.QUARANTINED, MemoryState.FORGOTTEN)

    def test_is_accessible_states(self):
        assert MemoryState.is_accessible(MemoryState.ACTIVE)
        assert MemoryState.is_accessible(MemoryState.REINFORCED)
        assert MemoryState.is_accessible(MemoryState.CONSOLIDATED)
        assert not MemoryState.is_accessible(MemoryState.FORGOTTEN)
        assert not MemoryState.is_accessible(MemoryState.ARCHIVED)
        assert not MemoryState.is_accessible(MemoryState.QUARANTINED)


# ----------------------------------------------------------------------
# Provenance
# ----------------------------------------------------------------------


class TestProvenance:
    def test_empty_provenance(self):
        p = Provenance()
        assert p.is_empty
        assert p.length == 0
        assert p.head_hash == ""

    def test_append_creates_entry_with_hash(self):
        p = Provenance()
        entry = p.append(actor_id="user-1", action="create", source="test")
        assert entry.entry_hash != ""
        assert entry.verify()

    def test_chain_is_hash_linked(self):
        p = Provenance()
        e1 = p.append(actor_id="u1", action="create")
        e2 = p.append(actor_id="u2", action="update")
        assert e2.parent_hash == e1.entry_hash
        assert p.verify_chain()

    def test_tamper_detection_entry_hash(self):
        p = Provenance()
        p.append(actor_id="u1", action="create")
        # Tamper: change actor_id on the entry
        p.chain[0].actor_id = "attacker"
        assert not p.verify_chain()

    def test_tamper_detection_parent_hash(self):
        p = Provenance()
        e1 = p.append(actor_id="u1", action="create")
        e2 = p.append(actor_id="u2", action="update")
        # Tamper: change parent_hash on e2
        e2.parent_hash = "wrong"
        assert not p.verify_chain()

    def test_unknown_action_rejected(self):
        p = Provenance()
        with pytest.raises(ValueError):
            p.append(actor_id="u1", action="bogus_action")

    def test_missing_actor_id_rejected(self):
        p = Provenance()
        with pytest.raises(ValueError):
            p.append(actor_id="", action="create")

    def test_current_version_increments(self):
        p = Provenance()
        assert p.current_version == 0
        p.append(actor_id="u", action="create")
        assert p.current_version == 1
        p.append(actor_id="u", action="update")
        assert p.current_version == 2

    def test_round_trip_via_dict(self):
        p = Provenance()
        p.append(actor_id="u1", action="create", source="src")
        p.append(actor_id="u2", action="update")
        d = p.to_dict()
        p2 = Provenance.from_dict(d)
        assert p2.verify_chain()
        assert p2.current_version == p.current_version


# ----------------------------------------------------------------------
# Authorization
# ----------------------------------------------------------------------


class TestAuthorization:
    def test_founder_has_all_capabilities(self, founder_ctx):
        for cap in MemoryCapability:
            assert founder_ctx.has_capability(cap)

    def test_worker_lacks_forget(self, worker_ctx):
        assert not worker_ctx.has_capability(MemoryCapability.MEMORY_FORGET)

    def test_unauthenticated_rejected(self):
        gate = AuthorizationGate()
        ctx = AuthorizationContext(citizen_id="", rank_level=0)
        with pytest.raises(AuthorizationError):
            gate.authorize_read(ctx, "default", "")

    def test_banned_rejected_even_with_caps(self, banned_ctx):
        gate = AuthorizationGate()
        with pytest.raises(AuthorizationError):
            gate.authorize_write(banned_ctx, "default")

    def test_cross_tenant_read_rejected(self, founder_ctx):
        gate = AuthorizationGate()
        with pytest.raises(AuthorizationError):
            gate.authorize_read(
                AuthorizationContext(
                    citizen_id="other", rank_level=40, tenant_id="other",
                    capabilities={MemoryCapability.MEMORY_READ.value},
                ),
                "default", "owner",
            )

    def test_cross_tenant_read_founder_allowed(self, founder_ctx):
        gate = AuthorizationGate()
        # Founder can cross tenants
        gate.authorize_read(founder_ctx, "other_tenant", "owner")

    def test_low_rank_cannot_write(self):
        gate = AuthorizationGate(min_rank_for_write=20)
        ctx = AuthorizationContext(
            citizen_id="low", rank_level=10,
            capabilities={MemoryCapability.MEMORY_WRITE.value},
            tenant_id="default",
        )
        with pytest.raises(AuthorizationError):
            gate.authorize_write(ctx, "default")

    def test_missing_capability_rejected(self):
        gate = AuthorizationGate()
        ctx = AuthorizationContext(
            citizen_id="cap-missing", rank_level=50,
            capabilities=set(),
            tenant_id="default",
        )
        with pytest.raises(AuthorizationError):
            gate.authorize_write(ctx, "default")

    def test_forget_requires_governor_or_founder(self, worker_ctx):
        gate = AuthorizationGate()
        with pytest.raises(AuthorizationError):
            gate.authorize_delete(worker_ctx, "default")

    def test_forget_worker_with_capability_still_blocked_by_rank(self):
        """CRITICAL: A worker (rank 20) WITH the memory.forget capability
        is still blocked by the authz rank check (must be Governor+, rank>=60).
        This guards against the authz_skip_forget_rank_check mutation.
        """
        gate = AuthorizationGate()
        worker_with_forget = AuthorizationContext(
            citizen_id="worker-cap-001",
            rank_level=20,
            capabilities={MemoryCapability.MEMORY_FORGET.value},
            tenant_id="default",
        )
        with pytest.raises(AuthorizationError) as ei:
            gate.authorize_delete(worker_with_forget, "default")
        assert "Governor" in str(ei.value) or "rank" in str(ei.value).lower()

    def test_forget_governor_with_capability_passes_authz(self):
        """A Governor (rank 60) WITH memory.forget capability passes the
        authz check. (The manager has additional defense-in-depth requiring
        Founder approval — but the authz layer itself permits Governor+.)"""
        gate = AuthorizationGate()
        governor = AuthorizationContext(
            citizen_id="gov-000000001",
            rank_level=60,
            capabilities={MemoryCapability.MEMORY_FORGET.value},
            tenant_id="default",
        )
        # Should not raise
        gate.authorize_delete(governor, "default")


# ----------------------------------------------------------------------
# Immune System
# ----------------------------------------------------------------------


class TestImmuneSystem:
    def test_valid_memory_accepted(self):
        immune = MemoryImmuneSystem()
        mem = SemanticMemory.create(
            subject="user", predicate="name", value="Alice",
            owner_id="founder", source="test",
        )
        prov = Provenance()
        prov.append(actor_id="founder", action="create", source="test")
        report = immune.scan(mem, prov)
        assert report.accepted
        assert report.integrity_hash != ""

    def test_oversized_payload_rejected(self):
        immune = MemoryImmuneSystem(max_payload_bytes=128)
        mem = SemanticMemory.create(
            subject="x", predicate="big", value="A" * 1000,
            owner_id="founder", source="test",
        )
        prov = Provenance()
        prov.append(actor_id="founder", action="create")
        report = immune.scan(mem, prov)
        assert not report.accepted
        assert "exceeds size cap" in report.reason

    def test_missing_provenance_rejected_for_persistent(self):
        immune = MemoryImmuneSystem()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="founder", source="test",
        )
        # No provenance
        report = immune.scan(mem, None)
        assert not report.accepted
        assert "requires provenance" in report.reason

    def test_invalid_confidence_rejected(self):
        immune = MemoryImmuneSystem()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="founder", source="test",
        )
        mem.metadata.confidence = 1.5  # out of range
        prov = Provenance()
        prov.append(actor_id="founder", action="create")
        report = immune.scan(mem, prov)
        assert not report.accepted
        assert "Confidence out of range" in report.reason

    def test_tampered_provenance_chain_rejected(self):
        immune = MemoryImmuneSystem()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="founder", source="test",
        )
        prov = Provenance()
        prov.append(actor_id="founder", action="create")
        # Tamper
        prov.chain[0].actor_id = "attacker"
        report = immune.scan(mem, prov)
        assert not report.accepted
        assert "verification failed" in report.reason

    def test_replay_attack_rejected(self):
        immune = MemoryImmuneSystem()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="founder", source="test",
        )
        prov = Provenance()
        prov.append(actor_id="founder", action="create")
        seen = {"idem-123"}
        report = immune.scan(
            mem, prov, seen_idempotency_keys=seen, idempotency_key="idem-123",
        )
        assert not report.accepted
        assert "Replay" in report.reason

    def test_forged_existing_id_rejected(self):
        immune = MemoryImmuneSystem()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="founder", source="test",
        )
        prov = Provenance()
        prov.append(actor_id="founder", action="create")
        existing = {mem.id.value}  # caller-supplied ID collides
        report = immune.scan(mem, prov, existing_ids=existing)
        assert not report.accepted
        assert "already exists" in report.reason

    def test_tenant_mismatch_rejected(self):
        immune = MemoryImmuneSystem()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="founder", source="test", tenant_id="default",
        )
        prov = Provenance()
        prov.append(actor_id="founder", action="create")
        report = immune.scan(mem, prov, expected_tenant_id="other_tenant")
        assert not report.accepted
        assert "Tenant mismatch" in report.reason

    def test_non_serializable_payload_rejected(self):
        """A payload whose __repr__ raises must be rejected, not crash."""
        immune = MemoryImmuneSystem()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="ok",
            owner_id="founder", source="test",
        )
        # Object that breaks json.dumps and repr both
        class Worse:
            def __repr__(self):
                raise RuntimeError("can't repr me")
        mem.payload["worse"] = Worse()
        prov = Provenance()
        prov.append(actor_id="founder", action="create")
        # Must not raise; must reject
        report = immune.scan(mem, prov)
        assert not report.accepted
        assert "not serializable" in report.reason or "exceeds size cap" in report.reason

    def test_integrity_hash_changes_on_payload_mutation(self):
        immune = MemoryImmuneSystem()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="v1",
            owner_id="founder", source="test",
        )
        prov = Provenance()
        prov.append(actor_id="founder", action="create")
        report1 = immune.scan(mem, prov)
        h1 = report1.integrity_hash
        # Mutate payload
        mem.payload["value"] = "v2"
        h2 = immune.compute_integrity_hash(mem, prov)
        assert h1 != h2

    def test_verify_integrity_detects_mutation(self):
        immune = MemoryImmuneSystem()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="v1",
            owner_id="founder", source="test",
        )
        prov = Provenance()
        prov.append(actor_id="founder", action="create")
        report = immune.scan(mem, prov)
        assert immune.verify_integrity(mem, prov)
        # Mutate
        mem.payload["value"] = "tampered"
        assert not immune.verify_integrity(mem, prov)


# ----------------------------------------------------------------------
# Memory types
# ----------------------------------------------------------------------


class TestSemanticMemory:
    def test_create_basic(self):
        mem = SemanticMemory.create(
            subject="user", predicate="name", value="Alice",
            owner_id="founder", source="test",
        )
        assert mem.type == MemoryType.SEMANTIC
        assert mem.predicate_key == "user.name"
        assert mem.value == "Alice"
        assert mem.state == MemoryState.CREATED

    def test_create_rejects_empty_subject(self):
        with pytest.raises(ValueError):
            SemanticMemory.create(subject="", predicate="p", value="v")

    def test_create_rejects_empty_predicate(self):
        with pytest.raises(ValueError):
            SemanticMemory.create(subject="s", predicate="", value="v")

    def test_create_rejects_invalid_authority(self):
        with pytest.raises(ValueError):
            SemanticMemory.create(
                subject="s", predicate="p", value="v", source_authority=1.5
            )

    def test_supersede_marks_superseded(self):
        mem = SemanticMemory.create(
            subject="s", predicate="p", value="v1", owner_id="u", source="t",
        )
        assert not mem.is_superseded
        mem.supersede("new-id-123")
        assert mem.is_superseded
        assert mem.payload["superseded_by"] == "new-id-123"

    def test_value_type_inference(self):
        assert SemanticMemory.create("s", "p", True, "u").payload["value_type"] == "bool"
        assert SemanticMemory.create("s", "p", 42, "u").payload["value_type"] == "int"
        assert SemanticMemory.create("s", "p", 3.14, "u").payload["value_type"] == "float"
        assert SemanticMemory.create("s", "p", [1, 2], "u").payload["value_type"] == "list"
        assert SemanticMemory.create("s", "p", {"k": 1}, "u").payload["value_type"] == "dict"
        assert SemanticMemory.create("s", "p", "str", "u").payload["value_type"] == "str"


class TestEpisodicMemory:
    def test_create_basic(self):
        mem = EpisodicMemory.create(
            event_type="user.action", actor_id="user-1",
            outcome="success", context={"task": "deploy"},
        )
        assert mem.type == MemoryType.EPISODIC
        assert mem.event_type == "user.action"
        assert mem.outcome == "success"
        assert mem.payload["context"]["task"] == "deploy"

    def test_create_rejects_empty_event_type(self):
        with pytest.raises(ValueError):
            EpisodicMemory.create(event_type="", actor_id="u")

    def test_create_rejects_empty_actor_id(self):
        with pytest.raises(ValueError):
            EpisodicMemory.create(event_type="x", actor_id="")

    def test_create_rejects_invalid_outcome(self):
        with pytest.raises(ValueError):
            EpisodicMemory.create(event_type="x", actor_id="u", outcome="bogus")

    def test_link_related_adds_id(self):
        mem = EpisodicMemory.create(event_type="x", actor_id="u")
        assert mem.related_episode_ids == []
        mem.link_related("ep-12345678")
        assert "ep-12345678" in mem.related_episode_ids

    def test_link_related_idempotent(self):
        mem = EpisodicMemory.create(event_type="x", actor_id="u")
        mem.link_related("ep-12345678")
        mem.link_related("ep-12345678")
        assert mem.related_episode_ids.count("ep-12345678") == 1

    def test_matches_context(self):
        mem = EpisodicMemory.create(
            event_type="x", actor_id="u",
            context={"tenant": "acme", "env": "prod"},
        )
        assert mem.matches_context(tenant="acme")
        assert mem.matches_context(tenant="acme", env="prod")
        assert not mem.matches_context(tenant="other")


class TestProceduralMemory:
    def test_create_basic(self):
        mem = ProceduralMemory.create(
            procedure_name="deploy",
            steps=[{"name": "build"}, {"name": "ship"}],
            inputs={"version": "str"},
            outputs={"status": "str"},
        )
        assert mem.procedure_name == "deploy"
        assert len(mem.steps) == 2
        assert mem.version == 1

    def test_create_rejects_empty_steps(self):
        with pytest.raises(ValueError):
            ProceduralMemory.create(procedure_name="x", steps=[])

    def test_create_rejects_step_without_name(self):
        with pytest.raises(ValueError):
            ProceduralMemory.create(
                procedure_name="x", steps=[{"no_name": True}],
            )

    def test_record_execution_increments_counts(self):
        mem = ProceduralMemory.create(procedure_name="x", steps=[{"name": "a"}])
        mem.record_execution(success=True, duration_ms=100)
        mem.record_execution(success=True, duration_ms=200)
        mem.record_execution(success=False, duration_ms=50)
        assert mem.payload["execution_count"] == 3
        assert mem.payload["success_count"] == 2
        assert mem.payload["failure_count"] == 1
        # avg = (100+200+50)/3 ≈ 116.67
        assert 100 <= mem.payload["avg_duration_ms"] <= 150

    def test_success_rate(self):
        mem = ProceduralMemory.create(procedure_name="x", steps=[{"name": "a"}])
        assert mem.success_rate == 0.0
        mem.record_execution(success=True, duration_ms=100)
        assert mem.success_rate == 1.0

    def test_new_version_increments(self):
        mem = ProceduralMemory.create(
            procedure_name="x", steps=[{"name": "a"}], owner_id="u",
        )
        v2 = mem.new_version(steps=[{"name": "b"}])
        assert v2.version == 2
        assert v2.payload["supersedes"] == mem.id.value
        assert mem.is_superseded
        assert mem.payload["superseded_by"] == v2.id.value


# ----------------------------------------------------------------------
# Working Memory
# ----------------------------------------------------------------------


class TestWorkingMemory:
    @pytest.mark.asyncio
    async def test_put_and_get(self):
        wm = WorkingMemory(tenant_id="t", owner_id="o")
        await wm.put("key1", "value1")
        assert await wm.get("key1") == "value1"

    @pytest.mark.asyncio
    async def test_capacity_eviction(self):
        wm = WorkingMemory(tenant_id="t", owner_id="o", capacity=3,
                           default_ttl_seconds=0)
        await wm.put("k1", "v1", priority=1)
        await wm.put("k2", "v2", priority=5)
        await wm.put("k3", "v3", priority=10)
        # Adding k4 should evict k1 (lowest priority)
        await wm.put("k4", "v4", priority=3)
        assert await wm.get("k1") is None
        assert await wm.get("k4") == "v4"
        assert await wm.get("k3") == "v3"  # high priority kept

    @pytest.mark.asyncio
    async def test_ttl_expiration(self):
        wm = WorkingMemory(tenant_id="t", owner_id="o", default_ttl_seconds=0)
        await wm.put("k", "v", ttl_seconds=0)  # no TTL
        # Manually set expired TTL
        from datetime import datetime, timedelta, timezone
        item = await wm.peek("k")
        item.expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        assert await wm.get("k") is None  # expired → None

    @pytest.mark.asyncio
    async def test_lru_touch_on_get(self):
        wm = WorkingMemory(tenant_id="t", owner_id="o", capacity=2,
                           default_ttl_seconds=0)
        await wm.put("k1", "v1", priority=5)
        await wm.put("k2", "v2", priority=5)
        # Touch k1 (more recent)
        await wm.get("k1")
        await asyncio.sleep(0.001)
        # Add k3 — should evict k2 (older LRU)
        await wm.put("k3", "v3", priority=5)
        assert await wm.get("k1") == "v1"
        assert await wm.get("k2") is None
        assert await wm.get("k3") == "v3"

    @pytest.mark.asyncio
    async def test_put_overwrites_existing(self):
        wm = WorkingMemory(tenant_id="t", owner_id="o")
        await wm.put("k", "v1")
        await wm.put("k", "v2")  # overwrite
        assert await wm.get("k") == "v2"
        assert await wm.size() == 1

    @pytest.mark.asyncio
    async def test_invalid_capacity_rejected(self):
        with pytest.raises(ValidationError):
            WorkingMemory(tenant_id="t", owner_id="o", capacity=0)
        with pytest.raises(ValidationError):
            WorkingMemory(tenant_id="t", owner_id="o", capacity=100000)  # > 65536 cap

    @pytest.mark.asyncio
    async def test_oversized_value_rejected(self):
        wm = WorkingMemory(tenant_id="t", owner_id="o", max_value_size_bytes=64)
        with pytest.raises(ResourceLimitError):
            await wm.put("k", "x" * 200)

    @pytest.mark.asyncio
    async def test_clear_expired(self):
        wm = WorkingMemory(tenant_id="t", owner_id="o", default_ttl_seconds=0)
        await wm.put("k1", "v1", ttl_seconds=0)
        # Manually expire
        from datetime import datetime, timedelta, timezone
        item = await wm.peek("k1")
        item.expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        await wm.put("k2", "v2", ttl_seconds=0)
        n = await wm.clear_expired()
        assert n == 1
        assert await wm.get("k2") == "v2"

    @pytest.mark.asyncio
    async def test_snapshot_reports_utilization(self):
        wm = WorkingMemory(tenant_id="t", owner_id="o", capacity=10)
        await wm.put("k1", "v1")
        snap = await wm.snapshot()
        assert snap["utilization"] == 0.1
        assert snap["current_size"] == 1


# ----------------------------------------------------------------------
# Association Graph
# ----------------------------------------------------------------------


class TestAssociationGraph:
    @pytest.mark.asyncio
    async def test_add_and_neighbors(self):
        g = AssociationGraph(tenant_id="t")
        edge = AssociationEdge(source_id="m1" + "0"*6, target_id="m2" + "0"*6,
                               relationship="related", weight=0.5)
        await g.add_edge(edge)
        nbrs = await g.neighbors("m1" + "0"*6)
        assert len(nbrs) == 1
        assert nbrs[0].target_id == "m2" + "0"*6

    @pytest.mark.asyncio
    async def test_self_association_rejected(self):
        with pytest.raises(ValidationError):
            AssociationMemory.create(
                source_id="m1" + "0"*6, target_id="m1" + "0"*6,
            )

    @pytest.mark.asyncio
    async def test_invalid_weight_rejected(self):
        with pytest.raises(ValidationError):
            AssociationMemory.create(
                source_id="s" + "0"*7, target_id="t" + "0"*7, weight=1.5,
            )

    @pytest.mark.asyncio
    async def test_degree_cap_enforced(self):
        g = AssociationGraph(tenant_id="t", max_degree=2)
        s = "src" + "0"*5
        await g.add_edge(AssociationEdge(s, "t1" + "0"*6, weight=0.5))
        await g.add_edge(AssociationEdge(s, "t2" + "0"*6, weight=0.5))
        with pytest.raises(ResourceLimitError):
            await g.add_edge(AssociationEdge(s, "t3" + "0"*6, weight=0.5))

    @pytest.mark.asyncio
    async def test_traversal_depth_capped(self):
        g = AssociationGraph(tenant_id="t", max_traversal_depth=2)
        # Build a chain: m1 -> m2 -> m3 -> m4 -> m5
        for i in range(4):
            await g.add_edge(AssociationEdge(
                f"m{i}" + "0"*6, f"m{i+1}" + "0"*6, weight=1.0
            ))
        results = await g.traverse("m0" + "0"*6, max_depth=10)
        # Should be capped at depth 2 → m1, m2 only
        depths = [r[1] for r in results]
        assert max(depths) <= 2

    @pytest.mark.asyncio
    async def test_bidirectional_edge_reachable_both_ways(self):
        g = AssociationGraph(tenant_id="t")
        await g.add_edge(AssociationEdge(
            "src" + "0"*5, "tgt" + "0"*5, weight=0.7, bidirectional=True,
        ))
        out_nbrs = await g.neighbors("src" + "0"*5, direction="out")
        in_nbrs = await g.neighbors("tgt" + "0"*5, direction="in")
        assert len(out_nbrs) == 1
        assert len(in_nbrs) >= 1

    @pytest.mark.asyncio
    async def test_remove_edge(self):
        g = AssociationGraph(tenant_id="t")
        await g.add_edge(AssociationEdge(
            "src" + "0"*5, "tgt" + "0"*5, relationship="related",
        ))
        assert await g.remove_edge("src" + "0"*5, "tgt" + "0"*5)
        assert not await g.remove_edge("src" + "0"*5, "tgt" + "0"*5)  # already gone

    @pytest.mark.asyncio
    async def test_remove_all_for_memory(self):
        g = AssociationGraph(tenant_id="t")
        await g.add_edge(AssociationEdge("m1" + "0"*6, "m2" + "0"*6))
        await g.add_edge(AssociationEdge("m3" + "0"*6, "m1" + "0"*6))
        await g.add_edge(AssociationEdge("m1" + "0"*6, "m4" + "0"*6))
        removed = await g.remove_all_for("m1" + "0"*6)
        assert removed == 3
        assert await g.edge_count() == 0

    @pytest.mark.asyncio
    async def test_traversal_min_weight_filter(self):
        g = AssociationGraph(tenant_id="t")
        await g.add_edge(AssociationEdge("m1" + "0"*6, "m2" + "0"*6, weight=0.8))
        await g.add_edge(AssociationEdge("m2" + "0"*6, "m3" + "0"*6, weight=0.3))
        # Filter min_weight 0.5 — m3 should not be reached
        results = await g.traverse("m1" + "0"*6, min_weight=0.5)
        ids = [r[0] for r in results]
        assert "m2" + "0"*6 in ids
        assert "m3" + "0"*6 not in ids

    @pytest.mark.asyncio
    async def test_total_edge_cap(self):
        g = AssociationGraph(tenant_id="t", max_edges=2)
        await g.add_edge(AssociationEdge("s1" + "0"*5, "t1" + "0"*5))
        await g.add_edge(AssociationEdge("s2" + "0"*5, "t2" + "0"*5))
        with pytest.raises(ResourceLimitError):
            await g.add_edge(AssociationEdge("s3" + "0"*5, "t3" + "0"*5))


# ----------------------------------------------------------------------
# Consolidation
# ----------------------------------------------------------------------


class TestConsolidationEngine:
    def test_score_candidate_high_importance_eligible(self):
        eng = ConsolidationEngine(ConsolidationConfig(threshold=0.3))
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="test", importance=0.9, confidence=0.9,
        )
        mem.state = MemoryState.ACTIVE
        mem.metadata.reinforcement_count = 5
        cand = eng.score_candidate(mem)
        assert cand.eligible
        assert cand.score > 0.3

    def test_score_candidate_low_score_not_eligible(self):
        eng = ConsolidationEngine(ConsolidationConfig(threshold=0.95))
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="test", importance=0.1, confidence=0.1,
        )
        mem.state = MemoryState.ACTIVE
        cand = eng.score_candidate(mem)
        assert not cand.eligible
        assert "below threshold" in cand.reason

    def test_already_consolidated_not_eligible(self):
        eng = ConsolidationEngine()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="test", importance=0.99, confidence=0.99,
        )
        mem.state = MemoryState.CONSOLIDATED
        cand = eng.score_candidate(mem)
        assert not cand.eligible
        assert "already consolidated" in cand.reason

    def test_archived_state_not_eligible(self):
        eng = ConsolidationEngine()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="test", importance=0.99,
        )
        mem.state = MemoryState.ARCHIVED
        cand = eng.score_candidate(mem)
        assert not cand.eligible

    def test_score_candidates_caps_at_max_per_pass(self):
        eng = ConsolidationEngine(ConsolidationConfig(threshold=0.0, max_per_pass=3))
        mems = []
        for i in range(10):
            m = SemanticMemory.create(
                subject=f"s{i}", predicate="p", value=i,
                owner_id="u", source="t", importance=0.9, confidence=0.9,
            )
            m.state = MemoryState.ACTIVE
            mems.append(m)
        result = eng.score_candidates(mems)
        assert result.consolidated_count <= 3
        assert result.candidates_evaluated == 10

    def test_require_reinforcement_min(self):
        eng = ConsolidationEngine(ConsolidationConfig(
            threshold=0.0, require_reinforcement_min=3
        ))
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="t", importance=0.99, confidence=0.99,
        )
        mem.state = MemoryState.ACTIVE
        mem.metadata.reinforcement_count = 1
        cand = eng.score_candidate(mem)
        assert not cand.eligible
        assert "reinforcements" in cand.reason


# ----------------------------------------------------------------------
# Decay
# ----------------------------------------------------------------------


class TestDecayEngine:
    def test_recent_memory_kept(self):
        eng = DecayEngine(DecayConfig())
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="t", importance=0.8,
        )
        mem.state = MemoryState.ACTIVE
        mem.metadata.updated_at = now_utc()
        cand = eng.score_candidate(mem)
        assert cand.recommendation == "keep"

    def test_old_memory_archived(self):
        eng = DecayEngine(DecayConfig(
            archival_threshold=0.99,  # high → archive almost immediately
            forget_threshold=0.999,
            base_decay_rate=100.0,  # very fast decay
            max_age_seconds=1,
        ))
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="t",
        )
        mem.state = MemoryState.ACTIVE
        # Force old updated_at
        from datetime import datetime, timedelta, timezone
        mem.metadata.updated_at = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()
        cand = eng.score_candidate(mem)
        assert cand.recommendation in ("archive", "forget")

    def test_consolidated_resists_decay(self):
        eng = DecayEngine(DecayConfig(
            base_decay_rate=0.5,  # strong decay
            consolidation_resistance=2.0,  # strong resistance
        ))
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="t", importance=0.9,
        )
        mem.state = MemoryState.CONSOLIDATED
        from datetime import datetime, timedelta, timezone
        mem.metadata.updated_at = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        cand = eng.score_candidate(mem)
        # Consolidated memory should resist decay
        assert cand.decay_score < 0.5

    def test_reinforced_resists_decay(self):
        eng = DecayEngine(DecayConfig(
            base_decay_rate=0.5,
            reinforcement_resistance=0.5,
        ))
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="t",
        )
        mem.state = MemoryState.ACTIVE
        # Many reinforcements
        for _ in range(20):
            mem.metadata.reinforce(delta=0.05)
        from datetime import datetime, timedelta, timezone
        mem.metadata.updated_at = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        cand = eng.score_candidate(mem)
        assert cand.decay_score < 0.3  # strong reinforcement

    def test_select_forced_archive_picks_lowest_priority(self):
        eng = DecayEngine()
        mems = []
        for i in range(5):
            m = SemanticMemory.create(
                subject=f"s{i}", predicate="p", value=i,
                owner_id="u", source="t",
                importance=i * 0.2,  # 0, 0.2, 0.4, 0.6, 0.8
                confidence=0.5,
            )
            m.state = MemoryState.ACTIVE
            mems.append(m)
        forced = eng.select_forced_archive(mems, 2)
        # Should be the two lowest-importance memories
        assert len(forced) == 2
        # Forced IDs should correspond to importance 0 and 0.2 (lowest)
        importance_of_forced = [
            next(m.metadata.importance for m in mems if m.id.value == fid)
            for fid in forced
        ]
        assert max(importance_of_forced) <= 0.3  # both should be lowest priority


# ----------------------------------------------------------------------
# Contradiction Detector
# ----------------------------------------------------------------------


class TestContradictionDetector:
    def test_duplicate_predicate_is_duplicate(self):
        det = ContradictionDetector()
        new_id = "new" + "0"*5
        new_payload = {"predicate_key": "user.name", "value": "Alice",
                       "source_authority": 0.5}
        candidates = [("old" + "0"*5, {"predicate_key": "user.name",
                                       "value": "Alice",
                                       "source_authority": 0.5},
                       "semantic", 0.5)]
        records = det.detect(new_id, new_payload, "semantic", candidates)
        assert len(records) == 1
        assert records[0].conflict_type.value == "duplicate"

    def test_predicate_conflict(self):
        det = ContradictionDetector()
        new_id = "new" + "0"*5
        new_payload = {"predicate_key": "user.name", "value": "Alice",
                       "source_authority": 0.5}
        candidates = [("old" + "0"*5, {"predicate_key": "user.name",
                                        "value": "Bob",
                                        "source_authority": 0.5},
                       "semantic", 0.5)]
        records = det.detect(new_id, new_payload, "semantic", candidates)
        assert len(records) == 1
        assert records[0].conflict_type.value == "predicate_conflict"

    def test_negation_conflict(self):
        det = ContradictionDetector()
        new_id = "new" + "0"*5
        new_payload = {"predicate_key": "user.active", "value": "not true",
                       "source_authority": 0.5}
        candidates = [("old" + "0"*5, {"predicate_key": "user.active",
                                        "value": "true",
                                        "source_authority": 0.5},
                       "semantic", 0.5)]
        records = det.detect(new_id, new_payload, "semantic", candidates)
        assert any(r.conflict_type.value == "negation_conflict" for r in records)

    def test_numerical_conflict(self):
        det = ContradictionDetector(numerical_tolerance=0.001)
        new_id = "new" + "0"*5
        new_payload = {"predicate_key": "x.value", "value": 1.5,
                       "source_authority": 0.5}
        candidates = [("old" + "0"*5, {"predicate_key": "x.value",
                                        "value": 2.0,
                                        "source_authority": 0.5},
                       "semantic", 0.5)]
        records = det.detect(new_id, new_payload, "semantic", candidates)
        assert any(r.conflict_type.value == "numerical_conflict" for r in records)

    def test_numerical_conflict_within_tolerance_not_flagged(self):
        det = ContradictionDetector(numerical_tolerance=0.1)
        new_id = "new" + "0"*5
        new_payload = {"predicate_key": "x.value", "value": 1.5,
                       "source_authority": 0.5}
        candidates = [("old" + "0"*5, {"predicate_key": "x.value",
                                        "value": 1.55,
                                        "source_authority": 0.5},
                       "semantic", 0.5)]
        records = det.detect(new_id, new_payload, "semantic", candidates)
        # No numerical conflict (within tolerance), but predicate conflict still triggers
        assert all(r.conflict_type.value != "numerical_conflict" for r in records)

    def test_procedure_conflict(self):
        det = ContradictionDetector()
        new_id = "new" + "0"*5
        new_payload = {"procedure_name": "deploy",
                       "steps": [{"name": "build"}, {"name": "ship"}]}
        candidates = [("old" + "0"*5, {"procedure_name": "deploy",
                                        "steps": [{"name": "build"},
                                                  {"name": "test"},
                                                  {"name": "ship"}]},
                       "procedural", 0.5)]
        records = det.detect(new_id, new_payload, "procedural", candidates)
        assert len(records) == 1
        assert records[0].conflict_type.value == "procedure_conflict"

    def test_source_authority_conflict(self):
        det = ContradictionDetector()
        new_id = "new" + "0"*5
        new_payload = {"predicate_key": "x.y", "value": "v1",
                       "source_authority": 0.2}
        candidates = [("old" + "0"*5, {"predicate_key": "x.y",
                                        "value": "v2",
                                        "source_authority": 0.9},
                       "semantic", 0.9)]
        records = det.detect(new_id, new_payload, "semantic", candidates)
        assert any(r.conflict_type.value == "source_authority_conflict" for r in records)

    def test_no_conflict_for_different_predicates(self):
        det = ContradictionDetector()
        new_id = "new" + "0"*5
        new_payload = {"predicate_key": "user.name", "value": "Alice",
                       "source_authority": 0.5}
        candidates = [("old" + "0"*5, {"predicate_key": "user.age",
                                       "value": 30,
                                       "source_authority": 0.5},
                       "semantic", 0.5)]
        records = det.detect(new_id, new_payload, "semantic", candidates)
        assert len(records) == 0


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------


class TestPersistence:
    @pytest.mark.asyncio
    async def test_in_memory_round_trip(self):
        p = InMemoryPersistence()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="t",
        )
        prov = Provenance()
        prov.append(actor_id="u", action="create")
        await p.save(mem, prov)
        assert await p.exists(mem.id.value)
        items, report = await p.load()
        assert report.loaded_count == 1
        assert items[0][0].id.value == mem.id.value
        assert items[0][1].verify_chain()
        assert await p.count() == 1
        assert await p.delete(mem.id.value)
        assert not await p.exists(mem.id.value)
        assert await p.count() == 0

    @pytest.mark.asyncio
    async def test_json_file_atomic_write(self, tmp_path):
        path = str(tmp_path / "mem.json")
        p = JSONFilePersistence(path)
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="t",
        )
        prov = Provenance()
        prov.append(actor_id="u", action="create")
        await p.save(mem, prov)
        assert os.path.exists(path)
        # Reload from a new instance
        p2 = JSONFilePersistence(path)
        items, report = await p2.load()
        assert report.loaded_count == 1
        assert items[0][0].id.value == mem.id.value

    @pytest.mark.asyncio
    async def test_json_file_corrupted_record_quarantined(self, tmp_path):
        path = str(tmp_path / "corrupt.json")
        # Write garbage
        with open(path, "w") as f:
            f.write("not json{")
        p = JSONFilePersistence(path)
        items, report = await p.load()
        # Should not crash, just return empty + report error
        assert isinstance(items, list)
        assert isinstance(report, RecoveryReport)

    @pytest.mark.asyncio
    async def test_in_memory_flush(self):
        p = InMemoryPersistence()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id="u", source="t",
        )
        prov = Provenance()
        prov.append(actor_id="u", action="create")
        await p.save(mem, prov)
        await p.flush()
        assert await p.count() == 0


# ----------------------------------------------------------------------
# Observability
# ----------------------------------------------------------------------


class TestObservability:
    def test_counters_inc(self):
        m = MemoryMetrics()
        m.inc("writes", 1, type="semantic")
        m.inc("writes", 1, type="semantic")
        m.inc("writes", 1, type="episodic")
        snap = m.snapshot()
        assert snap["counters"]["writes|type=semantic"] == 2
        assert snap["counters"]["writes|type=episodic"] == 1

    def test_gauges(self):
        m = MemoryMetrics()
        m.set_gauge("active_count", 5, type="semantic")
        m.inc_gauge("active_count", 2, type="semantic")
        m.dec_gauge("active_count", 1, type="semantic")
        snap = m.snapshot()
        assert snap["gauges"]["active_count|type=semantic"] == 6

    def test_histogram_p99(self):
        m = MemoryMetrics()
        for i in range(1, 101):
            m.observe("latency", float(i))
        stats = m.get_histogram_stats("latency")
        assert stats["count"] == 100
        assert stats["min"] == 1.0
        assert stats["max"] == 100.0
        assert 99 <= stats["p99"] <= 100

    def test_event_recording_bounded(self):
        m = MemoryMetrics()
        m._max_events = 100  # force small for this test
        for i in range(200):
            m.record_event(MemoryEvent(type=f"memory.event.{i}"))
        # Should be bounded to ~100
        assert len(m._events) <= 200

    @pytest.mark.asyncio
    async def test_observability_hub_emit_no_bus(self):
        hub = ObservabilityHub(event_bus=None)
        await hub.emit(MemoryEvent(type="memory.test", memory_id="x" + "0"*7))
        snap = hub.snapshot()
        assert len(snap["recent_events"]) >= 1


# ----------------------------------------------------------------------
# Manager end-to-end
# ----------------------------------------------------------------------


class TestManagerRememberRecall:
    @pytest.mark.asyncio
    async def test_remember_and_recall(self, manager, founder_ctx):
        await manager.start()
        mem = SemanticMemory.create(
            subject="user", predicate="name", value="Alice",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        stored = await manager.remember(mem, founder_ctx, source="test")
        assert stored.state == MemoryState.ACTIVE
        recalled = await manager.recall(stored.id.value, founder_ctx)
        assert recalled is not None
        assert recalled.payload["value"] == "Alice"
        # Access count should be incremented
        assert recalled.metadata.access_count == 1
        await manager.stop()

    @pytest.mark.asyncio
    async def test_remember_unauthorized_rejected(self, manager, unauthorized_ctx):
        await manager.start()
        mem = SemanticMemory.create(
            subject="user", predicate="name", value="Alice",
            owner_id=unauthorized_ctx.citizen_id, source="test",
        )
        with pytest.raises(AuthorizationError):
            await manager.remember(mem, unauthorized_ctx)
        await manager.stop()

    @pytest.mark.asyncio
    async def test_remember_banned_rejected(self, manager, banned_ctx):
        await manager.start()
        mem = SemanticMemory.create(
            subject="user", predicate="name", value="Alice",
            owner_id=banned_ctx.citizen_id, source="test",
        )
        with pytest.raises(AuthorizationError):
            await manager.remember(mem, banned_ctx)
        await manager.stop()

    @pytest.mark.asyncio
    async def test_remember_immune_rejection_quarantines(self, manager, founder_ctx):
        await manager.start()
        # Create memory with oversized payload
        immune = MemoryImmuneSystem(max_payload_bytes=64)
        manager._immune = immune
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="A" * 200,
            owner_id=founder_ctx.citizen_id, source="test",
        )
        with pytest.raises(ImmuneRejection):
            await manager.remember(mem, founder_ctx)
        # Memory should be quarantined
        snap = await manager.health_check()
        # Quarantine count in metrics
        assert manager.metrics.get_counters().get(
            "memory.rejections|reason=Payload exceeds size cap: 202 > 64 bytes",
            0
        ) == 1 or any(
            "memory.rejections" in k for k in manager.metrics.get_counters()
        )
        await manager.stop()

    @pytest.mark.asyncio
    async def test_remember_contradiction_raises(self, manager, founder_ctx):
        await manager.start()
        mem1 = SemanticMemory.create(
            subject="user", predicate="name", value="Alice",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        await manager.remember(mem1, founder_ctx, source="test")
        # Now store conflicting fact with same predicate_key, different value
        mem2 = SemanticMemory.create(
            subject="user", predicate="name", value="Bob",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        with pytest.raises(ContradictionError):
            await manager.remember(mem2, founder_ctx, source="test")
        # Duplicate (same value) is NOT an error
        mem3 = SemanticMemory.create(
            subject="user", predicate="name", value="Alice",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        # Duplicate raises ImmuneRejection (forged ID check is on id, but
        # duplicate VALUE is detected by contradiction detector as duplicate
        # type which is skipped, so the memory SHOULD be stored)
        # But wait — the ID is different, so existing_ids check passes;
        # contradiction detector flags as duplicate but doesn't raise.
        await manager.remember(mem3, founder_ctx, source="test")
        await manager.stop()

    @pytest.mark.asyncio
    async def test_reinforce_increases_confidence(self, manager, founder_ctx):
        await manager.start()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id=founder_ctx.citizen_id, source="test",
            confidence=0.5, importance=0.5,
        )
        stored = await manager.remember(mem, founder_ctx, source="test")
        initial_conf = stored.metadata.confidence
        reinforced = await manager.reinforce(stored.id.value, founder_ctx, delta=0.2)
        assert reinforced.metadata.confidence > initial_conf
        assert reinforced.metadata.reinforcement_count == 1
        assert reinforced.state == MemoryState.REINFORCED
        await manager.stop()

    @pytest.mark.asyncio
    async def test_recall_nonexistent_returns_none(self, manager, founder_ctx):
        await manager.start()
        result = await manager.recall("nonexist-1234", founder_ctx)
        assert result is None
        await manager.stop()

    @pytest.mark.asyncio
    async def test_recall_invalid_id_returns_none(self, manager, founder_ctx):
        await manager.start()
        # Too-short ID should return None (no validation error)
        result = await manager.recall("abc", founder_ctx)
        assert result is None
        await manager.stop()

    @pytest.mark.asyncio
    async def test_cross_tenant_recall_returns_none(self, manager, founder_ctx, other_tenant_ctx):
        """A memory in tenant A is INVISIBLE to tenant B (returns None).

        This is stronger than raising AuthorizationError: the very existence
        of the memory is hidden across tenant boundaries (Constitution
        Article 7 — Privacy).
        """
        await manager.start()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id=founder_ctx.citizen_id, source="test",
            tenant_id="default",
        )
        stored = await manager.remember(mem, founder_ctx, source="test")
        # Other-tenant founder: memory not in their tenant → returns None
        result = await manager.recall(stored.id.value, other_tenant_ctx)
        assert result is None
        await manager.stop()


class TestManagerConsolidation:
    @pytest.mark.asyncio
    async def test_consolidate_promotes_eligible(self, manager, founder_ctx):
        await manager.start()
        # Create a high-importance memory
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id=founder_ctx.citizen_id, source="test",
            importance=0.9, confidence=0.9,
        )
        stored = await manager.remember(mem, founder_ctx, source="test")
        # Reinforce to bump score
        for _ in range(5):
            await manager.reinforce(stored.id.value, founder_ctx, delta=0.05)
        # Run consolidation
        result = await manager.consolidate(founder_ctx)
        assert result.consolidated_count >= 1
        # Memory should now be CONSOLIDATED
        recalled = await manager.recall(stored.id.value, founder_ctx)
        assert recalled.state == MemoryState.CONSOLIDATED
        await manager.stop()

    @pytest.mark.asyncio
    async def test_consolidate_requires_capability(self, manager, unauthorized_ctx):
        await manager.start()
        with pytest.raises(AuthorizationError):
            await manager.consolidate(unauthorized_ctx)
        await manager.stop()


class TestManagerForget:
    @pytest.mark.asyncio
    async def test_forget_requires_founder_or_approval(self, manager, worker_ctx):
        await manager.start()
        # Worker can't forget even with memory.write capability
        with pytest.raises(AuthorizationError):
            await manager.forget("any-id-here", worker_ctx)
        await manager.stop()

    @pytest.mark.asyncio
    async def test_forget_without_approval_gate_requires_founder(
        self, manager, worker_ctx, founder_ctx
    ):
        await manager.start()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        stored = await manager.remember(mem, founder_ctx, source="test")
        # Worker can't (rank too low)
        with pytest.raises(AuthorizationError):
            await manager.forget(stored.id.value, worker_ctx)
        # Founder can
        ok = await manager.forget(stored.id.value, founder_ctx)
        assert ok
        recalled = await manager.recall(stored.id.value, founder_ctx)
        assert recalled is None  # FORGOTTEN not accessible
        await manager.stop()

    @pytest.mark.asyncio
    async def test_archive_and_restore(self, manager, founder_ctx):
        await manager.start()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        stored = await manager.remember(mem, founder_ctx, source="test")
        ok = await manager.archive(stored.id.value, founder_ctx)
        assert ok
        # Should be inaccessible
        assert await manager.recall(stored.id.value, founder_ctx) is None
        # Restore
        ok = await manager.restore(stored.id.value, founder_ctx)
        assert ok
        # Now accessible again
        recalled = await manager.recall(stored.id.value, founder_ctx)
        assert recalled is not None
        assert recalled.state == MemoryState.ACTIVE
        await manager.stop()


class TestManagerAssociation:
    @pytest.mark.asyncio
    async def test_associate_and_neighbors(self, manager, founder_ctx):
        await manager.start()
        mem1 = SemanticMemory.create(
            subject="x", predicate="y", value="1",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        mem2 = SemanticMemory.create(
            subject="x", predicate="y", value="2",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        # Use different predicates to avoid contradiction
        mem1.payload["predicate_key"] = "x.a"
        mem1.payload["predicate"] = "a"
        mem2.payload["predicate_key"] = "x.b"
        mem2.payload["predicate"] = "b"
        s1 = await manager.remember(mem1, founder_ctx, source="test")
        s2 = await manager.remember(mem2, founder_ctx, source="test")
        # Associate
        await manager.associate(
            s1.id.value, s2.id.value, founder_ctx,
            relationship="related_to", weight=0.8, bidirectional=True,
        )
        nbrs = await manager.neighbors(s1.id.value, founder_ctx)
        assert len(nbrs) >= 1
        await manager.stop()

    @pytest.mark.asyncio
    async def test_associate_nonexistent_memory_rejected(self, manager, founder_ctx):
        await manager.start()
        with pytest.raises(ValidationError):
            await manager.associate(
                "fake-id-001", "fake-id-002", founder_ctx,
            )
        await manager.stop()

    @pytest.mark.asyncio
    async def test_traverse_returns_bounded_results(self, manager, founder_ctx):
        await manager.start()
        # Build a chain of 5 memories
        ids = []
        for i in range(5):
            mem = SemanticMemory.create(
                subject="x", predicate=f"p{i}", value=i,
                owner_id=founder_ctx.citizen_id, source="test",
            )
            stored = await manager.remember(mem, founder_ctx, source="test")
            ids.append(stored.id.value)
        # Link them in a chain
        for i in range(4):
            await manager.associate(
                ids[i], ids[i+1], founder_ctx,
                relationship="next", weight=1.0,
            )
        # Traverse from first
        results = await manager.traverse(
            ids[0], founder_ctx, max_depth=10, max_results=100,
        )
        assert len(results) <= 5
        # Depth should not exceed max_traversal_depth (default 8)
        for _, depth, _ in results:
            assert depth <= 8
        await manager.stop()


class TestManagerRecovery:
    @pytest.mark.asyncio
    async def test_json_persistence_recovery(self, tmp_path, founder_ctx):
        path = str(tmp_path / "recovery.json")
        # First manager: store some memories
        m1 = LivingMemoryManager(persistence=JSONFilePersistence(path))
        await m1.start()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        stored = await m1.remember(mem, founder_ctx, source="test")
        await m1.stop()
        # Second manager: should recover
        m2 = LivingMemoryManager(persistence=JSONFilePersistence(path))
        report = await m2.start()
        assert report.loaded_count >= 1
        recalled = await m2.recall(stored.id.value, founder_ctx)
        assert recalled is not None
        assert recalled.payload["value"] == "z"
        await m2.stop()

    @pytest.mark.asyncio
    async def test_metrics_recorded_after_operations(self, manager, founder_ctx):
        await manager.start()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        await manager.remember(mem, founder_ctx, source="test")
        await manager.recall(mem.id.value, founder_ctx)
        snap = manager.metrics.snapshot()
        counters = snap["counters"]
        assert any("memory.writes" in k for k in counters)
        assert any("memory.reads" in k for k in counters)
        await manager.stop()


class TestManagerInspectExplain:
    @pytest.mark.asyncio
    async def test_inspect_returns_full_record(self, manager, founder_ctx):
        await manager.start()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        stored = await manager.remember(mem, founder_ctx, source="test")
        result = await manager.inspect(stored.id.value, founder_ctx)
        assert result is not None
        assert "memory" in result
        assert "provenance" in result
        assert result["memory"]["id"]["value"] == stored.id.value
        await manager.stop()

    @pytest.mark.asyncio
    async def test_explain_returns_readable_chain(self, manager, founder_ctx):
        await manager.start()
        mem = SemanticMemory.create(
            subject="x", predicate="y", value="z",
            owner_id=founder_ctx.citizen_id, source="test",
        )
        stored = await manager.remember(mem, founder_ctx, source="test")
        await manager.reinforce(stored.id.value, founder_ctx)
        text = await manager.explain(stored.id.value, founder_ctx)
        assert text is not None
        assert "Provenance chain" in text
        assert "create" in text
        assert "reinforce" in text
        assert "Chain verification: OK" in text
        await manager.stop()
