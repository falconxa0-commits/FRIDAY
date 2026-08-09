# FRIDAY Engineering Platform — Developer Guide

## Overview

FRIDAY v3.2 includes a complete engineering platform that transforms it from a collection of AI agents into a structured engineering organization with persistent work management, knowledge management, continuous validation, and release management.

## Architecture

```
EngineeringOrg (core/engineering_org.py)
├── TaskQueue (core/task_system.py)         — durable task management
├── KnowledgeBase (core/knowledge_base.py)  — institutional memory
├── ValidationPipeline (core/validation_pipeline.py) — quality gates
└── ReleasePipeline (core/release_pipeline.py) — release management
```

All state is persisted to `.friday/`:
```
.friday/
├── tasks/
│   ├── queue.json          # Active tasks
│   ├── completed.json      # Completed tasks (history)
│   └── receipts/           # One receipt per completed task
├── knowledge/
│   ├── index.json          # Entry metadata index
│   └── entries/            # Full entry JSON files
├── benchmarks/
│   └── *.json              # Benchmark results
└── releases/
    └── v*.json             # Release manifests
```

## Engineering Organization

### Leads

| Lead | Role | Owns |
|------|------|------|
| Executive Orchestrator | Coordination | Organization, Work Management |
| Chief Architect | Architecture | Developer Experience |
| Security Lead | Security | Security |
| Testing Lead | Testing | Validation, Quality |
| Documentation Lead | Docs | Knowledge |
| Performance Lead | Performance | Performance |
| Research Lead | Research | Research |
| Release Manager | Releases | Release Pipeline |
| DevOps Lead | Operations | (supports all phases) |
| Refactoring Lead | Code Quality | (supports architect) |

### Creating Tasks

```python
from core.engineering_org import get_engineering_org
from core.task_system import TaskPhase, TaskPriority

org = get_engineering_org()
task = await org.create_and_assign_task(
    title="Add prompt injection defenses",
    description="Implement input sanitization layer for user messages",
    phase=TaskPhase.SECURITY,
    priority=TaskPriority.HIGH,
)
```

### Executing Tasks

```python
async def my_work_fn(task):
    # Do the actual work here
    pass

receipt = await org.execute_task(task.id, work_fn=my_work_fn)
# receipt is None if validation failed
```

## Task System

### Task Lifecycle

```
PENDING → READY → IN_PROGRESS → COMPLETED
            ↘ BLOCKED (dependency not met)
            ↘ FAILED (retries exhausted)
            ↘ CANCELLED
```

### Features

- **Priorities**: CRITICAL, HIGH, MEDIUM, LOW
- **Dependencies**: Tasks block until dependencies complete
- **Retries**: Configurable max_retries (default 3)
- **Checkpoints**: Resumable execution via `add_checkpoint()`
- **History**: Every event is logged with timestamp + actor
- **Receipts**: HMAC-SHA256 signed proof of completion

### Receipts

Every completed task generates a cryptographic receipt:

```python
from core.task_system import verify_receipt

receipt = await queue.complete_task(task_id, tests_passed=10, tests_failed=0)
assert verify_receipt(receipt)  # True
```

Receipts are persisted to `.friday/tasks/receipts/<task_id>.json` and can be independently verified by anyone with the HMAC secret.

## Knowledge Base

### Entry Types

| Type | Description |
|------|-------------|
| ADR | Architecture Decision Record |
| STANDARD | Coding standard or convention |
| LESSON | Lesson learned from failure/success |
| DESIGN | Design document |
| BENCHMARK | Benchmark result |
| RUNBOOK | Troubleshooting guide |
| RESEARCH | Research finding |
| RELEASE | Release note |

### Searching

```python
from core.knowledge_base import get_knowledge_base

kb = get_knowledge_base()
results = await kb.search("HMAC audit chain")
# Returns entries ranked by relevance (title > tags > summary > content)
```

## Validation Pipeline

### Checks

1. **Syntax check** — all Python files compile
2. **Import check** — core modules import without errors
3. **Linting** — ruff or pyflakes (if available)
4. **Unit tests** — full pytest suite
5. **Security regression** — security-specific tests
6. **Ledger chain** — audit chain integrity

### Running Validation

```python
from core.validation_pipeline import get_validation_pipeline

pipeline = get_validation_pipeline()
report = await pipeline.run()
print(report.summary)  # "6 passed, 0 failed, 0 skipped, 0 errors"
print(report.passed)   # True
```

## Release Pipeline

### Release Types

```
ALPHA → BETA → RC → STABLE → HOTFIX
```

### Creating Releases

```python
from core.release_pipeline import get_release_pipeline, ReleaseType

pipe = get_release_pipeline()
release = await pipe.create_release(
    version="3.3.0",
    release_type=ReleaseType.BETA,
    release_notes="Engineering platform + security hardening",
    predecessor="3.2.0",
)
await pipe.publish("3.3.0")
```

## CLI Commands

```bash
# Engineering org status
friday eng status

# Task management
friday eng tasks                    # list active tasks
friday eng task <id>                # show task details
friday eng create "Fix bug" --phase=security --priority=high
friday eng complete <id>            # mark task complete

# Knowledge base
friday eng kb list                  # list entries
friday eng kb search "query"        # search KB
friday eng kb show <id>             # show entry

# Releases
friday eng releases                 # list releases

# Validation
friday eng validate                 # run validation pipeline
friday eng validate --quick         # skip slow tests
```

## Seeding the Knowledge Base

The knowledge base comes pre-seeded with 12 entries documenting all prior engineering work:

```bash
python scripts/seed_knowledge_base.py
```

This creates:
- 4 ADRs (HMAC chain, AST scan, MCP risk_level, brain decomposition)
- 6 Lessons (skills/ dir, web_search, CostTracker, swarm coordination)
- 2 Benchmarks (startup, vector search)
- 2 Standards (hash includes approved_by, no silent exceptions)

## Integration with Existing Systems

The engineering platform is fully backward-compatible:
- No existing modules were modified
- All 818 existing tests still pass
- The platform is opt-in — existing CLI commands work unchanged
- The `friday eng` commands are the only new user-facing surface
