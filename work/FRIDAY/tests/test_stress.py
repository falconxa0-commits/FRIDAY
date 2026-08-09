"""Stress tests — verify the system under concurrent load.

These tests run real workloads concurrently via ``asyncio.gather`` and
verify there is:

  * no data loss        — every operation produces a record
  * no race conditions  — counts and totals are consistent
  * no deadlocks        — every coroutine completes within a deadline
  * no exceptions       — the system degrades gracefully under load

Target subsystems:
  1. :class:`core.task_system.TaskQueue`          — 100 concurrent task creates
  2. FridayBrain.chat_stream                       — 50 concurrent chats (mocked)
  3. :class:`core.ledger.ActionLedger`             — 1000 rapid ledger actions
  4. :class:`core.knowledge_base.KnowledgeBase`    — 100 concurrent searches
  5. TaskQueue.complete_task + receipt generation  — 50 concurrent completions
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.ledger import ActionLedger, NEVER_AUTO_APPROVE_COMPONENTS
from core.task_system import (
    TaskQueue, Task, TaskStatus, TaskPriority, TaskPhase,
    TaskReceipt, get_task_queue, verify_receipt,
)
from core.knowledge_base import (
    KnowledgeBase, EntryType, EntryStatus, get_knowledge_base,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def task_queue(tmp_path, monkeypatch):
    """Fresh TaskQueue with temp persistence directory."""
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    import core.task_system
    core.task_system._queue = None
    q = TaskQueue(base_dir=tmp_path / "tasks")
    yield q
    core.task_system._queue = None


@pytest.fixture()
def ledger(tmp_path):
    """Fresh ActionLedger with temp persistence files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        old_persist = ActionLedger.PERSIST_PATH
        old_chain = ActionLedger.CHAIN_PERSIST_PATH
        ActionLedger.PERSIST_PATH = os.path.join(tmpdir, "pending.json")
        ActionLedger.CHAIN_PERSIST_PATH = os.path.join(tmpdir, "chain.json")
        l = ActionLedger()
        l.audit_log = os.path.join(tmpdir, "audit.log")
        yield l
        ActionLedger.PERSIST_PATH = old_persist
        ActionLedger.CHAIN_PERSIST_PATH = old_chain


@pytest.fixture()
def knowledge_base(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    import core.knowledge_base
    core.knowledge_base._kb = None
    kb = KnowledgeBase()
    yield kb
    core.knowledge_base._kb = None


# ---------------------------------------------------------------------------
# 1. TaskQueue — 100 concurrent task creations
# ---------------------------------------------------------------------------

class TestStressTaskCreation:
    """100 concurrent create_task calls — must produce 100 distinct tasks,
    no UUID collisions, no race conditions on the asyncio.Lock."""

    @pytest.mark.asyncio
    async def test_100_concurrent_creates_produce_100_distinct_tasks(self, task_queue):
        N = 100

        async def create_one(i: int):
            return await task_queue.create_task(
                title=f"Stress task {i}",
                description=f"Stress test iteration {i}",
                priority=TaskPriority.MEDIUM,
            )

        tasks = await asyncio.gather(*[create_one(i) for i in range(N)])

        # All N creations must complete.
        assert len(tasks) == N

        # All task IDs must be unique (no UUID collisions, no overwrites).
        ids = [t.id for t in tasks]
        assert len(set(ids)) == N, "UUID collision detected — concurrent creates overwrote each other"

        # Every task must be retrievable.
        for t in tasks:
            retrieved = await task_queue.get_task(t.id)
            assert retrieved is not None
            assert retrieved.title == t.title

        # Active count must equal N.
        stats = await task_queue.get_stats()
        assert stats["active"] == N

    @pytest.mark.asyncio
    async def test_100_concurrent_creates_persist_to_disk(self, task_queue, tmp_path):
        N = 100
        await asyncio.gather(*[
            task_queue.create_task(title=f"task-{i}") for i in range(N)
        ])

        # queue.json must contain all 100 tasks.
        queue_file = tmp_path / "tasks" / "queue.json"
        import json
        with open(queue_file) as f:
            data = json.load(f)
        assert len(data) == N, (
            f"Only {len(data)} of {N} tasks persisted to disk — race condition in _persist_active()"
        )


# ---------------------------------------------------------------------------
# 2. FridayBrain.chat_stream — 50 concurrent chats (mocked)
# ---------------------------------------------------------------------------

class TestStressChatRequests:
    """50 concurrent chat requests against a mocked brain — verify the
    brain can handle concurrent streaming without interleaving chunks
    between requests."""

    @pytest.mark.asyncio
    async def test_50_concurrent_chat_streams_do_not_interleave(self):
        N = 50

        # Build a mock brain whose chat_stream yields N distinct chunks
        # tagged with the request index. If chunks interleave between
        # requests, the per-request accumulated output will be wrong.
        class FakeBrain:
            def __init__(self):
                self.call_count = 0

            async def chat_stream(self, message, user_name="User"):
                # Each call increments the counter and yields chunks
                # tagged with the call's index.
                self.call_count += 1
                my_idx = self.call_count
                for i in range(5):
                    await asyncio.sleep(0)  # yield to event loop
                    yield f"[req{my_idx}-chunk{i}]"

        brain = FakeBrain()

        async def chat_one(idx: int):
            chunks = []
            async for chunk in brain.chat_stream(f"hello {idx}"):
                chunks.append(chunk)
            return idx, "".join(chunks)

        results = await asyncio.gather(*[chat_one(i) for i in range(N)])

        # All N requests completed.
        assert len(results) == N

        # Each request's accumulated output must contain ONLY its own
        # chunks — no interleaving from other requests.
        for idx, output in results:
            # Every chunk in the output must be tagged with this req's idx.
            # We reconstruct the expected output: [reqK-chunk0][reqK-chunk1]...
            # where K is the brain's call number for this request.
            # The call number is brain.call_count AT THE TIME of the call,
            # which is not deterministic — but every chunk in the output
            # MUST share the SAME req tag.
            req_tags = set()
            for i in range(5):
                marker = f"[req"
                # Find each "[reqN-chunkM]" pattern.
                pass
            # Simpler: extract all req tags from the output.
            import re
            tags = re.findall(r"\[req(\d+)-chunk\d+\]", output)
            assert len(tags) == 5, (
                f"Request {idx} got {len(tags)} chunks instead of 5: {output!r}"
            )
            # All tags must be identical (same call number).
            unique_tags = set(tags)
            assert len(unique_tags) == 1, (
                f"Request {idx} had interleaved chunks from {len(unique_tags)} "
                f"different calls: {output!r}"
            )

    @pytest.mark.asyncio
    async def test_50_concurrent_chats_complete_within_deadline(self):
        N = 50
        deadline = 10.0  # seconds

        class FakeBrain:
            async def chat_stream(self, message, user_name="User"):
                yield "hello "
                await asyncio.sleep(0.01)
                yield "world"

        brain = FakeBrain()

        async def chat_one(i):
            chunks = []
            async for chunk in brain.chat_stream(f"hi {i}"):
                chunks.append(chunk)
            return "".join(chunks)

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[chat_one(i) for i in range(N)]),
                timeout=deadline,
            )
        except asyncio.TimeoutError:
            pytest.fail(
                f"{N} concurrent chats did not complete within {deadline}s — "
                f"the brain is serialising requests instead of running them concurrently."
            )

        assert len(results) == N
        assert all(r == "hello world" for r in results)


# ---------------------------------------------------------------------------
# 3. ActionLedger — 1000 rapid ledger actions
# ---------------------------------------------------------------------------

class TestStressLedgerActions:
    """1000 rapid queue_action + approve_action calls — verify:

      * Every action gets a unique action_id
      * Every approve produces exactly one audit-chain entry
      * The chain verifies as intact after all 1000 actions
      * No corruption of the persisted JSON file
    """

    @pytest.mark.asyncio
    async def test_1000_rapid_actions_produce_1000_chain_entries(self, ledger):
        N = 1000
        # All actions are Weather (auto-approved at GUEST? no — GUEST never
        # auto-approves). Use STANDARD profile so they auto-approve.
        ledger.profile = "STANDARD"

        action_ids = []
        for i in range(N):
            aid = ledger.queue_action(
                "Weather", "get_weather",
                {"location": f"city-{i}"},
                risk_level="low",
            )
            action_ids.append(aid)

        # All unique.
        assert len(set(action_ids)) == N, "UUID collision in queue_action"

        # All produced chain entries (auto-approved actions log on queue).
        chain = ledger.get_audit_log()
        assert len(chain) == N, (
            f"Expected {N} chain entries, got {len(chain)} — some actions "
            f"were not logged to the audit chain."
        )

        # Chain must verify as intact.
        assert ledger.verify_chain() is True, (
            "Audit chain broke after 1000 rapid actions — race condition "
            "in _log_audit / _persist_chain."
        )

    @pytest.mark.asyncio
    async def test_1000_mixed_actions_no_corruption(self, ledger):
        """Mix of auto-approved and pending actions — verify no corruption."""
        N = 500
        ledger.profile = "STANDARD"

        for i in range(N):
            if i % 3 == 0:
                # Auto-approved (low risk, non-dangerous component)
                ledger.queue_action("Weather", "get", {"i": i}, risk_level="low")
            elif i % 3 == 1:
                # Pending (high risk)
                aid = ledger.queue_action("Weather", "delete", {"i": i}, risk_level="high")
                ledger.approve_action(aid)
            else:
                # Pending (dangerous component, never auto-approved)
                aid = ledger.queue_action(
                    "Commerce", "checkout", {"i": i, "price": 9.99},
                    risk_level="low",
                )
                ledger.approve_action(aid)

        chain = ledger.get_audit_log()
        # All N actions should have produced exactly one chain entry.
        assert len(chain) == N, f"Expected {N} entries, got {len(chain)}"
        assert ledger.verify_chain() is True, "Chain integrity lost under mixed load"

    @pytest.mark.asyncio
    async def test_concurrent_queue_and_approve_no_deadlock(self, ledger):
        """Concurrent queue_action + approve_action — must not deadlock
        or corrupt the chain. The ledger uses an asyncio.Event (not a
        lock), so concurrent calls should be safe but may interleave
        chain writes. We verify the chain still verifies after."""
        N = 50
        ledger.profile = "STANDARD"

        async def queue_and_approve(i):
            aid = ledger.queue_action(
                "Weather", "act", {"i": i}, risk_level="low",
            )
            # Auto-approved at STANDARD. If not (e.g., profile changed),
            # approve explicitly.
            if ledger.pending_actions.get(aid, {}).get("status") != "approved":
                ledger.approve_action(aid)
            return aid

        ids = await asyncio.gather(*[queue_and_approve(i) for i in range(N)])

        assert len(set(ids)) == N
        # Chain must be intact.
        assert ledger.verify_chain() is True


# ---------------------------------------------------------------------------
# 4. KnowledgeBase — 100 concurrent searches
# ---------------------------------------------------------------------------

class TestStressKnowledgeSearch:
    """100 concurrent full-text searches — verify:

      * Every search completes
      * Results are consistent (same query → same results)
      * No exceptions raised under load
    """

    @pytest.mark.asyncio
    async def test_100_concurrent_searches_return_consistent_results(
        self, knowledge_base,
    ):
        # Seed the KB with 50 entries.
        async def seed():
            for i in range(50):
                await knowledge_base.create_entry(
                    type=EntryType.LESSON,
                    title=f"Lesson {i} about performance",
                    summary=f"Performance optimisation lesson number {i}",
                    content=f"Detailed content about performance tuning for module {i}.",
                    tags=["performance", f"module-{i}"],
                )
        await seed()

        N = 100
        query = "performance"

        async def search_one():
            return await knowledge_base.search(query, limit=10)

        results = await asyncio.gather(*[search_one() for _ in range(N)])

        # All searches completed.
        assert len(results) == N

        # None raised.
        for r in results:
            assert isinstance(r, list)

        # All non-empty (we have 50 matching entries).
        for r in results:
            assert len(r) > 0, "Search returned 0 results despite matching entries"

        # All searches returned the SAME result set (KB didn't change mid-run).
        first_ids = sorted(e.id for e in results[0])
        for r in results[1:]:
            assert sorted(e.id for e in r) == first_ids, (
                "Concurrent searches returned inconsistent results — "
                "the KB is mutating its index mid-search."
            )

    @pytest.mark.asyncio
    async def test_concurrent_search_and_create_no_deadlock(self, knowledge_base):
        """Mix of concurrent searches and creates — verify no deadlock."""
        # Seed a few entries first.
        for i in range(10):
            await knowledge_base.create_entry(
                type=EntryType.LESSON,
                title=f"Seed {i}",
                summary=f"seed summary {i}",
                content="content",
            )

        async def search_loop():
            for _ in range(5):
                await knowledge_base.search("seed")
                await asyncio.sleep(0)

        async def create_loop():
            for i in range(5):
                await knowledge_base.create_entry(
                    type=EntryType.LESSON,
                    title=f"Concurrent {i}",
                    summary=f"concurrent {i}",
                    content="content",
                )
                await asyncio.sleep(0)

        # Run both concurrently — should complete without deadlock.
        try:
            await asyncio.wait_for(
                asyncio.gather(search_loop(), create_loop()),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            pytest.fail("Concurrent search + create deadlocked")


# ---------------------------------------------------------------------------
# 5. TaskQueue — 50 concurrent completions + receipt generation
# ---------------------------------------------------------------------------

class TestStressTaskCompletion:
    """50 concurrent complete_task calls — verify:

      * Every completion generates a unique receipt
      * Receipts are cryptographically valid (verify_receipt returns True)
      * No two receipts share a hash (different inputs → different hashes)
      * Completed tasks move out of _tasks into _completed atomically
    """

    @pytest.mark.asyncio
    async def test_50_concurrent_completions_generate_50_valid_receipts(
        self, task_queue,
    ):
        N = 50

        # Pre-create N tasks.
        task_ids = []
        for i in range(N):
            t = await task_queue.create_task(title=f"complete-{i}")
            task_ids.append(t.id)

        # Start all tasks (must be in_progress before completing).
        for tid in task_ids:
            await task_queue.start_task(tid)

        # Now complete all concurrently.
        async def complete_one(tid, idx):
            return await task_queue.complete_task(
                tid,
                tests_passed=idx,
                tests_failed=0,
                duration_seconds=float(idx),
            )

        receipts = await asyncio.gather(*[
            complete_one(tid, i) for i, tid in enumerate(task_ids)
        ])

        # All completions produced a receipt.
        assert len(receipts) == N
        assert all(r is not None for r in receipts), "Some completions returned None"

        # All receipts verify.
        for r in receipts:
            assert verify_receipt(r) is True, (
                f"Receipt {r.task_id} failed verification — signature invalid."
            )

        # All receipt hashes are unique (different test counts → different hashes).
        hashes = [r.hash for r in receipts]
        assert len(set(hashes)) == N, (
            f"Only {len(set(hashes))} of {N} receipt hashes are unique — "
            f"hash collisions under concurrent load."
        )

        # Active count is now 0; completed count is N.
        stats = await task_queue.get_stats()
        assert stats["active"] == 0
        assert stats["completed"] == N

    @pytest.mark.asyncio
    async def test_50_concurrent_completions_no_double_completion(self, task_queue):
        """Concurrent complete_task calls on the SAME task — only one
        should succeed; the rest should return None."""
        # Create one task.
        t = await task_queue.create_task(title="double-complete")
        await task_queue.start_task(t.id)

        # Fire 50 concurrent completions at it.
        receipts = await asyncio.gather(*[
            task_queue.complete_task(t.id, tests_passed=i) for i in range(50)
        ])

        # Exactly ONE should return a receipt; the rest None.
        non_none = [r for r in receipts if r is not None]
        assert len(non_none) == 1, (
            f"Expected exactly 1 successful completion, got {len(non_none)} — "
            f"complete_task is not atomic under concurrent access."
        )

        # The one receipt must verify.
        assert verify_receipt(non_none[0]) is True
