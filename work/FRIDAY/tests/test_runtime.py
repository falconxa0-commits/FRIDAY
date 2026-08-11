"""Tests for the FRIDAY AI Runtime (Age IV)."""
import asyncio
import pytest
from pathlib import Path


# --- Runtime Manager ---
from core.runtime.runtime_manager import RuntimeManager, RuntimeState


class TestRuntimeManager:
    def test_initial_state_is_stopped(self):
        rm = RuntimeManager()
        assert rm.state == RuntimeState.STOPPED

    def test_get_status_returns_dict(self):
        rm = RuntimeManager()
        status = rm.get_status()
        assert status.state == RuntimeState.STOPPED
        assert "subsystems" in status.to_dict()

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        rm = RuntimeManager()
        await rm.start()
        assert rm.state == RuntimeState.RUNNING
        await rm.stop()
        assert rm.state == RuntimeState.STOPPED

    @pytest.mark.asyncio
    async def test_health_check(self):
        rm = RuntimeManager()
        await rm.start()
        health = await rm.health_check()
        assert isinstance(health, dict)
        await rm.stop()


# --- Event Bus ---
from core.runtime.event_bus import EventBus, Event


class TestEventBus:
    @pytest.mark.asyncio
    async def test_publish_subscribe(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("test.event", handler)
        await bus.publish("test.event", {"key": "value"})

        assert len(received) == 1
        assert received[0].data["key"] == "value"

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("*", handler)
        await bus.publish("type1", {})
        await bus.publish("type2", {})

        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("test", handler)
        bus.unsubscribe("test", handler)
        await bus.publish("test", {})

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_history(self):
        bus = EventBus()
        await bus.publish("event1", {"n": 1})
        await bus.publish("event2", {"n": 2})
        history = bus.get_history()
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_handler_error_doesnt_crash_bus(self):
        bus = EventBus()

        async def bad_handler(event):
            raise RuntimeError("Intentional error")

        async def good_handler(event):
            good_handler.called = True

        good_handler.called = False

        bus.subscribe("test", bad_handler)
        bus.subscribe("test", good_handler)
        await bus.publish("test", {})

        assert good_handler.called is True

    def test_stats(self):
        bus = EventBus()
        assert bus.get_stats()["total_events"] == 0


# --- Capability Registry ---
from core.runtime.capability_registry import CapabilityRegistry, Capability


class TestCapabilityRegistry:
    def test_register_and_resolve(self):
        registry = CapabilityRegistry()
        obj = {"name": "test"}
        registry.register("test.cap", obj)
        assert registry.resolve("test.cap") is obj

    def test_resolve_nonexistent(self):
        registry = CapabilityRegistry()
        assert registry.resolve("nonexistent") is None

    def test_unregister(self):
        registry = CapabilityRegistry()
        registry.register("test.cap", {"obj": True})
        assert registry.unregister("test.cap") is True
        assert registry.resolve("test.cap") is None

    def test_alias(self):
        registry = CapabilityRegistry()
        obj = {"name": "aliased"}
        registry.register("canonical.name", obj)
        registry.register_alias("alias", "canonical.name")
        assert registry.resolve("alias") is obj

    def test_has_capability(self):
        registry = CapabilityRegistry()
        registry.register("test.cap", {})
        assert registry.has_capability("test.cap") is True
        assert registry.has_capability("missing") is False

    def test_list_capabilities(self):
        registry = CapabilityRegistry()
        registry.register("cap1", {})
        registry.register("cap2", {})
        caps = registry.list_capabilities()
        assert len(caps) == 2

    def test_stats(self):
        registry = CapabilityRegistry()
        registry.register("cap1", {})
        assert registry.get_stats()["total_capabilities"] == 1


# --- Resource Manager ---
from core.runtime.resource_manager import ResourceManager, ResourceUsage


class TestResourceManager:
    def test_default_limits(self):
        rm = ResourceManager()
        limits = rm.get_limits()
        assert len(limits) >= 3  # memory, concurrent_requests, tokens

    def test_set_limit(self):
        rm = ResourceManager()
        rm.set_limit("custom_limit", 500)
        limits = rm.get_limits()
        custom = [l for l in limits if l.name == "custom_limit"]
        assert len(custom) == 1
        assert custom[0].limit == 500

    def test_increment_counter(self):
        rm = ResourceManager()
        rm.increment_counter("concurrent_requests")
        rm.increment_counter("concurrent_requests")
        assert rm._counters["concurrent_requests"] == 2

    def test_decrement_counter(self):
        rm = ResourceManager()
        rm.increment_counter("concurrent_requests", 5)
        rm.decrement_counter("concurrent_requests", 2)
        assert rm._counters["concurrent_requests"] == 3

    def test_get_usage(self):
        rm = ResourceManager()
        usage = rm.get_usage()
        assert isinstance(usage, ResourceUsage)
        assert usage.memory_mb >= 0

    def test_check_limits_no_alerts(self):
        rm = ResourceManager()
        rm.set_limit("memory_mb", 999999)
        alerts = rm.check_limits()
        assert len(alerts) == 0

    def test_check_limits_exceeded(self):
        rm = ResourceManager()
        rm.set_limit("concurrent_requests", 1)
        rm.increment_counter("concurrent_requests", 5)
        alerts = rm.check_limits()
        assert any("exceeded" in a for a in alerts)


# --- Scheduler ---
from core.runtime.scheduler import RuntimeScheduler, Job, JobType, JobStatus


class TestRuntimeScheduler:
    @pytest.mark.asyncio
    async def test_schedule_once(self):
        sched = RuntimeScheduler()
        await sched.start()

        results = []

        async def my_func():
            results.append("executed")

        job_id = sched.schedule_once(my_func, delay=0.1)
        await asyncio.sleep(0.3)

        assert len(results) == 1
        assert sched._jobs[job_id].status == JobStatus.COMPLETED

        await sched.stop()

    @pytest.mark.asyncio
    async def test_schedule_interval(self):
        sched = RuntimeScheduler()
        await sched.start()

        results = []

        async def my_func():
            results.append("tick")

        job_id = sched.schedule_interval(my_func, interval=0.05)
        await asyncio.sleep(0.2)

        assert len(results) >= 2  # At least 2 ticks

        await sched.stop()

    @pytest.mark.asyncio
    async def test_cancel_job(self):
        sched = RuntimeScheduler()
        await sched.start()

        async def my_func():
            pass

        job_id = sched.schedule_once(my_func, delay=10.0)
        assert sched.cancel(job_id) is True
        assert sched._jobs[job_id].status == JobStatus.CANCELLED

        await sched.stop()

    def test_list_jobs(self):
        sched = RuntimeScheduler()
        assert len(sched.list_jobs()) == 0

    def test_stats(self):
        sched = RuntimeScheduler()
        stats = sched.get_stats()
        assert "total_jobs" in stats


# --- Execution Graph ---
from core.runtime.execution_graph import ExecutionGraph, GraphTask, TaskState


class TestExecutionGraph:
    @pytest.mark.asyncio
    async def test_simple_execution(self):
        graph = ExecutionGraph()

        async def task_a():
            return "a"

        graph.add_task("a", task_a)
        results = await graph.execute()

        assert results["a"] == "a"
        assert graph.get_task("a").state == TaskState.COMPLETED

    @pytest.mark.asyncio
    async def test_dependency_order(self):
        graph = ExecutionGraph()
        order = []

        async def task_a():
            order.append("a")
            return "a"

        async def task_b():
            order.append("b")
            return "b"

        graph.add_task("a", task_a)
        graph.add_task("b", task_b, depends_on=["a"])
        results = await graph.execute()

        assert order == ["a", "b"]
        assert results["a"] == "a"
        assert results["b"] == "b"

    @pytest.mark.asyncio
    async def test_parallel_execution(self):
        graph = ExecutionGraph()
        started = []

        async def task_a():
            started.append("a")
            await asyncio.sleep(0.1)
            return "a"

        async def task_b():
            started.append("b")
            await asyncio.sleep(0.1)
            return "b"

        graph.add_task("a", task_a)
        graph.add_task("b", task_b)
        await graph.execute()

        # Both should start before either finishes
        assert len(started) == 2

    @pytest.mark.asyncio
    async def test_failure_skips_dependents(self):
        graph = ExecutionGraph()

        async def failing_task():
            raise RuntimeError("Intentional failure")

        async def dependent_task():
            return "should not run"

        graph.add_task("failing", failing_task)
        graph.add_task("dependent", dependent_task, depends_on=["failing"])
        results = await graph.execute()

        assert graph.get_task("failing").state == TaskState.FAILED
        assert graph.get_task("dependent").state == TaskState.SKIPPED
        assert "dependent" not in results

    def test_detect_cycles(self):
        graph = ExecutionGraph()
        graph.add_task("a", depends_on=["b"])
        graph.add_task("b", depends_on=["a"])
        assert graph.detect_cycles() is True

    def test_no_cycles(self):
        graph = ExecutionGraph()
        graph.add_task("a")
        graph.add_task("b", depends_on=["a"])
        graph.add_task("c", depends_on=["b"])
        assert graph.detect_cycles() is False

    def test_stats(self):
        graph = ExecutionGraph()
        graph.add_task("a")
        stats = graph.get_stats()
        assert stats["total_tasks"] == 1
        assert stats["has_cycles"] is False
