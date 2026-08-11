"""Tests for the Brain Bridge — runtime <-> brain integration."""
import asyncio
import pytest

from core.brain_bridge import BrainBridge, BridgeStatus, get_brain_bridge


class TestBrainBridge:
    def test_initial_state(self):
        bridge = BrainBridge()
        assert bridge.status.initialized is False
        assert bridge.status.runtime_started is False

    def test_status_to_dict(self):
        status = BridgeStatus(initialized=True, runtime_started=True)
        d = status.to_dict()
        assert d["initialized"] is True
        assert d["runtime_started"] is True

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        bridge = BrainBridge()
        await bridge.start()
        assert bridge.status.runtime_started is True
        assert bridge.status.initialized is True
        assert bridge.status.capabilities_registered > 0
        assert bridge.status.agents_registered > 0
        await bridge.stop()
        assert bridge.status.runtime_started is False

    @pytest.mark.asyncio
    async def test_capabilities_registered(self):
        bridge = BrainBridge()
        await bridge.start()
        assert bridge.capability_registry is not None
        assert bridge.capability_registry.has_capability("brain.chat")
        assert bridge.capability_registry.has_capability("brain.memory")
        assert bridge.capability_registry.has_capability("brain.reasoning")
        assert bridge.capability_registry.has_capability("brain.creative")
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_agents_registered(self):
        bridge = BrainBridge()
        await bridge.start()
        assert bridge.agent_runtime is not None
        agents = await bridge.agent_runtime.list_agents()
        agent_names = [a.name for a in agents]
        assert "research" in agent_names
        assert "coding" in agent_names
        assert "writing" in agent_names
        assert "task" in agent_names
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_event_bus_available(self):
        bridge = BrainBridge()
        await bridge.start()
        assert bridge.event_bus is not None
        stats = bridge.event_bus.get_stats()
        assert stats["total_events"] == 0
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_scheduler_available(self):
        bridge = BrainBridge()
        await bridge.start()
        assert bridge.scheduler is not None
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_workflow_runtime_available(self):
        bridge = BrainBridge()
        await bridge.start()
        assert bridge.workflow_runtime is not None
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_plugin_runtime_available(self):
        bridge = BrainBridge()
        await bridge.start()
        assert bridge.plugin_runtime is not None
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_lifecycle_manager_available(self):
        bridge = BrainBridge()
        await bridge.start()
        assert bridge.lifecycle_manager is not None
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_memory_runtime_available(self):
        bridge = BrainBridge()
        await bridge.start()
        assert bridge.memory_runtime is not None
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_health_check(self):
        bridge = BrainBridge()
        await bridge.start()
        health = await bridge.health_check()
        assert isinstance(health, dict)
        assert "runtime" in health
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self):
        bridge = BrainBridge()
        await bridge.start()
        started_at_1 = bridge.status.started_at
        await bridge.start()  # Should not re-start
        assert bridge.status.started_at == started_at_1
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        bridge = BrainBridge()
        # Should not raise
        await bridge.stop()
        assert bridge.status.runtime_started is False

    @pytest.mark.asyncio
    async def test_get_status(self):
        bridge = BrainBridge()
        await bridge.start()
        status = bridge.get_status()
        assert status.runtime_started is True
        assert status.capabilities_registered > 0
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_singleton(self):
        bridge1 = get_brain_bridge()
        bridge2 = get_brain_bridge()
        assert bridge1 is bridge2
