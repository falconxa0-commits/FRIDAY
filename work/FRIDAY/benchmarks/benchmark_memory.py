"""FRIDAY Memory Usage Benchmark — RSS growth at each milestone.

Measures the resident set size (RSS) of the FRIDAY process at:

    1. Baseline: Python process RSS after importing FRIDAY modules
    2. After ``FridayBrain`` init
    3. After storing 100 / 1 000 memories (10 000 opt-in via --include-10k)
    4. After 100 chat requests (mocked GLM brain)

Memory is reported in MiB via ``psutil.Process().memory_info().rss``.

To keep the benchmark honest, every milestone uses the *real* code
path (no mocked ``FridayMemory``) so the RSS numbers reflect what a
production process would actually consume. The GLM client IS mocked
(otherwise we'd be measuring network wait, not memory).

The default config targets the milestones requested by the WAVE1-PERF
task spec (baseline → brain init → 100/1000 memories → 100 chat
requests) and finishes in ~15-25 s. ``--include-10k`` adds the
10 000-memory milestone (~30 s extra). ``--ledger-actions N`` enables
the optional ledger-action milestone (off by default — each
``approve_action`` re-hashes the chain and is ~25 ms/action).

Results are written to ``benchmarks/results_memory.json``.

Usage::

    python benchmarks/benchmark_memory.py
    python benchmarks/benchmark_memory.py --include-10k   # also test 10 000 memories
    python benchmarks/benchmark_memory.py --ledger-actions 100  # add ledger milestone
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List
from unittest.mock import MagicMock

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("FRIDAY_DEV_MODE", "1")
for _k in ("GLM_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
           "TAVILY_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_k, None)

import logging  # noqa: E402
logging.disable(logging.WARNING)

RESULTS_PATH = BENCH_DIR / "results_memory.json"

# A reasonably-sized canned chat response (mimics GLM-4-Flash output).
CANNED_RESPONSE = (
    "Sure — I've thought about this. Here's my take: the answer "
    "depends on three factors. First, the context window. Second, "
    "the retrieval quality. Third, the model's reasoning depth."
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def rss_mib() -> float:
    """Return current process RSS in MiB."""
    import psutil
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def _make_fake_glm_responses(content: str, finish_reason: str = "stop"):
    """Build streaming + non-streaming mock GLM response shapes."""
    fake_choice = SimpleNamespace(
        finish_reason=finish_reason,
        message=SimpleNamespace(content=content, tool_calls=None),
    )
    non_streaming = SimpleNamespace(choices=[fake_choice])

    words = content.split(" ")
    chunk_size = max(1, len(words) // 5)
    streaming_chunks = []
    for i in range(0, len(words), chunk_size):
        piece = " ".join(words[i:i + chunk_size])
        streaming_chunks.append(SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content=piece),
                finish_reason=None,
            )],
        ))
    streaming_chunks.append(SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=""),
            finish_reason=finish_reason,
        )],
    ))
    return streaming_chunks, non_streaming


def build_brain_with_mock_glm():
    from core.brain import FridayBrain
    from core.memory import FridayMemory

    # Real FridayMemory (with hash-based vector fallback) — we want to
    # measure real RSS growth, not a stub.
    memory = FridayMemory()
    brain = FridayBrain(memory=memory)

    streaming, non_streaming = _make_fake_glm_responses(CANNED_RESPONSE)
    fake_client = MagicMock()

    def _create(*args, **kwargs):
        if kwargs.get("stream"):
            return streaming
        return non_streaming

    fake_client.chat.completions.create.side_effect = _create

    brain.glm_brain._client = fake_client
    brain.glm_brain._use_api = True
    brain.glm_brain.available = lambda: True
    brain.glm_brain.api_key = brain.glm_brain.api_key or "test-mock-key"
    brain.local_brain.available = lambda: False
    brain.tools = []
    return brain


# ─────────────────────────────────────────────────────────────────────
# Milestone runners
# ─────────────────────────────────────────────────────────────────────

def milestone_after_imports() -> float:
    """Just imported FRIDAY modules — return current RSS."""
    return rss_mib()


def milestone_after_brain_init() -> float:
    """Construct FridayBrain and return RSS."""
    # Construct inside this function so the caller can capture RSS
    # immediately after.
    return None  # placeholder — real measurement done in run()


def populate_memories(memory, n: int) -> None:
    """Store ``n`` synthetic conversations through FridayMemory."""
    for i in range(n):
        memory.store_conversation(
            "user",
            f"This is memory entry number {i}. The user said something "
            f"interesting about topic {i % 50}.",
        )
        memory.store_conversation(
            "assistant",
            f"Got it — noting that down. Response {i}.",
        )


async def run_chat_requests(brain, n: int) -> None:
    """Drive ``n`` chat requests through the mocked brain."""
    for i in range(n):
        async for _ in brain.chat_stream(f"question number {i}"):
            pass


def run_ledger_actions(n: int) -> None:
    """Record ``n`` actions in the action ledger (hash-chained log).

    Each ``approve_action`` re-hashes the chain (~25 ms/action), so
    keep ``n`` small unless you have time to spare.
    """
    from core.ledger import get_ledger
    ledger = get_ledger()
    for i in range(n):
        # queue_action auto-approves low-risk actions in STANDARD/POWER
        # profiles; with the default GUEST profile everything queues.
        # Use a low-risk component so we exercise the fast path.
        action_id = ledger.queue_action(
            "PCControl",
            f"benchmark_action_{i}",
            {"index": i, "payload": "x" * 64},
            risk_level="low",
        )
        # Auto-approve so the hash chain grows (one entry per action).
        ledger.approve_action(action_id)


# ─────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────

def run(include_10k: bool = False, chat_requests: int = 100,
        ledger_actions: int = 0) -> Dict:
    print(f"MEM | Baseline RSS after imports: {rss_mib():.1f} MiB")

    tracemalloc.start()
    snapshots: List[Dict] = [{
        "milestone": "after_imports",
        "rss_mib": round(rss_mib(), 2),
    }]

    # ── Brain init ────────────────────────────────────────────────
    brain = build_brain_with_mock_glm()
    snapshots.append({
        "milestone": "after_brain_init",
        "rss_mib": round(rss_mib(), 2),
        "delta_mib": round(rss_mib() - snapshots[-1]["rss_mib"], 2),
    })
    print(f"MEM | After FridayBrain init: {snapshots[-1]['rss_mib']:.1f} MiB "
          f"(+{snapshots[-1]['delta_mib']:.1f})")

    # ── Memory population sweep ───────────────────────────────────
    # Default: 100, 1000 (per task spec). 10 000 is opt-in because
    # the hash-embed fallback makes populating 10 k vectors take
    # ~20-30 s on its own.
    memory_sizes = [100, 1000]
    if include_10k:
        memory_sizes.append(10000)
    for n in memory_sizes:
        t0 = time.perf_counter()
        populate_memories(brain.memory, n)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        rss = rss_mib()
        delta = rss - snapshots[-1]["rss_mib"]
        snapshots.append({
            "milestone": f"after_{n}_memories",
            "rss_mib": round(rss, 2),
            "delta_mib": round(delta, 2),
            "memories_stored": len(brain.memory._memories),
            "populate_ms": round(elapsed_ms, 1),
        })
        print(f"MEM | After {n:>5d} memories: "
              f"{rss:.1f} MiB (+{delta:.1f}) "
              f"[_memories={len(brain.memory._memories)}, "
              f"populate={elapsed_ms:.0f} ms]")

    # ── Chat requests ─────────────────────────────────────────────
    t0 = time.perf_counter()
    asyncio.run(run_chat_requests(brain, chat_requests))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    rss = rss_mib()
    delta = rss - snapshots[-1]["rss_mib"]
    snapshots.append({
        "milestone": f"after_{chat_requests}_chat_requests",
        "rss_mib": round(rss, 2),
        "delta_mib": round(delta, 2),
        "elapsed_ms": round(elapsed_ms, 1),
    })
    print(f"MEM | After {chat_requests} chat requests: "
          f"{rss:.1f} MiB (+{delta:.1f}) "
          f"[elapsed={elapsed_ms:.0f} ms]")

    # ── Ledger actions (optional, off by default — ~25 ms/action) ──
    if ledger_actions > 0:
        t0 = time.perf_counter()
        run_ledger_actions(ledger_actions)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        rss = rss_mib()
        delta = rss - snapshots[-1]["rss_mib"]
        snapshots.append({
            "milestone": f"after_{ledger_actions}_ledger_actions",
            "rss_mib": round(rss, 2),
            "delta_mib": round(delta, 2),
            "elapsed_ms": round(elapsed_ms, 1),
        })
        print(f"MEM | After {ledger_actions} ledger actions: "
              f"{rss:.1f} MiB (+{delta:.1f}) "
              f"[elapsed={elapsed_ms:.0f} ms]")

    # ── Summary stats ─────────────────────────────────────────────
    baseline = snapshots[0]["rss_mib"]
    peak = max(s["rss_mib"] for s in snapshots)
    total_growth = peak - baseline

    # Compute per-unit memory costs
    per_memory_unit = None
    for i, s in enumerate(snapshots):
        if s["milestone"].startswith("after_") and s["milestone"].endswith("_memories"):
            pass  # could compute marginal cost here
    # Marginal MiB per 1 000 memories (use last memory milestone)
    last_mem = next(
        (s for s in reversed(snapshots)
         if s["milestone"].endswith("_memories")),
        None,
    )
    first_mem = next(
        (s for s in snapshots
         if s["milestone"].endswith("_memories")),
        None,
    )
    if last_mem and first_mem and last_mem is not first_mem:
        mem_growth = last_mem["rss_mib"] - first_mem["rss_mib"]
        mem_count_delta = last_mem.get("memories_stored", 0) - first_mem.get("memories_stored", 0)
        if mem_count_delta > 0:
            per_memory_unit = round(
                mem_growth * 1024 / mem_count_delta, 3  # KiB per memory
            )

    result = {
        "benchmark": "memory",
        "snapshots": snapshots,
        "summary": {
            "baseline_rss_mib": round(baseline, 2),
            "peak_rss_mib": round(peak, 2),
            "total_growth_mib": round(total_growth, 2),
            "per_memory_kib": per_memory_unit,
        },
        "config": {
            "include_10k": include_10k,
            "chat_requests": chat_requests,
            "ledger_actions": ledger_actions,
        },
        "methodology": {
            "rss_source": "psutil.Process().memory_info().rss",
            "real_subsystems": "FridayMemory + FridayBrain (no mocks except GLM client)",
            "note": (
                "RSS includes Python interpreter + numpy + fastapi + "
                "all imported modules. Deltas between milestones "
                "isolate the cost of each subsystem. Ledger actions "
                "are off by default (each approve_action re-hashes "
                "the chain at ~25 ms/action)."
            ),
        },
    }
    RESULTS_PATH.write_text(json.dumps(result, indent=2))
    print(f"MEM | Wrote {RESULTS_PATH}")
    print(f"MEM | Peak RSS: {peak:.1f} MiB  "
          f"(growth from baseline: +{total_growth:.1f} MiB)")
    if per_memory_unit is not None:
        print(f"MEM | Marginal cost: ~{per_memory_unit} KiB per stored memory")
    return result


def main():
    p = argparse.ArgumentParser(description="FRIDAY memory benchmark")
    p.add_argument("--include-10k", action="store_true",
                   help="Also populate 10 000 memories (adds ~30 s)")
    p.add_argument("--chat-requests", type=int, default=100,
                   help="Number of mocked chat requests (default 100)")
    p.add_argument("--ledger-actions", type=int, default=0,
                   help="Optional ledger-action milestone count (default 0 = skip)")
    args = p.parse_args()
    run(include_10k=args.include_10k,
        chat_requests=args.chat_requests,
        ledger_actions=args.ledger_actions)


if __name__ == "__main__":
    main()
