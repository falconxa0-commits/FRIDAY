# FRIDAY AGE V — MILESTONE 3 MEMORY ARCHITECTURE

## Design Inspiration: Biological Memory Systems

The Living Memory system is inspired by (but not a literal simulation of)
biological memory. Each architectural element maps to a biological
counterpart, providing a recognizable mental model without constraining
the implementation.

| Biological Concept | Implementation | Module |
|--------------------|----------------|--------|
| Encoding (information enters memory) | `Memory` + `MemoryMetadata` + `Provenance.append(action="create")` | `base.py`, `provenance.py` |
| Short-term / Working memory | `WorkingMemory` (bounded, TTL, priority-ordered, LRU) | `working.py` |
| Episodic memory (events + context) | `EpisodicMemory` (event_type, actor, outcome, related_episodes) | `episodic.py` |
| Semantic memory (facts) | `SemanticMemory` (subject.predicate → value, source authority) | `semantic.py` |
| Procedural memory (skills/workflows) | `ProceduralMemory` (steps, versioning, success rates) | `procedural.py` |
| Associative memory (links) | `AssociationGraph` (weighted directed edges, bounded) | `association.py` |
| Consolidation (STM → LTM promotion) | `ConsolidationEngine` (6-signal scoring: importance, confidence, repetition, recency, source authority, success rate) | `consolidation.py` |
| Decay (forgetting) | `DecayEngine` (time + reinforcement resistance + age cap + forced eviction) | `decay.py` |
| Retrieval (reconstruction from context) | `manager.recall()`, `manager.search()`, `manager.traverse()` | `manager.py` |
| Association (linking memories) | `manager.associate()`, `AssociationGraph.add_edge()` | `manager.py`, `association.py` |
| Reconsolidation (retrieved memories can be updated) | `manager.reinforce()` (updates confidence + importance) | `manager.py` |
| Forgetting (governed deletion) | `manager.forget()` (requires Founder approval or approval gate) | `manager.py` |
| Homeostasis (bounded resources) | Capacity caps on tenants, memories, edges, working memory, idempotency cache | `manager.py`, `working.py`, `association.py` |
| Immune system (reject malicious/corrupted memory) | `MemoryImmuneSystem` (10 validators) | `immune.py` |
| Provenance (knowing where info came from) | `Provenance` (hash-chained entries with actor/action/source/process/timestamp) | `provenance.py` |
| Contradiction detection (no silent overwrite) | `ContradictionDetector` (5 conflict types: predicate, negation, numerical, procedural, source-authority) | `contradiction.py` |
| Recovery (survive crashes) | `JSONFilePersistence` (atomic writes), `RecoveryReport` (quarantine) | `persistence.py` |

## Layered Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    LivingMemoryManager (facade)                     │
│                  remember / recall / reinforce /                     │
│                  consolidate / decay / associate /                  │
│                  forget / archive / restore /                       │
│                  inspect / explain / traverse                       │
└─────────────────────────────────────────────────────────────────────┘
         │           │           │           │           │
         ▼           ▼           ▼           ▼           ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ Working  │ │ Episodic │ │ Semantic │ │ Procedure│ │Associative│
   │ Memory   │ │  Memory  │ │  Memory  │ │  Memory  │ │  Memory   │
   └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘
         │           │           │           │           │
         └───────────┴───────────┴───────────┴───────────┘
                              │
         ┌─────────────────────┴─────────────────────┐
         ▼                                           ▼
   ┌───────────┐    ┌──────────────┐    ┌──────────────────┐
   │   Authz   │◄──►│ ImmuneSystem │◄──►│   Provenance     │
   │  (gate)   │    │ (validators) │    │  (hash chain)    │
   └───────────┘    └──────────────┘    └──────────────────┘
         │                                           │
         ▼                                           ▼
   ┌───────────────────┐                  ┌──────────────────┐
   │ Contradiction Det.│                  │   Persistence     │
   │ (5 conflict types)│                  │ (InMem/JSON/Redis)│
   └───────────────────┘                  └──────────────────┘
         │                                           │
         ▼                                           ▼
   ┌───────────────────┐                  ┌──────────────────┐
   │Consolidation Eng. │                  │   Observability  │
   │(6-signal scoring) │                  │ (metrics+events) │
   └───────────────────┘                  └──────────────────┘
         │
         ▼
   ┌───────────────────┐
   │   Decay Engine    │
   │(time+reinforcement)│
   └───────────────────┘
```

## Memory Lifecycle State Machine

```
                                ┌──────────────┐
                                │   CREATED    │
                                └──────┬───────┘
                                       │
                                       ▼
                                ┌──────────────┐
                  ┌──────────────│   ACTIVE     │
                  │              └──────┬───────┘
                  │                     │
                  ▼                     ▼
            ┌──────────────┐    ┌──────────────┐
            │ QUARANTINED  │    │  REINFORCED  │
            └──────┬───────┘    └──────┬───────┘
                   │                    │
                   │                    ▼
                   │            ┌──────────────┐
                   │            │ CONSOLIDATING│
                   │            └──────┬───────┘
                   │                   │
                   │                   ▼
                   │            ┌──────────────┐
                   │            │ CONSOLIDATED │
                   │            └──────┬───────┘
                   │                   │
                   │                   ▼
                   │            ┌──────────────┐
                   │            │   DECAYING   │
                   │            └──────┬───────┘
                   │                   │
                   ▼                   ▼
            ┌──────────────┐    ┌──────────────┐
            │  FORGOTTEN   │    │   ARCHIVED   │ (restorable)
            │  (terminal)  │    └──────────────┘
            └──────────────┘
```

## Provenance Chain Structure

Each persistent memory carries a `Provenance` chain. Each entry is hash-
linked to the previous entry, enabling tamper detection:

```
Entry 1 (create):
  actor_id: "founder-00000001"
  action: "create"
  source: "test"
  process: "living_memory"
  timestamp: "2026-08-13T..."
  parent_hash: "" (genesis)
  entry_hash: SHA256(actor|action|timestamp|source|process|parent_hash|notes|correlation_id)

Entry 2 (reinforce):
  actor_id: "founder-00000001"
  action: "reinforce"
  parent_hash: <entry 1's entry_hash>
  entry_hash: SHA256(...)

Entry 3 (consolidate):
  actor_id: "founder-00000001"
  action: "consolidate"
  parent_hash: <entry 2's entry_hash>
  entry_hash: SHA256(...)
```

`verify_chain()` walks the chain and validates every hash. Any tampering
(entry content changed, parent_hash altered) breaks the chain.

## Authorization Model

Capability-based, fail-closed. Three layers of defense:

1. **`AuthorizationGate` (authz.py)** — rank + capability + tenant + ban checks
2. **`LivingMemoryManager` (manager.py)** — additional Founder-rank requirement
   for destructive operations (forget, resolve_contradiction)
3. **`ApprovalGate` (Age V governance)** — explicit human approval for
   irreversible operations

| Operation | Required Capability | Required Rank | Approval Gate |
|-----------|---------------------|----------------|---------------|
| recall | memory.read | any authenticated | — |
| remember | memory.write | Worker+ (20) | — |
| reinforce | memory.reinforce | any authenticated | — |
| consolidate | memory.consolidate | any authenticated | — |
| decay (archive) | memory.archive | any authenticated | — |
| associate | memory.associate | any authenticated | — |
| archive | memory.archive | any authenticated | — |
| restore | memory.restore | any authenticated | — |
| forget | memory.forget | Governor+ (60) OR Founder | optional |
| resolve_contradiction | memory.resolve_contradiction | Founder (100) OR approval gate | required |

## Resource Homeostasis Bounds

| Resource | Cap | Default |
|----------|-----|---------|
| Working memory capacity | 1 .. 65536 | 64 |
| Working memory value size | — | 8 KiB |
| Association graph max_degree | ≥1 | 64 |
| Association graph max_traversal_depth | ≥1 | 8 |
| Association graph max_edges | ≥1 | 100,000 |
| Tenant max_memories_per_tenant | ≥1 | 10,000 |
| Tenant max_contradictions_per_tenant | ≥1 | 1,000 |
| Manager max_tenants | ≥1 | 1,000 |
| Manager max_working_memories | ≥1 | 10,000 |
| Manager idempotency cache | bounded | 10,000 (drops oldest 20% when full) |
| Memory payload size | — | 256 KiB hard cap |
| Memory metadata tags | — | 64 |
| Provenance chain length | — | 1,000 entries |
| Observability event buffer | bounded | 5,000 (drops oldest 10% when full) |

## Integration with Age IV / Age V

The Living Memory system composes (does NOT reimplement) with:

- `core/civilization/citizen.py` — `Citizen`, `CitizenRank`, `CitizenRegistry`
- `core/civilization/identity.py` — `IdentityEngine`
- `core/civilization/reputation.py` — `ReputationSystem`
- `core/civilization/manager.py` — `CivilizationManager`
- `core/governance/constitution.py` — Articles 4 (Memory Governance), 6 (Reversibility), 7 (Privacy)
- `core/governance/policy.py` — `PolicyEngine`
- `core/governance/approval.py` — `ApprovalGate` (Founder override + council voting)
- `core/runtime/v5/distributed_event_bus.py` — `DistributedEventBus` (event emission)
- `core/runtime/v5/distributed_task_queue.py` — `DistributedTaskQueue` (future: scheduled decay/consolidation)
- `core/runtime/v5/circuit_breaker.py` — `CircuitBreaker` (future: persistence failure protection)
- `core/ledger.py` — `ActionLedger` (audit chain integration point)

## Extensibility Points

1. **New memory types** — extend `Memory` base class, add to `MemoryType` enum
2. **New persistence backends** — implement `PersistenceAdapter` ABC
3. **New immune validators** — add methods to `MemoryImmuneSystem`
4. **New contradiction types** — extend `ContradictionType` enum + detector logic
5. **New lifecycle states** — extend `MemoryState` enum + `ALLOWED_TRANSITIONS` table
6. **New consolidation signals** — extend `ConsolidationConfig` weights
7. **New decay policies** — extend `DecayConfig` parameters
8. **New association relationships** — use any string as `relationship` field

## What This Architecture Achieves

- **Reliability**: hash-chained provenance + integrity hash + atomic persistence
- **Security**: fail-closed authz + 10 immune validators + replay protection + tenant isolation
- **Bounded**: every resource has a cap; no unbounded growth possible
- **Auditable**: every mutation emits an event + appends to provenance chain
- **Governable**: destructive operations require Founder approval or approval gate
- **Recoverable**: atomic writes + corrupt-file quarantine + rollback on persist failure
- **Observable**: counters, gauges, histograms, lifecycle events, correlation IDs
- **Testable**: 100% mutation coverage; 232 tests across 6 test files
