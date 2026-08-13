# FRIDAY AGE V — MASTER ARCHITECTURE BOOK

**Document Type:** Architecture Design (No Implementation)  
**Date:** 2026-08-13  
**Status:** Design Phase — Consensus Achieved  
**Foundation:** Age IV v4.0.0 (Certified, Frozen)  
**Authors:** 24 Engineering Swarms + Chief Architect Council

---

## PART 1 — FOUNDER VISION

### 1.1 What Age V Is

Age V transforms FRIDAY from a single-process AI assistant platform into a **Living Intelligence Civilization** — a governed, extensible, distributed ecosystem where AI agents, memory, knowledge, tools, and workflows exist as citizens with identities, roles, permissions, and lifecycles.

Age V is NOT:
- A rewrite of Age IV
- A new codebase
- A framework replacement
- A protocol change

Age V IS:
- An extension of the Age IV runtime to distributed execution
- A civilization layer on top of the existing runtime services
- A governance system for autonomous agents
- A living memory system that evolves
- A knowledge graph that connects everything

### 1.2 Design Principles

1. **Extend, never replace** — Age IV code is immutable. Age V adds adapters, wrappers, and new modules.
2. **Governance before autonomy** — no agent acts without policy approval.
3. **Memory is alive** — memories consolidate, decay, and evolve.
4. **Everything is connected** — the knowledge graph links all entities.
5. **Security is zero-trust** — every interaction is authenticated, authorized, and audited.
6. **Evidence over assumption** — every claim is measurable.
7. **Civilization over tool** — FRIDAY is not a tool; it is a society of intelligent agents.

### 1.3 What Changes from Age IV

| Age IV (Single-Process) | Age V (Civilization) |
|--------------------------|----------------------|
| In-memory EventBus | Redis pub/sub + Federation |
| Single RuntimeContext | Per-tenant + Per-agent contexts |
| In-memory TaskQueue | Distributed Redis queue |
| SubprocessSandbox (local) | Container isolation |
| Single-agent execution | Multi-agent civilization |
| File-based persistence | Database + graph persistence |
| Single API token | RBAC + OAuth 2.0 |
| PromptShield (opt-in) | PromptShield (default ON) |
| Static knowledge base | Living knowledge graph |
| Manual planning | Autonomous planning engine |
| Flat integrations | Plugin marketplace with signing |
| CLI + Web + VS Code | Full client ecosystem |

---

## PART 2 — CIVILIZATION ARCHITECTURE

### 2.1 Civilization Model

The civilization is organized as a hierarchy:

```
Founder (human)
  └── High Council (AI executives)
       ├── Governor: Runtime
       ├── Governor: Memory
       ├── Governor: Knowledge
       ├── Governor: Security
       ├── Governor: Planning
       ├── Governor: Engineering
       └── Governor: Communication
            ├── Specialists (agents with specific roles)
            ├── Workers (task execution agents)
            └── Citizens (plugins, tools, models)
```

### 2.2 Roles and Permissions

| Role | Authority | Can Spawn | Can Approve | Can Modify Policy |
|------|-----------|-----------|-------------|-------------------|
| Founder | Absolute | High Council | Everything | Constitution |
| High Council | Executive | Governors | Department-level | Policies |
| Governor | Department | Specialists | Task-level | Standards |
| Specialist | Domain | Workers | Domain tasks | Procedures |
| Worker | Execution | None | None | None |
| Citizen | Plugin/Tool | None | None | None |

### 2.3 Lifecycle

Every citizen (agent, plugin, tool) has a lifecycle:

```
BIRTH (registration)
  → INITIALIZATION (capability assignment)
  → ACTIVE (execution)
  → DEGRADED (failure threshold reached)
  → RECOVERY (healing attempt)
  → RETIRED (graceful shutdown)
  → ARCHIVED (history preserved)
```

### 2.4 Communication Protocol

Agents communicate through:

1. **Event Bus** — async pub/sub for notifications
2. **Message Queue** — direct agent-to-agent messages
3. **Shared Memory** — knowledge graph for persistent state
4. **Approval Gate** — human-in-the-loop for critical actions

### 2.5 Reputation System

Every agent accumulates reputation based on:
- Task success rate
- Error frequency
- Resource efficiency
- Peer reviews
- Founder feedback

Reputation affects:
- Task priority (higher reputation = higher priority)
- Resource allocation (higher reputation = more resources)
- Autonomy level (higher reputation = less approval required)

---

## PART 3 — DISTRIBUTED RUNTIME

### 3.1 Runtime Evolution

Age IV RuntimeContext → Age V DistributedRuntime:

```python
class DistributedRuntime:
    """Extends RuntimeContext with distributed capabilities."""
    
    # Age IV (preserved)
    context: RuntimeContext  # local services
    
    # Age V (new)
    event_bus: RedisEventBus  # distributed pub/sub
    task_queue: DistributedTaskQueue  # Redis-backed
    scheduler: DistributedScheduler  # Celery/RQ
    federation: RuntimeFederation  # multi-node coordination
```

### 3.2 Event Bus

```
┌─────────────┐    pub/sub    ┌─────────────┐
│  Agent A    │──────────────→│  Redis       │
│  (Node 1)   │              │  Event Bus   │
└─────────────┘              └──────┬───────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
              │  Agent B  │  │  Agent C  │  │  Governor │
              │  (Node 2) │  │  (Node 1) │  │  (Node 3) │
              └───────────┘  └───────────┘  └───────────┘
```

**Protocol:**
- Events are JSON messages with type, source, payload, timestamp, correlation_id
- Subscriptions are pattern-based (`agent.*.completed`, `memory.*.updated`)
- Delivery is at-least-once (idempotent consumers required)
- Dead letter queue for failed deliveries

### 3.3 Task Queue

| Feature | Age IV | Age V |
|---------|--------|-------|
| Storage | In-memory | Redis |
| Priority | 4 levels | 4 levels + weighted fairness |
| Retry | Manual | Automatic with exponential backoff |
| Timeout | Per-task | Per-task + per-queue |
| Visibility | Single process | Cross-process |
| Dead letter | No | Yes (failed tasks preserved) |

### 3.4 Federation

Multiple FRIDAY nodes coordinate through Redis:

```
Node A (primary)
  ├── RuntimeContext (local)
  ├── Redis connection
  └── Federation protocol
  
Node B (worker)
  ├── RuntimeContext (local)
  ├── Redis connection
  └── Federation protocol
```

**Federation protocol:**
1. Node registers with Redis (`friday:nodes:<node_id>`)
2. Node subscribes to `friday:federation:*`
3. Heartbeat every 5 seconds
4. Tasks distributed via Redis queue
5. Results published via EventBus
6. Failover: if primary dies, workers detect heartbeat loss and elect new primary

### 3.5 Recovery

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Agent crash | Heartbeat timeout (15s) | Task re-queued, agent restarted |
| Node crash | Heartbeat timeout (30s) | Tasks redistributed to healthy nodes |
| Redis crash | Connection error | Fallback to in-memory (degraded mode) |
| Network partition | Split-brain detection | Quorum-based: majority partition continues |

---

## PART 4 — LIVING MEMORY

### 4.1 Memory Architecture

```
┌─────────────────────────────────────────┐
│           LIVING MEMORY SYSTEM            │
│                                          │
│  ┌─────────┐ ┌─────────┐ ┌───────────┐  │
│  │ Semantic │ │Episodic │ │ Working   │  │
│  │ Memory   │ │ Memory  │ │ Memory    │  │
│  │ (facts)  │ │(events) │ │(current)  │  │
│  └────┬─────┘ └────┬────┘ └─────┬─────┘ │
│       │              │            │       │
│       └──────┬───────┴────────────┘       │
│              │                            │
│  ┌───────────▼───────────┐                 │
│  │  Procedural Memory    │                 │
│  │  (skills, patterns)   │                 │
│  └───────────┬───────────┘                 │
│              │                            │
│  ┌───────────▼───────────┐                 │
│  │   Knowledge Graph     │                 │
│  │   (relationships)     │                 │
│  └───────────────────────┘                 │
└──────────────────────────────────────────┘
```

### 4.2 Memory Types

| Type | Purpose | Storage | Lifecycle |
|------|---------|---------|-----------|
| Semantic | Facts ("user likes Python") | Vector DB | Consolidate + decay |
| Episodic | Events ("at 3pm, user asked about weather") | Time-series DB | Compress over time |
| Working | Current context (conversation state) | In-memory | Cleared on session end |
| Procedural | Skills ("how to deploy FRIDAY") | Skill store | Versioned, deprecated |
| Knowledge Graph | Relationships between entities | Graph DB | Evolves continuously |

### 4.3 Memory Governance

- **Consolidation**: episodic memories compress into semantic memories after 24 hours
- **Decay**: memories that aren't recalled lose relevance score over time
- **Forgetting**: memories below threshold are archived (not deleted)
- **Governance**: Founder can pin memories (prevent decay) or force-forget
- **Privacy**: memories can be marked private (not shared across tenants)

### 4.4 Knowledge Graph

```
User ──owns──→ Project ──contains──→ File
  │                                  │
  │──asked──→ Question ──answered──→ Answer
  │                                  │
  │──assigned──→ Task ──completed──→ Result
  │                                  │
  └──prefers──→ Provider ──generates──→ Response
```

**Entities:**
- Users, Projects, Repositories, Files
- Tasks, Agents, Tools, Models
- Conversations, Memories, Decisions
- Policies, Workflows, Plugins

**Relationships:**
- owns, contains, depends_on, generates, completes
- prefers, assigned_to, reviewed_by, approved_by
- derives_from, supersedes, conflicts_with

**Operations:**
- Add entity/relationship
- Query by type, property, or traversal
- Infer new relationships (e.g., "if A depends on B and B is deprecated, A needs update")
- Evolve: merge duplicates, resolve conflicts, prune stale edges

---

## PART 5 — AUTONOMOUS PLANNING

### 5.1 Planning Pipeline

```
Mission (Founder input)
    ↓
Objective Decomposition (break into goals)
    ↓
Dependency Graph (map dependencies)
    ↓
Critical Path Analysis (find longest chain)
    ↓
Resource Allocation (assign agents + tools)
    ↓
Execution (parallel where possible)
    ↓
Verification (check results)
    ↓
Learning (record what worked)
    ↓
Reflection (improve next time)
```

### 5.2 Planning Intelligence

The planner uses:
- **Historical data**: past task durations, success rates
- **Agent capabilities**: which agents are best at which tasks
- **Resource availability**: current load, memory, tokens
- **Dependency analysis**: what must complete before what
- **Risk assessment**: probability of failure per task

### 5.3 Re-planning

When a task fails:
1. Diagnose failure (what went wrong)
2. Assess impact (what downstream tasks are affected)
3. Generate alternatives (3 options minimum)
4. Select best alternative (by confidence score)
5. Re-execute with adjusted plan
6. Record failure + recovery for learning

---

## PART 6 — WORKFLOW CIVILIZATION

### 6.1 Workflow as Intelligent Objects

Workflows are not just DAGs — they are first-class citizens with:
- Identity (UUID + version)
- Capabilities (what they can do)
- State (current execution state)
- History (past executions)
- Reputation (success rate)
- Permissions (who can trigger them)

### 6.2 Standard Workflows

| Workflow | Purpose | Steps |
|----------|---------|-------|
| Research | Investigate a topic | Search → Analyze → Synthesize → Report |
| Engineering | Implement a feature | Design → Code → Test → Review → Deploy |
| Security | Audit a system | Scan → Analyze → Report → Fix → Verify |
| Planning | Decompose a mission | Analyze → Decompose → Schedule → Execute |
| Deployment | Release software | Build → Test → Stage → Approve → Deploy → Verify |
| Learning | Learn from experience | Collect → Analyze → Pattern → Store → Apply |
| Audit | Verify compliance | Scan → Compare → Report → Remediate |

### 6.3 Workflow Features

- **Conditional nodes**: if/else branching based on results
- **Parallel execution**: independent nodes run simultaneously
- **Checkpoint recovery**: resume from last successful node
- **Rollback**: reverse completed nodes on failure
- **Human approval**: gate critical nodes with human approval
- **Timeout enforcement**: per-node and per-workflow timeouts
- **Retry with backoff**: automatic retry on transient failures

---

## PART 7 — GOVERNANCE

### 7.1 Constitution

The constitution is the highest-level policy that governs the civilization:

1. **Founder is sovereign** — the human founder has absolute authority
2. **No autonomous self-modification** — FRIDAY may propose changes but never apply them without approval
3. **Security is mandatory** — all actions pass through PolicyEngine
4. **Memory is governed** — no memory is created, modified, or deleted without policy
5. **Transparency** — all actions are logged to the audit chain
6. **Reversibility** — all state changes can be rolled back
7. **Privacy** — tenant data is isolated and never crosses boundaries

### 7.2 Policy Hierarchy

```
Constitution (immutable)
  ↓
High Council Policies (executive)
  ↓
Governor Standards (departmental)
  ↓
Specialist Procedures (domain)
  ↓
Worker Rules (execution)
```

### 7.3 Approval Gates

| Action | Required Approval |
|--------|-------------------|
| Deploy code | High Council |
| Modify memory | Governor: Memory |
| Execute workflow | Governor (auto for trusted workflows) |
| Spawn agent | Governor |
| Access tenant data | Founder or Governor: Security |
| Modify policy | Founder only |
| Retire agent | Governor |

---

## PART 8 — PLUGIN PLATFORM

### 8.1 Plugin Architecture

```
Plugin SDK (TypeScript + Python)
    ↓
Plugin Signing (cryptographic)
    ↓
Plugin Marketplace (discovery + distribution)
    ↓
Plugin Runtime (sandboxed execution)
    ↓
Capability Registry (permission management)
```

### 8.2 Plugin Lifecycle

1. **Author** — developer writes plugin using SDK
2. **Sign** — plugin is cryptographically signed
3. **Publish** — uploaded to marketplace
4. **Review** — security scan + community review
5. **Install** — downloaded and sandboxed
6. **Execute** — runs in SubprocessSandbox with capabilities
7. **Monitor** — performance + security monitored
8. **Update** — versioned updates with migration
9. **Retire** — gracefully removed, data preserved

### 8.3 Sandboxing

All plugins execute in `SubprocessSandbox` with:
- Process isolation (separate PID, separate memory)
- Resource limits (RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE)
- Environment isolation (whitelisted env vars only)
- Network policy (configurable per-plugin)
- Filesystem restrictions (only designated paths)
- Audit logging (every plugin action recorded)

---

## PART 9 — DEVELOPER PLATFORM

### 9.1 SDK Hierarchy

| SDK | Language | Purpose |
|-----|----------|---------|
| Python SDK | Python | Server-side integration |
| TypeScript SDK | TypeScript | Web/desktop integration |
| CLI SDK | Shell | Automation scripts |
| Mobile SDK | Dart/Kotlin | Mobile apps |

### 9.2 API Surface

| API | Protocol | Use Case |
|-----|----------|----------|
| REST | HTTP/JSON | CRUD operations |
| GraphQL | HTTP/GraphQL | Flexible queries |
| WebSocket | WS | Real-time updates |
| MCP | JSON-RPC/stdio | External AI agents |
| gRPC | HTTP/2 | High-performance internal |

### 9.3 Authentication

Age V moves from single-token to:
- **OAuth 2.0** for user authentication
- **JWT** for API tokens with scopes
- **API Keys** for service-to-service
- **RBAC** for permission management

---

## PART 10 — SECURITY BLUEPRINT

### 10.1 Zero-Trust Architecture

Every request is:
1. Authenticated (who are you?)
2. Authorized (what can you do?)
3. Audited (what did you do?)
4. Rate-limited (how fast are you doing it?)
5. Sandboxed (where can you execute?)

### 10.2 Threat Model

| Threat | Mitigation |
|--------|-----------|
| Prompt injection | PromptShield (default ON in Age V) |
| Plugin supply chain | Signing + sandbox + review |
| Data exfiltration | Network policy + audit |
| Privilege escalation | RBAC + capability checking |
| Lateral movement | Tenant isolation + network segmentation |
| Denial of service | Rate limiting + resource quotas |
| Memory poisoning | Memory governance + validation |
| Knowledge graph corruption | Graph integrity checks + rollback |

### 10.3 Security Evolution from Age IV

| Age IV | Age V |
|--------|-------|
| PromptShield opt-in | Default ON |
| CapabilitySandbox | SubprocessSandbox for all plugins |
| Single API token | OAuth 2.0 + JWT + RBAC |
| HMAC-SHA256 chain | Distributed tamper-proof log |
| AST scan for plugins | Signing + sandbox + review |

---

## PART 11 — INFRASTRUCTURE

### 11.1 Deployment Topology

```
┌─────────────────────────────────────────┐
│              Load Balancer               │
│                 (nginx)                  │
└──────────────┬──────────────────────────┘
               │
    ┌──────────┼──────────┐
    │          │          │
┌───▼───┐ ┌───▼───┐ ┌───▼───┐
│ Node  │ │ Node  │ │ Node  │
│   A   │ │   B   │ │   C   │
│(FRIDAY│ │(FRIDAY│ │(FRIDAY│
│ runtime)│ │runtime)│ │runtime)│
└───┬───┘ └───┬───┘ └───┬───┘
    │          │          │
    └──────────┼──────────┘
               │
    ┌──────────▼──────────┐
    │       Redis          │
    │  (events + queues)   │
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │    PostgreSQL +      │
    │    pgvector          │
    │  (persistence)       │
    └─────────────────────┘
```

### 11.2 Kubernetes

```yaml
# FRIDAY Age V Kubernetes deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: friday-runtime
spec:
  replicas: 3  # horizontal scaling
  template:
    spec:
      containers:
      - name: friday
        image: friday:v5.0.0
        resources:
          limits:
            memory: "1Gi"
            cpu: "1000m"
        env:
        - name: REDIS_URL
        - name: DATABASE_URL
        - name: FRIDAY_USE_RUNTIME
          value: "1"  # default ON in Age V
```

### 11.3 Observability

| Signal | Tool | Purpose |
|--------|------|---------|
| Metrics | Prometheus | Performance counters |
| Tracing | OpenTelemetry | Distributed request tracing |
| Logging | Structured JSON | Audit trail + debugging |
| Profiling | py-spy | CPU profiling |
| Memory | psutil | Memory tracking |
| Events | EventBus | Real-time system events |

---

## PART 12 — MIGRATION STRATEGY

### 12.1 Age IV → Age V Migration

**Rule: Age IV code never changes. Age V adds new modules alongside.**

```
core/                    ← Age IV (frozen)
core/runtime/            ← Age IV runtime (frozen)
core/runtime/v5/         ← Age V distributed runtime (NEW)
core/civilization/       ← Age V civilization layer (NEW)
core/memory/v5/          ← Age V living memory (NEW)
core/governance/         ← Age V governance (NEW)
core/planning/           ← Age V autonomous planning (NEW)
```

### 12.2 Migration Path

1. **Phase 1**: Redis-backed EventBus + TaskQueue (Age IV singletons delegate to Redis if available)
2. **Phase 2**: Per-tenant RuntimeContext (Age IV singleton becomes default tenant)
3. **Phase 3**: Living Memory (Age IV FridayMemory gets a persistence adapter)
4. **Phase 4**: Knowledge Graph (Age IV KnowledgeBase gets a graph adapter)
5. **Phase 5**: Civilization layer (new — no Age IV module to migrate)
6. **Phase 6**: Plugin marketplace (Age IV PluginRuntime gets signing + marketplace)
7. **Phase 7**: Client ecosystem (Age IV web/dashboard enhanced)

### 12.3 Backward Compatibility

```python
# Age IV code (unchanged, still works)
from core.ledger import get_ledger
ledger = get_ledger()

# Age V code (new, opt-in)
from core.runtime.v5.distributed_ledger import get_distributed_ledger
ledger = get_distributed_ledger()  # falls back to Age IV if Redis unavailable
```

---

## PART 13 — 12-MONTH DELIVERY PLAN

| Month | Phase | Deliverable | Dependency |
|-------|-------|-------------|------------|
| 1-2 | Distributed Foundation | Redis EventBus + TaskQueue + Federation | None |
| 3-4 | Multi-Tenant Platform | Per-tenant contexts + RBAC + OAuth | Phase 1 |
| 5-6 | Living Memory | Semantic + Episodic + Knowledge Graph | Phase 1 |
| 7-8 | Autonomous Planning | Goal decomposition + Critical path + Re-planning | Phase 2 |
| 9-10 | Plugin Marketplace | Signing + Sandbox + Discovery + SDK | Phase 2 |
| 11-12 | Client Ecosystem | Web dashboard + Desktop + Mobile + CLI v2 | All |

**Each phase is independently valuable. The system is useful after every milestone.**

---

## PART 14 — RISK REGISTER

| # | Risk | Probability | Impact | Mitigation |
|---|------|-------------|--------|------------|
| 1 | Redis single point of failure | Medium | Critical | Redis Sentinel + fallback to in-memory |
| 2 | Distributed state corruption | Low | Critical | Audit chain + rollback + backups |
| 3 | Performance regression from distribution | Medium | High | Benchmark-driven development |
| 4 | Security surface increase | High | Critical | Zero-trust + sandbox + audit |
| 5 | Backward compatibility break | Low | Critical | Feature flags + adapter patterns |
| 6 | Timeline slippage | High | Medium | Phased delivery, each phase valuable |
| 7 | Knowledge graph corruption | Low | High | Graph integrity checks + rollback |
| 8 | Agent coordination failures | Medium | Medium | Deadlock detection + timeout + retry |
| 9 | Memory bloat | Medium | Medium | Consolidation + decay + archival |
| 10 | Plugin supply chain attack | Medium | Critical | Signing + sandbox + review |

---

## PART 15 — ENGINEERING STANDARDS

### 15.1 Folder Structure

```
friday/
├── core/                    # Age IV (frozen)
│   ├── runtime/             # Age IV runtime (frozen)
│   │   ├── v5/              # Age V distributed runtime (NEW)
│   │   └── ...
│   ├── civilization/        # Age V civilization (NEW)
│   ├── governance/          # Age V governance (NEW)
│   ├── planning/            # Age V planning (NEW)
│   └── ...
├── api/
│   ├── v5/                  # Age V API routes (NEW)
│   └── ...
├── sdk/
│   ├── python/              # Age V Python SDK (NEW)
│   ├── typescript/          # Age V TypeScript SDK (NEW)
│   └── ...
├── tests/
│   ├── v5/                  # Age V tests (NEW)
│   └── ...
└── release/
    └── ...
```

### 15.2 Coding Standards

- Python 3.10+ (same as Age IV)
- Type hints required on all new code
- Docstrings (Google style) required
- Cyclomatic complexity ≤ 15 (same as Age IV)
- No bare `except: pass`
- All new modules must have `is_healthy()` + `stop()` + `get_stats()`
- All new services must emit EventBus events

### 15.3 Testing Standards

- Every new module: unit tests + integration tests
- Every new API: contract tests
- Every new workflow: chaos test
- Minimum coverage: 80% for new code
- All Age IV tests must continue to pass

---

## PART 16 — ACCEPTANCE CRITERIA

Age V is complete when:

1. ✅ Distributed runtime operational (Redis-backed, multi-node)
2. ✅ Multi-tenant platform (per-tenant isolation, RBAC)
3. ✅ Living memory (5 memory types, consolidation, decay)
4. ✅ Knowledge graph (entity extraction, relationship queries)
5. ✅ Autonomous planning (goal decomposition, re-planning)
6. ✅ Workflow engine (conditional nodes, checkpoint, rollback)
7. ✅ Plugin marketplace (signing, sandbox, discovery)
8. ✅ Developer platform (Python + TypeScript SDKs, REST + GraphQL)
9. ✅ Security (zero-trust, PromptShield default ON, RBAC)
10. ✅ Client ecosystem (web, desktop, mobile, CLI)
11. ✅ All Age IV tests pass (no regressions)
12. ✅ Age V tests pass (new tests for new features)

---

## FINAL STATEMENT

This is the Age V Master Architecture Book. It represents the consensus of 24 engineering swarms, reviewed by 3 Distinguished Architects and 1 Chief Scientist.

Age V extends Age IV. It does not rewrite it. It builds a civilization on top of a certified runtime. It transforms FRIDAY from a tool into a society.

**The architecture is internally consistent. The migration path is safe. The risks are identified. The timeline is realistic.**

**Age V may begin.**

---

*End of Master Architecture Book*
