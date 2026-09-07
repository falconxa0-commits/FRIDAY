"""Working Memory — active context with capacity limits and TTL.

Working memory holds the currently-active context for a citizen/session.
It is:
    - Bounded (capacity limit, default 64 items)
    - TTL-based (default 5 minutes)
    - Priority-ordered (higher priority items resist eviction)
    - Non-persistent (no provenance required — derived from active context)
    - Subject to eviction (LRU + priority + TTL)
    - Tenant-isolated (one working memory per tenant/citizen)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    Memory, MemoryID, MemoryMetadata, MemoryType, MemoryState,
    now_utc, ResourceLimitError, ValidationError,
)

logger = logging.getLogger("friday.living_memory.working")


@dataclass
class WorkingMemoryItem:
    """A single item in working memory."""
    id: MemoryID = field(default_factory=MemoryID)
    key: str = ""
    value: Any = None
    priority: int = 5  # 1..10, higher = more important
    created_at: str = field(default_factory=now_utc)
    last_accessed_at: str = field(default_factory=now_utc)
    expires_at: str = ""  # empty = no TTL
    owner_id: str = ""
    tenant_id: str = "default"
    correlation_id: str = ""

    def is_expired(self, now: Optional[str] = None) -> bool:
        if not self.expires_at:
            return False
        now = now or now_utc()
        return now >= self.expires_at

    def touch(self) -> None:
        self.last_accessed_at = now_utc()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id.to_dict(),
            "key": self.key,
            "value": self.value,
            "priority": self.priority,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "expires_at": self.expires_at,
            "owner_id": self.owner_id,
            "tenant_id": self.tenant_id,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorkingMemoryItem":
        from .base import MemoryID
        return cls(
            id=MemoryID.from_dict(d.get("id", {})),
            key=d.get("key", ""),
            value=d.get("value"),
            priority=int(d.get("priority", 5)),
            created_at=d.get("created_at", ""),
            last_accessed_at=d.get("last_accessed_at", ""),
            expires_at=d.get("expires_at", ""),
            owner_id=d.get("owner_id", ""),
            tenant_id=d.get("tenant_id", "default"),
            correlation_id=d.get("correlation_id", ""),
        )


class WorkingMemory:
    """Bounded, TTL-based, priority-ordered active context store.

    One instance per (tenant_id, owner_id). Thread/async safe.
    Eviction policy: evict items in this order when capacity is reached:
        1. Expired items
        2. Lowest priority
        3. LRU (oldest last_accessed_at)
    """

    def __init__(
        self,
        tenant_id: str,
        owner_id: str,
        capacity: int = 64,
        default_ttl_seconds: int = 300,  # 5 min
        max_value_size_bytes: int = 8192,
    ):
        if capacity < 1 or capacity > 65536:
            raise ValidationError(f"Invalid working memory capacity: {capacity}")
        if default_ttl_seconds < 0:
            raise ValidationError("TTL cannot be negative")
        self._tenant_id = tenant_id
        self._owner_id = owner_id
        self._capacity = capacity
        self._default_ttl_seconds = default_ttl_seconds
        self._max_value_size_bytes = max_value_size_bytes
        self._items: Dict[str, WorkingMemoryItem] = {}  # key -> item
        self._lock = asyncio.Lock()

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def capacity(self) -> int:
        return self._capacity

    async def put(
        self,
        key: str,
        value: Any,
        priority: int = 5,
        ttl_seconds: Optional[int] = None,
        correlation_id: str = "",
    ) -> WorkingMemoryItem:
        """Put an item into working memory. Evicts if over capacity."""
        if not key or not isinstance(key, str):
            raise ValidationError("Working memory key must be non-empty string")
        if priority < 1 or priority > 10:
            raise ValidationError("Priority must be 1..10")
        size = len(repr(value).encode("utf-8"))
        if size > self._max_value_size_bytes:
            raise ResourceLimitError(
                f"Working memory value too large: {size} > {self._max_value_size_bytes}"
            )
        async with self._lock:
            # If key exists, update in place (no eviction needed)
            if key in self._items:
                item = self._items[key]
                item.value = value
                item.priority = priority
                item.touch()
                if ttl_seconds is not None:
                    if ttl_seconds == 0:
                        item.expires_at = ""
                    else:
                        from datetime import datetime, timedelta
                        item.expires_at = (
                            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
                        ).isoformat()
                return item
            # New key — may need eviction
            ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
            expires_at = ""
            if ttl > 0:
                from datetime import datetime, timedelta
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=ttl)
                ).isoformat()
            item = WorkingMemoryItem(
                key=key,
                value=value,
                priority=priority,
                expires_at=expires_at,
                owner_id=self._owner_id,
                tenant_id=self._tenant_id,
                correlation_id=correlation_id,
            )
            self._items[key] = item
            # Evict if over capacity
            if len(self._items) > self._capacity:
                await self._evict(len(self._items) - self._capacity)
            return item

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value. Returns None if missing or expired."""
        async with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            if item.is_expired():
                del self._items[key]
                return None
            item.touch()
            return item.value

    async def peek(self, key: str) -> Optional[WorkingMemoryItem]:
        """Peek at an item without touching its LRU stats."""
        async with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            if item.is_expired():
                del self._items[key]
                return None
            return item

    async def remove(self, key: str) -> bool:
        async with self._lock:
            if key in self._items:
                del self._items[key]
                return True
            return False

    async def clear_expired(self) -> int:
        """Remove all expired items. Returns count removed."""
        async with self._lock:
            now = now_utc()
            expired_keys = [
                k for k, v in self._items.items() if v.is_expired(now)
            ]
            for k in expired_keys:
                del self._items[k]
            return len(expired_keys)

    async def list_items(self, sort_by: str = "priority") -> List[WorkingMemoryItem]:
        """List all (non-expired) items."""
        async with self._lock:
            now = now_utc()
            items = [v for v in self._items.values() if not v.is_expired(now)]
        if sort_by == "priority":
            items.sort(key=lambda x: (-x.priority, x.last_accessed_at))
        elif sort_by == "recency":
            items.sort(key=lambda x: x.last_accessed_at, reverse=True)
        elif sort_by == "created":
            items.sort(key=lambda x: x.created_at, reverse=True)
        return items

    async def size(self) -> int:
        async with self._lock:
            return len(self._items)

    async def snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            now = now_utc()
            items = [v for v in self._items.values() if not v.is_expired(now)]
        return {
            "tenant_id": self._tenant_id,
            "owner_id": self._owner_id,
            "capacity": self._capacity,
            "current_size": len(items),
            "utilization": round(len(items) / self._capacity, 4),
            "items": [i.to_dict() for i in items[:20]],
        }

    async def _evict(self, count: int) -> None:
        """Evict `count` items per policy: expired → lowest priority → LRU."""
        if count <= 0:
            return
        now = now_utc()
        # 1. Evict expired
        expired = [k for k, v in self._items.items() if v.is_expired(now)]
        for k in expired[:count]:
            del self._items[k]
        count -= len(expired[:count])
        if count <= 0:
            return
        # 2. Evict lowest priority + LRU
        items = sorted(
            self._items.items(),
            key=lambda kv: (kv[1].priority, kv[1].last_accessed_at),
        )
        for k, _ in items[:count]:
            del self._items[k]


__all__ = ["WorkingMemory", "WorkingMemoryItem"]
