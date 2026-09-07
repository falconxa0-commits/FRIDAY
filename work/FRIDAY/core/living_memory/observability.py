"""Observability — metrics, correlation IDs, lifecycle events for memory.

Composes with DistributedEventBus (if available). When no event bus is
configured, observability still works locally (in-memory metrics + logs).

Metrics tracked:
    - memory.writes (counter, by type)
    - memory.reads (counter, by type)
    - memory.reinforcements (counter)
    - memory.consolidations (counter)
    - memory.decays (counter)
    - memory.forgets (counter)
    - memory.quarantines (counter)
    - memory.associations (counter)
    - memory.contradictions_detected (counter)
    - memory.contradictions_resolved (counter)
    - memory.rejections (counter, by reason)
    - memory.recovery_quarantined (counter)
    - active_count (gauge, by type + state)

Each emit publishes a "memory.lifecycle" event on the bus with the
correlation_id for tracing.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .base import now_utc

logger = logging.getLogger("friday.living_memory.observability")


@dataclass
class MemoryEvent:
    """A lifecycle event emitted by the living memory system."""
    type: str                              # "memory.created" | "memory.reinforced" | ...
    memory_id: str = ""
    memory_type: str = ""
    actor_id: str = ""
    tenant_id: str = ""
    timestamp: str = field(default_factory=now_utc)
    correlation_id: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "memory_id": self.memory_id,
            "memory_type": self.memory_type,
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "data": dict(self.data),
        }


class MemoryMetrics:
    """In-memory metrics counters. Simple but bounded."""

    def __init__(self):
        self._counters: Dict[str, int] = defaultdict(int)
        self._gauges: Dict[str, int] = defaultdict(int)
        self._histograms: Dict[str, list] = defaultdict(list)
        self._events: list = []  # bounded to last N events
        self._max_events = 5000

    def inc(self, name: str, value: int = 1, **labels) -> None:
        key = self._key(name, **labels)
        self._counters[key] += value

    def dec(self, name: str, value: int = 1, **labels) -> None:
        key = self._key(name, **labels)
        self._counters[key] -= value

    def set_gauge(self, name: str, value: int, **labels) -> None:
        key = self._key(name, **labels)
        self._gauges[key] = value

    def inc_gauge(self, name: str, value: int = 1, **labels) -> None:
        key = self._key(name, **labels)
        self._gauges[key] += value

    def dec_gauge(self, name: str, value: int = 1, **labels) -> None:
        key = self._key(name, **labels)
        self._gauges[key] -= value

    def observe(self, name: str, value: float, **labels) -> None:
        key = self._key(name, **labels)
        self._histograms[key].append(value)
        # Keep last 1000 samples per histogram
        if len(self._histograms[key]) > 1000:
            self._histograms[key] = self._histograms[key][-1000:]

    def record_event(self, event: MemoryEvent) -> None:
        self._events.append(event)
        if len(self._events) > self._max_events:
            # Drop oldest 10%
            self._events = self._events[-int(self._max_events * 0.9):]

    def get_counters(self) -> Dict[str, int]:
        return dict(self._counters)

    def get_gauges(self) -> Dict[str, int]:
        return dict(self._gauges)

    def get_histogram_stats(self, name: str, **labels) -> Dict[str, float]:
        key = self._key(name, **labels)
        samples = self._histograms.get(key, [])
        if not samples:
            return {"count": 0, "min": 0, "max": 0, "mean": 0, "p50": 0, "p95": 0, "p99": 0}
        sorted_s = sorted(samples)
        n = len(sorted_s)
        return {
            "count": n,
            "min": sorted_s[0],
            "max": sorted_s[-1],
            "mean": sum(sorted_s) / n,
            "p50": sorted_s[n // 2],
            "p95": sorted_s[int(n * 0.95)] if n >= 20 else sorted_s[-1],
            "p99": sorted_s[int(n * 0.99)] if n >= 100 else sorted_s[-1],
        }

    def get_recent_events(self, limit: int = 100) -> list:
        return list(self._events[-limit:])

    def snapshot(self) -> Dict[str, Any]:
        return {
            "counters": self.get_counters(),
            "gauges": self.get_gauges(),
            "histograms": {
                k: self.get_histogram_stats(k.split("|")[0])
                for k in self._histograms
            },
            "recent_events": [e.to_dict() for e in self.get_recent_events(20)],
        }

    @staticmethod
    def _key(name: str, **labels) -> str:
        if not labels:
            return name
        parts = [f"{k}={v}" for k, v in sorted(labels.items())]
        return f"{name}|{'|'.join(parts)}"


class ObservabilityHub:
    """Observability hub for living memory.

    Wraps metrics + event emission. If an event_bus is provided (DistributedEventBus),
    events are published to it; otherwise they are kept locally for inspection.
    """

    def __init__(self, event_bus=None, metrics: Optional[MemoryMetrics] = None):
        self._event_bus = event_bus
        self.metrics = metrics or MemoryMetrics()

    async def emit(self, event: MemoryEvent) -> None:
        """Record + publish an event."""
        self.metrics.record_event(event)
        # Update gauge for active count by type+state
        # (Manager is responsible for set_gauge on size changes)
        if self._event_bus is not None:
            try:
                # DistributedEventBus.publish(type, data, source=, correlation_id=)
                await self._event_bus.publish(
                    event.type,
                    event.to_dict(),
                    source="living_memory",
                    correlation_id=event.correlation_id,
                )
            except Exception as e:
                # Don't let bus failures break memory ops
                logger.warning("Event bus publish failed: %s", e)

    def snapshot(self) -> Dict[str, Any]:
        return self.metrics.snapshot()


__all__ = ["MemoryEvent", "MemoryMetrics", "ObservabilityHub"]
