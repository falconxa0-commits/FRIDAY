"""Event Bus — pub/sub messaging between runtime subsystems.

Provides decoupled communication between components. Any subsystem
can publish events; any subsystem can subscribe to event types.

Usage::

    bus = EventBus()
    bus.subscribe("task.completed", my_handler)
    await bus.publish("task.completed", {"task_id": "123", "status": "done"})
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("friday.runtime.event_bus")


@dataclass
class Event:
    """A single event in the event bus."""
    type: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
            "source": self.source,
        }


class EventBus:
    """In-memory pub/sub event bus.

    Events are delivered to all subscribers of the event type.
    Subscribers are async callables that receive the Event.
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._history: List[Event] = []
        self._history_limit = 1000
        self._lock = asyncio.Lock()
        self._running = True

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Subscribe to events of a given type.

        Args:
            event_type: Event type to subscribe to (e.g., "task.completed").
                       Use "*" to subscribe to all events.
            handler: Async callable that receives an Event.
        """
        self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed handler to '{event_type}'")

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """Unsubscribe a handler from an event type."""
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    async def publish(self, event_type: str, data: Optional[Dict] = None, source: str = "") -> None:
        """Publish an event to all subscribers.

        Args:
            event_type: Event type (e.g., "task.completed").
            data: Event payload.
            source: Name of the publishing subsystem.
        """
        event = Event(type=event_type, data=data or {}, source=source)

        # Store in history
        async with self._lock:
            self._history.append(event)
            if len(self._history) > self._history_limit:
                self._history = self._history[-self._history_limit:]

        # Notify subscribers (both specific and wildcard)
        handlers = self._subscribers.get(event_type, []) + self._subscribers.get("*", [])

        for handler in handlers:
            try:
                await handler(event)
            except Exception as exc:
                logger.error(f"Event handler error for '{event_type}': {exc}")

    def get_history(self, event_type: Optional[str] = None, limit: int = 100) -> List[Event]:
        """Get recent events, optionally filtered by type."""
        events = self._history
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]

    async def is_healthy(self) -> bool:
        """Check if the event bus is healthy."""
        return self._running

    async def stop(self) -> None:
        """Stop the event bus."""
        self._running = False
        logger.info("Event bus stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get event bus statistics."""
        return {
            "total_events": len(self._history),
            "subscriber_count": sum(len(hs) for hs in self._subscribers.values()),
            "event_types": list(self._subscribers.keys()),
        }
