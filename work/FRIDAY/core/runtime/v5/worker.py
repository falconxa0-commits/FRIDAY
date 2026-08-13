"""Worker Runtime — task execution with claiming, heartbeat, and recovery.

A Worker is a runtime process that:
    1. Claims tasks from the DistributedTaskQueue
    2. Executes them with timeout enforcement
    3. Sends heartbeats during execution
    4. Reports success or failure
    5. Handles cancellation
    6. Recovers from crashes (via stale task reclamation)

Workers are registered citizens of the FRIDAY civilization.

Usage::

    worker = WorkerRuntime(
        worker_id="worker-1",
        task_queue=queue,
        federation=federation,
    )
    await worker.start()
    # ... worker runs in background ...
    await worker.stop()
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional

from core.runtime.v5.distributed_task_queue import (
    DistributedTaskQueue, DistributedTask, TaskStatus,
)

logger = logging.getLogger("friday.runtime.v5.worker")


class WorkerStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class WorkerStats:
    """Worker execution statistics."""
    tasks_claimed: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_cancelled: int = 0
    total_execution_time: float = 0.0
    last_task_id: str = ""
    last_task_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tasks_claimed": self.tasks_claimed,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "tasks_cancelled": self.tasks_cancelled,
            "total_execution_time": round(self.total_execution_time, 2),
            "last_task_id": self.last_task_id,
            "last_task_at": self.last_task_at,
        }


class WorkerRuntime:
    """A task execution worker.

    The worker polls the task queue for new tasks, claims them,
    executes them, and reports results. It sends heartbeats during
    execution to prevent stale-task reclamation.

    The worker uses a function registry to map func_name → callable.
    """

    HEARTBEAT_INTERVAL = 5  # seconds
    POLL_INTERVAL = 1  # seconds between polls when idle

    def __init__(
        self,
        worker_id: str = "",
        worker_name: str = "",
        task_queue: Optional[DistributedTaskQueue] = None,
        federation=None,
        capabilities: Optional[list] = None,
    ):
        self.id = worker_id or str(uuid.uuid4())
        self.name = worker_name or f"worker-{self.id[:8]}"
        self._task_queue = task_queue
        self._federation = federation
        self._capabilities = capabilities or []
        self._status = WorkerStatus.STOPPED
        self._current_task: Optional[DistributedTask] = None
        self._func_registry: Dict[str, Callable] = {}
        self._stats = WorkerStats()
        self._poll_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False
        self._shutdown_event = asyncio.Event()

    @property
    def status(self) -> WorkerStatus:
        return self._status

    @property
    def stats(self) -> WorkerStats:
        return self._stats

    def register_function(self, name: str, func: Callable) -> None:
        """Register a function that this worker can execute."""
        self._func_registry[name] = func
        logger.debug(f"Worker {self.name}: registered function '{name}'")

    def get_status(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self._status.value,
            "capabilities": self._capabilities,
            "current_task": self._current_task.id if self._current_task else None,
            "stats": self._stats.to_dict(),
        }

    async def start(self) -> None:
        """Start the worker."""
        self._running = True
        self._status = WorkerStatus.IDLE
        self._shutdown_event.clear()

        # Start polling loop
        self._poll_task = asyncio.create_task(self._poll_loop())

        logger.info(f"Worker started: {self.name} ({self.id[:8]})")

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        self._status = WorkerStatus.STOPPING
        self._running = False
        self._shutdown_event.set()

        # Wait for current task to complete or timeout
        if self._current_task:
            logger.info(f"Worker {self.name}: waiting for current task to complete...")
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                logger.warning(f"Worker {self.name}: timed out waiting for task completion")

        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        self._status = WorkerStatus.STOPPED
        logger.info(f"Worker stopped: {self.name}")

    async def _poll_loop(self) -> None:
        """Main worker loop: claim → execute → report."""
        while self._running:
            try:
                if self._status == WorkerStatus.BUSY:
                    await asyncio.sleep(self.POLL_INTERVAL)
                    continue

                # Try to claim a task
                task = await self._task_queue.claim(self.id)
                if not task:
                    await asyncio.sleep(self.POLL_INTERVAL)
                    continue

                # Execute the task
                await self._execute_task(task)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Worker {self.name} poll loop error: {exc}")
                self._status = WorkerStatus.FAILED
                await asyncio.sleep(5)  # backoff before retry
                self._status = WorkerStatus.IDLE

    async def _execute_task(self, task: DistributedTask) -> None:
        """Execute a single task with timeout and heartbeat."""
        self._status = WorkerStatus.BUSY
        self._current_task = task
        self._stats.tasks_claimed += 1
        self._stats.last_task_id = task.id
        self._stats.last_task_at = datetime.now(timezone.utc).isoformat()

        # Start heartbeat
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(task.id))

        start_time = asyncio.get_event_loop().time()

        try:
            # Get the function to execute
            func = self._func_registry.get(task.func_name)
            if not func:
                raise RuntimeError(f"Unknown function: {task.func_name}")

            # Execute with timeout
            if asyncio.iscoroutinefunction(func):
                result = await asyncio.wait_for(
                    func(*task.args, **task.kwargs),
                    timeout=task.timeout,
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(func, *task.args, **task.kwargs),
                    timeout=task.timeout,
                )

            # Report success
            await self._task_queue.complete(task.id, self.id, result)
            self._stats.tasks_completed += 1
            logger.info(f"Worker {self.name}: task {task.id[:8]} completed")

        except asyncio.TimeoutError:
            await self._task_queue.fail(
                task.id, self.id, f"Task timed out after {task.timeout}s"
            )
            self._stats.tasks_failed += 1
            logger.warning(f"Worker {self.name}: task {task.id[:8]} timed out")

        except asyncio.CancelledError:
            # Worker is shutting down — cancel the task
            await self._task_queue.cancel(task.id)
            self._stats.tasks_cancelled += 1
            raise  # re-raise to exit the loop

        except Exception as exc:
            await self._task_queue.fail(task.id, self.id, str(exc))
            self._stats.tasks_failed += 1
            logger.error(f"Worker {self.name}: task {task.id[:8]} failed: {exc}")

        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

            self._stats.total_execution_time += (
                asyncio.get_event_loop().time() - start_time
            )
            self._current_task = None
            self._status = WorkerStatus.IDLE

    async def _heartbeat_loop(self, task_id: str) -> None:
        """Send heartbeats while a task is running."""
        while self._running:
            try:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                await self._task_queue.heartbeat(task_id, self.id)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug(f"Heartbeat error: {exc}")

    async def is_healthy(self) -> bool:
        return self._status in (WorkerStatus.IDLE, WorkerStatus.BUSY)

    async def drain(self) -> None:
        """Stop accepting new tasks (graceful shutdown)."""
        self._status = WorkerStatus.STOPPING
        # The poll loop will stop claiming tasks
