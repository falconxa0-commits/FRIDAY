"""Kernel Event Loop — unified event processing.

Wraps the asyncio event loop with additional features:
    - Task tracking
    - Metrics collection
    - Graceful shutdown
    - Health monitoring

This is NOT a replacement for asyncio — it's a management layer
that sits on top of asyncio.run() and provides observability.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("friday.runtime.kernel.event_loop")


@dataclass
class EventLoopStats:
    """Statistics for the kernel event loop."""
    tasks_created: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_active: int = 0
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    uptime_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tasks_created": self.tasks_created,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "tasks_active": self.tasks_active,
            "started_at": self.started_at,
            "uptime_seconds": round(self.uptime_seconds, 1),
        }


class KernelEventLoop:
    """Unified event loop with tracking and metrics.

    Wraps asyncio to provide:
        - Task creation tracking
        - Completion/failure counting
        - Active task monitoring
        - Graceful shutdown
    """

    def __init__(self):
        self._stats = EventLoopStats()
        self._tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._start_time: Optional[float] = None
        self._lock = asyncio.Lock()

    @property
    def stats(self) -> EventLoopStats:
        if self._start_time:
            self._stats.uptime_seconds = time.time() - self._start_time
        self._stats.tasks_active = sum(
            1 for t in self._tasks.values() if not t.done()
        )
        return self._stats

    async def start(self) -> None:
        """Start the event loop manager."""
        self._running = True
        self._start_time = time.time()
        logger.info("Kernel event loop started")

    async def create_task(
        self,
        name: str,
        coro: asyncio.coroutines,
    ) -> asyncio.Task:
        """Create a tracked asyncio task."""
        async with self._lock:
            task = asyncio.create_task(coro, name=name)
            self._tasks[name] = task
            self._stats.tasks_created += 1

            # Add done callback
            task.add_done_callback(self._on_task_done)
            return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Callback when a task completes."""
        self._stats.tasks_completed += 1
        if task.exception():
            self._stats.tasks_failed += 1
            logger.error(f"Task {task.get_name()} failed: {task.exception()}")

    async def cancel_task(self, name: str) -> bool:
        """Cancel a tracked task."""
        task = self._tasks.get(name)
        if not task or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return True

    def get_active_tasks(self) -> List[str]:
        """Get names of active (not done) tasks."""
        return [name for name, task in self._tasks.items() if not task.done()]

    async def wait_all(self, timeout: float = 30.0) -> None:
        """Wait for all tasks to complete."""
        active = [t for t in self._tasks.values() if not t.done()]
        if active:
            await asyncio.wait_for(
                asyncio.gather(*active, return_exceptions=True),
                timeout=timeout,
            )

    async def shutdown(self) -> None:
        """Cancel all tasks and shut down."""
        self._running = False
        for name in list(self._tasks.keys()):
            await self.cancel_task(name)
        logger.info("Kernel event loop shut down")

    def get_stats(self) -> Dict[str, Any]:
        return self.stats.to_dict()

    async def is_healthy(self) -> bool:
        active = len(self.get_active_tasks())
        return active < 1000  # unhealthy if too many active tasks

    async def stop(self) -> None:
        await self.shutdown()
