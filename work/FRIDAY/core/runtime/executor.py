"""Runtime Executor — the unified execution path.

ALL runtime execution flows through this executor. It provides:
    - Synchronous execution (direct call)
    - Asynchronous execution (via scheduler)
    - Workflow execution (multi-step)
    - Graph execution (dependency-aware)

This replaces the pattern where different subsystems each have their
own execution logic. Now there is ONE executor that routes to the
appropriate runtime service.

Design principles:
    - **Single entry point**: All execution goes through RuntimeExecutor.
    - **Observable**: Every execution emits events.
    - **Resource-aware**: Every execution consumes managed resources.
    - **Capability-checked**: Every execution verifies permissions.

Usage::

    executor = RuntimeExecutor(
        event_bus=bus,
        execution_graph=graph,
        workflow_runtime=workflow,
        scheduler=scheduler,
        capability_registry=registry,
        resource_manager=resources,
    )
    
    # Execute a simple function
    result = await executor.execute(my_func, args=("hello",))
    
    # Execute a workflow
    result = await executor.execute_workflow(steps)
    
    # Execute with dependencies
    result = await executor.execute_graph(tasks)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("friday.runtime.executor")


@dataclass
class ExecutionResult:
    """Result of a runtime execution."""
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "pending"  # pending, running, success, failed, timeout
    result: Any = None
    error: str = ""
    duration_seconds: float = 0.0
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "result": self.result if not isinstance(self.result, bytes) else "<bytes>",
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class RuntimeExecutor:
    """The unified execution path for the FRIDAY runtime.

    All execution — whether a simple function call, a multi-step
    workflow, or a dependency graph — flows through this executor.
    """

    def __init__(
        self,
        event_bus=None,
        execution_graph=None,
        workflow_runtime=None,
        scheduler=None,
        capability_registry=None,
        resource_manager=None,
    ):
        self.event_bus = event_bus
        self.execution_graph = execution_graph
        self.workflow_runtime = workflow_runtime
        self.scheduler = scheduler
        self.capability_registry = capability_registry
        self.resource_manager = resource_manager
        self._execution_count = 0
        self._failure_count = 0

    async def execute(
        self,
        func: Callable,
        args: tuple = (),
        kwargs: Optional[Dict] = None,
        timeout: Optional[float] = None,
        capability: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute a single function through the runtime.

        Args:
            func: Async or sync callable.
            args: Positional arguments.
            kwargs: Keyword arguments.
            timeout: Optional timeout in seconds.
            capability: Required capability (checked if registry exists).

        Returns:
            ExecutionResult with status, result, and timing.
        """
        kwargs = kwargs or {}
        result = ExecutionResult(
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        # Check capability
        if capability and self.capability_registry:
            if not self.capability_registry.has_capability(capability):
                result.status = "failed"
                result.error = f"Missing capability: {capability}"
                result.completed_at = datetime.now(timezone.utc).isoformat()
                await self._emit_event("execution.failed", result, reason="missing_capability")
                self._failure_count += 1
                return result

        # Track resource usage
        if self.resource_manager:
            self.resource_manager.increment_counter("concurrent_requests")

        start = time.perf_counter()

        try:
            # Execute with optional timeout
            if asyncio.iscoroutinefunction(func):
                if timeout:
                    result.result = await asyncio.wait_for(
                        func(*args, **kwargs), timeout=timeout
                    )
                else:
                    result.result = await func(*args, **kwargs)
            else:
                # Run sync function in thread
                if timeout:
                    result.result = await asyncio.wait_for(
                        asyncio.to_thread(func, *args, **kwargs),
                        timeout=timeout,
                    )
                else:
                    result.result = await asyncio.to_thread(func, *args, **kwargs)

            result.status = "success"
            await self._emit_event("execution.completed", result)

        except asyncio.TimeoutError:
            result.status = "timeout"
            result.error = f"Timed out after {timeout}s"
            await self._emit_event("execution.timeout", result)
            self._failure_count += 1

        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)
            await self._emit_event("execution.failed", result, reason=str(exc))
            self._failure_count += 1

        finally:
            result.duration_seconds = time.perf_counter() - start
            result.completed_at = datetime.now(timezone.utc).isoformat()
            self._execution_count += 1
            if self.resource_manager:
                self.resource_manager.decrement_counter("concurrent_requests")

        return result

    async def execute_workflow(self, steps: List[Dict]) -> Dict[str, Any]:
        """Execute a multi-step workflow.

        Args:
            steps: List of step dicts with keys: id, func, depends_on.

        Returns:
            Dict mapping step_id → result.
        """
        if not self.workflow_runtime:
            return {"error": "Workflow runtime not available"}

        await self._emit_event("workflow.started", {"step_count": len(steps)})

        try:
            from core.runtime.workflow_runtime import WorkflowStep
            wf_steps = []
            for step in steps:
                wf_steps.append(WorkflowStep(
                    id=step["id"],
                    name=step.get("name", step["id"]),
                    func=step.get("func"),
                    depends_on=step.get("depends_on", []),
                ))

            workflow = await self.workflow_runtime.create_workflow(
                name="executor_workflow",
                steps=wf_steps,
            )
            results = await self.workflow_runtime.execute_workflow(workflow.id)

            await self._emit_event("workflow.completed", {"step_count": len(results)})
            return results

        except Exception as exc:
            await self._emit_event("workflow.failed", {"error": str(exc)})
            return {"error": str(exc)}

    async def execute_graph(self, tasks: List[Dict]) -> Dict[str, Any]:
        """Execute a dependency graph of tasks.

        Args:
            tasks: List of task dicts with keys: id, func, depends_on.

        Returns:
            Dict mapping task_id → result.
        """
        if not self.execution_graph:
            return {"error": "Execution graph not available"}

        await self._emit_event("graph.started", {"task_count": len(tasks)})

        try:
            # Clear existing tasks
            self.execution_graph._tasks.clear()

            for task in tasks:
                self.execution_graph.add_task(
                    task_id=task["id"],
                    func=task.get("func"),
                    depends_on=task.get("depends_on", []),
                )

            results = await self.execution_graph.execute()

            await self._emit_event("graph.completed", {"task_count": len(results)})
            return results

        except Exception as exc:
            await self._emit_event("graph.failed", {"error": str(exc)})
            return {"error": str(exc)}

    async def schedule(
        self,
        func: Callable,
        delay: float = 0.0,
        *args,
        **kwargs,
    ) -> str:
        """Schedule a function for deferred execution.

        Args:
            func: Async callable.
            delay: Seconds to wait before execution.
            *args, **kwargs: Arguments for the function.

        Returns:
            Job ID.
        """
        if not self.scheduler:
            raise RuntimeError("Scheduler not available")

        job_id = self.scheduler.schedule_once(func, delay, *args, **kwargs)
        await self._emit_event("execution.scheduled", {"job_id": job_id, "delay": delay})
        return job_id

    async def _emit_event(
        self, event_type: str, result: Any, reason: str = ""
    ) -> None:
        """Emit an execution event."""
        if not self.event_bus:
            return

        data = {}
        if isinstance(result, ExecutionResult):
            data = result.to_dict()
        elif isinstance(result, dict):
            data = result

        if reason:
            data["reason"] = reason

        await self.event_bus.publish(event_type, data, source="executor")

    def get_stats(self) -> Dict[str, Any]:
        """Get executor statistics."""
        return {
            "total_executions": self._execution_count,
            "total_failures": self._failure_count,
            "failure_rate": (
                self._failure_count / self._execution_count * 100
                if self._execution_count > 0
                else 0.0
            ),
        }

    async def is_healthy(self) -> bool:
        """Check if the executor is healthy."""
        return True

    async def stop(self) -> None:
        """Stop the executor."""
        logger.info("Runtime executor stopped")
