"""Lifecycle Manager — tracks runtime component lifecycle states.

The lifecycle manager supervises runtime components through a standard
state machine:

    UNINITIALIZED → INITIALIZED → STARTING → RUNNING
                                              ↓
                                          DEGRADED
                                              ↓
                                          STOPPING → STOPPED

``FAILED`` is a terminal-ish state reachable from *any* other state when
a component raises during a transition. ``restart`` is a convenience that
runs ``stop`` followed by ``start`` on the same component handle.

Components are duck-typed: any object that has ``start`` / ``stop`` /
``is_healthy`` coroutines (or methods) can be registered. Missing hooks
are tolerated — the manager just won't call them.

Usage::

    lm = LifecycleManager()
    handle = await lm.register_component("event_bus", EventBus())
    await lm.start_component("event_bus")          # → RUNNING
    await lm.health_check_all()                    # → {"event_bus": True}
    await lm.restart_component("event_bus")        # stop, then start again
    await lm.stop()                                # stop ALL components
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.lifecycle_manager")


class ComponentState(str, Enum):
    """Lifecycle states a component can be in."""

    UNINITIALIZED = "uninitialized"
    INITIALIZED = "initialized"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


# Valid forward transitions. FAILED is reachable from any state and is
# handled separately in `_transition` so it doesn't appear here.
_VALID_TRANSITIONS: Dict[ComponentState, List[ComponentState]] = {
    ComponentState.UNINITIALIZED: [ComponentState.INITIALIZED],
    ComponentState.INITIALIZED: [ComponentState.STARTING, ComponentState.STOPPED],
    ComponentState.STARTING: [ComponentState.RUNNING, ComponentState.FAILED],
    ComponentState.RUNNING: [
        ComponentState.DEGRADED,
        ComponentState.STOPPING,
        ComponentState.FAILED,
    ],
    ComponentState.DEGRADED: [
        ComponentState.RUNNING,
        ComponentState.STOPPING,
        ComponentState.FAILED,
    ],
    ComponentState.STOPPING: [ComponentState.STOPPED, ComponentState.FAILED],
    ComponentState.STOPPED: [ComponentState.STARTING],  # restart re-enters STARTING
    ComponentState.FAILED: [ComponentState.STARTING, ComponentState.STOPPED],
}


@dataclass
class ComponentHandle:
    """A registered component + its lifecycle metadata.

    Attributes:
        name: Human-readable identifier.
        component: The wrapped object (must expose optional
            ``start``/``stop``/``is_healthy`` hooks).
        state: Current :class:`ComponentState`.
        started_at: ISO-8601 of last successful start, or ``""``.
        stopped_at: ISO-8601 of last successful stop, or ``""``.
        restart_count: Number of successful restarts.
        failure_count: Number of recorded failures.
        last_error: Most recent error message, or ``""``.
        created_at: ISO-8601 registration timestamp.
    """

    name: str
    component: Any
    state: ComponentState = ComponentState.UNINITIALIZED
    started_at: str = ""
    stopped_at: str = ""
    restart_count: int = 0
    failure_count: int = 0
    last_error: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "restart_count": self.restart_count,
            "failure_count": self.failure_count,
            "last_error": self.last_error,
            "created_at": self.created_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _maybe_call(component: Any, method_name: str) -> bool:
    """Call ``component.method_name`` if it exists.

    Tolerates both coroutine and plain function hooks. Returns True if
    the call succeeded (or the method didn't exist — absent hook is not
    a failure), False if it raised.
    """
    fn = getattr(component, method_name, None)
    if fn is None:
        return True  # not implemented — tolerated
    try:
        result = fn()
        if asyncio.iscoroutine(result):
            await result
        return True
    except Exception as exc:
        logger.warning(f"Component {component!r}.{method_name} raised: {exc}")
        return False


class LifecycleManager:
    """Manages component lifecycle states and transitions."""

    def __init__(self) -> None:
        self._components: Dict[str, ComponentHandle] = {}
        self._lock = asyncio.Lock()
        self._total_starts: int = 0
        self._total_stops: int = 0
        self._total_failures: int = 0
        self._total_restarts: int = 0
        self._running: bool = True

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    async def register_component(
        self, name: str, component: Any
    ) -> ComponentHandle:
        """Register a new component.

        Raises ``ValueError`` if ``name`` is already registered.
        """
        async with self._lock:
            if name in self._components:
                raise ValueError(f"Component '{name}' is already registered")
            handle = ComponentHandle(
                name=name,
                component=component,
                state=ComponentState.INITIALIZED,  # registered == initialized
            )
            self._components[name] = handle
            logger.info(f"Registered component '{name}'")
            return handle

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def _transition(
        self, handle: ComponentHandle, target: ComponentState
    ) -> bool:
        """Validate and apply a state transition (no side effects)."""
        current = handle.state
        if target == ComponentState.FAILED:
            # FAILED is reachable from anywhere
            handle.state = ComponentState.FAILED
            handle.failure_count += 1
            self._total_failures += 1
            return True
        allowed = _VALID_TRANSITIONS.get(current, [])
        if target not in allowed:
            logger.warning(
                f"Invalid transition for '{handle.name}': "
                f"{current.value} → {target.value}"
            )
            return False
        handle.state = target
        return True

    async def start_component(self, name: str) -> bool:
        """Move a component through STARTING → RUNNING.

        Returns False if the component is unknown, already running, or
        its ``start`` hook raised (in which case it transitions to
        FAILED).
        """
        async with self._lock:
            handle = self._components.get(name)
            if handle is None:
                logger.warning(f"start_component: unknown '{name}'")
                return False

            if handle.state in (
                ComponentState.RUNNING,
                ComponentState.STARTING,
            ):
                return handle.state == ComponentState.RUNNING

            # Move to STARTING
            if not self._transition(handle, ComponentState.STARTING):
                return False

            ok = await _maybe_call(handle.component, "start")
            if not ok:
                handle.last_error = "start hook raised"
                self._transition(handle, ComponentState.FAILED)
                return False

            handle.state = ComponentState.RUNNING
            handle.started_at = _now_iso()
            handle.last_error = ""
            self._total_starts += 1
            logger.info(f"Component '{name}' started")
            return True

    async def stop_component(self, name: str) -> bool:
        """Move a component through STOPPING → STOPPED.

        Returns False if the component is unknown. Returns True if the
        component was already stopped. If the ``stop`` hook raises, the
        component is still marked STOPPED (we can't keep running it),
        but ``failure_count`` is incremented.
        """
        async with self._lock:
            handle = self._components.get(name)
            if handle is None:
                logger.warning(f"stop_component: unknown '{name}'")
                return False

            if handle.state == ComponentState.STOPPED:
                return True

            if not self._transition(handle, ComponentState.STOPPING):
                return False

            ok = await _maybe_call(handle.component, "stop")
            if not ok:
                handle.last_error = "stop hook raised"
                handle.failure_count += 1
                self._total_failures += 1

            handle.state = ComponentState.STOPPED
            handle.stopped_at = _now_iso()
            self._total_stops += 1
            logger.info(f"Component '{name}' stopped")
            return True

    async def restart_component(self, name: str) -> bool:
        """Stop then start a component. Bumps ``restart_count`` on success."""
        async with self._lock:
            handle = self._components.get(name)
            if handle is None:
                logger.warning(f"restart_component: unknown '{name}'")
                return False

        # Release the lock for the stop/start calls (they take it again).
        stopped = await self.stop_component(name)
        if not stopped:
            return False
        started = await self.start_component(name)
        if started:
            async with self._lock:
                handle = self._components.get(name)
                if handle is not None:
                    handle.restart_count += 1
                    self._total_restarts += 1
            logger.info(f"Component '{name}' restarted")
        return started

    async def mark_degraded(self, name: str) -> bool:
        """Mark a running component as degraded."""
        async with self._lock:
            handle = self._components.get(name)
            if handle is None:
                return False
            return self._transition(handle, ComponentState.DEGRADED)

    async def mark_failed(self, name: str, error: str = "") -> bool:
        """Force a component into FAILED state from any state."""
        async with self._lock:
            handle = self._components.get(name)
            if handle is None:
                return False
            handle.last_error = error
            return self._transition(handle, ComponentState.FAILED)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    async def get_component_state(self, name: str) -> Optional[ComponentState]:
        """Return the current state of a component, or None if unknown.

        The signature in the spec returns ``ComponentState``; we widen to
        ``Optional[ComponentState]`` so unknown names don't raise. Tests
        can assert ``is not None`` when they expect a registered name.
        """
        handle = self._components.get(name)
        return handle.state if handle else None

    async def get_all_states(self) -> Dict[str, ComponentState]:
        return {name: h.state for name, h in self._components.items()}

    async def health_check_all(self) -> Dict[str, bool]:
        """Per-component health. Components without ``is_healthy`` are
        considered healthy iff their state is RUNNING or DEGRADED."""
        results: Dict[str, bool] = {}
        # Snapshot names so we don't hold the lock during await.
        for name, handle in list(self._components.items()):
            if handle.state == ComponentState.RUNNING:
                fn = getattr(handle.component, "is_healthy", None)
                if fn is None:
                    results[name] = True
                    continue
                try:
                    result = fn()
                    if asyncio.iscoroutine(result):
                        result = await result
                    results[name] = bool(result)
                except Exception:
                    results[name] = False
            elif handle.state == ComponentState.DEGRADED:
                # Degraded is "alive but unwell"
                results[name] = False
            else:
                results[name] = False
        return results

    async def is_healthy(self) -> bool:
        """True iff every registered component reports healthy."""
        if not self._components:
            return True
        results = await self.health_check_all()
        return all(results.values())

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    async def stop(self) -> None:
        """Stop all registered components (best-effort)."""
        async with self._lock:
            # Stop in reverse-registration order.
            names = list(reversed(self._components.keys()))
        for name in names:
            try:
                await self.stop_component(name)
            except Exception as exc:
                logger.error(f"stop() failed for '{name}': {exc}")
        self._running = False
        logger.info("Lifecycle manager stopped all components")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        """Synchronous snapshot of lifecycle stats."""
        state_counts: Dict[str, int] = {}
        for handle in self._components.values():
            state_counts[handle.state.value] = (
                state_counts.get(handle.state.value, 0) + 1
            )
        return {
            "total_components": len(self._components),
            "state_counts": state_counts,
            "total_starts": self._total_starts,
            "total_stops": self._total_stops,
            "total_restarts": self._total_restarts,
            "total_failures": self._total_failures,
            "components": [h.to_dict() for h in self._components.values()],
        }
