"""Runtime Manager — lifecycle management for all runtime subsystems."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime")


class RuntimeState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass
class RuntimeStatus:
    """Current status of the runtime."""
    state: RuntimeState = RuntimeState.STOPPED
    started_at: str = ""
    uptime_seconds: float = 0.0
    subsystems: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "started_at": self.started_at,
            "uptime_seconds": self.uptime_seconds,
            "subsystems": dict(self.subsystems),
        }


class RuntimeManager:
    """Manages the FRIDAY AI Runtime lifecycle."""

    def __init__(self):
        self._state = RuntimeState.STOPPED
        self._started_at: Optional[datetime] = None
        self._subsystems: Dict[str, Any] = {}
        self._subsystem_states: Dict[str, RuntimeState] = {}
        self._lock = asyncio.Lock()

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def event_bus(self):
        return self._subsystems.get("event_bus")

    @property
    def scheduler(self):
        return self._subsystems.get("scheduler")

    @property
    def resource_manager(self):
        return self._subsystems.get("resource_manager")

    @property
    def execution_graph(self):
        return self._subsystems.get("execution_graph")

    @property
    def capability_registry(self):
        return self._subsystems.get("capability_registry")

    async def start(self) -> None:
        """Start all runtime subsystems in dependency order."""
        async with self._lock:
            if self._state == RuntimeState.RUNNING:
                return

            self._state = RuntimeState.STARTING
            logger.info("Starting FRIDAY Runtime...")

            startup_order = [
                ("event_bus", self._start_event_bus),
                ("capability_registry", self._start_capability_registry),
                ("resource_manager", self._start_resource_manager),
                ("scheduler", self._start_scheduler),
                ("execution_graph", self._start_execution_graph),
            ]

            for name, starter in startup_order:
                try:
                    await starter()
                    self._subsystem_states[name] = RuntimeState.RUNNING
                    logger.info(f"  ✓ {name} started")
                except Exception as exc:
                    self._subsystem_states[name] = RuntimeState.FAILED
                    logger.error(f"  ✗ {name} failed: {exc}")

            self._state = RuntimeState.RUNNING
            self._started_at = datetime.now(timezone.utc)
            logger.info("FRIDAY Runtime started")

    async def _start_event_bus(self) -> None:
        from core.runtime.event_bus import EventBus
        self._subsystems["event_bus"] = EventBus()

    async def _start_capability_registry(self) -> None:
        from core.runtime.capability_registry import CapabilityRegistry
        self._subsystems["capability_registry"] = CapabilityRegistry()

    async def _start_resource_manager(self) -> None:
        from core.runtime.resource_manager import ResourceManager
        self._subsystems["resource_manager"] = ResourceManager()

    async def _start_scheduler(self) -> None:
        from core.runtime.scheduler import RuntimeScheduler
        self._subsystems["scheduler"] = RuntimeScheduler(event_bus=self.event_bus)

    async def _start_execution_graph(self) -> None:
        from core.runtime.execution_graph import ExecutionGraph
        self._subsystems["execution_graph"] = ExecutionGraph()

    async def stop(self) -> None:
        """Stop all runtime subsystems gracefully."""
        async with self._lock:
            if self._state != RuntimeState.RUNNING:
                return

            self._state = RuntimeState.STOPPING
            logger.info("Stopping FRIDAY Runtime...")

            for name in reversed(["event_bus", "capability_registry", "resource_manager", "scheduler", "execution_graph"]):
                subsystem = self._subsystems.get(name)
                if subsystem and hasattr(subsystem, "stop"):
                    try:
                        await subsystem.stop()
                        self._subsystem_states[name] = RuntimeState.STOPPED
                    except Exception as exc:
                        logger.error(f"  ✗ {name} stop failed: {exc}")

            self._state = RuntimeState.STOPPED
            logger.info("FRIDAY Runtime stopped")

    def get_status(self) -> RuntimeStatus:
        """Get current runtime status."""
        uptime = 0.0
        if self._started_at and self._state == RuntimeState.RUNNING:
            uptime = (datetime.now(timezone.utc) - self._started_at).total_seconds()
        return RuntimeStatus(
            state=self._state,
            started_at=self._started_at.isoformat() if self._started_at else "",
            uptime_seconds=uptime,
            subsystems={k: v.value for k, v in self._subsystem_states.items()},
        )

    async def health_check(self) -> Dict[str, bool]:
        """Check health of all subsystems."""
        results = {}
        for name, subsystem in self._subsystems.items():
            if hasattr(subsystem, "is_healthy"):
                try:
                    results[name] = await subsystem.is_healthy()
                except Exception:
                    results[name] = False
            else:
                results[name] = True
        return results


_runtime: Optional[RuntimeManager] = None


def get_runtime_manager() -> RuntimeManager:
    global _runtime
    if _runtime is None:
        _runtime = RuntimeManager()
    return _runtime
