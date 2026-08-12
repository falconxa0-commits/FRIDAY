"""Chaos engineering tests — fault injection across the runtime layer.

Each test deliberately injects a fault into a real runtime subsystem
(no mocks where avoidable) and verifies the subsystem either:

    * recovers gracefully (continues serving after the fault), OR
    * degrades gracefully (returns a clear error, preserves state), OR
    * fails closed (refuses to proceed rather than corrupt data).

This file is the chaos counterpart to ``tests/test_chaos.py`` (which
targets the task queue, ledger, and validation pipeline). The 12 chaos
scenarios below target the runtime layer: ProcessManager, MemoryPool,
RuntimeContext, EventBus, IPC channels, DriverManager, model drivers,
RuntimeExecutor, RuntimeScheduler, KernelScheduler, and WorkflowRuntime.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys

import pytest

from core.runtime.event_bus import EventBus
from core.runtime.execution_graph import TaskState
from core.runtime.executor import RuntimeExecutor
from core.runtime.kernel.ipc_manager import IPCManager
from core.runtime.kernel.kernel_scheduler import KernelScheduler, TaskPriority
from core.runtime.kernel.process_manager import (
    ProcessManager,
    ProcessState,
)
from core.runtime.memory_runtime import MemoryRuntime
from core.runtime.drivers.driver_manager import DriverManager
from core.runtime.drivers.model_driver import ModelDriver, ModelResult
from core.runtime.runtime_context import RuntimeContext
from core.runtime.scheduler import (
    JobStatus,
    RuntimeScheduler,
)
from core.runtime.workflow_runtime import (
    WorkflowRuntime,
    WorkflowStatusEnum,
    WorkflowStep,
)


# ---------------------------------------------------------------------------
# 1. Process crash recovery
# ---------------------------------------------------------------------------

class TestChaosProcessCrashRecovery:
    """Kill a managed process via OS signal — verify the ProcessManager
    survives, the killed process is marked as failed/killed, and a
    subsequent spawn still succeeds."""

    @pytest.mark.asyncio
    async def test_external_kill_does_not_crash_process_manager(self):
        pm = ProcessManager()
        try:
            # Spawn a long-running sleep so we have time to kill it.
            proc = await pm.spawn(
                "chaos-sleep",
                ["sleep", "60"],
                timeout=10.0,
            )
            assert proc.state == ProcessState.RUNNING
            assert proc.pid > 0

            # Kill it directly via OS signal (simulating an OOM-kill
            # or a crash). The ProcessManager itself doesn't know yet.
            os.kill(proc.pid, signal.SIGKILL)

            # Wait on the killed process — ProcessManager.wait() should
            # observe the non-zero returncode and mark FAILED (or the
            # explicit kill path marks KILLED).
            result = await pm.wait("chaos-sleep")
            assert result.state in (
                ProcessState.FAILED,
                ProcessState.KILLED,
                ProcessState.COMPLETED,
            )

            # Crucially, the ProcessManager itself is still usable —
            # we can spawn another process immediately.
            proc2 = await pm.spawn("chaos-after", ["echo", "ok"], timeout=5.0)
            assert proc2.state == ProcessState.RUNNING
            await pm.wait("chaos-after")
            assert pm.get_process("chaos-after").state in (
                ProcessState.COMPLETED,
                ProcessState.FAILED,
            )
        finally:
            await pm.stop()


# ---------------------------------------------------------------------------
# 2. Memory pressure
# ---------------------------------------------------------------------------

class TestChaosMemoryPressure:
    """Fill a MemoryPool to capacity — verify subsequent allocations
    are REFUSED (not silently dropped, not silently evicting)."""

    @pytest.mark.asyncio
    async def test_allocation_refused_when_pool_full(self):
        mr = MemoryRuntime()
        try:
            pool = await mr.create_pool("chaos-pool", max_size_bytes=1024)

            # Fill it exactly.
            ok_a = await mr.allocate("chaos-pool", "k1", b"a" * 512)
            ok_b = await mr.allocate("chaos-pool", "k2", b"b" * 512)
            assert ok_a and ok_b

            # Now at capacity — any further allocation must be refused.
            ok_c = await mr.allocate("chaos-pool", "k3", b"c" * 1)
            assert ok_c is False

            # Pool stats reflect the refusal.
            stats = mr.get_stats()
            assert stats["total_refusals"] >= 1

            # Existing data is intact (not corrupted by the refused alloc).
            data = await mr.retrieve("chaos-pool", "k1")
            assert data == b"a" * 512
        finally:
            await mr.stop()

    @pytest.mark.asyncio
    async def test_oversized_allocation_refused(self):
        mr = MemoryRuntime()
        try:
            await mr.create_pool("chaos-small", max_size_bytes=100)
            # Single allocation larger than the whole pool.
            ok = await mr.allocate("chaos-small", "huge", b"x" * 1000)
            assert ok is False
            # Pool still empty (not partially populated).
            usage = await mr.get_pool_usage("chaos-small")
            assert usage["size_bytes"] == 0
        finally:
            await mr.stop()


# ---------------------------------------------------------------------------
# 3. Runtime restart
# ---------------------------------------------------------------------------

class TestChaosRuntimeRestart:
    """Stop and restart RuntimeContext — verify all services recover
    to a clean state."""

    @pytest.mark.asyncio
    async def test_shutdown_and_reinitialize_recovers_services(self):
        ctx = RuntimeContext()
        try:
            await ctx.initialize()
            assert ctx.status.initialized
            assert ctx.event_bus is not None

            # Use a service — publish an event.
            await ctx.event_bus.publish(
                "chaos.restart.before", {"phase": "pre"}
            )
            assert len(ctx.event_bus.get_history()) == 1

            # Shutdown — all services must clear.
            await ctx.shutdown()
            assert not ctx.status.initialized
            assert ctx.event_bus is None

            # Restart — services re-created and usable.
            await ctx.initialize()
            assert ctx.status.initialized
            assert ctx.event_bus is not None

            # New event bus, fresh history.
            assert len(ctx.event_bus.get_history()) == 0

            await ctx.event_bus.publish(
                "chaos.restart.after", {"phase": "post"}
            )
            assert len(ctx.event_bus.get_history()) == 1
        finally:
            await ctx.shutdown()


# ---------------------------------------------------------------------------
# 4. Event storm
# ---------------------------------------------------------------------------

class TestChaosEventStorm:
    """Publish 1000 events in rapid succession — verify the EventBus
    handles them all without dropping any (history is complete)."""

    @pytest.mark.asyncio
    async def test_event_storm_1000_events_no_drop(self):
        bus = EventBus()
        try:
            received: list = []

            async def handler(event):
                received.append(event.type)

            bus.subscribe("chaos.storm", handler)

            # Publish 1000 events as fast as possible. Each publish
            # awaits the handler, so this also exercises handler
            # throughput — a slow handler would manifest as a slow
            # overall time, not a drop.
            for i in range(1000):
                await bus.publish("chaos.storm", {"i": i})

            # History reflects all 1000 (or up to the 1000-event
            # history_limit, which is exactly 1000).
            history = bus.get_history(limit=2000)
            assert len(history) >= 1000, (
                f"Expected >=1000 events in history, got {len(history)}"
            )

            # Handler received all 1000.
            assert len(received) == 1000, (
                f"Handler received {len(received)}/1000 events"
            )

            # Stats reflect the load.
            stats = bus.get_stats()
            assert stats["total_events"] >= 1000
        finally:
            await bus.stop()


# ---------------------------------------------------------------------------
# 5. Queue overflow
# ---------------------------------------------------------------------------

class TestChaosQueueOverflow:
    """Fill an IPC channel beyond capacity — verify ``send`` returns
    False (rather than blocking forever or silently dropping)."""

    @pytest.mark.asyncio
    async def test_send_returns_false_when_channel_full(self):
        ipc = IPCManager()
        try:
            ch = await ipc.create_channel("chaos-q", maxsize=5)

            # Fill exactly to capacity.
            for i in range(5):
                ok = await ch.send({"i": i})
                assert ok is True

            # Next send must be refused — not blocked.
            ok_overflow = await ch.send({"i": 999})
            assert ok_overflow is False, (
                "Send on full channel must return False, not block."
            )

            # Drain one slot.
            msg = await ch.receive(timeout=0.5)
            assert msg is not None
            assert msg["i"] == 0

            # Now we can send again.
            ok_again = await ch.send({"i": 1000})
            assert ok_again is True
        finally:
            await ipc.stop()


# ---------------------------------------------------------------------------
# 6. Driver failure
# ---------------------------------------------------------------------------

class TestChaosDriverFailure:
    """Call a driver that isn't registered — verify the
    DriverManager returns a structured error (not a crash)."""

    @pytest.mark.asyncio
    async def test_missing_tool_driver_returns_error_dict(self):
        dm = DriverManager()
        try:
            result = await dm.execute_tool("nonexistent_tool", {"x": 1})
            assert isinstance(result, dict)
            assert result.get("status") == "error"
            assert "not found" in result.get("error", "").lower()
        finally:
            await dm.stop()

    @pytest.mark.asyncio
    async def test_missing_model_driver_returns_error_dict(self):
        dm = DriverManager()
        try:
            result = await dm.execute_model("nonexistent_model", {"x": 1})
            assert isinstance(result, dict)
            assert result.get("status") == "error"
            assert "not found" in result.get("error", "").lower()
        finally:
            await dm.stop()

    @pytest.mark.asyncio
    async def test_missing_plugin_driver_returns_error_dict(self):
        dm = DriverManager()
        try:
            result = await dm.execute_plugin("nonexistent_plugin", {"x": 1})
            assert isinstance(result, dict)
            assert result.get("status") == "error"
            assert "not found" in result.get("error", "").lower()
        finally:
            await dm.stop()


# ---------------------------------------------------------------------------
# 7. Model timeout
# ---------------------------------------------------------------------------

class _SlowModel(ModelDriver):
    """A model driver that takes a long time to respond."""

    @property
    def name(self) -> str:
        return "slow_model"

    @property
    def provider(self) -> str:
        return "test"

    def is_available(self) -> bool:
        return True

    async def execute(self, params):  # type: ignore[override]
        await asyncio.sleep(10.0)
        return ModelResult(status="success", text="should-not-reach", model="slow")


class TestChaosModelTimeout:
    """Execute a slow model with a 0.001s timeout — verify the executor
    surfaces a timeout result, not a hung call."""

    @pytest.mark.asyncio
    async def test_model_execution_with_submillisecond_timeout(self):
        executor = RuntimeExecutor()
        slow = _SlowModel()

        result = await executor.execute(
            slow.execute,
            kwargs={"params": {}},
            timeout=0.001,
        )
        assert result.status == "timeout"
        assert "0.001" in result.error

    @pytest.mark.asyncio
    async def test_model_timeout_emits_timeout_event(self):
        """The executor must publish an ``execution.timeout`` event so
        downstream observers (metrics, alerting) can react."""
        bus = EventBus()
        executor = RuntimeExecutor(event_bus=bus)
        slow = _SlowModel()

        events_seen: list = []
        bus.subscribe("execution.timeout", lambda e: events_seen.append(e))

        await executor.execute(slow.execute, kwargs={"params": {}}, timeout=0.001)
        assert len(events_seen) == 1


# ---------------------------------------------------------------------------
# 8. Executor timeout (sync function path)
# ---------------------------------------------------------------------------

def _blocking_sleep(seconds: float) -> str:
    """A *synchronous* function that blocks the thread it runs in."""
    import time
    time.sleep(seconds)
    return "completed"


class TestChaosExecutorTimeout:
    """Execute a slow synchronous function via the executor with a
    timeout — verify the timeout fires."""

    @pytest.mark.asyncio
    async def test_sync_function_timeout(self):
        executor = RuntimeExecutor()

        # ``asyncio.to_thread`` runs the sync function in a worker
        # thread; ``wait_for`` cancels it after the timeout. The
        # cancellation surfaces as TimeoutError.
        result = await executor.execute(
            _blocking_sleep,
            args=(10.0,),
            timeout=0.1,
        )
        assert result.status == "timeout"
        assert "0.1" in result.error

    @pytest.mark.asyncio
    async def test_executor_failure_increments_failure_count(self):
        executor = RuntimeExecutor()

        def explode():
            raise RuntimeError("kaboom")

        await executor.execute(explode)
        stats = executor.get_stats()
        assert stats["total_executions"] == 1
        assert stats["total_failures"] == 1
        assert stats["failure_rate"] == 100.0


# ---------------------------------------------------------------------------
# 9. Scheduler failure
# ---------------------------------------------------------------------------

class TestChaosSchedulerFailure:
    """Schedule a function that raises — verify the scheduler captures
    the error in the Job's ``last_error`` field (not propagates it)."""

    @pytest.mark.asyncio
    async def test_failing_scheduled_function_records_error(self):
        scheduler = RuntimeScheduler()
        await scheduler.start()
        try:
            async def boom():
                raise ValueError("scheduler-chaos-boom")

            job_id = scheduler.schedule_once(boom, delay=0.05)
            # Wait long enough for the job to run.
            await asyncio.sleep(0.4)

            job = next(j for j in scheduler.list_jobs() if j.id == job_id)
            assert job.status == JobStatus.FAILED
            assert "scheduler-chaos-boom" in job.last_error
        finally:
            await scheduler.stop()


# ---------------------------------------------------------------------------
# 10. Concurrent execution (50 tasks)
# ---------------------------------------------------------------------------

class TestChaosConcurrentExecution:
    """Submit 50 tasks to the KernelScheduler — verify all complete."""

    @pytest.mark.asyncio
    async def test_fifty_tasks_all_complete(self):
        scheduler = KernelScheduler()

        async def small_task(n: int) -> int:
            await asyncio.sleep(0.001)
            return n * 2

        for i in range(50):
            await scheduler.submit(
                f"chaos-task-{i}",
                small_task,
                TaskPriority.NORMAL,
                i,
            )

        # run_all drains every queue.
        results = await scheduler.run_all()

        # All 50 must have completed.
        assert len(results) == 50
        assert all(t.status == "completed" for t in results), (
            f"Some tasks did not complete: "
            f"{[(t.id, t.status, t.error) for t in results if t.status != 'completed']}"
        )

        # Results are correct (each is i*2).
        for t in results:
            assert t.result == int(t.id.split("-")[-1]) * 2

        # Stats reflect the load.
        stats = scheduler.get_stats()
        assert stats["total_executed"] == 50
        assert stats["total_failures"] == 0

    @pytest.mark.asyncio
    async def test_priority_ordering_under_load(self):
        """CRITICAL tasks must execute before NORMAL ones when both
        are queued before the scheduler runs."""
        scheduler = KernelScheduler()
        execution_order: list = []

        async def record(label: str):
            execution_order.append(label)

        # Submit a mix of priorities.
        await scheduler.submit("n1", record, TaskPriority.NORMAL, "n1")
        await scheduler.submit("c1", record, TaskPriority.CRITICAL, "c1")
        await scheduler.submit("n2", record, TaskPriority.NORMAL, "n2")
        await scheduler.submit("c2", record, TaskPriority.CRITICAL, "c2")

        await scheduler.run_all()
        # All criticals first.
        assert execution_order == ["c1", "c2", "n1", "n2"]


# ---------------------------------------------------------------------------
# 11. Cancellation
# ---------------------------------------------------------------------------

class TestChaosCancellation:
    """Cancel a scheduled job before it runs — verify its status
    flips to CANCELLED and the function never executes."""

    @pytest.mark.asyncio
    async def test_cancel_running_scheduler_job(self):
        scheduler = RuntimeScheduler()
        await scheduler.start()
        try:
            executed = {"ran": False}

            async def slow_func():
                await asyncio.sleep(5.0)
                executed["ran"] = True
                return "should-not-complete"

            job_id = scheduler.schedule_once(slow_func, delay=0.05)

            # Give the scheduler a tick to start the asyncio task.
            await asyncio.sleep(0.01)
            assert scheduler.cancel(job_id) is True

            # Let the cancellation propagate.
            await asyncio.sleep(0.1)

            job = next(j for j in scheduler.list_jobs() if j.id == job_id)
            assert job.status == JobStatus.CANCELLED, (
                f"Expected CANCELLED, got {job.status}"
            )
            assert executed["ran"] is False, (
                "Cancelled job's function must NOT execute"
            )
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_cancel_unknown_job_returns_false(self):
        scheduler = RuntimeScheduler()
        await scheduler.start()
        try:
            assert scheduler.cancel("does-not-exist") is False
        finally:
            await scheduler.stop()


# ---------------------------------------------------------------------------
# 12. Workflow failure propagation
# ---------------------------------------------------------------------------

class TestChaosWorkflowFailurePropagation:
    """A failing workflow step must cause its dependents to be SKIPPED
    (not run, not silently succeed, not deadlock)."""

    @pytest.mark.asyncio
    async def test_dependent_steps_skipped_when_dependency_fails(self):
        wr = WorkflowRuntime()
        try:
            async def fail_step():
                raise ValueError("workflow-chaos-fail")

            async def dependent(deps):
                return "should-not-run"

            wf = await wr.create_workflow(
                "chaos_fail_propagation",
                steps=[
                    WorkflowStep(id="root", name="Root", func=fail_step),
                    WorkflowStep(
                        id="dep1", name="Dep1",
                        func=dependent, depends_on=["root"],
                    ),
                    WorkflowStep(
                        id="dep2", name="Dep2",
                        func=dependent, depends_on=["root"],
                    ),
                    # Grandchild — depends on a skipped step.
                    WorkflowStep(
                        id="grandchild", name="Grandchild",
                        func=dependent, depends_on=["dep1"],
                    ),
                ],
            )

            results = await wr.execute_workflow(wf.id)
            status = await wr.get_workflow_status(wf.id)

            # Workflow overall is FAILED (one step failed).
            assert status.status == WorkflowStatusEnum.FAILED

            # Root failed.
            assert status.step_states["root"] == TaskState.FAILED.value

            # Direct dependents were skipped (not run).
            assert status.step_states["dep1"] == TaskState.SKIPPED.value
            assert status.step_states["dep2"] == TaskState.SKIPPED.value

            # Grandchild was also skipped (transitive propagation).
            assert status.step_states["grandchild"] == TaskState.SKIPPED.value

            # Error message is recorded for the failing step.
            assert "root" in status.step_errors
            assert "workflow-chaos-fail" in status.step_errors["root"]

            # Results dict does NOT contain the skipped steps.
            assert "root" not in results
            assert "dep1" not in results
            assert "grandchild" not in results
        finally:
            await wr.stop()

    @pytest.mark.asyncio
    async def test_workflow_with_one_failing_step_does_not_skip_independent_steps(self):
        """Sibling steps that DON'T depend on the failing one must
        still run — failure propagation is precise, not blanket."""
        wr = WorkflowRuntime()
        try:
            async def fail_step():
                raise ValueError("workflow-chaos-fail-2")

            async def ok_step():
                return "ran-ok"

            wf = await wr.create_workflow(
                "chaos_partial_fail",
                steps=[
                    WorkflowStep(id="fail", name="Fail", func=fail_step),
                    WorkflowStep(id="ok", name="OK", func=ok_step),
                ],
            )

            results = await wr.execute_workflow(wf.id)
            status = await wr.get_workflow_status(wf.id)

            assert status.status == WorkflowStatusEnum.FAILED
            assert status.step_states["fail"] == TaskState.FAILED.value
            # The independent step still ran.
            assert status.step_states["ok"] == TaskState.COMPLETED.value
            assert results.get("ok") == "ran-ok"
        finally:
            await wr.stop()
