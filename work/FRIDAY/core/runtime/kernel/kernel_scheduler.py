"""Kernel Scheduler — fair priority-based task scheduling.

Provides priority-based scheduling with round-robin within each
priority level. This is NOT an OS scheduler — it's a userspace
cooperative scheduler for managing runtime tasks.

Design:
    - 4 priority levels: CRITICAL > HIGH > NORMAL > LOW
    - Round-robin within each level
    - Higher priority always preempts lower
    - Fair: no task can starve indefinitely (aging)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger("friday.runtime.kernel.scheduler")


class TaskPriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass
class ScheduledTask:
    """A task in the kernel scheduler."""
    id: str
    func: Callable
    priority: TaskPriority = TaskPriority.NORMAL
    args: tuple = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    status: str = "pending"  # pending, running, completed, failed
    result: Any = None
    error: str = ""
    # Aging: increase priority after waiting too long
    age_ticks: int = 0

    @property
    def wait_time(self) -> float:
        return time.time() - self.created_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "priority": self.priority.name,
            "status": self.status,
            "wait_time": round(self.wait_time, 3),
            "created_at": datetime.fromtimestamp(self.created_at, tz=timezone.utc).isoformat(),
        }


class KernelScheduler:
    """Fair priority-based scheduler.

    Tasks are organized into 4 priority queues. The scheduler always
    processes the highest-priority queue first. Within a queue, tasks
    are processed in FIFO order with aging to prevent starvation.
    """

    # Aging threshold: after this many ticks waiting, bump priority
    AGING_THRESHOLD = 100  # ticks

    def __init__(self):
        self._queues: Dict[TaskPriority, Deque[ScheduledTask]] = {
            TaskPriority.CRITICAL: deque(),
            TaskPriority.HIGH: deque(),
            TaskPriority.NORMAL: deque(),
            TaskPriority.LOW: deque(),
        }
        self._completed: List[ScheduledTask] = []
        self._running = False
        self._tick_count = 0
        self._total_executed = 0
        self._total_failures = 0
        self._lock = asyncio.Lock()

    async def submit(
        self,
        task_id: str,
        func: Callable,
        priority: TaskPriority = TaskPriority.NORMAL,
        *args,
        **kwargs,
    ) -> str:
        """Submit a task to the scheduler."""
        task = ScheduledTask(
            id=task_id, func=func, priority=priority,
            args=args, kwargs=kwargs,
        )
        async with self._lock:
            self._queues[priority].append(task)
        logger.debug(f"Submitted task {task_id} at priority {priority.name}")
        return task_id

    async def next_task(self) -> Optional[ScheduledTask]:
        """Get the next task to execute (highest priority, oldest)."""
        async with self._lock:
            self._tick_count += 1
            self._apply_aging()

            for priority in TaskPriority:
                queue = self._queues[priority]
                if queue:
                    return queue.popleft()
        return None

    def _apply_aging(self) -> None:
        """Bump priority of tasks that have been waiting too long."""
        for priority in [TaskPriority.LOW, TaskPriority.NORMAL, TaskPriority.HIGH]:
            queue = self._queues[priority]
            if not queue:
                continue
            remaining = deque()
            while queue:
                task = queue.popleft()
                task.age_ticks += 1
                if task.age_ticks >= self.AGING_THRESHOLD and priority > TaskPriority.CRITICAL:
                    # Bump to next higher priority
                    new_priority = TaskPriority(priority.value - 1)
                    self._queues[new_priority].append(task)
                    logger.debug(f"Aged task {task.id} to {new_priority.name}")
                else:
                    remaining.append(task)
            self._queues[priority] = remaining

    async def run_once(self) -> Optional[ScheduledTask]:
        """Execute the next pending task."""
        task = await self.next_task()
        if not task:
            return None

        task.started_at = time.time()
        task.status = "running"

        try:
            if asyncio.iscoroutinefunction(task.func):
                task.result = await task.func(*task.args, **task.kwargs)
            else:
                task.result = await asyncio.to_thread(task.func, *task.args, **task.kwargs)
            task.status = "completed"
            self._total_executed += 1
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            self._total_failures += 1
            logger.error(f"Task {task.id} failed: {exc}")

        task.completed_at = time.time()
        self._completed.append(task)
        return task

    async def run_all(self) -> List[ScheduledTask]:
        """Execute all pending tasks until queues are empty."""
        results = []
        while True:
            task = await self.run_once()
            if not task:
                break
            results.append(task)
        return results

    def get_queue_depths(self) -> Dict[str, int]:
        """Get pending task count by priority."""
        return {p.name: len(q) for p, q in self._queues.items()}

    def get_stats(self) -> Dict[str, Any]:
        return {
            "tick_count": self._tick_count,
            "total_executed": self._total_executed,
            "total_failures": self._total_failures,
            "queue_depths": self.get_queue_depths(),
            "completed_count": len(self._completed),
        }

    async def is_healthy(self) -> bool:
        total_pending = sum(len(q) for q in self._queues.values())
        return total_pending < 1000  # unhealthy if backlog > 1000

    async def stop(self) -> None:
        self._running = False
        logger.info("Kernel scheduler stopped")
