"""Plugin Driver — base class for plugin execution drivers.

Plugin drivers wrap third-party integrations with a uniform interface.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class PluginResult:
    """Result of a plugin execution."""
    status: str = "pending"
    data: Any = None
    error: str = ""
    plugin_name: str = ""
    action: str = ""
    duration_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "data": self.data,
            "error": self.error,
            "plugin_name": self.plugin_name,
            "action": self.action,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
        }


class PluginDriver(abc.ABC):
    """Abstract base class for plugin drivers."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @property
    @abc.abstractmethod
    def capabilities(self) -> List[str]: ...

    @abc.abstractmethod
    async def execute(self, params: Dict[str, Any]) -> PluginResult: ...


class WeatherPluginDriver(PluginDriver):
    """Example plugin driver for weather integration."""

    @property
    def name(self) -> str:
        return "weather"

    @property
    def capabilities(self) -> List[str]:
        return ["weather.read"]

    async def execute(self, params: Dict[str, Any]) -> PluginResult:
        city = params.get("city", "unknown")
        return PluginResult(
            status="success",
            data={"city": city, "temp": "25C"},
            plugin_name=self.name,
            action="get_weather",
        )
