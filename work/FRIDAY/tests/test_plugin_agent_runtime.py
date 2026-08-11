"""Tests for the Plugin Runtime and Agent Runtime.

Covers:
    Plugin Runtime (15 tests):
        - register / unregister / duplicate detection
        - execute (sync + async), missing plugin / missing method
        - capability check, capability enforcement (PermissionError)
        - execution counters and last_executed timestamp
        - list / stats / is_healthy / stop

    Agent Runtime (21 tests):
        - register / unregister / duplicate detection
        - execute (sync + async), result + duration
        - failure counting, consecutive-failure circuit breaker
        - failed-state rejection, recover_agent
        - success resets consecutive_failures
        - concurrency limit (reject + release)
        - get_agent_status / list / stats / is_healthy / stop

All tests use lightweight mock objects — no real AI calls.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

import pytest

from core.runtime.agent_runtime import (
    AgentHandle,
    AgentResult,
    AgentRuntime,
    AgentStatus,
)
from core.runtime.plugin_runtime import (
    PluginHandle,
    PluginRuntime,
    requires_capability,
)


# ===========================================================================
# Mock plugins
# ===========================================================================
class CapabilityMappedPlugin:
    """Plugin that declares required capabilities via REQUIRED_CAPABILITIES."""

    REQUIRED_CAPABILITIES: Dict[str, Any] = {
        "read": "filesystem.read",
        "write": ["filesystem.write"],
        "network_fetch": "network.http",
    }

    def __init__(self) -> None:
        self.calls: List[tuple] = []

    async def read(self, path: str) -> str:
        self.calls.append(("read", path))
        return f"contents:{path}"

    def write(self, path: str, content: str) -> str:
        self.calls.append(("write", path, content))
        return f"wrote:{path}"

    async def network_fetch(self, url: str) -> str:
        self.calls.append(("fetch", url))
        return f"body:{url}"

    def status(self) -> str:
        # No declared required capability — open execution.
        return "healthy"


class DecoratedPlugin:
    """Plugin that uses the @requires_capability decorator."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []

    @requires_capability("memory.write")
    async def remember(self, key: str, value: Any) -> str:
        self.calls.append(("remember", key, value))
        return f"stored:{key}={value}"

    @requires_capability("memory.read")
    def recall(self, key: str) -> str:
        self.calls.append(("recall", key))
        return f"value:{key}"

    def version(self) -> str:
        return "1.0.0"


class RaisingPlugin:
    """Plugin whose ``execute`` always raises."""

    def execute(self, task: Dict) -> str:
        raise RuntimeError("plugin boom")


# ===========================================================================
# Mock agents
# ===========================================================================
class AsyncAgent:
    """Agent whose async ``execute`` succeeds."""

    def __init__(self) -> None:
        self.calls: List[Dict] = []

    async def execute(self, task: Dict) -> str:
        self.calls.append(task)
        return f"result:{task.get('id', 'none')}"


class SyncAgent:
    """Agent whose sync ``execute`` succeeds."""

    def __init__(self) -> None:
        self.calls: List[Dict] = []

    def execute(self, task: Dict) -> str:
        self.calls.append(task)
        return f"sync-result:{task.get('id', 'none')}"


class FailingAgent:
    """Agent that always raises."""

    def __init__(self, error: str = "agent boom") -> None:
        self.error = error
        self.calls = 0

    async def execute(self, task: Dict) -> str:
        self.calls += 1
        raise RuntimeError(self.error)


class FlakyAgent:
    """Agent that fails ``fail_count`` times then succeeds."""

    def __init__(self, fail_count: int = 3) -> None:
        self.fail_count = fail_count
        self.calls = 0

    async def execute(self, task: Dict) -> str:
        self.calls += 1
        if self.calls <= self.fail_count:
            raise RuntimeError(f"scheduled failure {self.calls}")
        return f"recovered:{self.calls}"


class SlowAgent:
    """Agent that sleeps; tracks concurrent active executions."""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.completed = 0

    async def execute(self, task: Dict) -> str:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            self.completed += 1
            return "done"
        finally:
            self.active -= 1


# ===========================================================================
# Plugin Runtime tests
# ===========================================================================
class TestPluginRuntime:
    # --- registration --------------------------------------------------
    @pytest.mark.asyncio
    async def test_register_plugin_returns_handle(self):
        rt = PluginRuntime()
        handle = await rt.register_plugin(
            "p", CapabilityMappedPlugin(), ["filesystem.read"]
        )
        assert isinstance(handle, PluginHandle)
        assert handle.name == "p"
        assert handle.capabilities == ["filesystem.read"]
        assert handle.execution_count == 0
        assert handle.last_executed == ""
        assert handle.registered_at  # ISO timestamp

    @pytest.mark.asyncio
    async def test_register_plugin_rejects_empty_name(self):
        rt = PluginRuntime()
        with pytest.raises(ValueError, match="non-empty string"):
            await rt.register_plugin("", object(), [])

    @pytest.mark.asyncio
    async def test_register_duplicate_plugin_raises(self):
        rt = PluginRuntime()
        await rt.register_plugin("p", object(), [])
        with pytest.raises(ValueError, match="already registered"):
            await rt.register_plugin("p", object(), [])

    @pytest.mark.asyncio
    async def test_unregister_plugin_returns_true(self):
        rt = PluginRuntime()
        await rt.register_plugin("p", object(), [])
        assert await rt.unregister_plugin("p") is True
        # Unregistering again is a no-op.
        assert await rt.unregister_plugin("p") is False

    @pytest.mark.asyncio
    async def test_unregister_unknown_plugin_returns_false(self):
        rt = PluginRuntime()
        assert await rt.unregister_plugin("nope") is False

    # --- execution ------------------------------------------------------
    @pytest.mark.asyncio
    async def test_execute_plugin_async_method(self):
        rt = PluginRuntime()
        plugin = CapabilityMappedPlugin()
        await rt.register_plugin("p", plugin, ["filesystem.read"])
        result = await rt.execute_plugin("p", "read", "/etc/hosts")
        assert result == "contents:/etc/hosts"
        assert plugin.calls == [("read", "/etc/hosts")]

    @pytest.mark.asyncio
    async def test_execute_plugin_sync_method(self):
        rt = PluginRuntime()
        plugin = CapabilityMappedPlugin()
        await rt.register_plugin(
            "p", plugin, ["filesystem.read", "filesystem.write"]
        )
        result = await rt.execute_plugin(
            "p", "write", "/tmp/x", "hello"
        )
        assert result == "wrote:/tmp/x"
        assert plugin.calls == [("write", "/tmp/x", "hello")]

    @pytest.mark.asyncio
    async def test_execute_plugin_unknown_raises_keyerror(self):
        rt = PluginRuntime()
        with pytest.raises(KeyError, match="not found"):
            await rt.execute_plugin("nope", "read")

    @pytest.mark.asyncio
    async def test_execute_plugin_missing_method_raises_attributeerror(self):
        rt = PluginRuntime()
        await rt.register_plugin("p", CapabilityMappedPlugin(), [])
        with pytest.raises(AttributeError, match="no callable method"):
            await rt.execute_plugin("p", "nonexistent")

    @pytest.mark.asyncio
    async def test_execute_plugin_increments_execution_count(self):
        rt = PluginRuntime()
        plugin = CapabilityMappedPlugin()
        await rt.register_plugin("p", plugin, [])
        await rt.execute_plugin("p", "status")
        await rt.execute_plugin("p", "status")
        handles = await rt.list_plugins()
        assert handles[0].execution_count == 2
        assert handles[0].last_executed  # timestamp set

    # --- capability checks ---------------------------------------------
    @pytest.mark.asyncio
    async def test_check_capability_present(self):
        rt = PluginRuntime()
        await rt.register_plugin(
            "p", object(), ["filesystem.read", "network.http"]
        )
        assert await rt.check_capability("p", "filesystem.read") is True
        assert await rt.check_capability("p", "network.http") is True

    @pytest.mark.asyncio
    async def test_check_capability_absent(self):
        rt = PluginRuntime()
        await rt.register_plugin("p", object(), ["filesystem.read"])
        assert await rt.check_capability("p", "network.http") is False

    @pytest.mark.asyncio
    async def test_check_capability_unknown_plugin(self):
        rt = PluginRuntime()
        assert await rt.check_capability("nope", "anything") is False

    @pytest.mark.asyncio
    async def test_execute_with_required_capability_succeeds(self):
        rt = PluginRuntime()
        plugin = CapabilityMappedPlugin()
        await rt.register_plugin(
            "p", plugin, ["filesystem.read", "filesystem.write"]
        )
        # Both methods have their required capabilities granted.
        assert await rt.execute_plugin("p", "read", "/x") == "contents:/x"
        assert await rt.execute_plugin("p", "write", "/y", "z") == "wrote:/y"

    @pytest.mark.asyncio
    async def test_execute_without_required_capability_raises_permission(self):
        rt = PluginRuntime()
        plugin = CapabilityMappedPlugin()
        await rt.register_plugin("p", plugin, ["filesystem.read"])
        # 'write' requires 'filesystem.write' which is NOT granted.
        with pytest.raises(PermissionError, match="filesystem.write"):
            await rt.execute_plugin("p", "write", "/x", "y")

    @pytest.mark.asyncio
    async def test_execute_unrestricted_method_skips_capability_check(self):
        rt = PluginRuntime()
        plugin = CapabilityMappedPlugin()
        # No capabilities granted, but 'status' has no requirement.
        await rt.register_plugin("p", plugin, [])
        assert await rt.execute_plugin("p", "status") == "healthy"

    @pytest.mark.asyncio
    async def test_permission_denied_does_not_increment_execution_count(self):
        rt = PluginRuntime()
        plugin = CapabilityMappedPlugin()
        await rt.register_plugin("p", plugin, ["filesystem.read"])
        with pytest.raises(PermissionError):
            await rt.execute_plugin("p", "write", "/x", "y")
        handles = await rt.list_plugins()
        assert handles[0].execution_count == 0
        # 'status' is unrestricted; it should still execute fine.
        await rt.execute_plugin("p", "status")
        handles = await rt.list_plugins()
        assert handles[0].execution_count == 1

    @pytest.mark.asyncio
    async def test_requires_capability_decorator(self):
        rt = PluginRuntime()
        plugin = DecoratedPlugin()
        await rt.register_plugin(
            "p", plugin, ["memory.read", "memory.write"]
        )
        assert await rt.execute_plugin("p", "remember", "k", "v") == "stored:k=v"
        assert await rt.execute_plugin("p", "recall", "k") == "value:k"
        # Method without decorator + no REQUIRED_CAPABILITIES → open.
        assert await rt.execute_plugin("p", "version") == "1.0.0"

    @pytest.mark.asyncio
    async def test_decorator_missing_capability_raises(self):
        rt = PluginRuntime()
        plugin = DecoratedPlugin()
        # Grant read but not write.
        await rt.register_plugin("p", plugin, ["memory.read"])
        with pytest.raises(PermissionError, match="memory.write"):
            await rt.execute_plugin("p", "remember", "k", "v")
        # read still works.
        assert await rt.execute_plugin("p", "recall", "k") == "value:k"

    @pytest.mark.asyncio
    async def test_list_plugins_returns_all_handles(self):
        rt = PluginRuntime()
        await rt.register_plugin("a", object(), ["x"])
        await rt.register_plugin("b", object(), ["y"])
        handles = await rt.list_plugins()
        names = sorted(h.name for h in handles)
        assert names == ["a", "b"]

    @pytest.mark.asyncio
    async def test_get_stats_returns_summary(self):
        rt = PluginRuntime()
        plugin = CapabilityMappedPlugin()
        await rt.register_plugin("p", plugin, ["filesystem.read"])
        await rt.execute_plugin("p", "status")
        await rt.execute_plugin("p", "read", "/x")
        stats = rt.get_stats()
        assert stats["running"] is True
        assert stats["total_plugins"] == 1
        assert stats["total_executions"] == 2
        assert stats["permission_denied_count"] == 0
        assert stats["plugins"][0]["name"] == "p"
        assert stats["plugins"][0]["execution_count"] == 2

    @pytest.mark.asyncio
    async def test_is_healthy_when_running(self):
        rt = PluginRuntime()
        assert await rt.is_healthy() is True

    @pytest.mark.asyncio
    async def test_stop_clears_plugins_and_makes_unhealthy(self):
        rt = PluginRuntime()
        await rt.register_plugin("p", object(), [])
        assert await rt.is_healthy() is True
        await rt.stop()
        assert await rt.is_healthy() is False
        assert await rt.list_plugins() == []
        # Stats reflect the cleared state.
        assert rt.get_stats()["total_plugins"] == 0


# ===========================================================================
# Agent Runtime tests
# ===========================================================================
class TestAgentRuntime:
    # --- registration --------------------------------------------------
    @pytest.mark.asyncio
    async def test_register_agent_returns_handle(self):
        rt = AgentRuntime()
        handle = await rt.register_agent("a", AsyncAgent())
        assert isinstance(handle, AgentHandle)
        assert handle.name == "a"
        assert handle.max_concurrent == 1
        assert handle.current_concurrent == 0
        assert handle.execution_count == 0
        assert handle.failure_count == 0
        assert handle.consecutive_failures == 0
        assert handle.status == AgentStatus.IDLE
        assert handle.registered_at

    @pytest.mark.asyncio
    async def test_register_agent_custom_max_concurrent(self):
        rt = AgentRuntime()
        handle = await rt.register_agent(
            "a", AsyncAgent(), max_concurrent=5
        )
        assert handle.max_concurrent == 5

    @pytest.mark.asyncio
    async def test_register_agent_rejects_invalid_max_concurrent(self):
        rt = AgentRuntime()
        with pytest.raises(ValueError, match="max_concurrent"):
            await rt.register_agent("a", AsyncAgent(), max_concurrent=0)

    @pytest.mark.asyncio
    async def test_register_duplicate_agent_raises(self):
        rt = AgentRuntime()
        await rt.register_agent("a", AsyncAgent())
        with pytest.raises(ValueError, match="already registered"):
            await rt.register_agent("a", AsyncAgent())

    @pytest.mark.asyncio
    async def test_unregister_agent(self):
        rt = AgentRuntime()
        await rt.register_agent("a", AsyncAgent())
        assert await rt.unregister_agent("a") is True
        assert await rt.unregister_agent("a") is False
        assert await rt.list_agents() == []

    @pytest.mark.asyncio
    async def test_unregister_unknown_returns_false(self):
        rt = AgentRuntime()
        assert await rt.unregister_agent("nope") is False

    # --- execution ------------------------------------------------------
    @pytest.mark.asyncio
    async def test_execute_agent_async_returns_result(self):
        rt = AgentRuntime()
        await rt.register_agent("a", AsyncAgent())
        result = await rt.execute_agent("a", {"id": "t1"})
        assert isinstance(result, AgentResult)
        assert result.status == "success"
        assert result.result == "result:t1"
        assert result.error == ""
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_execute_agent_sync_returns_result(self):
        rt = AgentRuntime()
        await rt.register_agent("a", SyncAgent())
        result = await rt.execute_agent("a", {"id": "s1"})
        assert result.status == "success"
        assert result.result == "sync-result:s1"

    @pytest.mark.asyncio
    async def test_execute_agent_records_duration(self):
        rt = AgentRuntime()
        await rt.register_agent("a", SlowAgent(delay=0.05))
        result = await rt.execute_agent("a", {"id": "slow"})
        assert result.status == "success"
        # Allow generous lower bound for CI jitter.
        assert result.duration_seconds >= 0.04

    @pytest.mark.asyncio
    async def test_execute_agent_unknown_raises(self):
        rt = AgentRuntime()
        with pytest.raises(KeyError, match="not found"):
            await rt.execute_agent("nope", {})

    @pytest.mark.asyncio
    async def test_execute_agent_updates_counters(self):
        rt = AgentRuntime()
        await rt.register_agent("a", AsyncAgent())
        await rt.execute_agent("a", {"id": 1})
        await rt.execute_agent("a", {"id": 2})
        handles = await rt.list_agents()
        assert handles[0].execution_count == 2
        assert handles[0].last_executed

    # --- failure handling ----------------------------------------------
    @pytest.mark.asyncio
    async def test_execute_agent_failure_increments_failure_count(self):
        rt = AgentRuntime()
        await rt.register_agent("a", FailingAgent())
        result = await rt.execute_agent("a", {})
        assert result.status == "failed"
        assert "RuntimeError" in result.error
        assert "agent boom" in result.error
        handles = await rt.list_agents()
        assert handles[0].failure_count == 1
        assert handles[0].consecutive_failures == 1
        assert handles[0].execution_count == 1
        # Not yet at the circuit-breaker threshold.
        assert handles[0].status == AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_three_consecutive_failures_marks_agent_failed(self):
        rt = AgentRuntime()
        agent = FailingAgent()
        await rt.register_agent("a", agent)
        for _ in range(3):
            result = await rt.execute_agent("a", {})
            assert result.status == "failed"
        handle = (await rt.list_agents())[0]
        assert handle.failure_count == 3
        assert handle.consecutive_failures == 3
        assert handle.status == AgentStatus.FAILED

    @pytest.mark.asyncio
    async def test_execute_failed_agent_raises(self):
        rt = AgentRuntime()
        agent = FailingAgent()
        await rt.register_agent("a", agent)
        for _ in range(3):
            await rt.execute_agent("a", {})
        # 4th call must be rejected because the agent is FAILED.
        with pytest.raises(RuntimeError, match="FAILED state"):
            await rt.execute_agent("a", {})

    @pytest.mark.asyncio
    async def test_recover_agent_resets_status(self):
        rt = AgentRuntime()
        agent = FailingAgent()
        await rt.register_agent("a", agent)
        for _ in range(3):
            await rt.execute_agent("a", {})
        assert await rt.get_agent_status("a") == AgentStatus.FAILED
        assert await rt.recover_agent("a") is True
        assert await rt.get_agent_status("a") == AgentStatus.IDLE
        handle = (await rt.list_agents())[0]
        assert handle.consecutive_failures == 0
        # failure_count is cumulative — NOT reset.
        assert handle.failure_count == 3

    @pytest.mark.asyncio
    async def test_recover_unknown_agent_returns_false(self):
        rt = AgentRuntime()
        assert await rt.recover_agent("nope") is False

    @pytest.mark.asyncio
    async def test_recover_non_failed_agent_returns_false(self):
        rt = AgentRuntime()
        await rt.register_agent("a", AsyncAgent())
        # Agent is IDLE — recovering it is a no-op.
        assert await rt.recover_agent("a") is False

    @pytest.mark.asyncio
    async def test_success_resets_consecutive_failures(self):
        rt = AgentRuntime()
        agent = FlakyAgent(fail_count=1)
        await rt.register_agent("a", agent)
        # 1 failure.
        await rt.execute_agent("a", {})
        handle = (await rt.list_agents())[0]
        assert handle.consecutive_failures == 1
        # Next call succeeds → counter resets.
        await rt.execute_agent("a", {})
        handle = (await rt.list_agents())[0]
        assert handle.consecutive_failures == 0
        assert handle.failure_count == 1  # cumulative
        assert handle.status == AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_agent_works_after_recovery(self):
        rt = AgentRuntime()
        # Fails 3 times (triggers FAILED), then succeeds on the 4th call.
        agent = FlakyAgent(fail_count=3)
        await rt.register_agent("a", agent)
        for _ in range(3):
            await rt.execute_agent("a", {})
        assert await rt.get_agent_status("a") == AgentStatus.FAILED
        assert await rt.recover_agent("a") is True
        result = await rt.execute_agent("a", {"id": "post"})
        assert result.status == "success"
        assert result.result == "recovered:4"

    # --- concurrency ----------------------------------------------------
    @pytest.mark.asyncio
    async def test_concurrency_limit_rejects_excess(self):
        rt = AgentRuntime()
        agent = SlowAgent(delay=0.05)
        await rt.register_agent("a", agent, max_concurrent=2)
        results = await asyncio.gather(
            rt.execute_agent("a", {"i": 1}),
            rt.execute_agent("a", {"i": 2}),
            rt.execute_agent("a", {"i": 3}),
            rt.execute_agent("a", {"i": 4}),
            return_exceptions=True,
        )
        successes = [
            r for r in results
            if isinstance(r, AgentResult) and r.status == "success"
        ]
        errors = [
            r for r in results if isinstance(r, Exception)
        ]
        assert len(successes) == 2
        assert len(errors) == 2
        for err in errors:
            assert "max concurrency" in str(err)
        # The agent never saw more than 2 concurrent executions.
        assert agent.max_active == 2
        assert agent.completed == 2

    @pytest.mark.asyncio
    async def test_concurrency_release_allows_next_execution(self):
        rt = AgentRuntime()
        agent = SlowAgent(delay=0.02)
        await rt.register_agent("a", agent, max_concurrent=1)
        # First execution completes, freeing the slot.
        r1 = await rt.execute_agent("a", {"i": 1})
        assert r1.status == "success"
        # Second execution should now be allowed.
        r2 = await rt.execute_agent("a", {"i": 2})
        assert r2.status == "success"
        handle = (await rt.list_agents())[0]
        assert handle.execution_count == 2
        assert handle.current_concurrent == 0
        assert handle.status == AgentStatus.IDLE

    # --- introspection --------------------------------------------------
    @pytest.mark.asyncio
    async def test_get_agent_status(self):
        rt = AgentRuntime()
        await rt.register_agent("a", AsyncAgent())
        assert await rt.get_agent_status("a") == AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_get_agent_status_unknown_raises(self):
        rt = AgentRuntime()
        with pytest.raises(KeyError, match="not found"):
            await rt.get_agent_status("nope")

    @pytest.mark.asyncio
    async def test_list_agents_returns_all(self):
        rt = AgentRuntime()
        await rt.register_agent("a", AsyncAgent())
        await rt.register_agent("b", AsyncAgent())
        handles = await rt.list_agents()
        names = sorted(h.name for h in handles)
        assert names == ["a", "b"]

    @pytest.mark.asyncio
    async def test_get_stats_returns_summary(self):
        rt = AgentRuntime()
        agent = FailingAgent()
        await rt.register_agent("a", agent)
        await rt.execute_agent("a", {})  # success — no, this fails
        await rt.execute_agent("a", {})  # 2nd failure
        stats = rt.get_stats()
        assert stats["running"] is True
        assert stats["total_agents"] == 1
        assert stats["total_executions"] == 2
        assert stats["total_successes"] == 0
        assert stats["total_failures"] == 2
        assert stats["total_recoveries"] == 0
        assert stats["agents"][0]["name"] == "a"
        assert stats["agents"][0]["failure_count"] == 2
        assert stats["agents"][0]["consecutive_failures"] == 2

    @pytest.mark.asyncio
    async def test_is_healthy_with_failed_agent(self):
        rt = AgentRuntime()
        agent = FailingAgent()
        await rt.register_agent("a", agent)
        # Initially healthy.
        assert await rt.is_healthy() is True
        for _ in range(3):
            await rt.execute_agent("a", {})
        # Now an agent is FAILED → runtime reports unhealthy.
        assert await rt.is_healthy() is False
        await rt.recover_agent("a")
        assert await rt.is_healthy() is True

    @pytest.mark.asyncio
    async def test_stop_clears_agents_and_makes_unhealthy(self):
        rt = AgentRuntime()
        await rt.register_agent("a", AsyncAgent())
        assert await rt.is_healthy() is True
        await rt.stop()
        assert await rt.is_healthy() is False
        assert await rt.list_agents() == []
        assert rt.get_stats()["total_agents"] == 0
