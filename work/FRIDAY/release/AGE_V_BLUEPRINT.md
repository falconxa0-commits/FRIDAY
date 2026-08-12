# FRIDAY AGE V — MASTER BLUEPRINT

**Document Type:** Architecture Design (No Implementation)  
**Date:** 2026-08-13  
**Status:** Design Phase  
**Foundation:** Age IV (v4.0.0 — Certified)

## Vision

FRIDAY Age V transforms FRIDAY from a single-process AI assistant into an AI Operating System — a platform that manages AI agents, runtime contexts, memory, knowledge, and execution the way a traditional OS manages processes, memory, and files.

Age V does NOT rewrite Age IV. It extends it.

## Mission

Build upon the certified Age IV foundation to create:
1. A distributed runtime that scales beyond a single process
2. A multi-tenant architecture that serves multiple users
3. A living knowledge graph that evolves with the system
4. An autonomous planning engine that decomposes goals into work
5. A plugin marketplace with sandboxed execution
6. A developer platform with SDKs and APIs

## Principles

1. **Never rewrite Age IV** — extend, don't replace
2. **Backward compatible** — Age IV code continues to work
3. **Security first** — every new capability is sandboxed
4. **Observable** — every action is measured
5. **Distributed** — designed for horizontal scaling from day one
6. **Plugin-native** — every capability is a plugin

## Architecture

### Layer 1: Distributed Runtime (extends Age IV RuntimeContext)
- Redis-backed EventBus (replaces in-memory EventBus)
- Redis-backed Scheduler (replaces in-process Scheduler)
- Redis-backed TaskQueue (replaces in-memory TaskQueue)
- Redis-backed SessionStore (replaces in-memory SessionRuntime)
- Process pool for parallel execution
- Multi-process coordination via Redis pub/sub

### Layer 2: Multi-Tenant Platform
- Per-tenant RuntimeContext (isolated services)
- Per-tenant memory (separate vector stores)
- Per-tenant ledger (separate audit chains)
- Per-tenant API tokens with RBAC
- Tenant lifecycle management (create, suspend, delete)

### Layer 3: Agent Civilization
- Multi-agent coordination protocol
- Agent communication via message passing
- Agent specialization (research, coding, writing, ops)
- Agent lifecycle (spawn, monitor, retire)
- Agent resource quotas

### Layer 4: Living Memory
- Persistent vector store (Supabase pgvector or Pinecone)
- Memory consolidation (compress old memories)
- Memory decay (forget irrelevant information)
- Cross-session memory (remember across restarts)
- Semantic memory graph (relationships between facts)

### Layer 5: Knowledge Graph
- Entity extraction from conversations
- Relationship detection
- Knowledge queries (SPARQL-like)
- Knowledge evolution (update, merge, conflict resolution)
- Knowledge export/import

### Layer 6: Autonomous Planning
- Goal decomposition engine
- Dependency graph builder
- Effort estimation
- Critical path detection
- Automatic re-planning on failure

### Layer 7: Workflow Engine
- DAG-based workflow execution
- Conditional nodes (if/else branching)
- Parallel execution
- Checkpoint recovery
- Rollback support
- Human-in-the-loop approval nodes

### Layer 8: Plugin Marketplace
- Plugin signing (cryptographic)
- Plugin sandboxing (SubprocessSandbox for all plugins)
- Plugin versioning
- Plugin dependency resolution
- Plugin revenue sharing
- Plugin discovery (search, ratings, reviews)

### Layer 9: Developer Platform
- Python SDK (async-first)
- TypeScript SDK (for web/desktop)
- REST API (OpenAPI spec)
- GraphQL API (for flexible queries)
- WebSocket API (for real-time updates)
- MCP Server (for external AI agents)

### Layer 10: Client Ecosystem
- Web dashboard (Next.js, real-time)
- Desktop app (Electron or Tauri)
- Mobile app (React Native)
- CLI (enhanced rich TUI)
- VS Code extension (enhanced)
- Browser extension (enhanced)

## Runtime Evolution

Age IV Runtime → Age V Distributed Runtime:

| Age IV (Single-Process) | Age V (Distributed) |
|--------------------------|----------------------|
| In-memory EventBus | Redis pub/sub |
| In-process Scheduler | Celery / RQ |
| In-memory TaskQueue | Redis-backed queue |
| Single RuntimeContext | Per-tenant RuntimeContext |
| SubprocessSandbox (local) | Container sandbox (Docker) |
| Single-agent execution | Multi-agent coordination |
| File-based persistence | Database persistence |
| Single API token | RBAC with per-user tokens |

## Security Evolution

| Age IV | Age V |
|--------|-------|
| Single API token | OAuth 2.0 + JWT |
| CapabilitySandbox | Container isolation |
| PromptShield (opt-in) | PromptShield (default ON) |
| PolicyEngine (deny-by-default) | PolicyEngine + ACL + audit |
| HMAC-SHA256 audit chain | Tamper-proof distributed log |
| AST scan for plugins | Signed plugins + sandbox |

## Roadmap

### Phase 1: Distributed Foundation (4-6 weeks)
- Redis-backed EventBus, Scheduler, TaskQueue
- Multi-process RuntimeManager
- Health monitoring across processes

### Phase 2: Multi-Tenant (4-6 weeks)
- Per-tenant RuntimeContext
- Per-tenant memory isolation
- RBAC with per-user tokens
- Tenant lifecycle management

### Phase 3: Agent Civilization (6-8 weeks)
- Agent communication protocol
- Multi-agent coordination
- Agent specialization and routing
- Agent resource management

### Phase 4: Living Memory + Knowledge Graph (4-6 weeks)
- Persistent vector store
- Memory consolidation and decay
- Knowledge graph with entity extraction
- Cross-session memory

### Phase 5: Autonomous Planning + Workflow (4-6 weeks)
- Goal decomposition engine
- DAG workflow with conditional nodes
- Checkpoint recovery and rollback

### Phase 6: Plugin Marketplace + Developer Platform (4-6 weeks)
- Plugin signing and sandboxing
- Python + TypeScript SDKs
- REST + GraphQL + WebSocket APIs
- Enhanced MCP server

### Phase 7: Client Ecosystem (6-8 weeks)
- Web dashboard (Next.js)
- Desktop app (Tauri)
- Mobile app (React Native)
- Enhanced CLI + VS Code extension

## Risks

1. **Complexity explosion** — distributed systems are harder to debug
   - Mitigation: Extensive observability, chaos engineering

2. **Security surface increase** — more endpoints, more attack vectors
   - Mitigation: Zero-trust architecture, defense in depth

3. **Performance regression** — distributed coordination has overhead
   - Mitigation: Benchmark-driven development, cache hierarchy

4. **Backward compatibility** — Age IV code must continue to work
   - Mitigation: Feature flags, adapter patterns, extensive regression tests

5. **Timeline risk** — 30-40 weeks of work
   - Mitigation: Phased delivery, each phase is independently valuable

## Milestones

- M1: Redis-backed runtime (single-tenant, distributed execution)
- M2: Multi-tenant platform (per-user isolation)
- M3: Agent civilization (multi-agent coordination)
- M4: Living memory (persistent, evolving knowledge)
- M5: Plugin marketplace (signed, sandboxed, discoverable)
- M6: Developer platform (SDKs, APIs, documentation)
- M7: Client ecosystem (web, desktop, mobile, CLI)
- M8: Age V certification

## Estimated Timeline

30-40 weeks of focused engineering with 4-6 engineers.

Each phase is independently valuable — the system is useful after each milestone.
