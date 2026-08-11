"""Runtime Context — dependency injection for the entire runtime.

Replaces module-level singletons with a single RuntimeContext that
carries all runtime services. This is the foundation of dependency
injection for the FRIDAY runtime.

Design principles:
    - **No globals**: All runtime state lives in RuntimeContext.
    - **Injectable**: Components receive their dependencies via constructor.
    - **Testable**: Tests can create a RuntimeContext with mock services.
    - **Backward-compatible**: Existing singletons still work; RuntimeContext
      is opt-in.

Usage::

    ctx = RuntimeContext()
    await ctx.initialize()  # creates all services
    
    # Access services
    scheduler = ctx.scheduler
    event_bus = ctx.event_bus
    workflow = ctx.workflow_runtime
    
    # Inject into components
    my_component = MyComponent(ctx)
    
    await ctx.shutdown()
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.context")


@dataclass
class RuntimeContextStatus:
    """Status of the runtime context."""
    initialized: bool = False
    started_at: str = ""
    service_count: int = 0
    services: Dict[str, str] = field(default_factory=dict)  # name → state

    def to_dict(self) -> Dict[str, Any]:
        return {
            "initialized": self.initialized,
            "started_at": self.started_at,
            "service_count": self.service_count,
            "services": dict(self.services),
        }


class RuntimeContext:
    """The single dependency injection container for the FRIDAY runtime.

    All runtime services are created and managed here. Components that
    need runtime access receive a RuntimeContext instance rather than
    importing module-level singletons.

    This does NOT replace existing singletons — it provides a path
    to dependency injection that new code can use. Existing code
    continues to work unchanged.
    """

    def __init__(self):
        self._status = RuntimeContextStatus()
        self._services: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    @property
    def status(self) -> RuntimeContextStatus:
        return self._status

    @property
    def event_bus(self):
        return self._services.get("event_bus")

    @property
    def capability_registry(self):
        return self._services.get("capability_registry")

    @property
    def resource_manager(self):
        return self._services.get("resource_manager")

    @property
    def scheduler(self):
        return self._services.get("scheduler")

    @property
    def execution_graph(self):
        return self._services.get("execution_graph")

    @property
    def context_runtime(self):
        return self._services.get("context_runtime")

    @property
    def state_runtime(self):
        return self._services.get("state_runtime")

    @property
    def session_runtime(self):
        return self._services.get("session_runtime")

    @property
    def workflow_runtime(self):
        return self._services.get("workflow_runtime")

    @property
    def plugin_runtime(self):
        return self._services.get("plugin_runtime")

    @property
    def agent_runtime(self):
        return self._services.get("agent_runtime")

    @property
    def lifecycle_manager(self):
        return self._services.get("lifecycle_manager")

    @property
    def memory_runtime(self):
        return self._services.get("memory_runtime")

    @property
    def executor(self):
        """The unified runtime executor."""
        return self._services.get("executor")

    async def initialize(self) -> None:
        """Create and start all runtime services."""
        async with self._lock:
            if self._status.initialized:
                return

            logger.info("Initializing RuntimeContext...")

            # Create services in dependency order
            await self._create_event_bus()
            await self._create_capability_registry()
            await self._create_resource_manager()
            await self._create_scheduler()
            await self._create_execution_graph()
            await self._create_context_runtime()
            await self._create_state_runtime()
            await self._create_session_runtime()
            await self._create_workflow_runtime()
            await self._create_plugin_runtime()
            await self._create_agent_runtime()
            await self._create_lifecycle_manager()
            await self._create_memory_runtime()
            await self._create_executor()

            self._status.initialized = True
            self._status.started_at = datetime.now(timezone.utc).isoformat()
            self._status.service_count = len(self._services)
            self._status.services = {
                k: type(v).__name__ for k, v in self._services.items()
            }

            logger.info(
                f"RuntimeContext initialized: {self._status.service_count} services"
            )

    async def _create_event_bus(self) -> None:
        from core.runtime.event_bus import EventBus
        self._services["event_bus"] = EventBus()

    async def _create_capability_registry(self) -> None:
        from core.runtime.capability_registry import CapabilityRegistry
        self._services["capability_registry"] = CapabilityRegistry()

    async def _create_resource_manager(self) -> None:
        from core.runtime.resource_manager import ResourceManager
        self._services["resource_manager"] = ResourceManager()

    async def _create_scheduler(self) -> None:
        from core.runtime.scheduler import RuntimeScheduler
        self._services["scheduler"] = RuntimeScheduler(
            event_bus=self.event_bus
        )
        await self.scheduler.start()

    async def _create_execution_graph(self) -> None:
        from core.runtime.execution_graph import ExecutionGraph
        self._services["execution_graph"] = ExecutionGraph()

    async def _create_context_runtime(self) -> None:
        from core.runtime.context_runtime import ContextRuntime
        self._services["context_runtime"] = ContextRuntime(
            event_bus=self.event_bus
        )

    async def _create_state_runtime(self) -> None:
        from core.runtime.state_runtime import StateRuntime
        self._services["state_runtime"] = StateRuntime()

    async def _create_session_runtime(self) -> None:
        from core.runtime.session_runtime import SessionRuntime
        self._services["session_runtime"] = SessionRuntime()

    async def _create_workflow_runtime(self) -> None:
        from core.runtime.workflow_runtime import WorkflowRuntime
        self._services["workflow_runtime"] = WorkflowRuntime(
            event_bus=self.event_bus
        )

    async def _create_plugin_runtime(self) -> None:
        from core.runtime.plugin_runtime import PluginRuntime
        self._services["plugin_runtime"] = PluginRuntime()

    async def _create_agent_runtime(self) -> None:
        from core.runtime.agent_runtime import AgentRuntime
        self._services["agent_runtime"] = AgentRuntime()

    async def _create_lifecycle_manager(self) -> None:
        from core.runtime.lifecycle_manager import LifecycleManager
        self._services["lifecycle_manager"] = LifecycleManager()

    async def _create_memory_runtime(self) -> None:
        from core.runtime.memory_runtime import MemoryRuntime
        self._services["memory_runtime"] = MemoryRuntime()

    async def _create_executor(self) -> None:
        from core.runtime.executor import RuntimeExecutor
        self._services["executor"] = RuntimeExecutor(
            event_bus=self.event_bus,
            execution_graph=self.execution_graph,
            workflow_runtime=self.workflow_runtime,
            scheduler=self.scheduler,
            capability_registry=self.capability_registry,
            resource_manager=self.resource_manager,
        )

    async def shutdown(self) -> None:
        """Shut down all services in reverse order."""
        async with self._lock:
            if not self._status.initialized:
                return

            logger.info("Shutting down RuntimeContext...")

            # Shut down in reverse order
            shutdown_order = [
                "executor", "memory_runtime", "lifecycle_manager",
                "agent_runtime", "plugin_runtime", "workflow_runtime",
                "session_runtime", "state_runtime", "context_runtime",
                "execution_graph", "scheduler", "resource_manager",
                "capability_registry", "event_bus",
            ]

            for name in shutdown_order:
                service = self._services.get(name)
                if service and hasattr(service, "stop"):
                    try:
                        await service.stop()
                    except Exception as exc:
                        logger.error(f"Error stopping {name}: {exc}")

            self._services.clear()
            self._status.initialized = False
            logger.info("RuntimeContext shut down")

    def get_service(self, name: str) -> Optional[Any]:
        """Get a service by name."""
        return self._services.get(name)

    def register_service(self, name: str, service: Any) -> None:
        """Register an additional service."""
        self._services[name] = service
        self._status.service_count = len(self._services)
        self._status.services[name] = type(service).__name__

    async def health_check(self) -> Dict[str, bool]:
        """Check health of all services."""
        results = {}
        for name, service in self._services.items():
            if hasattr(service, "is_healthy"):
                try:
                    results[name] = await service.is_healthy()
                except Exception:
                    results[name] = False
            else:
                results[name] = True
        return results

    def get_status(self) -> RuntimeContextStatus:
        return self._status


# Singleton (the only global — all other services are accessed through it)
_context: Optional[RuntimeContext] = None


def get_runtime_context() -> RuntimeContext:
    """Get the singleton RuntimeContext.

    This is the ONLY module-level singleton in the runtime layer.
    All other services are accessed through the context.
    """
    global _context
    if _context is None:
        _context = RuntimeContext()
    return _context
