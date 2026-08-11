"""Context Runtime — manages execution context for chains of operations.

Execution context is the structured data that flows through a chain of
operations. Contexts can be chained hierarchically (parent → child) so
that an operation can inherit data from its caller while overriding or
extending only the keys it cares about.

Usage::

    ctx_rt = ContextRuntime()
    parent = await ctx_rt.create_context("ingest", {"source": "api"})
    child  = await ctx_rt.create_context("parse", {"format": "json"})
    await ctx_rt.chain_contexts(parent.id, child.id)

    # child.data now contains {"format": "json", "source": "api"}
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.context_runtime")


@dataclass
class Context:
    """A single execution context.

    Attributes:
        id: Unique identifier.
        name: Human-readable name (e.g., "ingest", "transform").
        data: Arbitrary key-value payload.
        parent_id: ID of the parent context, if chained.
        children: IDs of child contexts.
        created_at: ISO-8601 creation timestamp.
        updated_at: ISO-8601 last-update timestamp.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[str] = None
    children: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "data": dict(self.data),
            "parent_id": self.parent_id,
            "children": list(self.children),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ContextRuntime:
    """Manages execution contexts for operations.

    Contexts support hierarchical chaining: a child context inherits
    its parent's data unless the child explicitly overrides a key.
    """

    def __init__(self, event_bus: Any = None):
        self._contexts: Dict[str, Context] = {}
        self._event_bus = event_bus
        self._running = True

    async def create_context(
        self, name: str, data: Optional[Dict[str, Any]] = None
    ) -> Context:
        """Create a new context.

        Args:
            name: Human-readable name for the context.
            data: Initial key-value payload.

        Returns:
            The created Context.
        """
        ctx = Context(name=name, data=dict(data or {}))
        self._contexts[ctx.id] = ctx
        logger.debug(f"Created context '{name}' ({ctx.id[:8]})")

        if self._event_bus:
            await self._event_bus.publish(
                "context.created",
                {"context_id": ctx.id, "name": name},
                source="context_runtime",
            )
        return ctx

    async def get_context(self, context_id: str) -> Optional[Context]:
        """Get a context by ID."""
        return self._contexts.get(context_id)

    async def update_context(
        self, context_id: str, updates: Dict[str, Any]
    ) -> Optional[Context]:
        """Update a context's data with a shallow merge.

        Args:
            context_id: ID of the context to update.
            updates: Key-value pairs to merge into ``context.data``.

        Returns:
            The updated Context, or None if not found.
        """
        ctx = self._contexts.get(context_id)
        if ctx is None:
            return None

        ctx.data.update(updates)
        ctx.updated_at = datetime.now(timezone.utc).isoformat()

        # Propagate to children so they see the merged view via their
        # resolved snapshot — children always inherit from parent.
        if self._event_bus:
            await self._event_bus.publish(
                "context.updated",
                {"context_id": ctx.id, "keys": list(updates.keys())},
                source="context_runtime",
            )
        return ctx

    async def delete_context(self, context_id: str) -> bool:
        """Delete a context.

        The context is removed from its parent's children list and its
        own children are detached (orphaned, not deleted).
        """
        ctx = self._contexts.get(context_id)
        if ctx is None:
            return False

        # Detach from parent
        if ctx.parent_id and ctx.parent_id in self._contexts:
            parent = self._contexts[ctx.parent_id]
            if context_id in parent.children:
                parent.children.remove(context_id)

        # Orphan children
        for child_id in ctx.children:
            child = self._contexts.get(child_id)
            if child is not None and child.parent_id == context_id:
                child.parent_id = None

        del self._contexts[context_id]

        if self._event_bus:
            await self._event_bus.publish(
                "context.deleted",
                {"context_id": context_id},
                source="context_runtime",
            )
        logger.debug(f"Deleted context {context_id[:8]}")
        return True

    async def list_contexts(self) -> List[Context]:
        """List all known contexts."""
        return list(self._contexts.values())

    async def chain_contexts(self, parent_id: str, child_id: str) -> bool:
        """Chain a child context under a parent.

        After chaining, the child's data is extended with the parent's
        data (parent keys that the child does not already define).

        Args:
            parent_id: ID of the parent context.
            child_id: ID of the child context.

        Returns:
            True if the chaining succeeded, False if either ID is
            unknown or the chaining would create a cycle.
        """
        parent = self._contexts.get(parent_id)
        child = self._contexts.get(child_id)
        if parent is None or child is None:
            return False
        if parent_id == child_id:
            return False

        # Cycle check: walk up the parent chain from `parent`. If we
        # encounter child_id, chaining would create a cycle.
        cursor: Optional[str] = parent_id
        seen: set = set()
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            if cursor == child_id:
                return False  # would create a cycle
            node = self._contexts.get(cursor)
            cursor = node.parent_id if node else None

        # Detach from any existing parent
        if child.parent_id and child.parent_id in self._contexts:
            old_parent = self._contexts[child.parent_id]
            if child_id in old_parent.children:
                old_parent.children.remove(child_id)

        child.parent_id = parent_id
        if child_id not in parent.children:
            parent.children.append(child_id)

        # Inherit parent data (parent keys that child doesn't override)
        merged = dict(parent.data)
        merged.update(child.data)
        child.data = merged
        child.updated_at = datetime.now(timezone.utc).isoformat()

        if self._event_bus:
            await self._event_bus.publish(
                "context.chained",
                {"parent_id": parent_id, "child_id": child_id},
                source="context_runtime",
            )
        logger.debug(
            f"Chained context {child_id[:8]} under parent {parent_id[:8]}"
        )
        return True

    async def resolve_context(self, context_id: str) -> Dict[str, Any]:
        """Resolve a context to its fully-merged data dict.

        Walks the parent chain and returns the union of all ancestor
        data with the context's own data taking precedence.
        """
        ctx = self._contexts.get(context_id)
        if ctx is None:
            return {}

        merged: Dict[str, Any] = {}
        chain: List[Context] = []
        cursor: Optional[str] = context_id
        seen: set = set()
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            node = self._contexts.get(cursor)
            if node is None:
                break
            chain.append(node)
            cursor = node.parent_id

        # Apply ancestor data first (oldest ancestor wins defaults),
        # then more recent ancestors override, then the context itself.
        for node in reversed(chain):
            merged.update(node.data)
        return merged

    async def is_healthy(self) -> bool:
        """Check if the context runtime is healthy."""
        return self._running

    async def stop(self) -> None:
        """Stop the context runtime."""
        self._running = False
        self._contexts.clear()
        logger.info("Context runtime stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get context runtime statistics."""
        roots = sum(1 for c in self._contexts.values() if c.parent_id is None)
        return {
            "total_contexts": len(self._contexts),
            "root_contexts": roots,
            "chained_contexts": sum(
                1 for c in self._contexts.values() if c.parent_id is not None
            ),
        }
