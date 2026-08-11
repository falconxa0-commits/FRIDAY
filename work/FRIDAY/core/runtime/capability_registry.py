"""Capability Registry — service discovery for runtime subsystems.

Registers and discovers capabilities (services) that subsystems provide.
Enables loose coupling — a subsystem can request a capability without
knowing which module provides it.

Usage::

    registry = CapabilityRegistry()
    registry.register("llm.chat", GLMBrain(), metadata={"provider": "glm"})
    brain = registry.resolve("llm.chat")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.capability_registry")


@dataclass
class Capability:
    """A registered capability."""
    name: str
    provider: Any
    metadata: Dict[str, Any] = field(default_factory=dict)
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "provider_type": type(self.provider).__name__,
            "metadata": self.metadata,
            "registered_at": self.registered_at,
        }


class CapabilityRegistry:
    """Service registry for runtime capabilities.

    Capabilities are named services (e.g., "llm.chat", "memory.store")
    that can be provided by any module.
    """

    def __init__(self):
        self._capabilities: Dict[str, Capability] = {}
        self._aliases: Dict[str, str] = {}  # alias → canonical name

    def register(
        self, name: str, provider: Any, metadata: Optional[Dict] = None
    ) -> Capability:
        """Register a capability.

        Args:
            name: Capability name (e.g., "llm.chat").
            provider: The object that provides this capability.
            metadata: Optional metadata about the provider.
        """
        cap = Capability(name=name, provider=provider, metadata=metadata or {})
        self._capabilities[name] = cap
        logger.info(f"Registered capability: {name} ({type(provider).__name__})")
        return cap

    def unregister(self, name: str) -> bool:
        """Unregister a capability."""
        if name in self._capabilities:
            del self._capabilities[name]
            # Remove any aliases pointing to this capability
            for alias, target in list(self._aliases.items()):
                if target == name:
                    del self._aliases[alias]
            return True
        return False

    def resolve(self, name: str) -> Optional[Any]:
        """Resolve a capability name to its provider.

        Args:
            name: Capability name or alias.

        Returns:
            The provider object, or None if not found.
        """
        # Check aliases
        canonical = self._aliases.get(name, name)
        cap = self._capabilities.get(canonical)
        return cap.provider if cap else None

    def register_alias(self, alias: str, canonical_name: str) -> None:
        """Register an alias for a capability name."""
        self._aliases[alias] = canonical_name

    def list_capabilities(self) -> List[Capability]:
        """List all registered capabilities."""
        return list(self._capabilities.values())

    def has_capability(self, name: str) -> bool:
        """Check if a capability is registered."""
        canonical = self._aliases.get(name, name)
        return canonical in self._capabilities

    async def is_healthy(self) -> bool:
        """Check if the registry is healthy."""
        return True

    async def stop(self) -> None:
        """Stop the registry."""
        self._capabilities.clear()
        self._aliases.clear()
        logger.info("Capability registry stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        return {
            "total_capabilities": len(self._capabilities),
            "total_aliases": len(self._aliases),
            "capabilities": [c.to_dict() for c in self._capabilities.values()],
        }
