"""Plugin Runtime — capability-scoped plugin lifecycle and execution.

This module provides a *capability-scoped* (NOT a full sandbox) runtime for
loading, registering, and executing plugins. Plugins are arbitrary Python
objects that expose methods. Each plugin is registered with a list of
*granted* capabilities (e.g. ``["filesystem.read", "network.http"]``).

Plugin methods may *declare* the capabilities they require — either:

1. Via the :func:`requires_capability` decorator on the method::

       class FileReader:
           @requires_capability("filesystem.read")
           async def read(self, path: str) -> str:
               ...

2. Via a ``REQUIRED_CAPABILITIES`` class attribute (a dict mapping method
   name -> capability string or list of capability strings)::

       class FileReader:
           REQUIRED_CAPABILITIES = {"read": "filesystem.read"}
           async def read(self, path): ...

When a method is executed, the runtime checks that every *required*
capability is present in the plugin's *granted* capabilities. If not, a
``PermissionError`` is raised *before* the method is invoked.

Usage::

    rt = PluginRuntime()
    handle = await rt.register_plugin(
        "file_reader", FileReader(), ["filesystem.read"]
    )
    contents = await rt.execute_plugin("file_reader", "read", "/etc/hosts")
    await rt.stop()
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger("friday.runtime.plugin_runtime")


# ---------------------------------------------------------------------------
# Capability decorator
# ---------------------------------------------------------------------------
def requires_capability(*capabilities: str) -> Callable[[Callable], Callable]:
    """Decorator marking a plugin method as requiring specific capabilities.

    Example::

        class FileReader:
            @requires_capability("filesystem.read")
            async def read(self, path: str) -> str:
                return open(path).read()

    The decorated function gains a ``__required_capabilities__`` attribute
    that :class:`PluginRuntime` inspects at execution time. The decorator
    is composable — multiple applications accumulate capabilities.
    """
    for cap in capabilities:
        if not isinstance(cap, str) or not cap:
            raise ValueError(f"Capability must be a non-empty string, got: {cap!r}")

    def decorator(func: Callable) -> Callable:
        existing = list(getattr(func, "__required_capabilities__", []))
        func.__required_capabilities__ = existing + list(capabilities)
        return func

    return decorator


# ---------------------------------------------------------------------------
# PluginHandle
# ---------------------------------------------------------------------------
@dataclass
class PluginHandle:
    """A registered plugin and its metadata."""

    name: str
    plugin: Any
    capabilities: List[str] = field(default_factory=list)
    registered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    execution_count: int = 0
    last_executed: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "plugin_type": type(self.plugin).__name__,
            "capabilities": list(self.capabilities),
            "registered_at": self.registered_at,
            "execution_count": self.execution_count,
            "last_executed": self.last_executed,
        }


# ---------------------------------------------------------------------------
# PluginRuntime
# ---------------------------------------------------------------------------
class PluginRuntime:
    """Manages plugin lifecycle with capability-based permissions.

    This is NOT a process-level sandbox — plugins run in the same Python
    interpreter as the host. The runtime enforces *capability scoping*:
    a plugin may only invoke methods whose declared capabilities are
    present in the plugin's granted capability set.
    """

    def __init__(self) -> None:
        self._plugins: Dict[str, PluginHandle] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._running: bool = True
        self._total_executions: int = 0
        self._permission_denied_count: int = 0

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    async def register_plugin(
        self,
        name: str,
        plugin: Any,
        capabilities: List[str],
    ) -> PluginHandle:
        """Register a plugin under ``name`` with the granted ``capabilities``.

        Args:
            name: Unique plugin identifier.
            plugin: The plugin object (any callable methods).
            capabilities: Capabilities granted to this plugin.

        Returns:
            The :class:`PluginHandle` for the registered plugin.

        Raises:
            ValueError: If ``name`` is already registered.
        """
        if not name or not isinstance(name, str):
            raise ValueError("Plugin name must be a non-empty string")
        if plugin is None:
            raise ValueError("Plugin cannot be None")
        capabilities = list(capabilities or [])

        async with self._lock:
            if name in self._plugins:
                raise ValueError(f"Plugin already registered: {name}")
            handle = PluginHandle(
                name=name,
                plugin=plugin,
                capabilities=capabilities,
            )
            self._plugins[name] = handle
            logger.info(
                "Registered plugin '%s' (%s) with capabilities: %s",
                name,
                type(plugin).__name__,
                capabilities,
            )
            return handle

    async def unregister_plugin(self, name: str) -> bool:
        """Unregister a plugin by name.

        Returns:
            True if the plugin was registered and is now removed;
            False if no plugin was registered under ``name``.
        """
        async with self._lock:
            if name in self._plugins:
                del self._plugins[name]
                logger.info("Unregistered plugin '%s'", name)
                return True
            return False

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    async def execute_plugin(
        self,
        name: str,
        method: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute ``method`` on plugin ``name`` with capability enforcement.

        The capability check happens *before* the method is invoked. If
        the method declares a required capability that the plugin does
        not have, :class:`PermissionError` is raised and the method is
        NOT called.

        Args:
            name: Registered plugin name.
            method: Attribute name of the method to call.
            *args, **kwargs: Passed through to the method.

        Returns:
            Whatever the plugin method returns.

        Raises:
            KeyError: If the plugin is not registered.
            AttributeError: If the plugin has no callable ``method``.
            PermissionError: If a required capability is not granted.
        """
        # Phase 1: validate + capability check (under lock, no execution)
        plugin_obj, func = await self._resolve_and_authorize(name, method)

        # Phase 2: execute outside the lock so concurrent plugin calls
        # don't serialize.
        succeeded = False
        result: Any = None
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            succeeded = True
        finally:
            # Phase 3: update counters (under lock). We always increment
            # execution_count when the method was actually invoked, even
            # on failure — but NOT when the call was rejected by the
            # capability check (that path raises before this point).
            async with self._lock:
                handle = self._plugins.get(name)
                if handle is not None:
                    handle.execution_count += 1
                    handle.last_executed = datetime.now(timezone.utc).isoformat()
                self._total_executions += 1

        return result

    async def _resolve_and_authorize(
        self, name: str, method: str
    ) -> tuple:
        """Look up the plugin, check capabilities, and return (plugin, func).

        Held under ``self._lock`` so that the capability check is atomic
        with respect to unregister/re-register.
        """
        async with self._lock:
            handle = self._plugins.get(name)
            if handle is None:
                raise KeyError(f"Plugin not found: {name}")

            required = self._get_required_capabilities(handle.plugin, method)
            for cap in required:
                if cap not in handle.capabilities:
                    self._permission_denied_count += 1
                    logger.warning(
                        "Permission denied: plugin '%s' lacks capability '%s' for method '%s'",
                        name,
                        cap,
                        method,
                    )
                    raise PermissionError(
                        f"Plugin '{name}' lacks required capability "
                        f"'{cap}' for method '{method}'"
                    )

            plugin_obj = handle.plugin
            func = getattr(plugin_obj, method, None)
            if func is None or not callable(func):
                raise AttributeError(
                    f"Plugin '{name}' has no callable method '{method}'"
                )
            return plugin_obj, func

    def _get_required_capabilities(
        self, plugin: Any, method: str
    ) -> List[str]:
        """Return the list of capabilities required to call ``method``.

        Sources, in priority order:
            1. ``@requires_capability`` decorator on the method
               (``func.__required_capabilities__``).
            2. ``plugin.required_capabilities`` dict (instance attribute).
            3. ``plugin.REQUIRED_CAPABILITIES`` dict (class attribute).

        If none of these declare requirements for ``method``, returns
        an empty list (open execution).
        """
        # Source 1: method-level decorator
        func = getattr(plugin, method, None)
        if func is not None:
            method_required = getattr(func, "__required_capabilities__", None)
            if method_required:
                return list(method_required)

        # Source 2 / 3: plugin-level capability map
        cap_map: Optional[Dict[str, Union[str, List[str]]]] = getattr(
            plugin, "required_capabilities", None
        )
        if cap_map is None:
            cap_map = getattr(plugin, "REQUIRED_CAPABILITIES", None)
        if cap_map is None:
            return []

        required = cap_map.get(method)
        if required is None:
            return []
        if isinstance(required, str):
            return [required]
        return list(required)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    async def list_plugins(self) -> List[PluginHandle]:
        """Return all registered plugin handles (snapshot)."""
        async with self._lock:
            return list(self._plugins.values())

    async def check_capability(
        self, plugin_name: str, capability: str
    ) -> bool:
        """Return True if ``plugin_name`` has been granted ``capability``."""
        async with self._lock:
            handle = self._plugins.get(plugin_name)
            if handle is None:
                return False
            return capability in handle.capabilities

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def is_healthy(self) -> bool:
        """Return True if the runtime is running."""
        return self._running

    async def stop(self) -> None:
        """Stop the runtime and clear all registered plugins."""
        async with self._lock:
            self._running = False
            count = len(self._plugins)
            self._plugins.clear()
            logger.info(
                "Plugin runtime stopped (cleared %d plugin(s))", count
            )

    def get_stats(self) -> Dict[str, Any]:
        """Return a summary of runtime state and per-plugin stats."""
        return {
            "running": self._running,
            "total_plugins": len(self._plugins),
            "total_executions": self._total_executions,
            "permission_denied_count": self._permission_denied_count,
            "plugins": [h.to_dict() for h in self._plugins.values()],
        }
