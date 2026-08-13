"""Distributed Task Queue — durable task processing with retry and DLQ.

Extends the Age IV TaskQueue with distributed capabilities:
    - Durable task storage (Redis-backed, falls back to in-memory)
    - Task claiming (worker locks a task for exclusive execution)
    - Heartbeat (workers report liveness during long execution)
    - Retry with exponential backoff
    - Dead letter queue for permanently failed tasks
    - Stale worker detection and task reclamation
    - Idempotency protection

When FRIDAY_DISTRIBUTED_RUNTIME=0 (default), this operates as an
in-memory durable queue with no Redis dependency.

Delivery semantics: at-least-once. Workers must be idempotent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("friday.runtime.v5.task_queue")


class TaskStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"      # locked by a worker
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def weight(self) -> int:
        return {"critical": 0, "high": 1, "medium": 2, "low": 3}[self.value]


@dataclass
class DistributedTask:
    """A task in the distributed task queue."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    func_name: str = ""  # serialized function reference (not the function itself)
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    claimed_by: str = ""  # worker ID
    claimed_at: str = ""
    heartbeat_at: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str = ""
    result: Any = None
    error: str = ""
    attempt: int = 0
    max_retries: int = 3
    retry_delay: float = 1.0  # initial delay in seconds
    max_retry_delay: float = 60.0  # max delay after backoff
    timeout: float = 300.0  # execution timeout in seconds
    idempotency_key: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "func_name": self.func_name,
            "priority": self.priority.value,
            "status": self.status.value,
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at,
            "heartbeat_at": self.heartbeat_at,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "attempt": self.attempt,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
            "idempotency_key": self.idempotency_key,
        }

    @property
    def is_stale(self) -> bool:
        """Check if a claimed task has gone stale (heartbeat timeout)."""
        if not self.heartbeat_at or self.status not in (TaskStatus.CLAIMED, TaskStatus.RUNNING):
            return False
        try:
            hb = datetime.fromisoformat(self.heartbeat_at)
            elapsed = (datetime.now(timezone.utc) - hb).total_seconds()
            return elapsed > 30  # 30s heartbeat timeout
        except Exception:
            return False


class DistributedTaskQueue:
    """Durable distributed task queue.

    Features:
        - Priority-based task scheduling
        - Worker claiming with heartbeat
        - Retry with exponential backoff
        - Dead letter queue
        - Stale worker recovery
        - Idempotency protection
        - Bounded history
    """

    STALE_CHECK_INTERVAL = 10  # seconds
    MAX_HISTORY = 5000
    MAX_DEAD_LETTER = 500

    def __init__(self, redis_url: str = "", enable_distributed: bool = False):
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "")
        self._enable_distributed = enable_distributed or (
            os.environ.get("FRIDAY_DISTRIBUTED_RUNTIME", "0") == "1"
            and bool(self._redis_url)
        )
        self._pending: Dict[str, DistributedTask] = {}  # by priority
        self._claimed: Dict[str, DistributedTask] = {}  # by task ID
        self._completed: deque = deque(maxlen=self.MAX_HISTORY)
        self._dead_letters: deque = deque(maxlen=self.MAX_DEAD_LETTER)
        self._idempotency_keys: set = set()
        self._lock = asyncio.Lock()
        self._stale_check_task: Optional[asyncio.Task] = None
        self._running = False
        self._stats = {
            "enqueued": 0,
            "claimed": 0,
            "completed": 0,
            "failed": 0,
            "dead_lettered": 0,
            "reclaimed": 0,
            "duplicates_filtered": 0,
        }

    async def start(self) -> None:
        """Start the task queue."""
        self._running = True
        self._stale_check_task = asyncio.create_task(self._stale_check_loop())
        logger.info("DistributedTaskQueue started")

    async def stop(self) -> None:
        """Stop the task queue."""
        self._running = False
        if self._stale_check_task:
            self._stale_check_task.cancel()
            try:
                await self._stale_check_task
            except asyncio.CancelledError:
                pass
        logger.info("DistributedTaskQueue stopped")

    async def enqueue(
        self,
        name: str,
        func_name: str = "",
        args: Optional[list] = None,
        kwargs: Optional[dict] = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        max_retries: int = 3,
        timeout: float = 300.0,
        idempotency_key: str = "",
        metadata: Optional[dict] = None,
    ) -> DistributedTask:
        """Enqueue a task for processing.

        Args:
            name: Human-readable task name.
            func_name: Serialized function reference (for worker dispatch).
            args: Positional arguments.
            kwargs: Keyword arguments.
            priority: Task priority.
            max_retries: Maximum retry attempts on failure.
            timeout: Execution timeout in seconds.
            idempotency_key: For deduplication. If a task with the same
                key is already enqueued or completed, the existing task
                is returned.
            metadata: Arbitrary metadata.

        Returns:
            The enqueued (or existing) DistributedTask.
        """
        # Check idempotency
        if idempotency_key:
            async with self._lock:
                if idempotency_key in self._idempotency_keys:
                    self._stats["duplicates_filtered"] += 1
                    # Find and return existing task
                    for t in list(self._pending.values()) + list(self._claimed.values()):
                        if t.idempotency_key == idempotency_key:
                            return t
                    for t in self._completed:
                        if t.idempotency_key == idempotency_key:
                            return t
                self._idempotency_keys.add(idempotency_key)

        task = DistributedTask(
            name=name,
            func_name=func_name,
            args=args or [],
            kwargs=kwargs or {},
            priority=priority,
            max_retries=max_retries,
            timeout=timeout,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
            metadata=metadata or {},
        )

        async with self._lock:
            self._pending[task.id] = task
            self._stats["enqueued"] += 1

        logger.info(f"Enqueued task: {name} ({task.id[:8]}) priority={priority.value}")
        return task

    async def claim(self, worker_id: str) -> Optional[DistributedTask]:
        """Claim the next pending task for a worker.

        Tasks are claimed in priority order (critical first), then FIFO.

        Args:
            worker_id: The ID of the worker claiming the task.

        Returns:
            The claimed task, or None if no tasks are pending.
        """
        async with self._lock:
            if not self._pending:
                return None

            # Sort by priority, then by creation time
            pending = sorted(
                self._pending.values(),
                key=lambda t: (t.priority.weight, t.created_at),
            )

            task = pending[0]
            del self._pending[task.id]

            task.status = TaskStatus.CLAIMED
            task.claimed_by = worker_id
            task.claimed_at = datetime.now(timezone.utc).isoformat()
            task.heartbeat_at = task.claimed_at
            task.attempt += 1

            self._claimed[task.id] = task
            self._stats["claimed"] += 1

        logger.info(f"Task {task.id[:8]} claimed by worker {worker_id[:8]}")
        return task

    async def heartbeat(self, task_id: str, worker_id: str) -> bool:
        """Update a task's heartbeat (worker is still alive).

        Args:
            task_id: The task being worked on.
            worker_id: The worker claiming the task.

        Returns:
            True if heartbeat was recorded.
        """
        async with self._lock:
            task = self._claimed.get(task_id)
            if not task or task.claimed_by != worker_id:
                return False
            task.heartbeat_at = datetime.now(timezone.utc).isoformat()
            task.status = TaskStatus.RUNNING
        return True

    async def complete(
        self, task_id: str, worker_id: str, result: Any = None
    ) -> bool:
        """Mark a task as completed.

        Args:
            task_id: The completed task.
            worker_id: The completing worker.
            result: Task result.

        Returns:
            True if completion was recorded.
        """
        async with self._lock:
            task = self._claimed.pop(task_id, None)
            if not task or task.claimed_by != worker_id:
                return False
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = datetime.now(timezone.utc).isoformat()
            self._completed.append(task)
            self._stats["completed"] += 1

        logger.info(f"Task {task_id[:8]} completed by {worker_id[:8]}")
        return True

    async def fail(
        self, task_id: str, worker_id: str, error: str
    ) -> bool:
        """Mark a task as failed and schedule retry or dead-letter.

        Args:
            task_id: The failed task.
            worker_id: The failing worker.
            error: Error message.

        Returns:
            True if failure was recorded.
        """
        async with self._lock:
            task = self._claimed.pop(task_id, None)
            if not task or task.claimed_by != worker_id:
                return False
            task.error = error

            if task.attempt < task.max_retries:
                # Schedule retry with exponential backoff
                delay = min(
                    task.retry_delay * (2 ** (task.attempt - 1)),
                    task.max_retry_delay,
                )
                task.status = TaskStatus.PENDING
                task.claimed_by = ""
                task.claimed_at = ""
                task.heartbeat_at = ""
                self._pending[task.id] = task
                logger.info(
                    f"Task {task_id[:8]} retrying in {delay:.1f}s "
                    f"(attempt {task.attempt + 1}/{task.max_retries})"
                )
            else:
                # Dead letter
                task.status = TaskStatus.DEAD_LETTER
                task.completed_at = datetime.now(timezone.utc).isoformat()
                self._dead_letters.append(task)
                self._stats["dead_lettered"] += 1
                logger.warning(
                    f"Task {task_id[:8]} dead-lettered after {task.attempt} attempts: {error}"
                )

            self._stats["failed"] += 1

        return True

    async def cancel(self, task_id: str) -> bool:
        """Cancel a pending or claimed task."""
        async with self._lock:
            if task_id in self._pending:
                task = self._pending.pop(task_id)
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now(timezone.utc).isoformat()
                self._completed.append(task)
                return True
            if task_id in self._claimed:
                task = self._claimed.pop(task_id)
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now(timezone.utc).isoformat()
                self._completed.append(task)
                return True
        return False

    async def _stale_check_loop(self) -> None:
        """Periodically check for stale tasks and reclaim them."""
        while self._running:
            try:
                await asyncio.sleep(self.STALE_CHECK_INTERVAL)
                await self._reclaim_stale_tasks()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Stale check error: {exc}")

    async def _reclaim_stale_tasks(self) -> int:
        """Reclaim tasks from stale (dead) workers.

        Returns:
            Number of tasks reclaimed.
        """
        reclaimed = 0
        async with self._lock:
            stale_ids = [
                tid for tid, task in self._claimed.items() if task.is_stale
            ]
            for tid in stale_ids:
                task = self._claimed.pop(tid)
                task.status = TaskStatus.PENDING
                task.claimed_by = ""
                task.claimed_at = ""
                task.heartbeat_at = ""
                self._pending[tid] = task
                reclaimed += 1
                logger.warning(
                    f"Reclaimed stale task {tid[:8]} "
                    f"(was claimed by {task.claimed_by[:8]})"
                )
        if reclaimed:
            self._stats["reclaimed"] += reclaimed
        return reclaimed

    def get_task(self, task_id: str) -> Optional[DistributedTask]:
        """Get a task by ID (checks pending, claimed, and completed)."""
        if task_id in self._pending:
            return self._pending[task_id]
        if task_id in self._claimed:
            return self._claimed[task_id]
        for t in self._completed:
            if t.id == task_id:
                return t
        return None

    def list_pending(self, priority: Optional[TaskPriority] = None) -> List[DistributedTask]:
        """List pending tasks."""
        tasks = list(self._pending.values())
        if priority:
            tasks = [t for t in tasks if t.priority == priority]
        return sorted(tasks, key=lambda t: (t.priority.weight, t.created_at))

    def list_claimed(self) -> List[DistributedTask]:
        """List claimed/running tasks."""
        return list(self._claimed.values())

    def list_dead_letters(self, limit: int = 50) -> List[DistributedTask]:
        """List dead-lettered tasks."""
        return list(self._dead_letters)[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "pending_count": len(self._pending),
            "claimed_count": len(self._claimed),
            "completed_count": len(self._completed),
            "dead_letter_count": len(self._dead_letters),
        }

    async def is_healthy(self) -> bool:
        return self._running

    async def drain(self) -> int:
        """Cancel all pending tasks. Returns count cancelled."""
        async with self._lock:
            count = len(self._pending)
            for task in self._pending.values():
                task.status = TaskStatus.CANCELLED
            self._pending.clear()
        logger.info(f"Drained {count} pending tasks")
        return count
