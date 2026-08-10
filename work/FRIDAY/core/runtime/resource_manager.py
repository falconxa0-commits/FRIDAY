"""Resource Manager — tracks and limits resource usage.

Monitors:
    - Memory usage (RSS)
    - CPU usage
    - Token consumption (LLM)
    - Concurrent requests
    - File descriptors

Enforces configurable limits and generates alerts when resources
approach their limits.

Usage::

    rm = ResourceManager()
    rm.set_limit("memory_mb", 512)
    rm.set_limit("concurrent_requests", 100)
    usage = rm.get_usage()
    # usage → {"memory_mb": 234, "concurrent_requests": 5}
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.resource_manager")

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


@dataclass
class ResourceUsage:
    """Current resource usage snapshot."""
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    concurrent_requests: int = 0
    total_tokens_consumed: int = 0
    file_descriptors: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_mb": round(self.memory_mb, 1),
            "cpu_percent": round(self.cpu_percent, 1),
            "concurrent_requests": self.concurrent_requests,
            "total_tokens_consumed": self.total_tokens_consumed,
            "file_descriptors": self.file_descriptors,
            "timestamp": self.timestamp,
        }


@dataclass
class ResourceLimit:
    """A configured resource limit."""
    name: str
    limit: float
    current: float = 0.0
    unit: str = ""

    @property
    def usage_pct(self) -> float:
        if self.limit == 0:
            return 0.0
        return (self.current / self.limit) * 100

    @property
    def exceeded(self) -> bool:
        return self.current > self.limit

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "limit": self.limit,
            "current": self.current,
            "usage_pct": round(self.usage_pct, 1),
            "exceeded": self.exceeded,
            "unit": self.unit,
        }


class ResourceManager:
    """Tracks and limits resource usage.

    Provides real-time visibility into resource consumption and
    enforces configurable limits.
    """

    DEFAULT_LIMITS = {
        "memory_mb": 1024,       # 1 GB
        "concurrent_requests": 100,
        "total_tokens_per_day": 1_000_000,
    }

    def __init__(self):
        self._limits: Dict[str, ResourceLimit] = {}
        self._counters: Dict[str, float] = {}
        self._running = True

        # Initialize with defaults
        for name, limit in self.DEFAULT_LIMITS.items():
            self._limits[name] = ResourceLimit(
                name=name, limit=limit, unit=self._get_unit(name)
            )

    def _get_unit(self, name: str) -> str:
        return {
            "memory_mb": "MB",
            "concurrent_requests": "requests",
            "total_tokens_per_day": "tokens",
        }.get(name, "")

    def set_limit(self, name: str, limit: float) -> None:
        """Set a resource limit."""
        if name in self._limits:
            self._limits[name].limit = limit
        else:
            self._limits[name] = ResourceLimit(
                name=name, limit=limit, unit=self._get_unit(name)
            )
        logger.info(f"Resource limit set: {name}={limit}")

    def increment_counter(self, name: str, amount: float = 1) -> None:
        """Increment a resource counter."""
        self._counters[name] = self._counters.get(name, 0) + amount
        if name in self._limits:
            self._limits[name].current = self._counters[name]

    def decrement_counter(self, name: str, amount: float = 1) -> None:
        """Decrement a resource counter."""
        self._counters[name] = max(0, self._counters.get(name, 0) - amount)
        if name in self._limits:
            self._limits[name].current = self._counters[name]

    def get_usage(self) -> ResourceUsage:
        """Get current resource usage."""
        usage = ResourceUsage()

        if _PSUTIL_AVAILABLE:
            try:
                process = psutil.Process()
                mem_info = process.memory_info()
                usage.memory_mb = mem_info.rss / (1024 * 1024)
                usage.cpu_percent = process.cpu_percent(interval=0.1)
                usage.file_descriptors = process.num_fds() if hasattr(process, 'num_fds') else 0
            except Exception:
                pass

        usage.concurrent_requests = int(self._counters.get("concurrent_requests", 0))
        usage.total_tokens_consumed = int(self._counters.get("total_tokens_per_day", 0))

        # Update limits with current values
        self._limits["memory_mb"].current = usage.memory_mb
        self._limits["concurrent_requests"].current = usage.concurrent_requests

        return usage

    def get_limits(self) -> List[ResourceLimit]:
        """Get all resource limits."""
        return list(self._limits.values())

    def check_limits(self) -> List[str]:
        """Check if any limits are exceeded. Returns list of alerts."""
        alerts = []
        for limit in self._limits.values():
            if limit.exceeded:
                alerts.append(
                    f"Resource limit exceeded: {limit.name} "
                    f"({limit.current}/{limit.limit} {limit.unit})"
                )
            elif limit.usage_pct > 80:
                alerts.append(
                    f"Resource approaching limit: {limit.name} "
                    f"({limit.usage_pct:.0f}% of {limit.limit} {limit.unit})"
                )
        return alerts

    async def is_healthy(self) -> bool:
        """Check if all resources are within limits."""
        return not any(l.exceeded for l in self._limits.values())

    async def stop(self) -> None:
        """Stop the resource manager."""
        self._running = False
        logger.info("Resource manager stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get resource manager statistics."""
        usage = self.get_usage()
        return {
            "usage": usage.to_dict(),
            "limits": [l.to_dict() for l in self._limits.values()],
            "alerts": self.check_limits(),
        }
