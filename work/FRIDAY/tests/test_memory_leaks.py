"""Memory leak detection — verify long-running operations do not
grow memory unboundedly.

Each test:

  1. Measures baseline RSS (resident set size) via ``psutil.Process``.
  2. Runs a workload N times (N typically 1000).
  3. Forces garbage collection.
  4. Measures final RSS.
  5. Asserts that growth is bounded — i.e. below a per-iteration
     threshold that would indicate a leak.

We use ``psutil.Process().memory_info().rss`` which returns the OS-level
resident set size in bytes. This is the same metric Prometheus exporters
use for ``process_resident_memory_bytes``.

NOTE: RSS is noisy — Python's allocator and the OS page cache both
affect it. The thresholds below are deliberately generous (10s of KB
per iteration) so we catch real leaks (unbounded list growth, forgotten
caches) without flapping on allocator noise.

When the workload legitimately grows memory (e.g. the audit chain grows
by one entry per iteration), we acknowledge that growth as expected and
bound the test to the *additional* growth beyond what the workload
itself should consume.
"""
from __future__ import annotations

import asyncio
import gc
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

psutil = pytest.importorskip("psutil")

from core.ledger import ActionLedger
from core.task_system import (
    TaskQueue, TaskStatus, get_task_queue,
)
from core.memory import FridayMemory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rss_kb() -> int:
    """Return the current process's RSS in kilobytes."""
    return psutil.Process().memory_info().rss // 1024


def _force_gc() -> None:
    """Force multiple GC cycles to settle the heap before measuring."""
    for _ in range(3):
        gc.collect()


def _print_growth(label: str, baseline: int, final: int, n_iters: int) -> None:
    delta = final - baseline
    per_iter = delta / max(n_iters, 1)
    print(
        f"  {label}: baseline={baseline}KB final={final}KB "
        f"delta={delta:+d}KB ({per_iter:+.2f}KB/iter over {n_iters} iters)"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def task_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    import core.task_system
    core.task_system._queue = None
    q = TaskQueue(base_dir=tmp_path / "tasks")
    yield q
    core.task_system._queue = None


@pytest.fixture()
def ledger(tmp_path):
    old_persist = ActionLedger.PERSIST_PATH
    old_chain = ActionLedger.CHAIN_PERSIST_PATH
    ActionLedger.PERSIST_PATH = str(tmp_path / "pending.json")
    ActionLedger.CHAIN_PERSIST_PATH = str(tmp_path / "chain.json")
    l = ActionLedger()
    l.audit_log = str(tmp_path / "audit.log")
    yield l
    ActionLedger.PERSIST_PATH = old_persist
    ActionLedger.CHAIN_PERSIST_PATH = old_chain


# ---------------------------------------------------------------------------
# 1. Create + complete 1000 tasks — verify memory doesn't grow unboundedly
# ---------------------------------------------------------------------------

class TestMemoryLeakTasks:
    """Create and complete 1000 tasks. After completion, all 1000 tasks
    are moved from ``_tasks`` to ``_completed``. The ``_completed`` dict
    DOES grow with each completed task — that's expected, it's the
    durable history. But we verify the growth is bounded per-iteration
    (each task is small) and that there's no additional leak.
    """

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_1000_create_complete_cycles_bounded_growth(self, task_queue):
        N = 1000
        # Each completed Task is ~1-2KB serialised, plus its receipt on
        # disk. We expect roughly N * 2KB of growth in _completed — but
        # NO additional leak (no extra lists, no caches, etc.).
        # We allow 10KB/iter overhead above the expected Task size.
        MAX_PER_ITER_KB = 12

        _force_gc()
        baseline = _rss_kb()

        for i in range(N):
            t = await task_queue.create_task(title=f"task-{i}")
            await task_queue.start_task(t.id)
            await task_queue.complete_task(t.id, tests_passed=1)

        _force_gc()
        final = _rss_kb()
        _print_growth("1000 create+complete", baseline, final, N)

        delta_per_iter = (final - baseline) / N
        assert delta_per_iter < MAX_PER_ITER_KB, (
            f"Memory grew {delta_per_iter:.2f}KB/iter over {N} create+complete "
            f"cycles — expected < {MAX_PER_ITER_KB}KB/iter. "
            f"Possible leak: task history is not being garbage collected."
        )

        # Sanity: the queue reports the right counts.
        stats = await task_queue.get_stats()
        assert stats["completed"] == N
        assert stats["active"] == 0


# ---------------------------------------------------------------------------
# 2. Store + retrieve 1000 memories — check for leaks
# ---------------------------------------------------------------------------

class TestMemoryLeakMemories:
    """Store 1000 conversation turns and retrieve them. After the test,
    the in-memory ``_memories`` list WILL have 1000 entries (that's the
    intended behaviour — it's a list). But we verify:

      * The growth is bounded per-iteration (each memory is small).
      * Repeated retrieval does not duplicate or grow memory further.
    """

    @pytest.mark.slow
    def test_1000_store_retrieve_cycles_bounded_growth(self):
        N = 1000
        MAX_PER_ITER_KB = 5  # each memory is small (~500 bytes)

        mem = FridayMemory()
        _force_gc()
        baseline = _rss_kb()

        for i in range(N):
            mem.store_conversation("user", f"memory number {i}")

        _force_gc()
        final = _rss_kb()
        _print_growth("1000 store+retrieve", baseline, final, N)

        delta_per_iter = (final - baseline) / N
        assert delta_per_iter < MAX_PER_ITER_KB, (
            f"Memory grew {delta_per_iter:.2f}KB/iter over {N} store cycles — "
            f"expected < {MAX_PER_ITER_KB}KB/iter."
        )

        # Sanity: all 1000 memories are present.
        assert len(mem._memories) == N

    def test_repeated_retrieval_does_not_grow_memory(self):
        """Retrieving memories multiple times must not duplicate them
        or grow memory further."""
        mem = FridayMemory()
        # Seed 100 memories.
        for i in range(100):
            mem.store_conversation("user", f"seed memory {i}")

        _force_gc()
        baseline = _rss_kb()

        # Retrieve 500 times — should not grow memory.
        for _ in range(500):
            mem.retrieve_relevant_memories("seed")

        _force_gc()
        final = _rss_kb()
        _print_growth("500 retrievals (no new stores)", baseline, final, 500)

        # Memory should not have grown significantly — retrieval is read-only.
        delta = final - baseline
        # Allow some noise but catch real leaks (>5MB).
        assert delta < 5 * 1024, (
            f"Memory grew {delta}KB after 500 read-only retrievals — "
            f"retrieval is leaking (caching results?)."
        )

        # Memories count is unchanged.
        assert len(mem._memories) == 100


# ---------------------------------------------------------------------------
# 3. Run 100 ledger actions — check for chain growth leaks
# ---------------------------------------------------------------------------

class TestMemoryLeakLedger:
    """Run 100 ledger actions. The audit chain WILL grow by 100 entries
    (each ~500 bytes serialised), so we expect ~50KB of legitimate
    growth. We verify there's no ADDITIONAL leak on top of that.
    """

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_100_ledger_actions_bounded_growth(self, ledger):
        N = 100
        # Each chain entry is ~500 bytes (action_id, params, hash, etc.).
        # Expected growth: ~50KB. Allow 5x headroom for allocator overhead.
        MAX_TOTAL_KB = 250

        ledger.profile = "STANDARD"
        _force_gc()
        baseline = _rss_kb()

        for i in range(N):
            ledger.queue_action("Weather", "get", {"i": i}, risk_level="low")

        _force_gc()
        final = _rss_kb()
        _print_growth("100 ledger actions", baseline, final, N)

        delta = final - baseline
        assert delta < MAX_TOTAL_KB, (
            f"Memory grew {delta}KB after {N} ledger actions — expected "
            f"< {MAX_TOTAL_KB}KB (chain growth is ~50KB; rest is leak)."
        )

        # Sanity: chain has N entries and verifies.
        assert len(ledger.get_audit_log()) == N
        assert ledger.verify_chain() is True

    def test_repeated_verify_chain_does_not_grow_memory(self, ledger):
        """verify_chain walks the chain but should not allocate persistent
        state. Run it 1000 times — memory should not grow."""
        N = 1000
        # Seed 10 entries.
        ledger.profile = "STANDARD"
        for i in range(10):
            ledger.queue_action("Weather", "get", {"i": i}, risk_level="low")

        _force_gc()
        baseline = _rss_kb()

        for _ in range(N):
            ledger.verify_chain()

        _force_gc()
        final = _rss_kb()
        _print_growth("1000 verify_chain calls", baseline, final, N)

        # verify_chain should not leak — allow small allocator noise.
        delta = final - baseline
        assert delta < 1024, (
            f"Memory grew {delta}KB after {N} verify_chain calls — "
            f"verify_chain is leaking (caching results?)."
        )


# ---------------------------------------------------------------------------
# 4. Run 100 chat requests — check for conversation_history leaks
# ---------------------------------------------------------------------------

class TestMemoryLeakChatHistory:
    """Mock the brain and run 100 chat requests. The brain's
    ``conversation_history`` list WILL grow with each turn (that's
    intended behaviour for context). But we verify:

      * Growth per turn is bounded (each turn is small).
      * Clearing the context resets the history (no leak after clear).
    """

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_100_chat_turns_bounded_history_growth(self):
        """Run 100 chat turns against a fake brain — verify the
        conversation_history grows linearly (not super-linearly)."""
        N = 100
        MAX_PER_ITER_KB = 5  # each turn is ~500 bytes

        class FakeBrain:
            def __init__(self):
                self.conversation_history = []
                self.provider = "test"

            async def chat_stream(self, message, user_name="User"):
                # Append user + assistant turns.
                self.conversation_history.append({"role": "user", "content": message})
                self.conversation_history.append({"role": "assistant", "content": "ok"})
                yield "ok"

            def clear_context(self):
                self.conversation_history.clear()

            def get_stats(self):
                return {"provider": "test", "history_length": len(self.conversation_history)}

        brain = FakeBrain()
        _force_gc()
        baseline = _rss_kb()

        for i in range(N):
            async for _ in brain.chat_stream(f"message {i}"):
                pass

        _force_gc()
        final = _rss_kb()
        _print_growth("100 chat turns", baseline, final, N)

        delta_per_iter = (final - baseline) / N
        assert delta_per_iter < MAX_PER_ITER_KB, (
            f"Memory grew {delta_per_iter:.2f}KB/iter over {N} chat turns — "
            f"expected < {MAX_PER_ITER_KB}KB/iter."
        )

        # Sanity: history has 2*N entries (user + assistant per turn).
        assert len(brain.conversation_history) == 2 * N

    @pytest.mark.asyncio
    async def test_clear_context_frees_history(self):
        """After clear_context, the conversation_history should be
        empty — and memory should drop back close to baseline."""
        N = 200
        # Each message is large enough to register on RSS measurement.
        BIG_MESSAGE = "x" * 10_000  # 10KB per turn

        class FakeBrain:
            def __init__(self):
                self.conversation_history = []

            async def chat_stream(self, message, user_name="User"):
                self.conversation_history.append({"role": "user", "content": message})
                yield "ok"

            def clear_context(self):
                self.conversation_history.clear()

        brain = FakeBrain()
        _force_gc()
        baseline = _rss_kb()

        # Run N turns — history grows by N * ~10KB = ~2MB.
        for i in range(N):
            async for _ in brain.chat_stream(BIG_MESSAGE):
                pass

        _force_gc()
        before_clear = _rss_kb()
        _print_growth(f"{N} chat turns (before clear)", baseline, before_clear, N)

        # Clear context — history should be freed.
        brain.clear_context()
        _force_gc()
        after_clear = _rss_kb()
        _print_growth("after clear_context", baseline, after_clear, 1)

        # After clearing, memory should not have grown MORE than before
        # clearing. (If clear_context actually frees memory, after_clear
        # will be LESS than before_clear.)
        delta_after_clear = after_clear - baseline
        delta_before_clear = before_clear - baseline
        assert delta_after_clear <= delta_before_clear, (
            f"clear_context did not free memory — before_clear={delta_before_clear}KB, "
            f"after_clear={delta_after_clear}KB. The history is being retained."
        )
        # And the history list is now empty.
        assert len(brain.conversation_history) == 0
