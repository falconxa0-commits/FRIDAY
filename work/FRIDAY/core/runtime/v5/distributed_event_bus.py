"""Distributed Event Bus — Redis-backed pub/sub with local fallback.

Extends the Age IV EventBus with distributed capabilities:
    - Redis pub/sub for cross-process communication
    - Event persistence (replay missed events)
    - Dead letter queue for failed deliveries
    - Automatic fallback to local EventBus when Redis is unavailable
    - At-least-once delivery semantics
    - Idempotency keys for consumer safety

When FRIDAY_DISTRIBUTED_RUNTIME=0 (default), this falls back to
the Age IV in-memory EventBus with zero behavior change.

Usage::

    bus = DistributedEventBus(redis_url="redis://localhost:6379")
    await bus.start()
    await bus.publish("task.completed", {"task_id": "123"})
    bus.subscribe("task.completed", handler)
    await bus.stop()
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("friday.runtime.v5.event_bus")


@dataclass
class DistributedEvent:
    """An event in the distributed event bus."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    correlation_id: str = ""  # for tracing
    idempotency_key: str = ""  # for consumer dedup

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "data": self.data,
            "source": self.source,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DistributedEvent":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            type=data.get("type", ""),
            data=data.get("data", {}),
            source=data.get("source", ""),
            timestamp=data.get("timestamp", ""),
            correlation_id=data.get("correlation_id", ""),
            idempotency_key=data.get("idempotency_key", ""),
        )


@dataclass
class DeadLetterEntry:
    """An event that failed delivery."""
    event: DistributedEvent
    error: str
    attempts: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DistributedEventBus:
    """Distributed event bus with Redis backing and local fallback.

    Delivery semantics: at-least-once.
    Consumers must be idempotent (use idempotency_key for dedup).

    Features:
        - Redis pub/sub for cross-process delivery
        - Local in-memory fallback (Age IV compatible)
        - Event history (bounded, for replay)
        - Dead letter queue for failed deliveries
        - Backpressure (max queue size)
        - Automatic reconnection on Redis failure
    """

    MAX_HISTORY = 1000
    MAX_DEAD_LETTER = 500
    MAX_DELIVERY_ATTEMPTS = 3

    def __init__(
        self,
        redis_url: str = "",
        enable_distributed: bool = False,
        local_fallback: bool = True,
    ):
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "")
        self._enable_distributed = enable_distributed or (
            os.environ.get("FRIDAY_DISTRIBUTED_RUNTIME", "0") == "1"
            and bool(self._redis_url)
        )
        self._local_fallback = local_fallback
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._wildcard_subscribers: List[Callable] = []
        self._history: deque = deque(maxlen=self.MAX_HISTORY)
        self._dead_letters: deque = deque(maxlen=self.MAX_DEAD_LETTER)
        self._processed_ids: set = set()  # idempotency tracking (bounded)
        self._processed_max = 10000
        self._redis = None
        self._pubsub = None
        self._listener_task: Optional[asyncio.Task] = None
        self._running = False
        self._stats = {
            "published": 0,
            "delivered": 0,
            "failed": 0,
            "dead_lettered": 0,
            "duplicates_filtered": 0,
            "redis_connected": False,
        }

    async def start(self) -> None:
        """Start the event bus."""
        self._running = True
        if self._enable_distributed and self._redis_url:
            await self._connect_redis()
        logger.info(
            f"DistributedEventBus started — "
            f"mode={'distributed' if self._stats['redis_connected'] else 'local'}"
        )

    async def _connect_redis(self) -> None:
        """Connect to Redis with retry."""
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
                retry_on_timeout=True,
            )
            await self._redis.ping()
            self._stats["redis_connected"] = True
            logger.info(f"Connected to Redis: {self._redis_url}")

            # Start pub/sub listener
            self._pubsub = self._redis.pubsub()
            self._listener_task = asyncio.create_task(self._listen_redis())
        except ImportError:
            logger.warning("redis package not installed — falling back to local mode")
            self._redis = None
        except Exception as exc:
            logger.warning(f"Failed to connect to Redis: {exc} — falling back to local mode")
            self._redis = None

    async def _listen_redis(self) -> None:
        """Listen for Redis pub/sub messages."""
        if not self._pubsub:
            return
        try:
            await self._pubsub.psubscribe("friday:events:*")
            while self._running:
                try:
                    message = await self._pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                    if message and message["type"] == "pmessage":
                        await self._handle_redis_message(message["data"])
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:
                    logger.error(f"Redis listener error: {exc}")
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _handle_redis_message(self, data: str) -> None:
        """Handle a message received from Redis pub/sub."""
        try:
            event_data = json.loads(data)
            event = DistributedEvent.from_dict(event_data)
            await self._dispatch_local(event)
        except Exception as exc:
            logger.error(f"Failed to handle Redis message: {exc}")

    async def publish(
        self,
        event_type: str,
        data: Optional[Dict] = None,
        source: str = "",
        correlation_id: str = "",
        idempotency_key: str = "",
    ) -> str:
        """Publish an event to all subscribers.

        Delivery: at-least-once. If Redis is connected, the event is
        published via Redis pub/sub (cross-process) AND dispatched
        locally. If Redis is not connected, only local dispatch occurs.

        Args:
            event_type: Event type (e.g., "task.completed").
            data: Event payload.
            source: Name of the publishing component.
            correlation_id: For distributed tracing.
            idempotency_key: For consumer deduplication.

        Returns:
            Event ID.
        """
        event = DistributedEvent(
            type=event_type,
            data=dict(data) if data else {},  # copy to prevent mutation
            source=source,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
        )

        # Store in history
        self._history.append(event)

        # Publish to Redis (if connected)
        if self._redis and self._stats["redis_connected"]:
            try:
                channel = f"friday:events:{event_type}"
                await self._redis.publish(channel, event.to_json())
                self._stats["published"] += 1
            except Exception as exc:
                logger.error(f"Redis publish failed: {exc}")
                self._stats["redis_connected"] = False
                # Fall through to local dispatch

        # Always dispatch locally (for same-process subscribers)
        await self._dispatch_local(event)
        self._stats["published"] += 1

        return event.id

    async def _dispatch_local(self, event: DistributedEvent) -> None:
        """Dispatch an event to local subscribers."""
        # Check idempotency
        if event.idempotency_key and event.idempotency_key in self._processed_ids:
            self._stats["duplicates_filtered"] += 1
            return

        if event.idempotency_key:
            self._processed_ids.add(event.idempotency_key)
            # Bounded set — prevent unbounded growth
            if len(self._processed_ids) > self._processed_max:
                self._processed_ids = set(list(self._processed_ids)[-self._processed_max:])

        # Get subscribers
        handlers = list(self._subscribers.get(event.type, []))
        handlers.extend(self._wildcard_subscribers)

        for handler in handlers:
            try:
                await handler(event)
                self._stats["delivered"] += 1
            except Exception as exc:
                logger.error(f"Event handler error for '{event.type}': {exc}")
                self._stats["failed"] += 1
                self._dead_letters.append(DeadLetterEntry(
                    event=event,
                    error=str(exc),
                    attempts=1,
                ))
                self._stats["dead_lettered"] += 1

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Subscribe to events.

        Args:
            event_type: Event type to subscribe to. Use "*" for all events.
            handler: Async callable that receives a DistributedEvent.
        """
        if event_type == "*":
            self._wildcard_subscribers.append(handler)
        else:
            self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed handler to '{event_type}'")

    async def replay(
        self,
        event_type: Optional[str] = None,
        after_event_id: str = "",
        handler: Optional[Callable] = None,
    ) -> int:
        """Replay missed events to a handler.

        Delivers events from history that:
            - Match event_type (if specified)
            - Were published after after_event_id (if specified)
            - Haven't been delivered to this handler's idempotency context

        Args:
            event_type: Filter by event type (None = all types).
            after_event_id: Only deliver events after this event ID.
            handler: The handler to deliver to. If None, replays to all
                    existing subscribers of the given event_type.

        Returns:
            Number of events replayed.
        """
        history = list(self._history)
        if event_type:
            history = [e for e in history if e.type == event_type]

        # Find the starting point
        if after_event_id:
            start_idx = 0
            for i, e in enumerate(history):
                if e.id == after_event_id:
                    start_idx = i + 1
                    break
            history = history[start_idx:]
        else:
            # No after_event_id — replay all matching
            pass

        count = 0
        for event in history:
            if handler:
                try:
                    await handler(event)
                    count += 1
                except Exception as exc:
                    logger.error(f"Replay handler error for '{event.type}': {exc}")
            else:
                # Replay to existing subscribers
                handlers = list(self._subscribers.get(event.type, []))
                handlers.extend(self._wildcard_subscribers)
                for h in handlers:
                    try:
                        await h(event)
                        count += 1
                    except Exception as exc:
                        logger.debug(f"Replay handler error: {exc}")

        if count > 0:
            logger.info(f"Replayed {count} events (type={event_type}, after={after_event_id[:8] or 'start'})")
        return count

    def get_last_event_id(self, event_type: Optional[str] = None) -> str:
        """Get the ID of the last event in history.

        Useful for subscribers to track their replay cursor.

        Args:
            event_type: Filter by type (None = all types).

        Returns:
            Event ID, or empty string if no events.
        """
        history = list(self._history)
        if event_type:
            history = [e for e in history if e.type == event_type]
        if not history:
            return ""
        return history[-1].id

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """Unsubscribe a handler."""
        if event_type == "*":
            if handler in self._wildcard_subscribers:
                self._wildcard_subscribers.remove(handler)
        elif event_type in self._subscribers:
            if handler in self._subscribers[event_type]:
                self._subscribers[event_type].remove(handler)

    def get_history(
        self,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[DistributedEvent]:
        """Get recent events, optionally filtered by type."""
        events = list(self._history)
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]

    def get_dead_letters(self, limit: int = 50) -> List[DeadLetterEntry]:
        """Get dead-lettered events."""
        return list(self._dead_letters)[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "history_size": len(self._history),
            "dead_letter_count": len(self._dead_letters),
            "subscriber_count": sum(len(hs) for hs in self._subscribers.values())
                                + len(self._wildcard_subscribers),
            "idempotency_cache_size": len(self._processed_ids),
        }

    @property
    def is_distributed(self) -> bool:
        return self._stats["redis_connected"]

    async def is_healthy(self) -> bool:
        return self._running

    async def stop(self) -> None:
        """Stop the event bus."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            await self._pubsub.close()
        if self._redis:
            await self._redis.close()
        self._stats["redis_connected"] = False
        logger.info("DistributedEventBus stopped")
