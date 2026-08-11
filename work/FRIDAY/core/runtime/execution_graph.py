"""Execution Graph — dependency-aware task execution.

Manages a directed acyclic graph (DAG) of tasks where each task can
depend on the completion of other tasks. Executes tasks in topological
order, maximizing parallelism.

Usage::

    graph = ExecutionGraph()
    graph.add_task("fetch_data", fetch_func)
    graph.add_task("process", process_func, depends_on=["fetch_data"])
    graph.add_task("save", save_func, depends_on=["process"])
    results = await graph.execute()
    # results → {"fetch_data": ..., "process": ..., "save": ...}
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("friday.runtime.execution_graph")


class TaskState(str, Enum):
    PENDING = "pending"
    READY = "ready"        # All dependencies met
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"    # Skipped because a dependency failed


@dataclass
class GraphTask:
    """A task in the execution graph."""
    id: str
    func: Optional[Callable] = None
    args: tuple = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    state: TaskState = TaskState.PENDING
    result: Any = None
    error: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "depends_on": self.depends_on,
            "state": self.state.value,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
        }


class ExecutionGraph:
    """Dependency-aware task execution engine.

    Tasks are added with optional dependencies. When executed, the
    graph runs all ready tasks in parallel, then proceeds to the
    next layer as dependencies complete.
    """

    def __init__(self):
        self._tasks: Dict[str, GraphTask] = {}
        self._lock = asyncio.Lock()
        self._running = False

    def add_task(
        self,
        task_id: str,
        func: Optional[Callable] = None,
        depends_on: Optional[List[str]] = None,
        *args,
        **kwargs,
    ) -> GraphTask:
        """Add a task to the graph.

        Args:
            task_id: Unique task identifier.
            func: Async callable to execute.
            depends_on: List of task IDs that must complete first.
            *args, **kwargs: Arguments for the function.
        """
        if task_id in self._tasks:
            raise ValueError(f"Task {task_id} already exists")

        task = GraphTask(
            id=task_id,
            func=func,
            args=args,
            kwargs=kwargs,
            depends_on=depends_on or [],
        )
        self._tasks[task_id] = task
        logger.debug(f"Added task '{task_id}' with deps: {depends_on or []}")
        return task

    def _get_ready_tasks(self) -> List[GraphTask]:
        """Get all tasks whose dependencies are all completed."""
        ready = []
        for task in self._tasks.values():
            if task.state != TaskState.PENDING:
                continue

            # Check if all dependencies are completed
            all_deps_complete = True
            for dep_id in task.depends_on:
                dep = self._tasks.get(dep_id)
                if not dep or dep.state != TaskState.COMPLETED:
                    all_deps_complete = False
                    break

            if all_deps_complete:
                task.state = TaskState.READY
                ready.append(task)

        return ready

    def _check_failed_deps(self) -> List[GraphTask]:
        """Mark tasks as skipped if any dependency failed."""
        skipped = []
        for task in self._tasks.values():
            if task.state != TaskState.PENDING:
                continue

            for dep_id in task.depends_on:
                dep = self._tasks.get(dep_id)
                if dep and dep.state in (TaskState.FAILED, TaskState.SKIPPED):
                    task.state = TaskState.SKIPPED
                    skipped.append(task)
                    break

        return skipped

    async def execute(self) -> Dict[str, Any]:
        """Execute all tasks in dependency order.

        Returns:
            Dict mapping task_id → result (for completed tasks).
        """
        self._running = True
        results: Dict[str, Any] = {}

        while self._running:
            # Check for failed dependencies
            self._check_failed_deps()

            # Get ready tasks
            ready = self._get_ready_tasks()

            if not ready:
                # Check if there are any pending tasks (would indicate a cycle)
                pending = [t for t in self._tasks.values() if t.state == TaskState.PENDING]
                if pending:
                    logger.error(f"Deadlock detected: {len(pending)} tasks stuck in PENDING")
                    for t in pending:
                        t.state = TaskState.FAILED
                        t.error = "Deadlock: unresolvable dependencies"
                break

            # Execute all ready tasks in parallel
            tasks = [self._execute_task(task) for task in ready]
            await asyncio.gather(*tasks, return_exceptions=True)

            # Collect results
            for task in ready:
                if task.state == TaskState.COMPLETED:
                    results[task.id] = task.result

        self._running = False
        return results

    async def _execute_task(self, task: GraphTask) -> None:
        """Execute a single task."""
        task.state = TaskState.RUNNING
        task.started_at = datetime.now(timezone.utc).isoformat()

        import time
        start = time.perf_counter()

        try:
            if task.func is None:
                task.result = None
            elif asyncio.iscoroutinefunction(task.func):
                task.result = await task.func(*task.args, **task.kwargs)
            else:
                task.result = task.func(*task.args, **task.kwargs)

            task.state = TaskState.COMPLETED
            task.completed_at = datetime.now(timezone.utc).isoformat()
            task.duration_seconds = time.perf_counter() - start

            logger.debug(f"Task '{task.id}' completed in {task.duration_seconds:.2f}s")

        except Exception as exc:
            task.state = TaskState.FAILED
            task.error = str(exc)
            task.duration_seconds = time.perf_counter() - start
            logger.error(f"Task '{task.id}' failed: {exc}")

    def get_task(self, task_id: str) -> Optional[GraphTask]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> List[GraphTask]:
        """Get all tasks in the graph."""
        return list(self._tasks.values())

    def detect_cycles(self) -> bool:
        """Detect if the graph contains cycles (using DFS)."""
        WHITE, GRAY, BLACK = 0, 1, 2
        colors = {tid: WHITE for tid in self._tasks}

        def dfs(tid: str) -> bool:
            colors[tid] = GRAY
            task = self._tasks[tid]
            for dep_id in task.depends_on:
                if dep_id not in colors:
                    continue  # External dependency
                if colors[dep_id] == GRAY:
                    return True  # Back edge → cycle
                if colors[dep_id] == WHITE and dfs(dep_id):
                    return True
            colors[tid] = BLACK
            return False

        for tid in self._tasks:
            if colors[tid] == WHITE:
                if dfs(tid):
                    return True
        return False

    async def is_healthy(self) -> bool:
        """Check if the execution graph is healthy."""
        return not self.detect_cycles()

    async def stop(self) -> None:
        """Stop the execution graph."""
        self._running = False
        logger.info("Execution graph stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get execution graph statistics."""
        return {
            "total_tasks": len(self._tasks),
            "by_state": {
                s.value: sum(1 for t in self._tasks.values() if t.state == s)
                for s in TaskState
            },
            "has_cycles": self.detect_cycles(),
        }
