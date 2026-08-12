"""Runtime Drivers — abstraction layer for external capabilities.

Drivers provide a uniform interface to:
    - Tools (web search, code execution, etc.)
    - Models (LLM providers: GLM, Claude, Gemini, etc.)
    - Plugins (third-party integrations)

Each driver type has a common interface, allowing the runtime to
treat all external capabilities uniformly.
"""
from core.runtime.drivers.driver_manager import DriverManager, DriverRegistry
from core.runtime.drivers.tool_driver import ToolDriver, ToolResult
from core.runtime.drivers.model_driver import ModelDriver, ModelResult
from core.runtime.drivers.plugin_driver import PluginDriver, PluginResult

__all__ = [
    "DriverManager", "DriverRegistry",
    "ToolDriver", "ToolResult",
    "ModelDriver", "ModelResult",
    "PluginDriver", "PluginResult",
]
