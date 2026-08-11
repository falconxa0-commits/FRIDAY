"""State Runtime — manages state machines for long-running processes.

A state machine has a current state and a set of valid transitions
(``from_state → [to_state, ...]``). Attempts to transition into a
state not declared as valid from the current state are rejected.

All transitions are recorded in an immutable history list so callers
can audit how a process arrived at its current state.

Usage::

    sm_rt = StateRuntime()
    sm = await sm_rt.create_state_machine("ingest", initial_state="idle")
    await sm_rt.add_transition_rule(sm.id, "idle", "running")
    await sm_rt.add_transition_rule(sm.id, "running", "done")
    await sm_rt.transition(sm.id, "running")  # → True
    await sm_rt.transition(sm.id, "done")     # → True
    await sm_rt.transition(sm.id, "idle")     # → False (no rule done→idle)
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.state_runtime")


@dataclass
class StateTransition:
    """A single recorded state transition."""
    from_state: str
    to_state: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_state": self.from_state,
            "to_state": self.to_state,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


@dataclass
class StateMachine:
    """A single state machine.

    Attributes:
        id: Unique identifier.
        name: Human-readable name.
        current_state: The current state name.
        valid_transitions: Mapping ``from_state → [to_state, ...]``.
        history: Ordered list of recorded transitions.
        created_at: ISO-8601 creation timestamp.
        updated_at: ISO-8601 last-transition timestamp.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    current_state: str = ""
    valid_transitions: Dict[str, List[str]] = field(default_factory=dict)
    history: List[StateTransition] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "current_state": self.current_state,
            "valid_transitions": {k: list(v) for k, v in self.valid_transitions.items()},
            "history": [t.to_dict() for t in self.history],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class StateRuntime:
    """Manages state machines for runtime processes."""

    def __init__(self, event_bus: Any = None):
        self._machines: Dict[str, StateMachine] = {}
        self._event_bus = event_bus
        self._running = True

    async def create_state_machine(
        self, name: str, initial_state: str
    ) -> StateMachine:
        """Create a new state machine.

        Args:
            name: Human-readable name.
            initial_state: The starting state.

        Returns:
            The created StateMachine.
        """
        sm = StateMachine(name=name, current_state=initial_state)
        self._machines[sm.id] = sm
        logger.debug(
            f"Created state machine '{name}' ({sm.id[:8]}) @ state '{initial_state}'"
        )

        if self._event_bus:
            await self._event_bus.publish(
                "state_machine.created",
                {"machine_id": sm.id, "name": name, "initial_state": initial_state},
                source="state_runtime",
            )
        return sm

    async def transition(
        self, machine_id: str, new_state: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Transition a state machine to a new state.

        Args:
            machine_id: ID of the state machine.
            new_state: Target state.
            metadata: Optional metadata to attach to the history entry.

        Returns:
            True if the transition was allowed and applied; False if
            the machine is unknown, the new state equals the current
            state, or no transition rule permits the move.
        """
        sm = self._machines.get(machine_id)
        if sm is None:
            return False

        if new_state == sm.current_state:
            # No-op: identical state. Recorded for auditability.
            sm.history.append(
                StateTransition(
                    from_state=sm.current_state,
                    to_state=new_state,
                    metadata={"noop": True, **(metadata or {})},
                )
            )
            sm.updated_at = datetime.now(timezone.utc).isoformat()
            return True

        allowed = sm.valid_transitions.get(sm.current_state, [])
        if new_state not in allowed:
            logger.warning(
                f"Invalid transition '{sm.current_state}' → '{new_state}' "
                f"for machine {machine_id[:8]}"
            )
            if self._event_bus:
                await self._event_bus.publish(
                    "state_machine.transition_rejected",
                    {
                        "machine_id": machine_id,
                        "from_state": sm.current_state,
                        "to_state": new_state,
                    },
                    source="state_runtime",
                )
            return False

        old_state = sm.current_state
        sm.history.append(
            StateTransition(
                from_state=old_state,
                to_state=new_state,
                metadata=metadata or {},
            )
        )
        sm.current_state = new_state
        sm.updated_at = datetime.now(timezone.utc).isoformat()

        if self._event_bus:
            await self._event_bus.publish(
                "state_machine.transition",
                {
                    "machine_id": machine_id,
                    "from_state": old_state,
                    "to_state": new_state,
                },
                source="state_runtime",
            )
        logger.debug(
            f"Machine {machine_id[:8]} transitioned '{old_state}' → '{new_state}'"
        )
        return True

    async def get_state(self, machine_id: str) -> Optional[str]:
        """Get the current state of a machine, or None if unknown."""
        sm = self._machines.get(machine_id)
        return sm.current_state if sm is not None else None

    async def add_transition_rule(
        self, machine_id: str, from_state: str, to_state: str
    ) -> bool:
        """Declare a transition ``from_state → to_state`` as valid.

        Returns:
            True if the rule was added (or already present); False if
            the machine is unknown.
        """
        sm = self._machines.get(machine_id)
        if sm is None:
            return False

        targets = sm.valid_transitions.setdefault(from_state, [])
        if to_state not in targets:
            targets.append(to_state)
        return True

    async def get_history(self, machine_id: str) -> List[StateTransition]:
        """Get the transition history for a machine.

        Returns:
            A list of StateTransition entries. Empty list if the
            machine is unknown.
        """
        sm = self._machines.get(machine_id)
        if sm is None:
            return []
        return list(sm.history)

    async def get_machine(self, machine_id: str) -> Optional[StateMachine]:
        """Get a state machine by ID."""
        return self._machines.get(machine_id)

    async def is_healthy(self) -> bool:
        """Check if the state runtime is healthy."""
        return self._running

    async def stop(self) -> None:
        """Stop the state runtime."""
        self._running = False
        self._machines.clear()
        logger.info("State runtime stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get state runtime statistics."""
        return {
            "total_machines": len(self._machines),
            "total_transitions_recorded": sum(
                len(m.history) for m in self._machines.values()
            ),
            "total_rules": sum(
                sum(len(v) for v in m.valid_transitions.values())
                for m in self._machines.values()
            ),
        }
