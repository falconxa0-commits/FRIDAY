"""Memory Runtime — manages runtime memory pools.

This is NOT the OS memory (RSS is handled by ``resource_manager.py``).
The memory runtime owns logical pools of bytes the runtime itself cares
about: conversation-history buffers, context-window caches, embedding
caches, scratch slabs, etc.

Each pool has a hard ``max_size_bytes`` ceiling. Allocations that would
push a pool over the ceiling are *refused* (``allocate`` returns False)
rather than evicting older entries — eviction policy is the caller's
responsibility. Keys are opaque strings; values are raw ``bytes``.

Usage::

    mr = MemoryRuntime()
    pool = await mr.create_pool("context", max_size_bytes=1024 * 1024)
    await mr.allocate("context", "msg-1", b"hello world")
    data = await mr.retrieve("context", "msg-1")  # → b"hello world"
    await mr.get_pool_usage("context")
    # → {"size_bytes": 11, "max_bytes": 1048576, "usage_pct": 0.0, "item_count": 1}
    await mr.clear_pool("context")  # → 1 (items cleared)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.memory_runtime")


@dataclass
class MemoryPool:
    """A single named memory pool.

    Attributes:
        name: Pool identifier.
        max_size_bytes: Hard byte ceiling.
        current_size_bytes: Bytes currently held.
        items: Mapping ``key → bytes``.
        created_at: ISO-8601 creation timestamp.
    """

    name: str
    max_size_bytes: int
    current_size_bytes: int = 0
    items: Dict[str, bytes] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def usage_pct(self) -> float:
        if self.max_size_bytes <= 0:
            return 0.0
        return (self.current_size_bytes / self.max_size_bytes) * 100.0

    @property
    def available_bytes(self) -> int:
        return max(0, self.max_size_bytes - self.current_size_bytes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "max_size_bytes": self.max_size_bytes,
            "current_size_bytes": self.current_size_bytes,
            "item_count": len(self.items),
            "usage_pct": round(self.usage_pct, 2),
            "available_bytes": self.available_bytes,
            "created_at": self.created_at,
        }


class MemoryRuntime:
    """Manages runtime memory pools and allocation."""

    def __init__(self) -> None:
        self._pools: Dict[str, MemoryPool] = {}
        self._lock = asyncio.Lock()
        self._total_allocations: int = 0
        self._total_refusals: int = 0
        self._total_deallocations: int = 0
        self._total_retrievals: int = 0
        self._total_pool_clears: int = 0
        self._running: bool = True

    # ------------------------------------------------------------------
    # Pool management
    # ------------------------------------------------------------------
    async def create_pool(self, name: str, max_size_bytes: int) -> MemoryPool:
        """Create a new memory pool.

        Raises ``ValueError`` if ``name`` is already registered or
        ``max_size_bytes`` is non-positive.
        """
        if max_size_bytes <= 0:
            raise ValueError(
                f"max_size_bytes must be positive (got {max_size_bytes})"
            )
        async with self._lock:
            if name in self._pools:
                raise ValueError(f"Pool '{name}' already exists")
            pool = MemoryPool(name=name, max_size_bytes=max_size_bytes)
            self._pools[name] = pool
            logger.info(
                f"Created pool '{name}' (max={max_size_bytes} bytes)"
            )
            return pool

    async def get_pool(self, name: str) -> Optional[MemoryPool]:
        return self._pools.get(name)

    async def list_pools(self) -> List[MemoryPool]:
        return list(self._pools.values())

    async def clear_pool(self, pool_name: str) -> int:
        """Drop every item from a pool. Returns count of items cleared.

        Returns 0 if the pool is unknown.
        """
        async with self._lock:
            pool = self._pools.get(pool_name)
            if pool is None:
                return 0
            cleared = len(pool.items)
            pool.items.clear()
            pool.current_size_bytes = 0
            self._total_pool_clears += 1
            logger.info(f"Cleared pool '{pool_name}' ({cleared} items)")
            return cleared

    async def delete_pool(self, pool_name: str) -> bool:
        """Remove a pool entirely. Returns True if it existed."""
        async with self._lock:
            if pool_name in self._pools:
                del self._pools[pool_name]
                logger.info(f"Deleted pool '{pool_name}'")
                return True
            return False

    # ------------------------------------------------------------------
    # Allocation
    # ------------------------------------------------------------------
    async def allocate(self, pool_name: str, key: str, data: bytes) -> bool:
        """Allocate ``data`` under ``key`` in ``pool_name``.

        Returns False if:
            - the pool is unknown,
            - ``data`` is not ``bytes``,
            - the allocation would exceed ``max_size_bytes`` (refused).

        Re-allocating an existing key replaces the value (and updates
        the size delta). If the *new* size would exceed the ceiling,
        the old value is preserved and the allocation is refused.
        """
        if not isinstance(data, (bytes, bytearray)):
            logger.warning(
                f"allocate: data must be bytes, got {type(data).__name__}"
            )
            return False
        data = bytes(data)  # normalize bytearray → bytes

        async with self._lock:
            pool = self._pools.get(pool_name)
            if pool is None:
                logger.warning(f"allocate: unknown pool '{pool_name}'")
                return False

            existing_size = (
                len(pool.items[key]) if key in pool.items else 0
            )
            new_total = (
                pool.current_size_bytes - existing_size + len(data)
            )
            if new_total > pool.max_size_bytes:
                self._total_refusals += 1
                logger.info(
                    f"allocate refused in '{pool_name}' for key '{key}': "
                    f"would reach {new_total}/{pool.max_size_bytes} bytes"
                )
                return False

            pool.items[key] = data
            pool.current_size_bytes = new_total
            self._total_allocations += 1
            return True

    async def retrieve(self, pool_name: str, key: str) -> Optional[bytes]:
        """Return the bytes stored under ``key``, or None if missing."""
        async with self._lock:
            pool = self._pools.get(pool_name)
            if pool is None:
                return None
            data = pool.items.get(key)
            if data is not None:
                self._total_retrievals += 1
            return data

    async def deallocate(self, pool_name: str, key: str) -> bool:
        """Drop a single key. Returns True if the key existed."""
        async with self._lock:
            pool = self._pools.get(pool_name)
            if pool is None:
                return False
            if key not in pool.items:
                return False
            size = len(pool.items[key])
            del pool.items[key]
            pool.current_size_bytes -= size
            self._total_deallocations += 1
            return True

    # ------------------------------------------------------------------
    # Usage
    # ------------------------------------------------------------------
    async def get_pool_usage(self, pool_name: str) -> Optional[Dict[str, Any]]:
        """Return a usage snapshot for a pool, or None if unknown.

        Shape::

            {"size_bytes": int, "max_bytes": int,
             "usage_pct": float, "item_count": int}
        """
        pool = self._pools.get(pool_name)
        if pool is None:
            return None
        return {
            "size_bytes": pool.current_size_bytes,
            "max_bytes": pool.max_size_bytes,
            "usage_pct": round(pool.usage_pct, 2),
            "item_count": len(pool.items),
        }

    # ------------------------------------------------------------------
    # Health / shutdown
    # ------------------------------------------------------------------
    async def is_healthy(self) -> bool:
        """Healthy iff runtime is running and no pool exceeds its ceiling."""
        if not self._running:
            return False
        for pool in self._pools.values():
            if pool.current_size_bytes > pool.max_size_bytes:
                return False
        return True

    async def stop(self) -> None:
        """Stop the memory runtime and clear all pools."""
        async with self._lock:
            self._running = False
            for pool in self._pools.values():
                pool.items.clear()
                pool.current_size_bytes = 0
            logger.info("Memory runtime stopped")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        """Synchronous snapshot of memory-runtime stats."""
        total_allocated = sum(
            p.current_size_bytes for p in self._pools.values()
        )
        total_capacity = sum(p.max_size_bytes for p in self._pools.values())
        return {
            "pool_count": len(self._pools),
            "total_allocated_bytes": total_allocated,
            "total_capacity_bytes": total_capacity,
            "total_allocations": self._total_allocations,
            "total_refusals": self._total_refusals,
            "total_deallocations": self._total_deallocations,
            "total_retrievals": self._total_retrievals,
            "total_pool_clears": self._total_pool_clears,
            "pools": [p.to_dict() for p in self._pools.values()],
        }
