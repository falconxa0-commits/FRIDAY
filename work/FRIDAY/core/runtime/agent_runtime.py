"""Agent Runtime — lifecycle, supervision, and recovery for AI agents.

Manages a registry of AI agents (any object exposing an ``execute(task)``
coroutine) and provides:

    - Registration with per-agent concurrency limits
    - Execution with timing and result capture
    - Failure counting and automatic circuit-breaking after
      :data:`AgentRuntime.MAX_CONSECUTIVE_FAILURES` (default 3) failures
    - Manual recovery via :meth:`AgentRuntime.recover_agent`
    - Introspection (status, list, stats)

Agents are *not* sandboxed — they run in the same interpreter. The
runtime's job is supervision: tracking execution, surfacing failures,
and preventing runaway concurrency.

Usage::

    rt = AgentRuntime()
    await rt.register_agent("coder", CodingAgent(), max_concurrent=2)
    result = await rt.execute_agent("coder", {"prompt": "hello"})
    if result.status == "failed":
        ...
    await rt.stop()
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.agent_runtime")


# ---------------------------------------------------------------------------
# Enums + dataclasses
# ---------------------------------------------------------------------------
class AgentStatus(str, Enum):
    """Lifecycle state of a single agent."""

    IDLE = "idle"
    BUSY = "busy"
    FAILED = "failed"


@dataclass
class AgentHandle:
    """A registered agent and its supervision metadata."""

    name: str
    agent: Any
    max_concurrent: int = 1
    current_concurrent: int = 0
    execution_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    last_executed: str = ""
    status: AgentStatus = AgentStatus.IDLE
    registered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "agent_type": type(self.agent).__name__,
            "max_concurrent": self.max_concurrent,
            "current_concurrent": self.current_concurrent,
            "execution_count": self.execution_count,
            "failure_count": self.failure_count,
            "consecutive_failures": self.consecutive_failures,
            "status": self.status.value,
            "last_executed": self.last_executed,
            "registered_at": self.registered_at,
        }


@dataclass
class AgentResult:
    """The outcome of a single ``execute_agent`` call."""

    status: str  # "success" or "failed"
    result: Any = None
    error: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "duration_seconds": round(self.duration_seconds, 6),
        }


# ---------------------------------------------------------------------------
# AgentRuntime
# ---------------------------------------------------------------------------
class AgentRuntime:
    """Manages AI agent lifecycle and execution.

    Each agent is identified by a unique name. The runtime tracks
    concurrent executions per agent and refuses new ones once
    ``max_concurrent`` is reached.

    When an agent raises during execution, ``failure_count`` and
    ``consecutive_failures`` are both incremented. After
    :data:`MAX_CONSECUTIVE_FAILURES` consecutive failures, the agent's
    status becomes :attr:`AgentStatus.FAILED`. A failed agent refuses
    further executions until :meth:`recover_agent` is called. A
    successful execution resets ``consecutive_failures`` to 0.
    """

    #: Number of consecutive failures before an agent is marked FAILED.
    MAX_CONSECUTIVE_FAILURES: int = 3

    def __init__(self) -> None:
        self._agents: Dict[str, AgentHandle] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._running: bool = True
        self._total_executions: int = 0
        self._total_successes: int = 0
        self._total_failures: int = 0
        self._total_recoveries: int = 0

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    async def register_agent(
        self,
        name: str,
        agent: Any,
        max_concurrent: int = 1,
    ) -> AgentHandle:
        """Register an agent.

        Args:
            name: Unique agent identifier.
            agent: Any object with an ``async def execute(self, task)``
                method (a sync ``execute`` is also accepted).
            max_concurrent: Maximum number of concurrent executions
                allowed for this agent. Must be >= 1.

        Returns:
            The :class:`AgentHandle` for the registered agent.

        Raises:
            ValueError: If ``name`` is already registered or
                ``max_concurrent`` < 1.
        """
        if not name or not isinstance(name, str):
            raise ValueError("Agent name must be a non-empty string")
        if agent is None:
            raise ValueError("Agent cannot be None")
        if max_concurrent < 1:
            raise ValueError(
                f"max_concurrent must be >= 1, got {max_concurrent}"
            )

        async with self._lock:
            if name in self._agents:
                raise ValueError(f"Agent already registered: {name}")
            handle = AgentHandle(
                name=name,
                agent=agent,
                max_concurrent=max_concurrent,
            )
            self._agents[name] = handle
            logger.info(
                "Registered agent '%s' (%s) max_concurrent=%d",
                name,
                type(agent).__name__,
                max_concurrent,
            )
            return handle

    async def unregister_agent(self, name: str) -> bool:
        """Unregister an agent.

        Returns:
            True if the agent was registered and is now removed;
            False otherwise.
        """
        async with self._lock:
            if name in self._agents:
                del self._agents[name]
                logger.info("Unregistered agent '%s'", name)
                return True
            return False

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    async def execute_agent(self, name: str, task: Dict) -> AgentResult:
        """Execute ``task`` on agent ``name``.

        Args:
            name: Registered agent name.
            task: A dict describing the task (passed to ``agent.execute``).

        Returns:
            An :class:`AgentResult` describing the outcome.

        Raises:
            KeyError: If the agent is not registered.
            RuntimeError: If the agent is in FAILED state (call
                :meth:`recover_agent` first) or if ``max_concurrent``
                has been reached.
        """
        # Phase 1: validate + reserve a concurrency slot (under lock)
        agent_obj = await self._reserve_slot(name)

        # Phase 2: execute outside the lock so concurrent executions
        # of the same (or other) agents don't serialize.
        start = time.monotonic()
        succeeded = False
        result_value: Any = None
        error_msg = ""
        try:
            execute_fn = getattr(agent_obj, "execute", None)
            if execute_fn is None or not callable(execute_fn):
                raise AttributeError(
                    f"Agent '{name}' must have a callable 'execute' method"
                )
            if asyncio.iscoroutinefunction(execute_fn):
                result_value = await execute_fn(task)
            else:
                result_value = execute_fn(task)
            succeeded = True
        except Exception as exc:  # noqa: BLE001 — surface as AgentResult
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Agent '%s' execution failed: %s", name, error_msg
            )
        finally:
            duration = time.monotonic() - start
            # Phase 3: release slot + update supervision state
            await self._release_slot(name, succeeded, duration)

        return AgentResult(
            status="success" if succeeded else "failed",
            result=result_value if succeeded else None,
            error=error_msg,
            duration_seconds=duration,
        )

    async def _reserve_slot(self, name: str) -> Any:
        """Validate the agent is callable and reserve a concurrency slot."""
        async with self._lock:
            handle = self._agents.get(name)
            if handle is None:
                raise KeyError(f"Agent not found: {name}")
            if handle.status == AgentStatus.FAILED:
                raise RuntimeError(
                    f"Agent '{name}' is in FAILED state; "
                    f"call recover_agent('{name}') before executing"
                )
            if handle.current_concurrent >= handle.max_concurrent:
                raise RuntimeError(
                    f"Agent '{name}' is at max concurrency "
                    f"({handle.max_concurrent}); "
                    f"currently running {handle.current_concurrent}"
                )
            handle.current_concurrent += 1
            if handle.status == AgentStatus.IDLE:
                handle.status = AgentStatus.BUSY
            return handle.agent

    async def _release_slot(
        self, name: str, succeeded: bool, duration: float
    ) -> None:
        """Release the concurrency slot and update failure/success state."""
        async with self._lock:
            handle = self._agents.get(name)
            if handle is None:
                # Agent was unregistered mid-execution; nothing to update.
                self._total_executions += 1
                if succeeded:
                    self._total_successes += 1
                else:
                    self._total_failures += 1
                return

            handle.current_concurrent = max(
                0, handle.current_concurrent - 1
            )
            handle.execution_count += 1
            handle.last_executed = datetime.now(timezone.utc).isoformat()
            self._total_executions += 1

            if succeeded:
                handle.consecutive_failures = 0
                self._total_successes += 1
                if (
                    handle.current_concurrent == 0
                    and handle.status == AgentStatus.BUSY
                ):
                    handle.status = AgentStatus.IDLE
            else:
                handle.failure_count += 1
                handle.consecutive_failures += 1
                self._total_failures += 1
                if (
                    handle.consecutive_failures
                    >= self.MAX_CONSECUTIVE_FAILURES
                ):
                    handle.status = AgentStatus.FAILED
                    logger.error(
                        "Agent '%s' marked FAILED after %d consecutive failures",
                        name,
                        handle.consecutive_failures,
                    )
                elif (
                    handle.current_concurrent == 0
                    and handle.status == AgentStatus.BUSY
                ):
                    handle.status = AgentStatus.IDLE

    # ------------------------------------------------------------------
    # Supervision
    # ------------------------------------------------------------------
    async def recover_agent(self, name: str) -> bool:
        """Recover a FAILED agent back to IDLE.

        Resets ``consecutive_failures`` to 0. Does NOT clear cumulative
        ``failure_count``.

        Returns:
            True if the agent was FAILED and is now IDLE;
            False if the agent is not registered or was not in FAILED state.
        """
        async with self._lock:
            handle = self._agents.get(name)
            if handle is None:
                return False
            if handle.status != AgentStatus.FAILED:
                return False
            handle.status = AgentStatus.IDLE
            handle.consecutive_failures = 0
            self._total_recoveries += 1
            logger.info("Agent '%s' recovered to IDLE", name)
            return True

    async def get_agent_status(self, name: str) -> AgentStatus:
        """Return the current status of agent ``name``.

        Raises:
            KeyError: If the agent is not registered.
        """
        async with self._lock:
            handle = self._agents.get(name)
            if handle is None:
                raise KeyError(f"Agent not found: {name}")
            return handle.status

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    async def list_agents(self) -> List[AgentHandle]:
        """Return all registered agent handles (snapshot)."""
        async with self._lock:
            return list(self._agents.values())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def is_healthy(self) -> bool:
        """Return True if the runtime is running and no agent is FAILED."""
        if not self._running:
            return False
        async with self._lock:
            return not any(
                h.status == AgentStatus.FAILED for h in self._agents.values()
            )

    async def stop(self) -> None:
        """Stop the runtime and clear all registered agents."""
        async with self._lock:
            self._running = False
            count = len(self._agents)
            self._agents.clear()
            logger.info(
                "Agent runtime stopped (cleared %d agent(s))", count
            )

    def get_stats(self) -> Dict[str, Any]:
        """Return a summary of runtime state and per-agent stats."""
        return {
            "running": self._running,
            "max_consecutive_failures": self.MAX_CONSECUTIVE_FAILURES,
            "total_agents": len(self._agents),
            "total_executions": self._total_executions,
            "total_successes": self._total_successes,
            "total_failures": self._total_failures,
            "total_recoveries": self._total_recoveries,
            "agents": [h.to_dict() for h in self._agents.values()],
        }
