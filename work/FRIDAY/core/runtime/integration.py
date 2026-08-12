"""Runtime Integration — wires all runtime subsystems together.

This is the single integration point that connects:
    - FridayBrain ↔ RuntimeExecutor (via BrainRuntimeAdapter)
    - DriverManager ↔ Tool/Model/Plugin drivers
    - KnowledgeFilesystem ↔ KnowledgeBase
    - WorkspaceManager ↔ Task execution
    - RuntimeKernel ↔ RuntimeContext
    - PromptShield ↔ All LLM paths
    - PolicyEngine ↔ All privileged operations
    - RuntimeObservability ↔ All subsystems
    - EventBus ↔ All state transitions

The integration layer does NOT modify existing modules. It creates
adapters and wires them together. Existing code continues to work.

Usage::

    integration = get_runtime_integration()
    await integration.start()  # wires everything together
    
    # Access the brain adapter (runtime-mediated brain access)
    adapter = integration.brain_adapter
    
    # Access the unified runtime context
    ctx = integration.context
    
    await integration.stop()
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.integration")


@dataclass
class IntegrationStatus:
    """Status of the runtime integration."""
    initialized: bool = False
    started_at: str = ""
    components_wired: int = 0
    components: Dict[str, str] = field(default_factory=dict)  # name → status

    def to_dict(self) -> Dict[str, Any]:
        return {
            "initialized": self.initialized,
            "started_at": self.started_at,
            "components_wired": self.components_wired,
            "components": dict(self.components),
        }


class RuntimeIntegration:
    """Wires all runtime subsystems together.

    This is the convergence point for the Runtime → Brain integration.
    It creates adapters, registers drivers, wires security, and
    connects all subsystems through the EventBus.

    The integration is NON-INVASIVE:
        - FridayBrain is NOT modified
        - Existing singletons still work
        - Existing tests still pass
        - The integration layer is opt-in
    """

    def __init__(self):
        self._status = IntegrationStatus()
        self._lock = asyncio.Lock()

        # Integration components
        self.context = None
        self.brain_adapter = None
        self.driver_manager = None
        self.observability = None
        self.kernel = None
        self.prompt_shield = None
        self.policy_engine = None
        self.event_bus = None

    @property
    def status(self) -> IntegrationStatus:
        return self._status

    async def start(self, brain: Any = None) -> None:
        """Start the full runtime integration.

        Args:
            brain: Optional FridayBrain instance. If provided, the
                   brain adapter will wrap it for runtime-mediated access.
        """
        async with self._lock:
            if self._status.initialized:
                return

            logger.info("Starting Runtime Integration...")

            # 1. Initialize RuntimeContext (DI container)
            await self._wire_runtime_context()

            # 2. Initialize RuntimeKernel
            await self._wire_kernel()

            # 3. Initialize security layers
            await self._wire_security()

            # 4. Initialize driver layer
            await self._wire_drivers()

            # 5. Initialize brain adapter
            await self._wire_brain_adapter(brain)

            # 6. Initialize observability
            await self._wire_observability()

            # 7. Emit integration event
            if self.event_bus:
                await self.event_bus.publish(
                    "runtime.integration.started",
                    {"components": self._status.components_wired},
                    source="integration",
                )

            self._status.initialized = True
            self._status.started_at = datetime.now(timezone.utc).isoformat()
            logger.info(
                f"Runtime Integration started: {self._status.components_wired} components wired"
            )

    async def _wire_runtime_context(self) -> None:
        """Initialize the RuntimeContext (DI container)."""
        try:
            from core.runtime.runtime_context import RuntimeContext
            self.context = RuntimeContext()
            await self.context.initialize()
            self.event_bus = self.context.event_bus
            self._status.components["runtime_context"] = "wired"
            self._status.components_wired += 1
            logger.info("  ✓ RuntimeContext wired")
        except Exception as exc:
            self._status.components["runtime_context"] = f"failed: {exc}"
            logger.error(f"  ✗ RuntimeContext failed: {exc}")

    async def _wire_kernel(self) -> None:
        """Initialize the RuntimeKernel."""
        try:
            from core.runtime.kernel.runtime_kernel import RuntimeKernel
            self.kernel = RuntimeKernel()
            await self.kernel.start()
            self._status.components["kernel"] = "wired"
            self._status.components_wired += 1
            logger.info("  ✓ RuntimeKernel wired")
        except Exception as exc:
            self._status.components["kernel"] = f"failed: {exc}"
            logger.error(f"  ✗ RuntimeKernel failed: {exc}")

    async def _wire_security(self) -> None:
        """Initialize security layers (PromptShield + PolicyEngine)."""
        try:
            from core.runtime.security.prompt_shield import PromptShield
            from core.runtime.security.policy_engine import create_default_policy_engine

            self.prompt_shield = PromptShield()
            self.policy_engine = create_default_policy_engine()

            # Allow all brain capabilities by default
            self.policy_engine.grant_capability("brain.chat")
            self.policy_engine.grant_capability("brain.reasoning")
            self.policy_engine.grant_capability("brain.creative")
            self.policy_engine.grant_capability("brain.memory")

            self._status.components["prompt_shield"] = "wired"
            self._status.components["policy_engine"] = "wired"
            self._status.components_wired += 2
            logger.info("  ✓ PromptShield + PolicyEngine wired")
        except Exception as exc:
            self._status.components["security"] = f"failed: {exc}"
            logger.error(f"  ✗ Security failed: {exc}")

    async def _wire_drivers(self) -> None:
        """Initialize the driver layer with auto-discovery."""
        try:
            from core.runtime.drivers.driver_manager import DriverManager
            from core.runtime.drivers.tool_driver import WebSearchToolDriver, CodeExecutionToolDriver
            from core.runtime.drivers.model_driver import GLMModelDriver, ClaudeModelDriver
            from core.runtime.drivers.plugin_driver import WeatherPluginDriver

            self.driver_manager = DriverManager()

            # Auto-register built-in drivers
            self.driver_manager.register_tool("web_search", WebSearchToolDriver())
            self.driver_manager.register_tool("code_execution", CodeExecutionToolDriver())
            self.driver_manager.register_model("glm", GLMModelDriver())
            self.driver_manager.register_model("claude", ClaudeModelDriver())
            self.driver_manager.register_plugin("weather", WeatherPluginDriver())

            # Allow tool/model/plugin capabilities in policy engine
            if self.policy_engine:
                self.policy_engine.grant_capability("tool.web_search")
                self.policy_engine.grant_capability("tool.code_execution")
                self.policy_engine.grant_capability("model.glm")
                self.policy_engine.grant_capability("model.claude")
                self.policy_engine.grant_capability("plugin.weather")

            self._status.components["driver_manager"] = "wired"
            self._status.components_wired += 1
            logger.info("  ✓ DriverManager wired (5 drivers registered)")
        except Exception as exc:
            self._status.components["drivers"] = f"failed: {exc}"
            logger.error(f"  ✗ Drivers failed: {exc}")

    async def _wire_brain_adapter(self, brain: Any = None) -> None:
        """Wire the brain adapter (runtime-mediated brain access)."""
        try:
            from core.runtime.brain_adapter import BrainRuntimeAdapter

            # Get executor from RuntimeContext
            executor = None
            if self.context:
                executor = self.context.executor

            self.brain_adapter = BrainRuntimeAdapter(
                brain=brain,
                executor=executor,
                event_bus=self.event_bus,
                prompt_shield=self.prompt_shield,
                policy_engine=self.policy_engine,
                driver_manager=self.driver_manager,
            )

            self._status.components["brain_adapter"] = "wired"
            self._status.components_wired += 1
            brain_status = "with brain" if brain else "without brain (adapter ready)"
            logger.info(f"  ✓ BrainRuntimeAdapter wired ({brain_status})")
        except Exception as exc:
            self._status.components["brain_adapter"] = f"failed: {exc}"
            logger.error(f"  ✗ Brain adapter failed: {exc}")

    async def _wire_observability(self) -> None:
        """Wire observability to collect metrics from all subsystems."""
        try:
            from core.runtime.observability import RuntimeObservability

            self.observability = RuntimeObservability(context=self.context)

            self._status.components["observability"] = "wired"
            self._status.components_wired += 1
            logger.info("  ✓ RuntimeObservability wired")
        except Exception as exc:
            self._status.components["observability"] = f"failed: {exc}"
            logger.error(f"  ✗ Observability failed: {exc}")

    async def stop(self) -> None:
        """Shut down the integration."""
        async with self._lock:
            if not self._status.initialized:
                return

            logger.info("Stopping Runtime Integration...")

            if self.kernel:
                await self.kernel.stop()

            if self.context:
                await self.context.shutdown()

            self._status.initialized = False
            logger.info("Runtime Integration stopped")

    async def health_check(self) -> Dict[str, bool]:
        """Check health of all integrated components."""
        results = {}

        if self.context:
            ctx_health = await self.context.health_check()
            results["runtime_context"] = all(ctx_health.values())
        if self.kernel:
            kernel_health = await self.kernel.health_check()
            results["kernel"] = kernel_health.healthy
        if self.prompt_shield:
            results["prompt_shield"] = await self.prompt_shield.is_healthy()
        if self.policy_engine:
            results["policy_engine"] = await self.policy_engine.is_healthy()
        if self.driver_manager:
            results["driver_manager"] = await self.driver_manager.is_healthy()
        if self.brain_adapter:
            results["brain_adapter"] = await self.brain_adapter.is_healthy()

        return results

    async def get_dashboard(self) -> Dict[str, Any]:
        """Get a unified dashboard of all integrated subsystems."""
        dashboard = {
            "integration_status": self._status.to_dict(),
            "health": await self.health_check(),
        }

        if self.observability:
            dashboard["runtime_metrics"] = await self.observability.get_dashboard()

        if self.brain_adapter:
            dashboard["brain_stats"] = self.brain_adapter.get_stats()

        if self.driver_manager:
            dashboard["driver_stats"] = self.driver_manager.get_stats()

        if self.kernel:
            dashboard["kernel_status"] = self.kernel.get_status()

        return dashboard


# Singleton
_integration: Optional[RuntimeIntegration] = None


def get_runtime_integration() -> RuntimeIntegration:
    """Get the singleton RuntimeIntegration instance."""
    global _integration
    if _integration is None:
        _integration = RuntimeIntegration()
    return _integration
