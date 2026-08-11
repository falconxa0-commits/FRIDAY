"""Runtime Scheduler — schedules and executes deferred tasks.

Provides:
    - One-shot timers (run after N seconds)
    - Interval timers (run every N seconds)
    - Cron-like scheduling (simplified)
    - Job queue with priority

Usage::

    scheduler = RuntimeScheduler(event_bus=bus)
    await scheduler.start()

    # Schedule a one-shot task
    job_id = scheduler.schedule_once(my_func, delay=5.0)

    # Schedule a recurring task
    job_id = scheduler.schedule_interval(my_func, interval=60.0)

    await scheduler.stop()
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("friday.runtime.scheduler")


class JobType(str, Enum):
    ONE_SHOT = "one_shot"
    INTERVAL = "interval"
    CRON = "cron"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """A scheduled job."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: JobType = JobType.ONE_SHOT
    func: Optional[Callable] = None
    args: tuple = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    delay: float = 0.0
    interval: float = 0.0
    next_run: float = 0.0
    status: JobStatus = JobStatus.PENDING
    run_count: int = 0
    last_run_at: str = ""
    last_error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "status": self.status.value,
            "delay": self.delay,
            "interval": self.interval,
            "next_run": self.next_run,
            "run_count": self.run_count,
            "last_run_at": self.last_run_at,
            "last_error": self.last_error,
            "created_at": self.created_at,
        }


class RuntimeScheduler:
    """Schedules and executes deferred tasks.

    Uses asyncio tasks for non-blocking execution. All jobs run in
    the background and their results are logged.
    """

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._jobs: Dict[str, Job] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the scheduler."""
        self._running = True
        logger.info("Runtime scheduler started")

    def schedule_once(
        self, func: Callable, delay: float = 0.0, *args, **kwargs
    ) -> str:
        """Schedule a one-shot job.

        Args:
            func: Async callable to execute.
            delay: Seconds to wait before execution.
            *args, **kwargs: Arguments to pass to the function.

        Returns:
            Job ID.
        """
        job = Job(
            type=JobType.ONE_SHOT,
            func=func,
            args=args,
            kwargs=kwargs,
            delay=delay,
            next_run=delay,
        )
        self._jobs[job.id] = job

        # Schedule execution
        task = asyncio.create_task(self._run_job(job))
        self._tasks[job.id] = task

        logger.info(f"Scheduled one-shot job {job.id[:8]} (delay={delay}s)")
        return job.id

    def schedule_interval(
        self, func: Callable, interval: float, *args, **kwargs
    ) -> str:
        """Schedule a recurring job.

        Args:
            func: Async callable to execute.
            interval: Seconds between executions.
            *args, **kwargs: Arguments to pass to the function.

        Returns:
            Job ID.
        """
        job = Job(
            type=JobType.INTERVAL,
            func=func,
            args=args,
            kwargs=kwargs,
            interval=interval,
            next_run=interval,
        )
        self._jobs[job.id] = job

        task = asyncio.create_task(self._run_interval_job(job))
        self._tasks[job.id] = task

        logger.info(f"Scheduled interval job {job.id[:8]} (interval={interval}s)")
        return job.id

    async def _run_job(self, job: Job) -> None:
        """Execute a one-shot job after its delay."""
        try:
            await asyncio.sleep(job.delay)

            if not self._running or job.status == JobStatus.CANCELLED:
                return

            job.status = JobStatus.RUNNING
            await self._execute(job)

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.last_error = str(exc)
            logger.error(f"Job {job.id[:8]} failed: {exc}")

    async def _run_interval_job(self, job: Job) -> None:
        """Execute an interval job repeatedly."""
        try:
            while self._running and job.status != JobStatus.CANCELLED:
                await asyncio.sleep(job.interval)

                if not self._running or job.status == JobStatus.CANCELLED:
                    break

                job.status = JobStatus.RUNNING
                await self._execute(job)
                job.status = JobStatus.PENDING

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.last_error = str(exc)

    async def _execute(self, job: Job) -> None:
        """Execute a job's function."""
        try:
            if asyncio.iscoroutinefunction(job.func):
                await job.func(*job.args, **job.kwargs)
            else:
                job.func(*job.args, **job.kwargs)

            job.run_count += 1
            job.last_run_at = datetime.now(timezone.utc).isoformat()
            job.status = JobStatus.COMPLETED

            if self.event_bus:
                await self.event_bus.publish(
                    "scheduler.job_completed",
                    {"job_id": job.id, "run_count": job.run_count},
                    source="scheduler",
                )

        except Exception as exc:
            job.status = JobStatus.FAILED
            job.last_error = str(exc)
            logger.error(f"Job {job.id[:8]} execution failed: {exc}")

            if self.event_bus:
                await self.event_bus.publish(
                    "scheduler.job_failed",
                    {"job_id": job.id, "error": str(exc)},
                    source="scheduler",
                )

    def cancel(self, job_id: str) -> bool:
        """Cancel a scheduled job."""
        job = self._jobs.get(job_id)
        if not job:
            return False

        job.status = JobStatus.CANCELLED

        task = self._tasks.get(job_id)
        if task:
            task.cancel()

        logger.info(f"Cancelled job {job_id[:8]}")
        return True

    def list_jobs(self) -> List[Job]:
        """List all scheduled jobs."""
        return list(self._jobs.values())

    async def is_healthy(self) -> bool:
        """Check if the scheduler is healthy."""
        return self._running

    async def stop(self) -> None:
        """Stop the scheduler and cancel all pending jobs."""
        self._running = False

        # Cancel all tasks
        for task in self._tasks.values():
            task.cancel()

        # Wait for cancellation
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

        # Mark all pending jobs as cancelled
        for job in self._jobs.values():
            if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                job.status = JobStatus.CANCELLED

        self._tasks.clear()
        logger.info("Runtime scheduler stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get scheduler statistics."""
        return {
            "total_jobs": len(self._jobs),
            "running": self._running,
            "by_status": {
                s.value: sum(1 for j in self._jobs.values() if j.status == s)
                for s in JobStatus
            },
        }
