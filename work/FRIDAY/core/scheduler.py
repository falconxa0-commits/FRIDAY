"""Friday Scheduler — task scheduling with persistence and event-driven triggers.

Key improvements:
- Scheduled tasks are persisted to the database so they survive restarts.
- Event-driven scheduling via ``asyncio.Event`` instead of pure polling.
- More efficient: tasks wake up only when needed.
"""

import asyncio
import datetime
import json
import logging
import os
import uuid
from typing import Dict, Any, Callable, List, Optional

logger = logging.getLogger("FridayScheduler")


class FridayScheduler:
    """Schedule and manage recurring and one-off tasks with persistence."""

    PERSIST_PATH = "scheduler_tasks.json"

    def __init__(self):
        self.tasks: Dict[str, dict] = {}
        self.running = False
        self.logger = logging.getLogger("FridayScheduler")
        self._wake_event = asyncio.Event()  # Event-driven wake-up
        self._load_persisted()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self):
        """Persist task metadata to disk (function references are not saved)."""
        try:
            serializable = {}
            for tid, task in self.tasks.items():
                serializable[tid] = {
                    "id": task["id"],
                    "name": task["name"],
                    "task_type": task.get("task_type", "interval"),
                    "interval": task["interval"],
                    "run_at": (
                        task["run_at"].isoformat()
                        if isinstance(task["run_at"], datetime.datetime)
                        else None
                    ),
                    "params": task.get("params", {}),
                    "last_run": (
                        task["last_run"].isoformat()
                        if isinstance(task.get("last_run"), datetime.datetime)
                        else None
                    ),
                    "status": task.get("status", "scheduled"),
                    "is_active": task.get("is_active", True),
                }
            with open(self.PERSIST_PATH, "w") as f:
                json.dump(serializable, f, indent=2, default=str)
        except Exception:
            self.logger.exception("Failed to persist scheduler tasks")

    def _load_persisted(self):
        """Load previously persisted task metadata from disk."""
        if not os.path.exists(self.PERSIST_PATH):
            return
        try:
            with open(self.PERSIST_PATH, "r") as f:
                data = json.load(f)
            for tid, task_data in data.items():
                task_data["func"] = None  # Functions must be re-registered
                run_at = task_data.get("run_at")
                if run_at:
                    task_data["run_at"] = datetime.datetime.fromisoformat(run_at)
                last_run = task_data.get("last_run")
                if last_run:
                    task_data["last_run"] = datetime.datetime.fromisoformat(last_run)
                self.tasks[tid] = task_data
            self.logger.info(
                f"Restored {len(self.tasks)} persisted scheduler tasks"
            )
        except Exception:
            self.logger.exception("Failed to load persisted scheduler tasks")

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    async def add_task(
        self,
        name: str,
        coroutine_func: Optional[Callable] = None,
        interval_seconds: Optional[int] = None,
        run_at: Optional[datetime.datetime] = None,
        params: Optional[Dict] = None,
        task_type: str = "interval",
    ) -> str:
        """Schedule a new task.

        Args:
            name: Human-readable task name.
            coroutine_func: Async callable to execute.  If ``None``, the
                task is persisted but won't execute until a function is
                registered later via ``update_task_func``.
            interval_seconds: Repeat interval in seconds (for recurring tasks).
            run_at: Specific time to run (for one-off tasks).
            params: Keyword arguments to pass to the coroutine.
            task_type: One of "interval", "cron", or "once".

        Returns:
            The task ID.
        """
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = {
            "id": task_id,
            "name": name,
            "func": coroutine_func,
            "task_type": task_type,
            "interval": interval_seconds,
            "run_at": run_at,
            "params": params or {},
            "last_run": None,
            "status": "scheduled",
            "is_active": True,
        }
        self._persist()
        self.logger.info(f"Scheduled task '{name}' (ID: {task_id})")

        # Wake the scheduler loop so it picks up the new task immediately
        self._wake_event.set()

        return task_id

    def update_task_func(self, task_id: str, func: Callable):
        """Register or replace the function for a persisted task."""
        if task_id in self.tasks:
            self.tasks[task_id]["func"] = func
        else:
            self.logger.warning(f"Task {task_id} not found for func update")

    # ------------------------------------------------------------------
    # Scheduler lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Start the scheduler loop."""
        if self.running:
            return
        self.running = True
        asyncio.create_task(self._loop())

    async def stop(self):
        """Stop the scheduler loop."""
        self.running = False
        self._wake_event.set()  # Break out of the wait

    async def _loop(self):
        """Main scheduling loop — event-driven with polling fallback."""
        while self.running:
            now = datetime.datetime.now()
            next_run_times = []

            for tid, task in list(self.tasks.items()):
                if not task.get("is_active", True):
                    continue
                if task.get("func") is None:
                    continue  # No function registered yet

                should_run = False

                if task.get("run_at") and now >= task["run_at"]:
                    should_run = True
                    task["run_at"] = None  # One-shot
                elif task.get("interval"):
                    if (
                        not task.get("last_run")
                        or (now - task["last_run"]).total_seconds()
                        >= task["interval"]
                    ):
                        should_run = True
                    else:
                        # Track when this task will next be due
                        elapsed = (
                            (now - task["last_run"]).total_seconds()
                            if task["last_run"]
                            else task["interval"]
                        )
                        remaining = task["interval"] - elapsed
                        next_run_times.append(remaining)

                if should_run:
                    task["status"] = "running"
                    try:
                        self.logger.info(
                            f"Executing scheduled task: {task['name']}"
                        )
                        await task["func"](**task.get("params", {}))
                        task["last_run"] = datetime.datetime.now()
                        task["status"] = (
                            "scheduled" if task.get("interval") else "completed"
                        )
                        if task["status"] == "completed":
                            self.tasks.pop(tid, None)
                    except Exception as exc:
                        self.logger.error(
                            f"Error in scheduled task '{task['name']}': {exc}"
                        )
                        task["status"] = "failed"

                    self._persist()

            # Sleep until the next task is due, or until woken by add_task
            wait_time = 10  # Default polling interval
            if next_run_times:
                wait_time = max(min(next_run_times), 1)
                wait_time = min(wait_time, 60)  # Cap at 60s

            self._wake_event.clear()
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(), timeout=wait_time
                )
            except asyncio.TimeoutError:
                pass  # Normal — just means no new tasks were added

    # ------------------------------------------------------------------
    # Query / management
    # ------------------------------------------------------------------

    def get_tasks(self) -> List[dict]:
        """Return all tasks."""
        return list(self.tasks.values())

    def get_active_tasks(self) -> List[dict]:
        """Return only active tasks."""
        return [t for t in self.tasks.values() if t.get("is_active", True)]

    def remove_task(self, task_id: str) -> bool:
        """Remove a task by ID."""
        if task_id in self.tasks:
            del self.tasks[task_id]
            self._persist()
            self._wake_event.set()
            return True
        return False

    def pause_task(self, task_id: str) -> bool:
        """Pause a task without removing it."""
        if task_id in self.tasks:
            self.tasks[task_id]["is_active"] = False
            self._persist()
            return True
        return False

    def resume_task(self, task_id: str) -> bool:
        """Resume a paused task."""
        if task_id in self.tasks:
            self.tasks[task_id]["is_active"] = True
            self._persist()
            self._wake_event.set()
            return True
        return False


# Module-level singleton
_scheduler: Optional[FridayScheduler] = None


def get_scheduler(instance=None) -> FridayScheduler:
    """Get the singleton FridayScheduler instance.

    Args:
        instance: Optional instance to inject. When provided, the
            singleton is replaced with this instance. This is primarily
            intended for tests to substitute mock/fake instances.
    """
    global _scheduler
    if instance is not None:
        _scheduler = instance
    if _scheduler is None:
        _scheduler = FridayScheduler()
    return _scheduler
