"""Living Memory Manager — unified facade for the entire living memory system.

Composes:
    - MemoryImmuneSystem (validation)
    - AuthorizationGate (capability + tenant)
    - PersistenceAdapter (in-memory / JSON / Redis)
    - AssociationGraph (per-tenant)
    - ContradictionDetector
    - ConsolidationEngine
    - DecayEngine
    - WorkingMemory (per-tenant per-owner)
    - ObservabilityHub (metrics + events)

Provides the biological memory operations:
    - remember()           → create + persist a memory
    - recall()             → retrieve by ID
    - recall_by_type()     → list memories of a type
    - search()             → filtered retrieval
    - reinforce()          → strengthen a memory
    - consolidate()        → run consolidation pass
    - decay()               → run decay pass
    - associate()          → create an association edge
    - neighbors()          → traverse associations
    - detect_contradictions() → check incoming memory against existing
    - resolve_contradiction() → governance-controlled resolution
    - forget()             → governed deletion (requires approval)
    - archive()            → reversible archival
    - restore()           → restore from archive
    - inspect()           → full memory + provenance dump
    - explain()           → human-readable provenance chain

All operations require an AuthorizationContext. All mutations emit events.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from .association import AssociationEdge, AssociationGraph, AssociationMemory
from .authz import AuthorizationContext, AuthorizationGate, MemoryCapability
from .base import (
    AuthorizationError, ContradictionError, ImmuneRejection, LifecycleError,
    Memory, MemoryID, MemoryMetadata, MemoryState, MemoryType, ResourceLimitError,
    ValidationError, new_correlation_id, now_utc, validate_transition,
)
from .consolidation import ConsolidationEngine, ConsolidationResult
from .contradiction import (
    ContradictionDetector, ContradictionRecord, ContradictionStatus,
)
from .decay import DecayEngine, DecayResult
from .episodic import EpisodicMemory
from .immune import ImmuneReport, MemoryImmuneSystem
from .observability import MemoryEvent, ObservabilityHub
from .persistence import (
    InMemoryPersistence, JSONFilePersistence, PersistenceAdapter, RecoveryReport,
    RedisPersistence,
)
from .procedural import ProceduralMemory
from .provenance import Provenance
from .semantic import SemanticMemory
from .working import WorkingMemory

logger = logging.getLogger("friday.living_memory.manager")


@dataclass
class LivingMemoryConfig:
    """Configuration for the living memory manager."""
    max_memories_per_tenant: int = 10_000
    max_contradictions_per_tenant: int = 1_000
    max_tenants: int = 1_000               # V4: cap total tenants (DoS protection)
    max_working_memories: int = 10_000      # V4: cap total WorkingMemory instances
    quarantine_enabled: bool = True
    auto_consolidate_on_remember: bool = False
    auto_decay_on_write: bool = False
    decay_interval_writes: int = 100  # run decay every N writes
    max_history_events: int = 5_000


@dataclass
class TenantState:
    """Per-tenant runtime state."""
    tenant_id: str
    memories: Dict[str, Tuple[Memory, Provenance]] = field(default_factory=dict)
    contradictions: Dict[str, ContradictionRecord] = field(default_factory=dict)
    association_graph: AssociationGraph = None  # type: ignore[assignment]
    write_count: int = 0
    last_decay_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "memory_count": len(self.memories),
            "contradiction_count": len(self.contradictions),
            "write_count": self.write_count,
            "last_decay_at": self.last_decay_at,
        }


class LivingMemoryManager:
    """Unified facade for living memory.

    Lifecycle:
        await manager.start()       # init subsystems
        await manager.remember(...) # store memory
        await manager.recall(...)   # retrieve
        await manager.stop()        # cleanup

    Backward compatible: works with no event_bus, no approval_gate, no
    citizen_registry. Those are optional composable Age V services.
    """

    def __init__(
        self,
        persistence: Optional[PersistenceAdapter] = None,
        event_bus=None,                       # Age V DistributedEventBus (optional)
        approval_gate=None,                   # Age V ApprovalGate (optional)
        citizen_registry=None,                # Age V CitizenRegistry (optional)
        config: Optional[LivingMemoryConfig] = None,
        immune: Optional[MemoryImmuneSystem] = None,
        consolidation_engine: Optional[ConsolidationEngine] = None,
        decay_engine: Optional[DecayEngine] = None,
    ):
        self._persistence = persistence or InMemoryPersistence()
        self._event_bus = event_bus
        self._approval_gate = approval_gate
        self._citizen_registry = citizen_registry
        self._config = config or LivingMemoryConfig()
        self._immune = immune or MemoryImmuneSystem()
        self._authz = AuthorizationGate()
        self._consolidation = consolidation_engine or ConsolidationEngine()
        self._decay = decay_engine or DecayEngine()
        self._contradiction = ContradictionDetector()
        self._observability = ObservabilityHub(event_bus=event_bus)
        self._tenants: Dict[str, TenantState] = {}
        self._working_memories: Dict[str, WorkingMemory] = {}  # key: tenant|owner
        self._lock = asyncio.Lock()
        self._started = False
        self._seen_idempotency: Set[str] = set()  # bounded LRU
        self._max_idempotency_cache = 10_000
        self._recovery_report: RecoveryReport = RecoveryReport()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> RecoveryReport:
        """Initialize: load persistent state, prepare subsystems."""
        if self._started:
            return self._recovery_report
        async with self._lock:
            self._started = True
            # Load from persistence
            items, report = await self._persistence.load()
            self._recovery_report = report
            for mem, prov in items:
                # Re-verify integrity on load
                if not self._immune.verify_integrity(mem, prov):
                    report.quarantined_count += 1
                    report.quarantined_ids.append(mem.id.value)
                    # Quarantine in memory (state mutation)
                    try:
                        validate_transition(mem.state, MemoryState.QUARANTINED)
                        mem.state = MemoryState.QUARANTINED
                    except LifecycleError:
                        pass
                tenant = self._get_or_create_tenant(mem.metadata.tenant_id)
                tenant.memories[mem.id.value] = (mem, prov)
            logger.info(
                "LivingMemoryManager started — loaded %d, quarantined %d",
                report.loaded_count, report.quarantined_count,
            )
        # Emit start event
        await self._emit_event("memory.system.started", "", "", "default", {
            "loaded_count": report.loaded_count,
            "quarantined_count": report.quarantined_count,
        })
        return report

    async def stop(self) -> None:
        """Persist all state and shutdown."""
        if not self._started:
            return
        async with self._lock:
            # Persist all memories (re-seal integrity hash first)
            for tenant in self._tenants.values():
                for mid, (mem, prov) in tenant.memories.items():
                    try:
                        await self._persist(mem, prov)
                    except Exception as e:
                        logger.error("Failed to persist %s: %s", mid[:8], e)
            self._started = False
        await self._emit_event("memory.system.stopped", "", "", "default", {})
        logger.info("LivingMemoryManager stopped")

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def recovery_report(self) -> RecoveryReport:
        return self._recovery_report

    @property
    def metrics(self):
        return self._observability.metrics

    # ------------------------------------------------------------------
    # Core operations: remember / recall / reinforce
    # ------------------------------------------------------------------

    async def remember(
        self,
        memory: Memory,
        ctx: AuthorizationContext,
        provenance: Optional[Provenance] = None,
        idempotency_key: str = "",
        source: str = "",
        notes: str = "",
    ) -> Memory:
        """Create + persist a memory. Validates via immune system.

        Args:
            memory: The memory to store (state must be CREATED).
            ctx: Authorization context for the caller.
            provenance: Optional pre-built provenance chain. If omitted, a
                fresh chain with a single "create" entry is created.
            idempotency_key: Optional replay-protection key.
            source: Source description for provenance.
            notes: Free-form notes for provenance.

        Returns:
            The stored memory (with integrity_hash set).

        Raises:
            AuthorizationError: caller not authorized.
            ImmuneRejection: memory failed validation.
            ContradictionError: a contradiction was detected (not silently overwritten).
            ResourceLimitError: tenant at memory cap.
        """
        if not self._started:
            raise RuntimeError("Manager not started; call start() first")

        # Authorize write
        self._authz.authorize_write(ctx, memory.metadata.tenant_id)

        # Build provenance if missing
        if provenance is None:
            provenance = Provenance()
            provenance.append(
                actor_id=ctx.citizen_id,
                action="create",
                source=source,
                process="living_memory",
                notes=notes,
                correlation_id=memory.metadata.correlation_id or new_correlation_id(),
            )
        elif provenance.is_empty:
            provenance.append(
                actor_id=ctx.citizen_id, action="create",
                source=source, process="living_memory",
                correlation_id=memory.metadata.correlation_id or new_correlation_id(),
            )

        # Get tenant state (create if missing)
        tenant = self._get_or_create_tenant(memory.metadata.tenant_id)

        # Hold the lock through immune scan + state mutation + tenant.memories write
        # to close the TOCTOU window (V1: existing_ids check + write must be atomic).
        async with self._lock:
            # Check tenant memory cap (homeostasis)
            if len(tenant.memories) >= self._config.max_memories_per_tenant:
                raise ResourceLimitError(
                    f"Tenant {memory.metadata.tenant_id} at memory cap "
                    f"{self._config.max_memories_per_tenant}"
                )
            existing_ids = set(tenant.memories.keys())

            # Run immune system scan (synchronous — safe under lock)
            report = self._immune.scan(
                memory=memory,
                provenance=provenance,
                existing_ids=existing_ids,
                seen_idempotency_keys=self._seen_idempotency,
                idempotency_key=idempotency_key,
                expected_tenant_id=ctx.tenant_id,
            )
            if not report.accepted:
                # Quarantine the memory (don't silently drop)
                if self._config.quarantine_enabled:
                    try:
                        validate_transition(memory.state, MemoryState.QUARANTINED)
                        memory.state = MemoryState.QUARANTINED
                        tenant.memories[memory.id.value] = (memory, provenance)
                    except LifecycleError:
                        pass
                self._observability.metrics.inc("memory.rejections", reason=report.reason)
                raise ImmuneRejection(report.reason, report.to_dict())

            # Track idempotency key AFTER immune acceptance but BEFORE
            # contradiction check (V7: if contradiction is raised, the key
            # must NOT be tracked — otherwise legitimate retry is rejected).
            # → Move tracking to AFTER contradiction check (below).

            # Detect contradictions (semantic + procedural)
            if memory.type in (MemoryType.SEMANTIC, MemoryType.PROCEDURAL):
                candidates = self._build_contradiction_candidates(tenant, memory)
                contradictions = self._contradiction.detect(
                    new_memory_id=memory.id.value,
                    new_payload=memory.payload,
                    new_type=memory.type.value,
                    candidates=candidates,
                )
                for c in contradictions:
                    if c.conflict_type.value == "duplicate":
                        # Duplicate is not a hard error; record + skip
                        self._observability.metrics.inc("memory.duplicates")
                        continue
                    # Real conflict — store + raise
                    tenant.contradictions[c.id] = c
                    self._observability.metrics.inc("memory.contradictions_detected")
                    if len(tenant.contradictions) > self._config.max_contradictions_per_tenant:
                        raise ResourceLimitError("Too many unresolved contradictions")
                    # Defer event emission (we're holding the lock)
                    raise ContradictionError(c.description)

            # Transition CREATED → ACTIVE
            validate_transition(memory.state, MemoryState.ACTIVE)
            memory.state = MemoryState.ACTIVE
            memory.metadata.updated_at = now_utc()

            # Now safe to track idempotency key (memory is committed)
            if idempotency_key:
                self._seen_idempotency.add(idempotency_key)
                if len(self._seen_idempotency) > self._max_idempotency_cache:
                    self._seen_idempotency = set(list(self._seen_idempotency)[int(self._max_idempotency_cache * 0.2):])

            # Write to tenant state FIRST (under lock)
            tenant.memories[memory.id.value] = (memory, provenance)
            tenant.write_count += 1

        # Persist OUTSIDE the lock (best-effort; in-memory state is already authoritative)
        # If persist fails, in-memory state remains consistent (chaos tests verify this).
        try:
            await self._persist(memory, provenance)
        except Exception as e:
            # Roll back the in-memory write to keep state consistent
            async with self._lock:
                tenant.memories.pop(memory.id.value, None)
            raise

        # Update metrics + emit event (outside lock — these can yield)
        self._observability.metrics.inc("memory.writes", type=memory.type.value)
        self._observability.metrics.inc_gauge(
            "memory.active_count",
            labels={"type": memory.type.value, "state": memory.state.value},
        )
        await self._emit_event(
            "memory.created", memory.id.value,
            memory.type.value, memory.metadata.tenant_id,
            {"actor_id": ctx.citizen_id, "state": memory.state.value},
            correlation_id=memory.metadata.correlation_id,
        )

        # Optional auto-decay
        if self._config.auto_decay_on_write and \
           tenant.write_count % self._config.decay_interval_writes == 0:
            asyncio.create_task(self.decay(ctx))

        return memory

    async def recall(
        self,
        memory_id: str,
        ctx: AuthorizationContext,
    ) -> Optional[Memory]:
        """Retrieve a memory by ID. Touches access stats."""
        if not self._started:
            raise RuntimeError("Manager not started")
        # Validate ID format (immune check)
        try:
            MemoryID.from_string(memory_id)
        except ValidationError:
            return None

        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            entry = tenant.memories.get(memory_id)
            if not entry:
                return None
            mem, prov = entry
            # Authorize read
            self._authz.authorize_read(ctx, mem.metadata.tenant_id, mem.metadata.owner_id)
            # Skip terminal/inaccessible states
            if not MemoryState.is_accessible(mem.state):
                return None
            mem.metadata.touch()
            self._observability.metrics.inc("memory.reads", type=mem.type.value)
            await self._emit_event(
                "memory.recalled", mem.id.value,
                mem.type.value, mem.metadata.tenant_id,
                {"actor_id": ctx.citizen_id},
            )
            return mem

    async def recall_by_type(
        self,
        memory_type: MemoryType,
        ctx: AuthorizationContext,
        limit: int = 100,
        state_filter: Optional[MemoryState] = None,
    ) -> List[Memory]:
        """List memories of a given type."""
        if limit < 1 or limit > 1000:
            raise ValidationError("limit must be 1..1000")
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            results: List[Memory] = []
            for mem, _ in tenant.memories.values():
                if mem.type != memory_type:
                    continue
                if state_filter and mem.state != state_filter:
                    continue
                if not MemoryState.is_accessible(mem.state):
                    continue
                # Authorize read
                try:
                    self._authz.authorize_read(ctx, mem.metadata.tenant_id, mem.metadata.owner_id)
                except AuthorizationError:
                    continue
                results.append(mem)
                if len(results) >= limit:
                    break
            return results

    async def search(
        self,
        ctx: AuthorizationContext,
        memory_type: Optional[MemoryType] = None,
        tags: Optional[List[str]] = None,
        min_confidence: float = 0.0,
        min_importance: float = 0.0,
        limit: int = 50,
    ) -> List[Memory]:
        """Filtered retrieval (no vector search — pure metadata filter)."""
        if limit < 1 or limit > 1000:
            raise ValidationError("limit must be 1..1000")
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            results: List[Memory] = []
            for mem, _ in tenant.memories.values():
                if memory_type and mem.type != memory_type:
                    continue
                if not MemoryState.is_accessible(mem.state):
                    continue
                if mem.metadata.confidence < min_confidence:
                    continue
                if mem.metadata.importance < min_importance:
                    continue
                if tags:
                    md_tags = set(mem.metadata.tags)
                    if not set(tags).issubset(md_tags):
                        continue
                try:
                    self._authz.authorize_read(ctx, mem.metadata.tenant_id, mem.metadata.owner_id)
                except AuthorizationError:
                    continue
                results.append(mem)
            # Sort by importance desc, then confidence desc
            results.sort(
                key=lambda m: (-m.metadata.importance, -m.metadata.confidence)
            )
            return results[:limit]

    async def reinforce(
        self,
        memory_id: str,
        ctx: AuthorizationContext,
        delta: float = 0.1,
        notes: str = "",
    ) -> Optional[Memory]:
        """Reinforce a memory: bump confidence + importance."""
        if not 0.0 <= delta <= 1.0:
            raise ValidationError("delta must be in [0, 1]")
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            entry = tenant.memories.get(memory_id)
            if not entry:
                return None
            mem, prov = entry
            self._authz.authorize_read(ctx, mem.metadata.tenant_id, mem.metadata.owner_id)
            self._authz.authorize_capability(
                ctx, MemoryCapability.MEMORY_REINFORCE, mem.metadata.tenant_id
            )
            # State must be accessible
            if not MemoryState.is_accessible(mem.state):
                raise LifecycleError(
                    f"Cannot reinforce memory in state {mem.state.value}"
                )
            # Apply reinforcement
            old_state = mem.state
            mem.metadata.reinforce(delta)
            # Transition to REINFORCED (allowed from ACTIVE/REINFORCED/CONSOLIDATED/DECAYING)
            try:
                validate_transition(mem.state, MemoryState.REINFORCED)
                mem.state = MemoryState.REINFORCED
            except LifecycleError:
                # Stay in current state if transition not allowed
                pass
            prov.append(
                actor_id=ctx.citizen_id, action="reinforce",
                process="living_memory", notes=notes or f"reinforce +{delta}",
                correlation_id=mem.metadata.correlation_id,
            )
            # Persist
            await self._persist(mem, prov)
            self._observability.metrics.inc("memory.reinforcements", type=mem.type.value)
            await self._emit_event(
                "memory.reinforced", mem.id.value,
                mem.type.value, mem.metadata.tenant_id,
                {"actor_id": ctx.citizen_id, "delta": delta,
                 "old_state": old_state.value, "new_state": mem.state.value},
            )
            return mem

    # ------------------------------------------------------------------
    # Consolidation + Decay
    # ------------------------------------------------------------------

    async def consolidate(
        self,
        ctx: AuthorizationContext,
        memory_ids: Optional[List[str]] = None,
    ) -> ConsolidationResult:
        """Run a consolidation pass.

        If memory_ids is None, scans all memories in the caller's tenant.
        """
        self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_CONSOLIDATE)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            memories = []
            for mid, (mem, _) in tenant.memories.items():
                if memory_ids and mid not in memory_ids:
                    continue
                if not MemoryState.is_accessible(mem.state):
                    continue
                memories.append(mem)
        # Score candidates
        result = self._consolidation.score_candidates(memories)
        # Apply transitions for eligible candidates
        for cand in result.candidates:
            if not cand.eligible:
                continue
            if not self._consolidation.eligible_for_transition(cand):
                continue
            async with self._lock:
                entry = tenant.memories.get(cand.memory_id)
                if not entry:
                    continue
                mem, prov = entry
                # Transition ACTIVE/REINFORCED/DECAYING → CONSOLIDATING → CONSOLIDATED
                try:
                    validate_transition(mem.state, MemoryState.CONSOLIDATING)
                    mem.state = MemoryState.CONSOLIDATING
                    validate_transition(mem.state, MemoryState.CONSOLIDATED)
                    mem.state = MemoryState.CONSOLIDATED
                    mem.metadata.updated_at = now_utc()
                    prov.append(
                        actor_id=ctx.citizen_id, action="consolidate",
                        process="consolidation_engine",
                        notes=f"score={cand.score:.3f}",
                        correlation_id=mem.metadata.correlation_id,
                    )
                    await self._persist(mem, prov)
                    self._observability.metrics.inc("memory.consolidations", type=mem.type.value)
                    await self._emit_event(
                        "memory.consolidated", mem.id.value,
                        mem.type.value, mem.metadata.tenant_id,
                        {"actor_id": ctx.citizen_id, "score": cand.score},
                    )
                except LifecycleError as e:
                    result.errors.append(f"{cand.memory_id}: {e}")
        return result

    async def decay(self, ctx: AuthorizationContext) -> DecayResult:
        """Run a decay pass."""
        self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_ARCHIVE)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            memories = [mem for mem, _ in tenant.memories.values()]
        # Score
        result = self._decay.score_candidates(memories)
        # Apply recommendations
        for cand in result.candidates:
            if cand.recommendation == "archive":
                async with self._lock:
                    entry = tenant.memories.get(cand.memory_id)
                    if not entry:
                        continue
                    mem, prov = entry
                    try:
                        validate_transition(mem.state, MemoryState.ARCHIVED)
                        mem.state = MemoryState.ARCHIVED
                        mem.metadata.updated_at = now_utc()
                        prov.append(
                            actor_id=ctx.citizen_id, action="archive",
                            process="decay_engine",
                            notes=f"decay_score={cand.decay_score:.3f}",
                            correlation_id=mem.metadata.correlation_id,
                        )
                        await self._persist(mem, prov)
                        self._observability.metrics.inc("memory.decays", action="archive")
                        await self._emit_event(
                            "memory.archived", mem.id.value,
                            mem.type.value, mem.metadata.tenant_id,
                            {"actor_id": ctx.citizen_id, "decay_score": cand.decay_score},
                        )
                    except LifecycleError as e:
                        result.errors.append(f"{cand.memory_id}: {e}")
            elif cand.recommendation == "forget":
                # Forget requires approval — defer to manager.forget()
                # Mark for review but don't auto-forget
                self._observability.metrics.inc("memory.decay_forget_pending")
        # Forced archival if over cap
        total_count = len(tenant.memories)
        if total_count > self._decay.config.max_total_memories:
            excess = total_count - self._decay.config.max_total_memories
            forced_ids = self._decay.select_forced_archive(memories, excess)
            for fid in forced_ids:
                async with self._lock:
                    entry = tenant.memories.get(fid)
                    if not entry:
                        continue
                    mem, prov = entry
                    try:
                        validate_transition(mem.state, MemoryState.ARCHIVED)
                        mem.state = MemoryState.ARCHIVED
                        mem.metadata.updated_at = now_utc()
                        prov.append(
                            actor_id=ctx.citizen_id, action="archive",
                            process="decay_engine_forced",
                            notes="forced archival due to capacity",
                            correlation_id=mem.metadata.correlation_id,
                        )
                        await self._persist(mem, prov)
                        result.forced_archive_count += 1
                    except LifecycleError:
                        pass
        tenant.last_decay_at = now_utc()
        return result

    # ------------------------------------------------------------------
    # Forget (governed, requires Founder approval)
    # ------------------------------------------------------------------

    async def forget(
        self,
        memory_id: str,
        ctx: AuthorizationContext,
        approval_id: Optional[str] = None,
        notes: str = "",
    ) -> bool:
        """Forget a memory. Requires Founder approval (Constitution Article 6).

        If approval_id is provided, it must reference a PENDING/approved
        ApprovalRequest in the ApprovalGate (if available). If the approval
        gate is unavailable, the caller must be Founder.
        """
        self._authz.authorize_delete(ctx, ctx.tenant_id)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            entry = tenant.memories.get(memory_id)
            if not entry:
                return False
            mem, prov = entry

            # Check approval (if approval gate configured)
            if self._approval_gate is not None:
                if not approval_id:
                    raise AuthorizationError(
                        "Forget requires explicit approval_id (constitution Article 6)"
                    )
                req = self._approval_gate.get_request(approval_id)
                if not req:
                    raise AuthorizationError(f"Approval {approval_id[:8]} not found")
                from .governance_compat import is_approval_approved  # local helper
                if not is_approval_approved(req):
                    raise AuthorizationError(
                        f"Approval {approval_id[:8]} not approved "
                        f"(status={req.status.value})"
                    )
            elif not ctx.is_founder:
                raise AuthorizationError(
                    "Forget without approval gate requires Founder rank"
                )

            # Transition to FORGOTTEN
            try:
                validate_transition(mem.state, MemoryState.FORGOTTEN)
                mem.state = MemoryState.FORGOTTEN
                mem.metadata.updated_at = now_utc()
                prov.append(
                    actor_id=ctx.citizen_id, action="forget",
                    process="living_memory", notes=notes,
                    correlation_id=mem.metadata.correlation_id,
                )
                await self._persist(mem, prov)
                # Remove from association graph
                graph = tenant.association_graph
                if graph is not None:
                    await graph.remove_all_for(memory_id)
                self._observability.metrics.inc("memory.forgets")
                await self._emit_event(
                    "memory.forgotten", mem.id.value,
                    mem.type.value, mem.metadata.tenant_id,
                    {"actor_id": ctx.citizen_id, "approval_id": approval_id or ""},
                )
                return True
            except LifecycleError as e:
                logger.error("Forget failed: %s", e)
                return False

    async def archive(
        self, memory_id: str, ctx: AuthorizationContext, notes: str = ""
    ) -> bool:
        """Manually archive a memory (reversible)."""
        self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_ARCHIVE)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            entry = tenant.memories.get(memory_id)
            if not entry:
                return False
            mem, prov = entry
            try:
                validate_transition(mem.state, MemoryState.ARCHIVED)
                mem.state = MemoryState.ARCHIVED
                mem.metadata.updated_at = now_utc()
                prov.append(
                    actor_id=ctx.citizen_id, action="archive",
                    process="living_memory", notes=notes,
                    correlation_id=mem.metadata.correlation_id,
                )
                await self._persist(mem, prov)
                self._observability.metrics.inc("memory.archive_manual")
                await self._emit_event(
                    "memory.archived", mem.id.value,
                    mem.type.value, mem.metadata.tenant_id,
                    {"actor_id": ctx.citizen_id, "manual": True},
                )
                return True
            except LifecycleError:
                return False

    async def restore(
        self, memory_id: str, ctx: AuthorizationContext, notes: str = ""
    ) -> bool:
        """Restore a memory from ARCHIVED → ACTIVE."""
        self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_RESTORE)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            entry = tenant.memories.get(memory_id)
            if not entry:
                return False
            mem, prov = entry
            try:
                validate_transition(mem.state, MemoryState.ACTIVE)
                mem.state = MemoryState.ACTIVE
                mem.metadata.updated_at = now_utc()
                prov.append(
                    actor_id=ctx.citizen_id, action="restore",
                    process="living_memory", notes=notes,
                    correlation_id=mem.metadata.correlation_id,
                )
                await self._persist(mem, prov)
                self._observability.metrics.inc("memory.restores")
                await self._emit_event(
                    "memory.restored", mem.id.value,
                    mem.type.value, mem.metadata.tenant_id,
                    {"actor_id": ctx.citizen_id},
                )
                return True
            except LifecycleError:
                return False

    # ------------------------------------------------------------------
    # Contradiction resolution
    # ------------------------------------------------------------------

    async def list_contradictions(
        self, ctx: AuthorizationContext, status: Optional[ContradictionStatus] = None
    ) -> List[ContradictionRecord]:
        """List contradictions in the caller's tenant."""
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            if status:
                return [c for c in tenant.contradictions.values() if c.status == status]
            return list(tenant.contradictions.values())

    async def resolve_contradiction(
        self,
        contradiction_id: str,
        ctx: AuthorizationContext,
        resolution: ContradictionStatus,
        approval_id: Optional[str] = None,
        notes: str = "",
    ) -> bool:
        """Resolve a contradiction. Requires Founder approval (Article 6)."""
        self._authz.authorize_capability(
            ctx, MemoryCapability.MEMORY_RESOLVE_CONTRADICTION
        )
        if resolution not in (
            ContradictionStatus.RESOLVED_KEEP_OLD,
            ContradictionStatus.RESOLVED_KEEP_NEW,
            ContradictionStatus.RESOLVED_MERGE,
            ContradictionStatus.RESOLVED_INVALID,
        ):
            raise ValidationError(f"Invalid resolution status: {resolution}")

        # Check approval (if gate configured)
        if self._approval_gate is not None:
            if not approval_id:
                raise AuthorizationError("Resolve requires explicit approval_id")
            req = self._approval_gate.get_request(approval_id)
            if not req:
                raise AuthorizationError(f"Approval {approval_id[:8]} not found")
            from .governance_compat import is_approval_approved
            if not is_approval_approved(req):
                raise AuthorizationError(
                    f"Approval {approval_id[:8]} not approved"
                )
        elif not ctx.is_founder:
            raise AuthorizationError(
                "Resolve without approval gate requires Founder rank"
            )

        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            c = tenant.contradictions.get(contradiction_id)
            if not c:
                return False
            if c.status != ContradictionStatus.DETECTED:
                return False  # already resolved
            c.status = resolution
            c.resolved_at = now_utc()
            c.resolved_by = ctx.citizen_id
            c.resolution_notes = notes

            # Apply resolution side-effects
            if resolution == ContradictionStatus.RESOLVED_KEEP_OLD:
                # Supersede the new memory (memory_b)
                entry_b = tenant.memories.get(c.memory_b_id)
                if entry_b:
                    mem_b, prov_b = entry_b
                    try:
                        validate_transition(mem_b.state, MemoryState.ARCHIVED)
                        mem_b.state = MemoryState.ARCHIVED
                        prov_b.append(
                            actor_id=ctx.citizen_id, action="archive",
                            process="contradiction_resolver",
                            notes=f"contradiction {contradiction_id} keep_old",
                            correlation_id=mem_b.metadata.correlation_id,
                        )
                        await self._persist(mem_b, prov_b)
                    except LifecycleError:
                        pass
            elif resolution == ContradictionStatus.RESOLVED_KEEP_NEW:
                entry_a = tenant.memories.get(c.memory_a_id)
                if entry_a:
                    mem_a, prov_a = entry_a
                    try:
                        validate_transition(mem_a.state, MemoryState.ARCHIVED)
                        mem_a.state = MemoryState.ARCHIVED
                        prov_a.append(
                            actor_id=ctx.citizen_id, action="archive",
                            process="contradiction_resolver",
                            notes=f"contradiction {contradiction_id} keep_new",
                            correlation_id=mem_a.metadata.correlation_id,
                        )
                        await self._persist(mem_a, prov_a)
                    except LifecycleError:
                        pass

            self._observability.metrics.inc("memory.contradictions_resolved")
            await self._emit_event(
                "memory.contradiction_resolved", "",
                "", ctx.tenant_id,
                {
                    "contradiction_id": contradiction_id,
                    "resolution": resolution.value,
                    "actor_id": ctx.citizen_id,
                },
            )
            return True

    # ------------------------------------------------------------------
    # Associations
    # ------------------------------------------------------------------

    async def associate(
        self,
        source_id: str,
        target_id: str,
        ctx: AuthorizationContext,
        relationship: str = "related",
        weight: float = 0.5,
        bidirectional: bool = False,
        notes: str = "",
    ) -> AssociationMemory:
        """Create an association edge between two memories."""
        self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_ASSOCIATE)
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            # Both memories must exist
            if source_id not in tenant.memories or target_id not in tenant.memories:
                raise ValidationError(
                    "Both source and target memories must exist for association"
                )
        edge = AssociationMemory.create(
            source_id=source_id, target_id=target_id,
            relationship=relationship, weight=weight,
            bidirectional=bidirectional,
            owner_id=ctx.citizen_id,
            tenant_id=ctx.tenant_id,
            correlation_id=new_correlation_id(),
        )
        # Build provenance
        prov = Provenance()
        prov.append(
            actor_id=ctx.citizen_id, action="associate",
            process="living_memory", notes=notes,
            correlation_id=edge.metadata.correlation_id,
        )
        # Run immune scan on the association memory
        report = self._immune.scan(
            memory=edge, provenance=prov,
            existing_ids=set(tenant.memories.keys()),
            expected_tenant_id=ctx.tenant_id,
        )
        if not report.accepted:
            raise ImmuneRejection(report.reason, report.to_dict())
        # Add to association graph
        graph = tenant.association_graph
        assoc_edge = AssociationEdge(
            source_id=source_id, target_id=target_id,
            relationship=relationship, weight=weight,
            bidirectional=bidirectional, created_by=ctx.citizen_id,
            correlation_id=edge.metadata.correlation_id,
        )
        await graph.add_edge(assoc_edge)
        # Persist association memory
        validate_transition(edge.state, MemoryState.ACTIVE)
        edge.state = MemoryState.ACTIVE
        async with self._lock:
            tenant.memories[edge.id.value] = (edge, prov)
        await self._persist(edge, prov)
        self._observability.metrics.inc("memory.associations")
        await self._emit_event(
            "memory.associated", edge.id.value,
            MemoryType.ASSOCIATIVE.value, ctx.tenant_id,
            {
                "actor_id": ctx.citizen_id,
                "source_id": source_id, "target_id": target_id,
                "relationship": relationship, "weight": weight,
            },
        )
        return edge

    async def neighbors(
        self, memory_id: str, ctx: AuthorizationContext,
        relationship: str = "", min_weight: float = 0.0,
        direction: str = "out",
    ) -> List[AssociationEdge]:
        """Get direct neighbors of a memory."""
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        # Authorize read on the source memory first
        async with self._lock:
            entry = tenant.memories.get(memory_id)
            if entry:
                mem = entry[0]
                self._authz.authorize_read(ctx, mem.metadata.tenant_id, mem.metadata.owner_id)
        return await tenant.association_graph.neighbors(
            memory_id, relationship=relationship,
            min_weight=min_weight, direction=direction,
        )

    async def traverse(
        self, memory_id: str, ctx: AuthorizationContext,
        max_depth: Optional[int] = None,
        min_weight: float = 0.0, relationship: str = "",
        max_results: int = 100,
    ) -> List[Tuple[str, int, float]]:
        """BFS traversal of associations.

        V5: Requires memory.read capability. Banned users and zero-capability
        contexts are denied — traversal leaks memory IDs and relationship metadata.
        """
        # V5: authorize read on the source memory
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            entry = tenant.memories.get(memory_id)
            if entry:
                mem = entry[0]
                self._authz.authorize_read(
                    ctx, mem.metadata.tenant_id, mem.metadata.owner_id,
                )
            else:
                # Even if memory doesn't exist, require read capability to traverse
                self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_READ)
        return await tenant.association_graph.traverse(
            memory_id, max_depth=max_depth, min_weight=min_weight,
            relationship=relationship, max_results=max_results,
        )

    # ------------------------------------------------------------------
    # Working memory (separate per-tenant per-owner store)
    # ------------------------------------------------------------------

    async def working_put(
        self, key: str, value: Any, ctx: AuthorizationContext,
        priority: int = 5, ttl_seconds: Optional[int] = None,
    ) -> Any:
        """Put an item in the caller's working memory.

        V8: Requires memory.write capability (fail-closed).
        """
        self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_WRITE)
        wm = self._get_or_create_working(ctx.tenant_id, ctx.citizen_id)
        item = await wm.put(key, value, priority=priority, ttl_seconds=ttl_seconds)
        self._observability.metrics.inc("memory.working.writes")
        return item

    async def working_get(self, key: str, ctx: AuthorizationContext) -> Any:
        """V8: Requires memory.read capability."""
        self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_READ)
        wm = self._get_or_create_working(ctx.tenant_id, ctx.citizen_id)
        v = await wm.get(key)
        self._observability.metrics.inc("memory.working.reads")
        return v

    async def working_remove(self, key: str, ctx: AuthorizationContext) -> bool:
        """V8: Requires memory.write capability (removal is a write operation)."""
        self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_WRITE)
        wm = self._get_or_create_working(ctx.tenant_id, ctx.citizen_id)
        return await wm.remove(key)

    async def working_snapshot(self, ctx: AuthorizationContext) -> Dict[str, Any]:
        """V8: Requires memory.read capability."""
        self._authz.authorize_capability(ctx, MemoryCapability.MEMORY_READ)
        wm = self._get_or_create_working(ctx.tenant_id, ctx.citizen_id)
        return await wm.snapshot()

    # ------------------------------------------------------------------
    # Inspect / explain
    # ------------------------------------------------------------------

    async def inspect(
        self, memory_id: str, ctx: AuthorizationContext
    ) -> Optional[Dict[str, Any]]:
        """Full dump of a memory + provenance + associations.

        V6: Returns None if the memory is in an inaccessible state (ARCHIVED,
        FORGOTTEN, QUARANTINED). This prevents leaking FORGOTTEN memory payloads.
        """
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            entry = tenant.memories.get(memory_id)
            if not entry:
                return None
            mem, prov = entry
            # V6: state gate — only accessible states are inspectable
            if not MemoryState.is_accessible(mem.state):
                return None
            self._authz.authorize_read(ctx, mem.metadata.tenant_id, mem.metadata.owner_id)
            neighbors = await tenant.association_graph.neighbors(memory_id)
            return {
                "memory": mem.to_dict(),
                "provenance": prov.to_dict(),
                "neighbors": [n.to_dict() for n in neighbors],
            }

    async def explain(
        self, memory_id: str, ctx: AuthorizationContext
    ) -> Optional[str]:
        """Human-readable provenance chain explanation.

        V6: Returns None if memory is in an inaccessible state.
        """
        tenant = self._get_or_create_tenant(ctx.tenant_id)
        async with self._lock:
            entry = tenant.memories.get(memory_id)
            if not entry:
                return None
            mem, prov = entry
            # V6: state gate
            if not MemoryState.is_accessible(mem.state):
                return None
            self._authz.authorize_read(ctx, mem.metadata.tenant_id, mem.metadata.owner_id)
        lines = [
            f"Memory {mem.id.value[:8]} ({mem.type.value}, state={mem.state.value})",
            f"Created by: {prov.creator[:8] if prov.creator else '<unknown>'} "
            f"at {prov.created_at or '<unknown>'}",
            f"Version: {prov.current_version}",
            f"Integrity hash: {mem.integrity_hash[:16]}...",
            f"Confidence: {mem.metadata.confidence:.3f}",
            f"Importance: {mem.metadata.importance:.3f}",
            f"Access count: {mem.metadata.access_count}",
            f"Reinforcements: {mem.metadata.reinforcement_count}",
            "",
            "Provenance chain:",
        ]
        for i, e in enumerate(prov.chain):
            lines.append(
                f"  [{i+1}] {e.action} by {e.actor_id[:8]} "
                f"at {e.timestamp} | source={e.source or '-'} "
                f"| process={e.process or '-'}"
            )
            if e.notes:
                lines.append(f"      notes: {e.notes}")
        chain_ok = prov.verify_chain()
        lines.append("")
        lines.append(f"Chain verification: {'OK' if chain_ok else 'BROKEN'}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Stats / health
    # ------------------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        return {
            "started": self._started,
            "persistence_durable": self._persistence.is_durable,
            "tenants": {tid: t.to_dict() for tid, t in self._tenants.items()},
            "metrics": self._observability.snapshot(),
            "recovery": self._recovery_report.to_dict(),
        }

    async def get_stats(self) -> Dict[str, Any]:
        return await self.health_check()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create_tenant(self, tenant_id: str) -> TenantState:
        if tenant_id not in self._tenants:
            # V4: enforce tenant cap to prevent DoS via unbounded tenant creation
            if len(self._tenants) >= self._config.max_tenants:
                raise ResourceLimitError(
                    f"Tenant cap reached: {self._config.max_tenants} (tenant_id={tenant_id})"
                )
            self._tenants[tenant_id] = TenantState(
                tenant_id=tenant_id,
                association_graph=AssociationGraph(tenant_id=tenant_id),
            )
        return self._tenants[tenant_id]

    def _get_or_create_working(self, tenant_id: str, owner_id: str) -> WorkingMemory:
        key = f"{tenant_id}|{owner_id}"
        if key not in self._working_memories:
            # V4: enforce working memory cap
            if len(self._working_memories) >= self._config.max_working_memories:
                raise ResourceLimitError(
                    f"Working memory cap reached: {self._config.max_working_memories}"
                )
            self._working_memories[key] = WorkingMemory(
                tenant_id=tenant_id, owner_id=owner_id,
            )
        return self._working_memories[key]

    async def _persist(self, memory: Memory, provenance: Provenance) -> None:
        """Re-seal integrity hash (post-mutation) and persist atomically.

        This is the single point through which all persistence flows.
        It guarantees that the stored integrity_hash matches the in-memory
        state, so recovery verify_integrity() passes.
        """
        memory.integrity_hash = self._immune.compute_integrity_hash(memory, provenance)
        await self._persistence.save(memory, provenance)

    def _build_contradiction_candidates(
        self, tenant: TenantState, new_memory: Memory
    ) -> List[tuple]:
        """Build (memory_id, payload, type, source_authority) tuples for detector."""
        candidates = []
        for mid, (mem, _) in tenant.memories.items():
            if mem.type != new_memory.type:
                continue
            if not MemoryState.is_accessible(mem.state):
                continue
            auth = 0.5
            if new_memory.type == MemoryType.SEMANTIC:
                auth = float(mem.payload.get("source_authority", 0.5))
            candidates.append((mid, mem.payload, mem.type.value, auth))
        return candidates

    async def _emit_event(
        self, event_type: str, memory_id: str, memory_type: str,
        tenant_id: str, data: Dict[str, Any],
        correlation_id: str = "",
    ) -> None:
        event = MemoryEvent(
            type=event_type, memory_id=memory_id, memory_type=memory_type,
            tenant_id=tenant_id, data=data, correlation_id=correlation_id,
        )
        await self._observability.emit(event)


__all__ = ["LivingMemoryManager", "LivingMemoryConfig", "TenantState"]
