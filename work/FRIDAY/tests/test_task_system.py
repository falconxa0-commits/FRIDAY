"""Tests for the engineering task system."""
import asyncio
import json
import os
import tempfile
import pytest

from core.task_system import (
    TaskQueue, Task, TaskStatus, TaskPriority, TaskPhase,
    TaskReceipt, TaskCheckpoint, TaskHistoryEntry,
    get_task_queue, verify_receipt,
)


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    """Fresh TaskQueue with temp directory."""
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    # Reset singleton
    import core.task_system
    core.task_system._queue = None
    q = TaskQueue(base_dir=tmp_path / "tasks")
    yield q
    core.task_system._queue = None


class TestTaskCreation:
    def test_create_task_returns_task_with_id(self, queue):
        task = asyncio.run(queue.create_task(
            title="Test task",
            description="A test",
        ))
        assert task.id
        assert task.title == "Test task"
        assert task.status == TaskStatus.PENDING
        assert task.priority == TaskPriority.MEDIUM

    def test_create_task_with_all_fields(self, queue):
        task = asyncio.run(queue.create_task(
            title="Complex task",
            description="Detailed description",
            phase=TaskPhase.SECURITY,
            priority=TaskPriority.CRITICAL,
            dependencies=["dep-1", "dep-2"],
            assigned_to="security_lead",
            max_retries=5,
            metadata={"component": "mcp_server"},
        ))
        assert task.phase == TaskPhase.SECURITY
        assert task.priority == TaskPriority.CRITICAL
        assert task.dependencies == ["dep-1", "dep-2"]
        assert task.assigned_to == "security_lead"
        assert task.max_retries == 5
        assert task.metadata["component"] == "mcp_server"

    def test_create_task_persists_to_disk(self, queue, tmp_path):
        asyncio.run(queue.create_task(title="Persisted task"))
        queue_path = tmp_path / "tasks" / "queue.json"
        assert queue_path.exists()
        with open(queue_path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["title"] == "Persisted task"

    def test_create_task_adds_history_entry(self, queue):
        task = asyncio.run(queue.create_task(title="History test"))
        assert len(task.history) == 1
        assert task.history[0].event == "created"


class TestTaskLifecycle:
    def test_start_task_changes_status(self, queue):
        task = asyncio.run(queue.create_task(title="To start"))
        started = asyncio.run(queue.start_task(task.id))
        assert started.status == TaskStatus.IN_PROGRESS
        assert started.retry_count == 1

    def test_complete_task_generates_receipt(self, queue):
        task = asyncio.run(queue.create_task(title="To complete"))
        asyncio.run(queue.start_task(task.id))
        receipt = asyncio.run(queue.complete_task(
            task.id,
            tests_passed=10,
            tests_failed=0,
            duration_seconds=5.5,
        ))
        assert receipt is not None
        assert receipt.task_id == task.id
        assert receipt.tests_passed == 10
        assert receipt.tests_failed == 0
        assert receipt.hash  # non-empty

    def test_completed_task_has_receipt_hash(self, queue):
        task = asyncio.run(queue.create_task(title="Receipt hash"))
        asyncio.run(queue.start_task(task.id))
        asyncio.run(queue.complete_task(task.id))
        completed = asyncio.run(queue.get_task(task.id))
        assert completed.receipt_hash is not None
        assert completed.completed_at is not None
        assert completed.status == TaskStatus.COMPLETED

    def test_fail_task_can_retry(self, queue):
        task = asyncio.run(queue.create_task(title="Failing", max_retries=3))
        asyncio.run(queue.start_task(task.id))
        failed = asyncio.run(queue.fail_task(
            task.id, error="Something went wrong", can_retry=True
        ))
        assert failed.status == TaskStatus.PENDING
        assert failed.retry_count == 1

    def test_fail_task_permanent_after_max_retries(self, queue):
        task = asyncio.run(queue.create_task(title="Max retries", max_retries=1))
        asyncio.run(queue.start_task(task.id))
        # retry_count is now 1, max_retries is 1
        failed = asyncio.run(queue.fail_task(
            task.id, error="Final failure", can_retry=True
        ))
        assert failed.status == TaskStatus.FAILED

    def test_cancel_task(self, queue):
        task = asyncio.run(queue.create_task(title="Cancel me"))
        cancelled = asyncio.run(queue.cancel_task(task.id))
        assert cancelled.status == TaskStatus.CANCELLED


class TestDependencies:
    def test_task_blocked_by_uncompleted_dependency(self, queue):
        dep = asyncio.run(queue.create_task(title="Dependency"))
        task = asyncio.run(queue.create_task(
            title="Dependent",
            dependencies=[dep.id],
        ))
        started = asyncio.run(queue.start_task(task.id))
        assert started.status == TaskStatus.BLOCKED
        assert len(started.blockers) > 0

    def test_task_starts_when_dependency_completed(self, queue):
        dep = asyncio.run(queue.create_task(title="Dependency"))
        task = asyncio.run(queue.create_task(
            title="Dependent",
            dependencies=[dep.id],
        ))
        # Complete the dependency
        asyncio.run(queue.start_task(dep.id))
        asyncio.run(queue.complete_task(dep.id))
        # Now the dependent task should start
        started = asyncio.run(queue.start_task(task.id))
        assert started.status == TaskStatus.IN_PROGRESS

    def test_get_ready_tasks_excludes_blocked(self, queue):
        dep = asyncio.run(queue.create_task(title="Dep"))
        task = asyncio.run(queue.create_task(
            title="Blocked task",
            dependencies=[dep.id],
        ))
        ready = asyncio.run(queue.get_ready_tasks())
        ids = [t.id for t in ready]
        assert dep.id in ids
        assert task.id not in ids


class TestCheckpoints:
    def test_add_checkpoint(self, queue):
        task = asyncio.run(queue.create_task(title="Checkpoint test"))
        asyncio.run(queue.add_checkpoint(task.id, "phase1", progress=50))
        updated = asyncio.run(queue.get_task(task.id))
        assert len(updated.checkpoints) == 1
        assert updated.checkpoints[0].name == "phase1"
        assert updated.checkpoints[0].data["progress"] == 50

    def test_checkpoints_persisted(self, queue, tmp_path):
        task = asyncio.run(queue.create_task(title="Persist checkpoint"))
        asyncio.run(queue.add_checkpoint(task.id, "step1"))
        # Reload from disk
        queue2 = TaskQueue(base_dir=tmp_path / "tasks")
        reloaded = asyncio.run(queue2.get_task(task.id))
        assert reloaded is not None
        assert len(reloaded.checkpoints) == 1


class TestReceiptVerification:
    def test_receipt_verification_passes(self, queue):
        task = asyncio.run(queue.create_task(title="Verify receipt"))
        asyncio.run(queue.start_task(task.id))
        receipt = asyncio.run(queue.complete_task(task.id, tests_passed=5))
        assert verify_receipt(receipt) is True

    def test_receipt_tampering_detected(self, queue):
        task = asyncio.run(queue.create_task(title="Tamper receipt"))
        asyncio.run(queue.start_task(task.id))
        receipt = asyncio.run(queue.complete_task(task.id, tests_passed=5))
        # Tamper
        receipt.tests_passed = 100
        assert verify_receipt(receipt) is False


class TestPersistence:
    def test_queue_survives_restart(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
        import core.task_system
        core.task_system._queue = None

        q1 = TaskQueue(base_dir=tmp_path / "tasks")
        task = asyncio.run(q1.create_task(title="Survive restart"))
        task_id = task.id

        # Simulate restart
        core.task_system._queue = None
        q2 = TaskQueue(base_dir=tmp_path / "tasks")
        reloaded = asyncio.run(q2.get_task(task_id))
        assert reloaded is not None
        assert reloaded.title == "Survive restart"

    def test_completed_tasks_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
        import core.task_system
        core.task_system._queue = None

        q1 = TaskQueue(base_dir=tmp_path / "tasks")
        task = asyncio.run(q1.create_task(title="Complete and restart"))
        asyncio.run(q1.start_task(task.id))
        asyncio.run(q1.complete_task(task.id))

        # Restart
        core.task_system._queue = None
        q2 = TaskQueue(base_dir=tmp_path / "tasks")
        completed = asyncio.run(q2.get_task(task.id))
        assert completed is not None
        assert completed.status == TaskStatus.COMPLETED


class TestStats:
    def test_stats_returns_counts(self, queue):
        asyncio.run(queue.create_task(title="Task 1", priority=TaskPriority.HIGH))
        asyncio.run(queue.create_task(title="Task 2", priority=TaskPriority.LOW))
        asyncio.run(queue.create_task(title="Task 3", phase=TaskPhase.SECURITY))

        stats = asyncio.run(queue.get_stats())
        assert stats["active"] == 3
        assert stats["by_priority"]["high"] == 1
        assert stats["by_priority"]["low"] == 1
        assert stats["by_phase"]["security"] == 1
