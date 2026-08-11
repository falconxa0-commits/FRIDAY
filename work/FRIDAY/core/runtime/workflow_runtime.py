"""Workflow Runtime — manages multi-step workflows.

A workflow is a named, ordered set of steps (each a callable plus its
dependencies) that the runtime executes in dependency order on top of
the existing :class:`ExecutionGraph`. Workflows track their own
lifecycle status (``pending`` → ``running`` → ``completed`` /
``failed`` / ``cancelled``) independently of the underlying graph so
that the caller can poll a single status object.

Usage::

    wr = WorkflowRuntime()

    async def fetch():  return {"rows": [1, 2, 3]}
    async def transform(rows):  return [r * 2 for r in rows["rows"]]
    async def save(rows):  return {"saved": len(rows)}

    wf = await wr.create_workflow(
        "etl",
        steps=[
            WorkflowStep(id="fetch", name="Fetch", func=fetch),
            WorkflowStep(id="transform", name="Transform",
                        func=transform, depends_on=["fetch"]),
            WorkflowStep(id="save", name="Save",
                        func=save, depends_on=["transform"]),
        ],
    )
    results = await wr.execute_workflow(wf.id)
    # results → {"fetch": {"rows": [1,2,3]}, "transform": [2,4,6],
    #            "save": {"saved": 3}}
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.runtime.execution_graph import ExecutionGraph, TaskState

logger = logging.getLogger("friday.runtime.workflow_runtime")


class WorkflowStatusEnum(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class WorkflowStep:
    """A single step in a workflow.

    Attributes:
        id: Unique step identifier (used by ``depends_on``).
        name: Human-readable name.
        func: Async or sync callable. If the step declares
            ``depends_on``, the callable receives a single positional
            argument: a dict mapping each dependency step id to its
            result. If the callable declares no parameters (or is
            otherwise nullary in spirit), pass an empty dict.
        depends_on: List of step IDs that must complete first.
    """
    id: str
    name: str = ""
    func: Optional[Callable] = None
    depends_on: List[str] = field(default_factory=list)


@dataclass
class WorkflowStatus:
    """Snapshot of a workflow's execution state."""
    workflow_id: str
    name: str
    status: WorkflowStatusEnum = WorkflowStatusEnum.PENDING
    started_at: str = ""
    completed_at: str = ""
    step_states: Dict[str, str] = field(default_factory=dict)
    step_errors: Dict[str, str] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "step_states": dict(self.step_states),
            "step_errors": dict(self.step_errors),
            "error": self.error,
        }


@dataclass
class Workflow:
    """A registered workflow."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    steps: List[WorkflowStep] = field(default_factory=list)
    status: WorkflowStatus = field(default_factory=lambda: WorkflowStatus(
        workflow_id="", name=""
    ))
    graph: Optional[ExecutionGraph] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "steps": [
                {
                    "id": s.id,
                    "name": s.name,
                    "depends_on": list(s.depends_on),
                }
                for s in self.steps
            ],
            "status": self.status.to_dict(),
            "created_at": self.created_at,
        }


class WorkflowRuntime:
    """Manages multi-step workflows built on top of ExecutionGraph."""

    def __init__(self, event_bus: Any = None):
        self._workflows: Dict[str, Workflow] = {}
        self._event_bus = event_bus
        self._running = True

    async def create_workflow(
        self, name: str, steps: List[WorkflowStep]
    ) -> Workflow:
        """Register a new workflow.

        Args:
            name: Human-readable workflow name.
            steps: Ordered list of WorkflowStep objects.

        Returns:
            The created Workflow (status=pending). The workflow is
            not executed until ``execute_workflow`` is called.
        """
        # Validate uniqueness of step IDs
        ids = [s.id for s in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Workflow steps must have unique IDs")

        # Validate that all depends_on references exist
        id_set = set(ids)
        for step in steps:
            for dep in step.depends_on:
                if dep not in id_set:
                    raise ValueError(
                        f"Step '{step.id}' depends on unknown step '{dep}'"
                    )

        wf = Workflow(name=name, steps=list(steps))
        wf.status = WorkflowStatus(workflow_id=wf.id, name=name)
        wf.graph = self._build_graph(steps)
        self._workflows[wf.id] = wf

        # Seed step_states
        for step in steps:
            wf.status.step_states[step.id] = TaskState.PENDING.value

        logger.debug(
            f"Created workflow '{name}' ({wf.id[:8]}) with {len(steps)} steps"
        )

        if self._event_bus:
            await self._event_bus.publish(
                "workflow.created",
                {"workflow_id": wf.id, "name": name, "step_count": len(steps)},
                source="workflow_runtime",
            )
        return wf

    def _build_graph(self, steps: List[WorkflowStep]) -> ExecutionGraph:
        """Build an ExecutionGraph from workflow steps.

        Each step is wrapped in a closure that resolves its
        dependencies' results from the graph before invoking the
        step's function.
        """
        graph = ExecutionGraph()
        for step in steps:
            graph.add_task(
                step.id,
                self._make_step_runner(step.func, step.depends_on, graph),
                depends_on=step.depends_on,
            )
        return graph

    def _make_step_runner(
        self,
        func: Optional[Callable],
        depends_on: List[str],
        graph: ExecutionGraph,
    ) -> Callable:
        """Return a closure that runs a step with its dep results."""

        async def runner() -> Any:
            if func is None:
                return None

            # Gather dependency results
            dep_results: Dict[str, Any] = {}
            for dep_id in depends_on:
                dep_task = graph.get_task(dep_id)
                if dep_task is not None:
                    dep_results[dep_id] = dep_task.result

            try:
                if asyncio.iscoroutinefunction(func):
                    if depends_on:
                        return await func(dep_results)
                    return await func()
                # Sync function
                if depends_on:
                    return func(dep_results)
                return func()
            except TypeError:
                # Function signature may not accept the dep dict —
                # fall back to a nullary call.
                if asyncio.iscoroutinefunction(func):
                    return await func()
                return func()

        return runner

    async def execute_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Execute a registered workflow.

        Args:
            workflow_id: ID of the workflow to execute.

        Returns:
            Dict mapping step id → result for completed steps. Empty
            dict if the workflow is unknown or not in a runnable
            state.
        """
        wf = self._workflows.get(workflow_id)
        if wf is None or wf.graph is None:
            return {}

        if wf.status.status not in (
            WorkflowStatusEnum.PENDING,
            WorkflowStatusEnum.FAILED,
        ):
            # Already running or completed — refuse to re-execute.
            return {}

        wf.status.status = WorkflowStatusEnum.RUNNING
        wf.status.started_at = datetime.now(timezone.utc).isoformat()
        wf.status.error = ""

        if self._event_bus:
            await self._event_bus.publish(
                "workflow.started",
                {"workflow_id": workflow_id, "name": wf.name},
                source="workflow_runtime",
            )

        # Reset graph task states in case of re-execution after failure.
        for task in wf.graph.get_all_tasks():
            task.state = TaskState.PENDING
            task.result = None
            task.error = ""

        try:
            results = await wf.graph.execute()
        except Exception as exc:
            wf.status.status = WorkflowStatusEnum.FAILED
            wf.status.error = str(exc)
            wf.status.completed_at = datetime.now(timezone.utc).isoformat()
            logger.error(f"Workflow {workflow_id[:8]} failed: {exc}")
            return {}

        # Sync step states from the graph
        any_failed = False
        for task in wf.graph.get_all_tasks():
            wf.status.step_states[task.id] = task.state.value
            if task.error:
                wf.status.step_errors[task.id] = task.error
            if task.state == TaskState.FAILED:
                any_failed = True

        wf.status.status = (
            WorkflowStatusEnum.FAILED if any_failed
            else WorkflowStatusEnum.COMPLETED
        )
        wf.status.completed_at = datetime.now(timezone.utc).isoformat()

        if self._event_bus:
            await self._event_bus.publish(
                "workflow.completed" if not any_failed else "workflow.failed",
                {"workflow_id": workflow_id, "name": wf.name,
                 "step_count": len(wf.steps)},
                source="workflow_runtime",
            )

        return results

    async def get_workflow_status(self, workflow_id: str) -> WorkflowStatus:
        """Get the status of a workflow.

        Returns:
            A WorkflowStatus snapshot. If the workflow is unknown,
            returns a status with ``status=FAILED`` and
            ``error="unknown workflow"``.
        """
        wf = self._workflows.get(workflow_id)
        if wf is None:
            return WorkflowStatus(
                workflow_id=workflow_id,
                name="",
                status=WorkflowStatusEnum.FAILED,
                error="unknown workflow",
            )
        return wf.status

    async def cancel_workflow(self, workflow_id: str) -> bool:
        """Cancel a workflow.

        Cancelling marks the workflow as ``cancelled`` and stops the
        underlying graph. In-progress steps are not forcibly
        interrupted; the graph simply stops scheduling new ready
        tasks.

        Returns:
            True if the workflow was found and was in a cancellable
            state (pending or running); False otherwise.
        """
        wf = self._workflows.get(workflow_id)
        if wf is None:
            return False

        if wf.status.status not in (
            WorkflowStatusEnum.PENDING,
            WorkflowStatusEnum.RUNNING,
        ):
            return False

        wf.status.status = WorkflowStatusEnum.CANCELLED
        wf.status.completed_at = datetime.now(timezone.utc).isoformat()
        if wf.graph is not None:
            await wf.graph.stop()

        if self._event_bus:
            await self._event_bus.publish(
                "workflow.cancelled",
                {"workflow_id": workflow_id},
                source="workflow_runtime",
            )
        logger.debug(f"Cancelled workflow {workflow_id[:8]}")
        return True

    async def list_workflows(self) -> List[Workflow]:
        """List all registered workflows."""
        return list(self._workflows.values())

    async def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        """Get a workflow by ID."""
        return self._workflows.get(workflow_id)

    async def is_healthy(self) -> bool:
        """Check if the workflow runtime is healthy."""
        return self._running

    async def stop(self) -> None:
        """Stop the workflow runtime."""
        self._running = False
        for wf in self._workflows.values():
            if wf.graph is not None and wf.status.status == WorkflowStatusEnum.RUNNING:
                await wf.graph.stop()
        logger.info("Workflow runtime stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get workflow runtime statistics."""
        return {
            "total_workflows": len(self._workflows),
            "by_status": {
                s.value: sum(
                    1 for w in self._workflows.values()
                    if w.status.status == s
                )
                for s in WorkflowStatusEnum
            },
        }
