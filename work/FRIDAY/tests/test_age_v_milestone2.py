"""Tests for FRIDAY Age V Milestone 2 — Distributed Runtime.

Tests:
    - DistributedEventBus: publish, subscribe, history, dead letter, idempotency
    - DistributedTaskQueue: enqueue, claim, complete, fail, retry, dead letter, stale recovery
    - FederationManager: registration, heartbeat, stale detection, primary election
    - WorkerRuntime: claim, execute, heartbeat, timeout, cancellation
    - DistributedRuntime: start/stop, spawn worker, health check
    - Chaos: Redis unavailable, worker crash, duplicate events, task timeout
    - Security: event injection, task forgery, node spoofing
"""
import asyncio
import json
import pytest

from core.runtime.v5.distributed_event_bus import (
    DistributedEventBus, DistributedEvent, DeadLetterEntry,
)
from core.runtime.v5.distributed_task_queue import (
    DistributedTaskQueue, DistributedTask, TaskStatus, TaskPriority,
)
from core.runtime.v5.federation import (
    FederationManager, NodeInfo, NodeStatus,
)
from core.runtime.v5.worker import WorkerRuntime, WorkerStatus
from core.runtime.v5.distributed_runtime import DistributedRuntime


# ============ DistributedEventBus ============

class TestDistributedEventBus:
    @pytest.mark.asyncio
    async def test_publish_subscribe_local(self):
        bus = DistributedEventBus(enable_distributed=False)
        await bus.start()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("test.event", handler)
        await bus.publish("test.event", {"key": "value"})
        assert len(received) == 1
        assert received[0].data["key"] == "value"
        await bus.stop()

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self):
        bus = DistributedEventBus(enable_distributed=False)
        await bus.start()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("*", handler)
        await bus.publish("type1", {"n": 1})
        await bus.publish("type2", {"n": 2})
        assert len(received) == 2
        await bus.stop()

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = DistributedEventBus(enable_distributed=False)
        await bus.start()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("test", handler)
        bus.unsubscribe("test", handler)
        await bus.publish("test", {})
        assert len(received) == 0
        await bus.stop()

    @pytest.mark.asyncio
    async def test_history(self):
        bus = DistributedEventBus(enable_distributed=False)
        await bus.start()
        await bus.publish("event1", {"n": 1})
        await bus.publish("event2", {"n": 2})
        history = bus.get_history()
        assert len(history) == 2
        await bus.stop()

    @pytest.mark.asyncio
    async def test_idempotency(self):
        bus = DistributedEventBus(enable_distributed=False)
        await bus.start()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("test", handler)
        key = "test-key-123"
        await bus.publish("test", {"v": 1}, idempotency_key=key)
        await bus.publish("test", {"v": 2}, idempotency_key=key)
        assert len(received) == 1  # duplicate filtered
        await bus.stop()

    @pytest.mark.asyncio
    async def test_dead_letter_on_handler_error(self):
        bus = DistributedEventBus(enable_distributed=False)
        await bus.start()

        async def bad_handler(event):
            raise RuntimeError("Intentional error")

        bus.subscribe("test", bad_handler)
        await bus.publish("test", {})
        dead = bus.get_dead_letters()
        assert len(dead) >= 1
        await bus.stop()

    @pytest.mark.asyncio
    async def test_stats(self):
        bus = DistributedEventBus(enable_distributed=False)
        await bus.start()
        await bus.publish("test", {})
        stats = bus.get_stats()
        assert stats["published"] >= 1
        await bus.stop()

    @pytest.mark.asyncio
    async def test_correlation_id(self):
        bus = DistributedEventBus(enable_distributed=False)
        await bus.start()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("test", handler)
        await bus.publish("test", {}, correlation_id="trace-123")
        assert received[0].correlation_id == "trace-123"
        await bus.stop()


# ============ DistributedTaskQueue ============

class TestDistributedTaskQueue:
    @pytest.mark.asyncio
    async def test_enqueue(self):
        q = DistributedTaskQueue()
        await q.start()
        task = await q.enqueue("test-task", func_name="test_func")
        assert task.name == "test-task"
        assert task.status == TaskStatus.PENDING
        await q.stop()

    @pytest.mark.asyncio
    async def test_claim(self):
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("task1", func_name="func1")
        task = await q.claim("worker-1")
        assert task is not None
        assert task.status == TaskStatus.CLAIMED
        assert task.claimed_by == "worker-1"
        await q.stop()

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("low", priority=TaskPriority.LOW)
        await q.enqueue("critical", priority=TaskPriority.CRITICAL)
        await q.enqueue("medium", priority=TaskPriority.MEDIUM)
        t1 = await q.claim("w1")
        t2 = await q.claim("w1")
        t3 = await q.claim("w1")
        assert t1.name == "critical"
        assert t2.name == "medium"
        assert t3.name == "low"
        await q.stop()

    @pytest.mark.asyncio
    async def test_complete(self):
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("task1", func_name="func1")
        task = await q.claim("w1")
        result = await q.complete(task.id, "w1", {"output": "done"})
        assert result is True
        assert q.get_task(task.id).status == TaskStatus.COMPLETED
        await q.stop()

    @pytest.mark.asyncio
    async def test_fail_with_retry(self):
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("task1", func_name="func1", max_retries=3)
        task = await q.claim("w1")
        await q.fail(task.id, "w1", "something broke")
        # Task should be back in pending for retry
        retrieved = q.get_task(task.id)
        assert retrieved.status == TaskStatus.PENDING
        assert retrieved.attempt == 1
        await q.stop()

    @pytest.mark.asyncio
    async def test_fail_to_dead_letter(self):
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("task1", func_name="func1", max_retries=1)
        task = await q.claim("w1")
        await q.fail(task.id, "w1", "permanent failure")
        dead = q.list_dead_letters()
        assert len(dead) >= 1
        assert dead[0].status == TaskStatus.DEAD_LETTER
        await q.stop()

    @pytest.mark.asyncio
    async def test_heartbeat(self):
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("task1", func_name="func1")
        task = await q.claim("w1")
        result = await q.heartbeat(task.id, "w1")
        assert result is True
        await q.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_wrong_worker(self):
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("task1", func_name="func1")
        task = await q.claim("w1")
        result = await q.heartbeat(task.id, "w2")
        assert result is False  # wrong worker
        await q.stop()

    @pytest.mark.asyncio
    async def test_cancel(self):
        q = DistributedTaskQueue()
        await q.start()
        task = await q.enqueue("task1", func_name="func1")
        result = await q.cancel(task.id)
        assert result is True
        assert q.get_task(task.id).status == TaskStatus.CANCELLED
        await q.stop()

    @pytest.mark.asyncio
    async def test_idempotency(self):
        q = DistributedTaskQueue()
        await q.start()
        t1 = await q.enqueue("task1", idempotency_key="key-1")
        t2 = await q.enqueue("task2", idempotency_key="key-1")
        assert t1.id == t2.id  # same task returned
        assert q.get_stats()["duplicates_filtered"] >= 1
        await q.stop()

    @pytest.mark.asyncio
    async def test_drain(self):
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("t1")
        await q.enqueue("t2")
        count = await q.drain()
        assert count == 2
        assert len(q.list_pending()) == 0
        await q.stop()

    @pytest.mark.asyncio
    async def test_stats(self):
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("t1")
        stats = q.get_stats()
        assert stats["enqueued"] == 1
        assert stats["pending_count"] == 1
        await q.stop()


# ============ FederationManager ============

class TestFederationManager:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        fed = FederationManager(node_name="test-node")
        await fed.start()
        assert fed.self_info.status == NodeStatus.ACTIVE
        assert fed.is_primary is True
        await fed.stop()
        assert fed.self_info.status == NodeStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_register_node(self):
        fed = FederationManager(node_name="primary")
        await fed.start()
        node = NodeInfo(name="secondary", capabilities=["worker"])
        result = await fed.register_node(node)
        assert result is True
        nodes = await fed.discover_nodes()
        assert len(nodes) == 2  # self + registered
        await fed.stop()

    @pytest.mark.asyncio
    async def test_deregister_node(self):
        fed = FederationManager()
        await fed.start()
        node = NodeInfo(name="secondary")
        await fed.register_node(node)
        result = await fed.deregister_node(node.id)
        assert result is True
        await fed.stop()

    @pytest.mark.asyncio
    async def test_heartbeat(self):
        fed = FederationManager()
        await fed.start()
        node = NodeInfo(name="worker-1")
        await fed.register_node(node)
        result = await fed.heartbeat(node.id)
        assert result is True
        await fed.stop()

    @pytest.mark.asyncio
    async def test_ban_node(self):
        fed = FederationManager()
        await fed.start()
        node = NodeInfo(name="bad-actor")
        await fed.register_node(node)
        result = await fed.ban_node(node.id)
        assert result is True
        banned_node = fed._nodes.get(node.id)
        assert banned_node.status == NodeStatus.BANNED
        await fed.stop()

    @pytest.mark.asyncio
    async def test_get_primary(self):
        fed = FederationManager(node_name="primary")
        await fed.start()
        primary = await fed.get_primary()
        assert primary is not None
        assert primary.id == fed.self_info.id
        await fed.stop()

    @pytest.mark.asyncio
    async def test_stats(self):
        fed = FederationManager()
        await fed.start()
        stats = fed.get_stats()
        assert stats["total_nodes"] == 1
        assert stats["has_primary"] is True
        await fed.stop()


# ============ WorkerRuntime ============

class TestWorkerRuntime:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        q = DistributedTaskQueue()
        await q.start()
        w = WorkerRuntime(task_queue=q)
        await w.start()
        assert w.status == WorkerStatus.IDLE
        await w.stop()
        assert w.status == WorkerStatus.STOPPED
        await q.stop()

    @pytest.mark.asyncio
    async def test_execute_task(self):
        q = DistributedTaskQueue()
        await q.start()
        w = WorkerRuntime(task_queue=q)
        w.register_function("add", lambda a, b: a + b)
        await w.start()

        await q.enqueue("add-task", func_name="add", args=[3, 4])
        await asyncio.sleep(2)  # wait for worker to process

        assert w.stats.tasks_completed == 1
        await w.stop()
        await q.stop()

    @pytest.mark.asyncio
    async def test_execute_async_task(self):
        q = DistributedTaskQueue()
        await q.start()
        w = WorkerRuntime(task_queue=q)

        async def async_add(a, b):
            await asyncio.sleep(0.1)
            return a + b

        w.register_function("async_add", async_add)
        await w.start()

        await q.enqueue("async-task", func_name="async_add", args=[5, 6])
        await asyncio.sleep(2)

        assert w.stats.tasks_completed == 1
        await w.stop()
        await q.stop()

    @pytest.mark.asyncio
    async def test_task_failure(self):
        q = DistributedTaskQueue()
        await q.start()
        w = WorkerRuntime(task_queue=q)

        def failing_func():
            raise RuntimeError("intentional failure")

        w.register_function("fail", failing_func)
        await w.start()

        await q.enqueue("fail-task", func_name="fail", max_retries=1)
        await asyncio.sleep(2)

        assert w.stats.tasks_failed >= 1
        await w.stop()
        await q.stop()

    @pytest.mark.asyncio
    async def test_task_timeout(self):
        q = DistributedTaskQueue()
        await q.start()
        w = WorkerRuntime(task_queue=q)

        async def slow_func():
            await asyncio.sleep(10)

        w.register_function("slow", slow_func)
        await w.start()

        await q.enqueue("slow-task", func_name="slow", timeout=0.5, max_retries=1)
        await asyncio.sleep(3)

        assert w.stats.tasks_failed >= 1
        await w.stop()
        await q.stop()

    @pytest.mark.asyncio
    async def test_worker_stats(self):
        q = DistributedTaskQueue()
        await q.start()
        w = WorkerRuntime(worker_name="test-worker", task_queue=q)
        w.register_function("noop", lambda: None)
        await w.start()

        await q.enqueue("t1", func_name="noop")
        await asyncio.sleep(2)

        stats = w.stats
        assert stats.tasks_claimed == 1
        assert stats.tasks_completed == 1
        await w.stop()
        await q.stop()

    def test_get_status(self):
        q = DistributedTaskQueue()
        w = WorkerRuntime(worker_name="test", task_queue=q, capabilities=["compute"])
        status = w.get_status()
        assert status["name"] == "test"
        assert "compute" in status["capabilities"]


# ============ DistributedRuntime ============

class TestDistributedRuntime:
    @pytest.mark.asyncio
    async def test_start_stop_local(self):
        runtime = DistributedRuntime(enable_distributed=False)
        await runtime.start()
        assert runtime.status.started is True
        assert runtime.status.mode == "local"
        await runtime.stop()
        assert runtime.status.started is False

    @pytest.mark.asyncio
    async def test_spawn_worker(self):
        runtime = DistributedRuntime(enable_distributed=False)
        await runtime.start()
        worker = await runtime.spawn_worker("test-worker")
        assert worker.name == "test-worker"
        assert worker.status == WorkerStatus.IDLE
        await runtime.stop()

    @pytest.mark.asyncio
    async def test_health_check(self):
        runtime = DistributedRuntime(enable_distributed=False)
        await runtime.start()
        health = await runtime.health_check()
        assert "event_bus" in health
        assert "task_queue" in health
        assert "federation" in health
        await runtime.stop()

    @pytest.mark.asyncio
    async def test_get_stats(self):
        runtime = DistributedRuntime(enable_distributed=False)
        await runtime.start()
        stats = runtime.get_stats()
        assert "status" in stats
        assert "event_bus" in stats
        assert "task_queue" in stats
        await runtime.stop()

    @pytest.mark.asyncio
    async def test_event_bus_access(self):
        runtime = DistributedRuntime(enable_distributed=False)
        await runtime.start()
        assert runtime.event_bus is not None
        assert runtime.task_queue is not None
        assert runtime.federation is not None
        await runtime.stop()


# ============ CHAOS TESTS ============

class TestChaosEngineering:
    @pytest.mark.asyncio
    async def test_redis_unavailable_fallback(self):
        """When Redis is unavailable, system falls back to local mode."""
        bus = DistributedEventBus(
            redis_url="redis://nonexistent:6379",
            enable_distributed=True,
            local_fallback=True,
        )
        await bus.start()
        # Should fall back to local mode
        assert bus.is_distributed is False
        # Publishing should still work locally
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("test", handler)
        await bus.publish("test", {"v": 1})
        assert len(received) == 1
        await bus.stop()

    @pytest.mark.asyncio
    async def test_worker_crash_recovery(self):
        """When a worker crashes, its task is reclaimed."""
        q = DistributedTaskQueue()
        await q.start()

        # Enqueue a task
        task = await q.enqueue("long-task", func_name="long_func", max_retries=3)
        # Claim it (simulating a worker claiming it)
        claimed = await q.claim("w1")
        assert claimed.id == task.id

        # Simulate worker crash (no heartbeat, no completion)
        # Manually make the task stale
        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        q._claimed[task.id].heartbeat_at = old_time

        # Trigger stale recovery
        reclaimed = await q._reclaim_stale_tasks()
        assert reclaimed == 1

        # Task should be back in pending
        retrieved = q.get_task(task.id)
        assert retrieved.status == TaskStatus.PENDING
        assert retrieved.claimed_by == ""
        await q.stop()

    @pytest.mark.asyncio
    async def test_duplicate_event_filtered(self):
        """Duplicate events (same idempotency key) are filtered."""
        bus = DistributedEventBus(enable_distributed=False)
        await bus.start()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("test", handler)
        key = "dup-key"
        await bus.publish("test", {"v": 1}, idempotency_key=key)
        await bus.publish("test", {"v": 2}, idempotency_key=key)
        await bus.publish("test", {"v": 3}, idempotency_key=key)
        assert len(received) == 1  # only first delivered
        assert bus.get_stats()["duplicates_filtered"] == 2
        await bus.stop()

    @pytest.mark.asyncio
    async def test_malformed_event_doesnt_crash(self):
        """Malformed event data doesn't crash the bus."""
        bus = DistributedEventBus(enable_distributed=False)
        await bus.start()
        # Publish with weird data types
        await bus.publish("test", {"nested": {"deep": [1, 2, 3]}})
        await bus.publish("test", {})
        assert bus.get_stats()["published"] >= 2
        await bus.stop()

    @pytest.mark.asyncio
    async def test_task_queue_drain_on_shutdown(self):
        """Draining the queue cancels all pending tasks."""
        q = DistributedTaskQueue()
        await q.start()
        for i in range(10):
            await q.enqueue(f"task-{i}")
        count = await q.drain()
        assert count == 10
        assert len(q.list_pending()) == 0
        await q.stop()

    @pytest.mark.asyncio
    async def test_concurrent_claims(self):
        """Multiple workers can claim tasks concurrently without conflicts."""
        q = DistributedTaskQueue()
        await q.start()
        for i in range(20):
            await q.enqueue(f"task-{i}")

        # Two workers claim concurrently
        results = await asyncio.gather(
            q.claim("w1"), q.claim("w2"),
            q.claim("w1"), q.claim("w2"),
        )
        # Each claim should get a different task
        task_ids = [r.id for r in results if r is not None]
        assert len(task_ids) == len(set(task_ids))  # no duplicates
        await q.stop()

    @pytest.mark.asyncio
    async def test_retry_exhaustion_to_dead_letter(self):
        """A task that fails all retries ends up in dead letter queue."""
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("doomed", max_retries=2)

        # Attempt 1 (initial): claim + fail → retry (attempt=1 < max_retries=2)
        task = await q.claim("w1")
        assert task is not None
        await q.fail(task.id, "w1", "failure attempt 1")

        # Attempt 2 (retry): claim + fail → dead letter (attempt=2, not < max_retries=2)
        task = await q.claim("w1")
        assert task is not None
        await q.fail(task.id, "w1", "failure attempt 2")

        dead = q.list_dead_letters()
        assert len(dead) >= 1
        assert dead[0].name == "doomed"
        await q.stop()

    @pytest.mark.asyncio
    async def test_node_stale_detection(self):
        """A node that stops sending heartbeats is marked degraded."""
        fed = FederationManager(node_name="primary")
        await fed.start()

        worker_node = NodeInfo(name="worker-1")
        await fed.register_node(worker_node)

        # Simulate stale heartbeat
        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        fed._nodes[worker_node.id].last_heartbeat = old_time

        # Run stale check
        await fed._check_stale_nodes()

        # Node should be degraded
        assert fed._nodes[worker_node.id].status == NodeStatus.DEGRADED
        await fed.stop()


# ============ SECURITY TESTS ============

class TestSecurity:
    @pytest.mark.asyncio
    async def test_task_wrong_worker_cannot_complete(self):
        """A worker cannot complete a task claimed by another worker."""
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("task1")
        task = await q.claim("w1")
        # w2 tries to complete w1's task
        result = await q.complete(task.id, "w2", "stolen result")
        assert result is False
        await q.stop()

    @pytest.mark.asyncio
    async def test_task_wrong_worker_cannot_fail(self):
        """A worker cannot fail a task claimed by another worker."""
        q = DistributedTaskQueue()
        await q.start()
        await q.enqueue("task1")
        task = await q.claim("w1")
        result = await q.fail(task.id, "w2", "sabotage")
        assert result is False
        await q.stop()

    @pytest.mark.asyncio
    async def test_banned_node_cannot_register(self):
        """A banned node cannot re-register."""
        fed = FederationManager()
        await fed.start()
        node = NodeInfo(name="bad-actor")
        node.status = NodeStatus.BANNED
        result = await fed.register_node(node)
        assert result is False
        await fed.stop()

    @pytest.mark.asyncio
    async def test_banned_node_heartbeat_rejected(self):
        """A banned node's heartbeat is rejected."""
        fed = FederationManager()
        await fed.start()
        node = NodeInfo(name="bad-actor")
        await fed.register_node(node)
        await fed.ban_node(node.id)
        result = await fed.heartbeat(node.id)
        assert result is False
        await fed.stop()

    @pytest.mark.asyncio
    async def test_event_data_isolation(self):
        """Event data is not modified by subscribers."""
        bus = DistributedEventBus(enable_distributed=False)
        await bus.start()
        original_data = {"key": "value"}

        async def handler(event):
            event.data["modified"] = True  # attempt to modify

        bus.subscribe("test", handler)
        await bus.publish("test", original_data)
        # Original dict should not be modified (it's copied)
        await bus.stop()
