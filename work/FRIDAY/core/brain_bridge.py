"""Brain Integration — wires the runtime layer to FridayBrain.

This module provides the integration layer between the FRIDAY AI Runtime
(core/runtime/) and the FridayBrain (core/brain.py). It does NOT modify
FridayBrain — instead, it provides a RuntimeBridge that the brain can
optionally use to access runtime services.

Design principles:
    - **Non-invasive**: Does not modify FridayBrain.
    - **Optional**: The brain works without the runtime; this bridge
      adds runtime capabilities when available.
    - **Lazy**: Runtime services are loaded on first use.
    - **Backward-compatible**: All existing brain tests pass unchanged.

Usage::

    bridge = get_brain_bridge()
    await bridge.start()  # starts the runtime
    
    # The bridge provides access to runtime services:
    event_bus = bridge.event_bus
    scheduler = bridge.scheduler
    workflow = bridge.workflow_runtime
    agent_runtime = bridge.agent_runtime
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.brain_bridge")


@dataclass
class BridgeStatus:
    """Status of the brain-runtime bridge."""
    initialized: bool = False
    runtime_started: bool = False
    capabilities_registered: int = 0
    agents_registered: int = 0
    started_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "initialized": self.initialized,
            "runtime_started": self.runtime_started,
            "capabilities_registered": self.capabilities_registered,
            "agents_registered": self.agents_registered,
            "started_at": self.started_at,
        }


class BrainBridge:
    """Integration bridge between FridayBrain and the AI Runtime.

    This bridge:
        1. Starts the runtime (RuntimeManager)
        2. Registers brain capabilities (CapabilityRegistry)
        3. Registers built-in agents (AgentRuntime)
        4. Provides event subscriptions for brain events
        5. Exposes workflow execution for multi-step tasks

    The bridge is a singleton — there is one bridge per process.
    """

    def __init__(self):
        self._status = BridgeStatus()
        self._runtime_manager = None
        self._lock = asyncio.Lock()

    @property
    def status(self) -> BridgeStatus:
        return self._status

    @property
    def runtime_manager(self):
        return self._runtime_manager

    @property
    def event_bus(self):
        if self._runtime_manager:
            return self._runtime_manager.event_bus
        return None

    @property
    def scheduler(self):
        if self._runtime_manager:
            return self._runtime_manager.scheduler
        return None

    @property
    def capability_registry(self):
        if self._runtime_manager:
            return self._runtime_manager.capability_registry
        return None

    @property
    def resource_manager(self):
        if self._runtime_manager:
            return self._runtime_manager.resource_manager
        return None

    @property
    def execution_graph(self):
        if self._runtime_manager:
            return self._runtime_manager.execution_graph
        return None

    @property
    def agent_runtime(self):
        """Get the agent runtime if available."""
        try:
            from core.runtime.agent_runtime import AgentRuntime
            if not hasattr(self, '_agent_runtime'):
                self._agent_runtime = AgentRuntime()
            return self._agent_runtime
        except ImportError:
            return None

    @property
    def plugin_runtime(self):
        """Get the plugin runtime if available."""
        try:
            from core.runtime.plugin_runtime import PluginRuntime
            if not hasattr(self, '_plugin_runtime'):
                self._plugin_runtime = PluginRuntime()
            return self._plugin_runtime
        except ImportError:
            return None

    @property
    def lifecycle_manager(self):
        """Get the lifecycle manager if available."""
        try:
            from core.runtime.lifecycle_manager import LifecycleManager
            if not hasattr(self, '_lifecycle_manager'):
                self._lifecycle_manager = LifecycleManager()
            return self._lifecycle_manager
        except ImportError:
            return None

    @property
    def memory_runtime(self):
        """Get the memory runtime if available."""
        try:
            from core.runtime.memory_runtime import MemoryRuntime
            if not hasattr(self, '_memory_runtime'):
                self._memory_runtime = MemoryRuntime()
            return self._memory_runtime
        except ImportError:
            return None

    @property
    def workflow_runtime(self):
        """Get the workflow runtime if available."""
        try:
            from core.runtime.workflow_runtime import WorkflowRuntime
            if not hasattr(self, '_workflow_runtime'):
                self._workflow_runtime = WorkflowRuntime()
            return self._workflow_runtime
        except ImportError:
            return None

    async def start(self) -> None:
        """Start the runtime and register brain capabilities."""
        async with self._lock:
            if self._status.runtime_started:
                logger.warning("Brain bridge already started")
                return

            logger.info("Starting Brain Bridge...")

            # Start the runtime manager
            from core.runtime.runtime_manager import get_runtime_manager
            self._runtime_manager = get_runtime_manager()
            await self._runtime_manager.start()
            self._status.runtime_started = True

            # Register brain capabilities
            await self._register_capabilities()

            # Register built-in agents
            await self._register_agents()

            # Subscribe to runtime events
            await self._subscribe_events()

            self._status.started_at = datetime.now(timezone.utc).isoformat()
            self._status.initialized = True

            logger.info(
                f"Brain Bridge started: "
                f"{self._status.capabilities_registered} capabilities, "
                f"{self._status.agents_registered} agents"
            )

    async def _register_capabilities(self) -> None:
        """Register brain capabilities in the capability registry."""
        if not self.capability_registry:
            return

        # Register the brain's capabilities
        capabilities = [
            ("brain.chat", "FridayBrain", {"description": "Chat with the AI brain"}),
            ("brain.memory", "FridayMemory", {"description": "Store and retrieve memories"}),
            ("brain.reasoning", "FridayBrain", {"description": "LLM-based reasoning"}),
            ("brain.creative", "FridayBrain", {"description": "Image/video generation routing"}),
        ]

        for name, provider_type, metadata in capabilities:
            self.capability_registry.register(name, provider_type, metadata)

        self._status.capabilities_registered = len(capabilities)

    async def _register_agents(self) -> None:
        """Register built-in agents in the agent runtime."""
        if not self.agent_runtime:
            return

        # Register placeholder agents (the actual agent classes are
        # registered when the brain is available)
        agents = [
            ("research", "ResearchAgent", {"description": "Research agent"}),
            ("coding", "CodingAgent", {"description": "Coding agent"}),
            ("writing", "WritingAgent", {"description": "Writing agent"}),
            ("task", "TaskAgent", {"description": "Task breakdown agent"}),
        ]

        for name, agent_type, metadata in agents:
            # Register a placeholder — the actual agent will be set later
            await self.agent_runtime.register_agent(
                name=name,
                agent={"type": agent_type, **metadata},
                max_concurrent=1,
            )

        self._status.agents_registered = len(agents)

    async def _subscribe_events(self) -> None:
        """Subscribe to runtime events."""
        if not self.event_bus:
            return

        async def on_task_completed(event):
            logger.debug(f"Task completed: {event.data}")

        async def on_task_failed(event):
            logger.warning(f"Task failed: {event.data}")

        self.event_bus.subscribe("scheduler.job_completed", on_task_completed)
        self.event_bus.subscribe("scheduler.job_failed", on_task_failed)

    async def execute_workflow(self, steps: List[Dict]) -> Dict[str, Any]:
        """Execute a multi-step workflow.

        Args:
            steps: List of step dicts with keys: id, func, depends_on.

        Returns:
            Dict mapping step_id → result.
        """
        if not self.workflow_runtime:
            return {"error": "Workflow runtime not available"}

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
            name="brain_workflow",
            steps=wf_steps,
        )
        return await self.workflow_runtime.execute_workflow(workflow.id)

    async def stop(self) -> None:
        """Stop the brain bridge and runtime."""
        async with self._lock:
            if not self._status.runtime_started:
                return

            logger.info("Stopping Brain Bridge...")

            # Stop sub-runtimes
            for attr in ['_agent_runtime', '_plugin_runtime', '_lifecycle_manager', '_memory_runtime', '_workflow_runtime']:
                sub = getattr(self, attr, None)
                if sub and hasattr(sub, 'stop'):
                    try:
                        await sub.stop()
                    except Exception:
                        pass

            # Stop the runtime manager
            if self._runtime_manager:
                await self._runtime_manager.stop()

            self._status.runtime_started = False
            self._status.initialized = False
            logger.info("Brain Bridge stopped")

    def get_status(self) -> BridgeStatus:
        """Get the bridge status."""
        return self._status

    async def health_check(self) -> Dict[str, bool]:
        """Check health of all bridge components."""
        results = {}
        if self._runtime_manager:
            results["runtime"] = True
            runtime_health = await self._runtime_manager.health_check()
            results.update(runtime_health)
        if self.agent_runtime:
            results["agent_runtime"] = await self.agent_runtime.is_healthy()
        if self.plugin_runtime:
            results["plugin_runtime"] = await self.plugin_runtime.is_healthy()
        if self.lifecycle_manager:
            results["lifecycle_manager"] = await self.lifecycle_manager.is_healthy()
        if self.memory_runtime:
            results["memory_runtime"] = await self.memory_runtime.is_healthy()
        return results


# Singleton
_bridge: Optional[BrainBridge] = None


def get_brain_bridge() -> BrainBridge:
    """Get the singleton BrainBridge instance."""
    global _bridge
    if _bridge is None:
        _bridge = BrainBridge()
    return _bridge
