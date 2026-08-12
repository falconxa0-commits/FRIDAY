"""Driver Manager — registry and dispatch for all drivers.

Provides a single point of registration and lookup for:
    - Tool drivers (web search, code exec, etc.)
    - Model drivers (GLM, Claude, Gemini, etc.)
    - Plugin drivers (integrations)

Usage::

    dm = DriverManager()
    dm.register_tool("web_search", WebSearchDriver())
    dm.register_model("glm", GLMModelDriver())
    
    tool = dm.get_tool("web_search")
    result = await tool.execute({"query": "hello"})
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.drivers.manager")


class DriverRegistry:
    """Type-safe registry for drivers."""

    def __init__(self):
        self._tools: Dict[str, Any] = {}
        self._models: Dict[str, Any] = {}
        self._plugins: Dict[str, Any] = {}

    def register_tool(self, name: str, driver: Any) -> None:
        self._tools[name] = driver
        logger.info(f"Registered tool driver: {name}")

    def register_model(self, name: str, driver: Any) -> None:
        self._models[name] = driver
        logger.info(f"Registered model driver: {name}")

    def register_plugin(self, name: str, driver: Any) -> None:
        self._plugins[name] = driver
        logger.info(f"Registered plugin driver: {name}")

    def get_tool(self, name: str) -> Optional[Any]:
        return self._tools.get(name)

    def get_model(self, name: str) -> Optional[Any]:
        return self._models.get(name)

    def get_plugin(self, name: str) -> Optional[Any]:
        return self._plugins.get(name)

    def list_tools(self) -> List[str]:
        return list(self._tools.keys())

    def list_models(self) -> List[str]:
        return list(self._models.keys())

    def list_plugins(self) -> List[str]:
        return list(self._plugins.keys())

    def unregister_tool(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None

    def unregister_model(self, name: str) -> bool:
        return self._models.pop(name, None) is not None

    def unregister_plugin(self, name: str) -> bool:
        return self._plugins.pop(name, None) is not None

    def get_stats(self) -> Dict[str, Any]:
        return {
            "tool_count": len(self._tools),
            "model_count": len(self._models),
            "plugin_count": len(self._plugins),
            "tools": list(self._tools.keys()),
            "models": list(self._models.keys()),
            "plugins": list(self._plugins.keys()),
        }


class DriverManager:
    """Manages all driver registrations.

    This is the central registry that the RuntimeExecutor uses to
    dispatch tool/model/plugin calls.
    """

    def __init__(self):
        self.registry = DriverRegistry()
        self._execution_counts: Dict[str, int] = {}

    def register_tool(self, name: str, driver: Any) -> None:
        self.registry.register_tool(name, driver)

    def register_model(self, name: str, driver: Any) -> None:
        self.registry.register_model(name, driver)

    def register_plugin(self, name: str, driver: Any) -> None:
        self.registry.register_plugin(name, driver)

    async def execute_tool(self, name: str, params: Dict[str, Any]) -> Any:
        tool = self.registry.get_tool(name)
        if not tool:
            return {"status": "error", "error": f"Tool driver not found: {name}"}
        self._execution_counts[f"tool.{name}"] = self._execution_counts.get(f"tool.{name}", 0) + 1
        return await tool.execute(params)

    async def execute_model(self, name: str, params: Dict[str, Any]) -> Any:
        model = self.registry.get_model(name)
        if not model:
            return {"status": "error", "error": f"Model driver not found: {name}"}
        self._execution_counts[f"model.{name}"] = self._execution_counts.get(f"model.{name}", 0) + 1
        return await model.execute(params)

    async def execute_plugin(self, name: str, params: Dict[str, Any]) -> Any:
        plugin = self.registry.get_plugin(name)
        if not plugin:
            return {"status": "error", "error": f"Plugin driver not found: {name}"}
        self._execution_counts[f"plugin.{name}"] = self._execution_counts.get(f"plugin.{name}", 0) + 1
        return await plugin.execute(params)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "registry": self.registry.get_stats(),
            "execution_counts": self._execution_counts,
        }

    async def is_healthy(self) -> bool:
        return True

    async def stop(self) -> None:
        logger.info("Driver manager stopped")
