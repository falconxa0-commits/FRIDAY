"""Tests for Swarm v6 Runtime Convergence modules."""
import asyncio
import pytest

# --- Runtime Context ---
from core.runtime.runtime_context import RuntimeContext, RuntimeContextStatus


class TestRuntimeContext:
    def test_initial_state(self):
        ctx = RuntimeContext()
        assert ctx.status.initialized is False
        assert ctx.status.service_count == 0

    @pytest.mark.asyncio
    async def test_initialize_creates_services(self):
        ctx = RuntimeContext()
        await ctx.initialize()
        assert ctx.status.initialized is True
        assert ctx.status.service_count > 0
        assert ctx.event_bus is not None
        assert ctx.scheduler is not None
        assert ctx.execution_graph is not None
        assert ctx.workflow_runtime is not None
        assert ctx.executor is not None
        await ctx.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_clears_services(self):
        ctx = RuntimeContext()
        await ctx.initialize()
        await ctx.shutdown()
        assert ctx.status.initialized is False
        assert ctx.event_bus is None

    @pytest.mark.asyncio
    async def test_health_check(self):
        ctx = RuntimeContext()
        await ctx.initialize()
        health = await ctx.health_check()
        assert isinstance(health, dict)
        assert len(health) > 0
        await ctx.shutdown()

    @pytest.mark.asyncio
    async def test_register_custom_service(self):
        ctx = RuntimeContext()
        await ctx.initialize()
        ctx.register_service("custom", {"test": True})
        assert ctx.get_service("custom") == {"test": True}
        await ctx.shutdown()

    def test_status_to_dict(self):
        status = RuntimeContextStatus(initialized=True, service_count=5)
        d = status.to_dict()
        assert d["initialized"] is True
        assert d["service_count"] == 5


# --- Runtime Executor ---
from core.runtime.executor import RuntimeExecutor, ExecutionResult


class TestRuntimeExecutor:
    @pytest.mark.asyncio
    async def test_execute_async_function(self):
        from core.runtime.event_bus import EventBus
        ex = RuntimeExecutor(event_bus=EventBus())

        async def my_func(x):
            return x * 2

        result = await ex.execute(my_func, args=(5,))
        assert result.status == "success"
        assert result.result == 10

    @pytest.mark.asyncio
    async def test_execute_sync_function(self):
        from core.runtime.event_bus import EventBus
        ex = RuntimeExecutor(event_bus=EventBus())

        def my_func(x):
            return x + 1

        result = await ex.execute(my_func, args=(10,))
        assert result.status == "success"
        assert result.result == 11

    @pytest.mark.asyncio
    async def test_execute_with_timeout(self):
        from core.runtime.event_bus import EventBus
        ex = RuntimeExecutor(event_bus=EventBus())

        async def slow_func():
            await asyncio.sleep(10)
            return "done"

        result = await ex.execute(slow_func, timeout=0.1)
        assert result.status == "timeout"
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_failure(self):
        from core.runtime.event_bus import EventBus
        ex = RuntimeExecutor(event_bus=EventBus())

        async def failing_func():
            raise RuntimeError("Intentional failure")

        result = await ex.execute(failing_func)
        assert result.status == "failed"
        assert "Intentional failure" in result.error

    @pytest.mark.asyncio
    async def test_execute_with_capability_check(self):
        from core.runtime.event_bus import EventBus
        from core.runtime.capability_registry import CapabilityRegistry
        reg = CapabilityRegistry()
        ex = RuntimeExecutor(event_bus=EventBus(), capability_registry=reg)

        async def my_func():
            return "ok"

        # Without capability granted
        result = await ex.execute(my_func, capability="brain.chat")
        assert result.status == "failed"
        assert "Missing capability" in result.error

        # Grant capability
        reg.register("brain.chat", {"provider": "test"})
        result = await ex.execute(my_func, capability="brain.chat")
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_execute_workflow(self):
        from core.runtime.event_bus import EventBus
        from core.runtime.workflow_runtime import WorkflowRuntime
        wr = WorkflowRuntime(event_bus=EventBus())
        ex = RuntimeExecutor(
            event_bus=EventBus(),
            workflow_runtime=wr,
        )

        async def step1():
            return "step1"

        async def step2():
            return "step2"

        steps = [
            {"id": "s1", "func": step1},
            {"id": "s2", "func": step2, "depends_on": ["s1"]},
        ]
        results = await ex.execute_workflow(steps)
        assert "s1" in results
        assert "s2" in results

    @pytest.mark.asyncio
    async def test_execute_graph(self):
        from core.runtime.event_bus import EventBus
        from core.runtime.execution_graph import ExecutionGraph
        graph = ExecutionGraph()
        ex = RuntimeExecutor(
            event_bus=EventBus(),
            execution_graph=graph,
        )

        async def task_a():
            return "a"

        async def task_b():
            return "b"

        tasks = [
            {"id": "a", "func": task_a},
            {"id": "b", "func": task_b, "depends_on": ["a"]},
        ]
        results = await ex.execute_graph(tasks)
        assert results["a"] == "a"
        assert results["b"] == "b"

    def test_execution_result_to_dict(self):
        r = ExecutionResult(status="success", result="test")
        d = r.to_dict()
        assert d["status"] == "success"
        assert d["result"] == "test"

    def test_executor_stats(self):
        from core.runtime.event_bus import EventBus
        ex = RuntimeExecutor(event_bus=EventBus())
        stats = ex.get_stats()
        assert stats["total_executions"] == 0


# --- Runtime Observability ---
from core.runtime.observability import RuntimeObservability, Metric


class TestRuntimeObservability:
    def test_metric_to_dict(self):
        m = Metric(name="test", value=42, unit="ms", subsystem="test")
        d = m.to_dict()
        assert d["name"] == "test"
        assert d["value"] == 42
        assert d["unit"] == "ms"

    @pytest.mark.asyncio
    async def test_collect_without_context(self):
        obs = RuntimeObservability(context=None)
        metrics = await obs.collect_all()
        assert metrics == []

    @pytest.mark.asyncio
    async def test_collect_with_context(self):
        ctx = RuntimeContext()
        await ctx.initialize()
        obs = RuntimeObservability(context=ctx)
        metrics = await obs.collect_all()
        assert len(metrics) > 0
        # Should have metrics from multiple subsystems
        subsystems = set(m.subsystem for m in metrics)
        assert len(subsystems) > 5
        await ctx.shutdown()

    @pytest.mark.asyncio
    async def test_get_dashboard(self):
        ctx = RuntimeContext()
        await ctx.initialize()
        obs = RuntimeObservability(context=ctx)
        dashboard = await obs.get_dashboard()
        assert "timestamp" in dashboard
        assert "total_metrics" in dashboard
        assert "subsystems" in dashboard
        assert dashboard["total_metrics"] > 0
        await ctx.shutdown()

    @pytest.mark.asyncio
    async def test_get_health_summary(self):
        ctx = RuntimeContext()
        await ctx.initialize()
        obs = RuntimeObservability(context=ctx)
        summary = await obs.get_health_summary()
        assert "status" in summary
        assert "healthy" in summary
        assert "total" in summary
        assert summary["total"] > 0
        await ctx.shutdown()


# --- Security Policy Engine ---
from core.runtime.security import PolicyEngine, Policy, PolicyDecision, create_default_policy_engine


class TestPolicyEngine:
    def test_deny_by_default(self):
        engine = PolicyEngine()
        decision = engine.evaluate("unknown.capability")
        assert decision.allowed is False
        assert "deny by default" in decision.reason

    def test_grant_capability(self):
        engine = PolicyEngine()
        engine.grant_capability("test.cap")
        decision = engine.evaluate("test.cap")
        assert decision.allowed is True

    def test_deny_capability(self):
        engine = PolicyEngine()
        engine.deny_capability("dangerous.cap")
        decision = engine.evaluate("dangerous.cap")
        assert decision.allowed is False

    def test_explicit_deny_overrides_grant(self):
        engine = PolicyEngine()
        engine.grant_capability("test.cap")
        engine.deny_capability("test.cap")
        decision = engine.evaluate("test.cap")
        assert decision.allowed is False

    def test_policy_allow(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="allow_test",
            effect="allow",
            capabilities=["test.cap"],
        ))
        decision = engine.evaluate("test.cap")
        assert decision.allowed is True
        assert decision.policy_name == "allow_test"

    def test_policy_deny(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="deny_test",
            effect="deny",
            capabilities=["test.cap"],
        ))
        decision = engine.evaluate("test.cap")
        assert decision.allowed is False

    def test_decision_log(self):
        engine = PolicyEngine()
        engine.evaluate("cap1")
        engine.evaluate("cap2")
        log = engine.get_decision_log()
        assert len(log) == 2

    def test_stats(self):
        engine = PolicyEngine()
        engine.grant_capability("cap1")
        engine.evaluate("cap1")
        engine.evaluate("cap2")
        stats = engine.get_stats()
        assert stats["total_decisions"] == 2
        assert stats["allow_count"] == 1
        assert stats["deny_count"] == 1

    def test_check_execution_permission(self):
        engine = PolicyEngine()
        engine.grant_capability("exec.perm")
        assert engine.check_execution_permission("func", "exec.perm") is True
        assert engine.check_execution_permission("func", "missing.perm") is False
        assert engine.check_execution_permission("func") is True  # no cap required

    def test_default_policy_engine(self):
        engine = create_default_policy_engine()
        # Should allow brain.chat
        assert engine.evaluate("brain.chat").allowed is True
        # Should deny filesystem.delete
        assert engine.evaluate("filesystem.delete").allowed is False
        # Should deny unknown
        assert engine.evaluate("unknown.cap").allowed is False

    @pytest.mark.asyncio
    async def test_is_healthy(self):
        engine = PolicyEngine()
        assert await engine.is_healthy() is True
