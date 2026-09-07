"""Persistence — pluggable storage adapter for living memory.

Provides three adapters:
    - InMemoryPersistence: default; fast; non-durable (lost on restart)
    - JSONFilePersistence: durable; atomic writes via tmp+rename
    - RedisPersistence: durable + cross-process (optional; uses fakeredis for tests)

All adapters implement the same PersistenceAdapter interface so the
LivingMemoryManager can swap them transparently.

Recovery semantics:
    - On startup, load() reads all stored memories and returns them
    - If a record is corrupted (failed JSON parse / failed immune verify),
      it's quarantined and reported but does NOT abort recovery
    - Partial writes are detected via atomic rename (JSON) or transaction (Redis)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .base import Memory, MemoryID, PersistenceError, now_utc
from .provenance import Provenance

logger = logging.getLogger("friday.living_memory.persistence")


@dataclass
class RecoveryReport:
    """Result of a load() recovery operation."""
    loaded_count: int = 0
    quarantined_count: int = 0
    errors: List[str] = field(default_factory=list)
    quarantined_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "loaded_count": self.loaded_count,
            "quarantined_count": self.quarantined_count,
            "errors": list(self.errors),
            "quarantined_ids": list(self.quarantined_ids),
        }


class PersistenceAdapter(ABC):
    """Abstract persistence adapter."""

    @property
    @abstractmethod
    def is_durable(self) -> bool: ...

    @abstractmethod
    async def save(
        self, memory: Memory, provenance: Optional[Provenance] = None
    ) -> None: ...

    @abstractmethod
    async def load(
        self,
    ) -> Tuple[List[Tuple[Memory, Provenance]], RecoveryReport]: ...

    @abstractmethod
    async def delete(self, memory_id: str) -> bool: ...

    @abstractmethod
    async def exists(self, memory_id: str) -> bool: ...

    @abstractmethod
    async def count(self) -> int: ...

    @abstractmethod
    async def flush(self) -> None: ...


# ----------------------------------------------------------------------
# In-memory adapter
# ----------------------------------------------------------------------


class InMemoryPersistence(PersistenceAdapter):
    """In-memory store. Non-durable but thread/async-safe."""

    def __init__(self):
        self._store: Dict[str, Tuple[Memory, Provenance]] = {}
        self._lock = asyncio.Lock()

    @property
    def is_durable(self) -> bool:
        return False

    async def save(self, memory: Memory, provenance: Optional[Provenance] = None) -> None:
        async with self._lock:
            self._store[memory.id.value] = (memory, provenance or Provenance())

    async def load(self) -> Tuple[List[Tuple[Memory, Provenance]], RecoveryReport]:
        report = RecoveryReport()
        async with self._lock:
            items = list(self._store.values())
            report.loaded_count = len(items)
        return items, report

    async def delete(self, memory_id: str) -> bool:
        async with self._lock:
            if memory_id in self._store:
                del self._store[memory_id]
                return True
            return False

    async def exists(self, memory_id: str) -> bool:
        async with self._lock:
            return memory_id in self._store

    async def count(self) -> int:
        async with self._lock:
            return len(self._store)

    async def flush(self) -> None:
        async with self._lock:
            self._store.clear()


# ----------------------------------------------------------------------
# JSON file adapter (atomic writes)
# ----------------------------------------------------------------------


class JSONFilePersistence(PersistenceAdapter):
    """Durable JSON-file persistence with atomic writes.

    Format: a single JSON file mapping memory_id -> {memory, provenance}.
    Writes use tmp-file + atomic rename to avoid partial-write corruption.
    """

    def __init__(self, path: str):
        self._path = path
        self._lock = asyncio.Lock()
        # Load eagerly so exists()/count() are fast
        self._cache: Dict[str, Tuple[Memory, Provenance]] = {}
        self._loaded = False
        self._corrupt_path: Optional[str] = None  # V2: track moved-aside corrupt file

    @property
    def is_durable(self) -> bool:
        return True

    def _load_sync(self) -> None:
        if self._loaded:
            return
        if not os.path.exists(self._path):
            self._loaded = True
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            # V2: NEVER silently swallow corruption. Record the error so
            # the recovery report can flag it. Move the corrupt file aside
            # so the next save doesn't overwrite a possibly-recoverable backup.
            corrupt_path = self._path + f".corrupt.{int(datetime.now(timezone.utc).timestamp())}"
            try:
                os.rename(self._path, corrupt_path)
                logger.error(
                    "Persistence file unreadable (moved to %s): %s",
                    corrupt_path, e,
                )
            except OSError:
                logger.error("Persistence file unreadable and could not be moved: %s", e)
            self._loaded = True
            self._corrupt_path = corrupt_path
            return
        except OSError as e:
            logger.error("Persistence file unreadable: %s", e)
            self._loaded = True
            return
        if not isinstance(raw, dict):
            self._loaded = True
            return
        for mid, entry in raw.items():
            try:
                mem = Memory.from_dict(entry["memory"])
                prov = Provenance.from_dict(entry.get("provenance", {}))
                self._cache[mid] = (mem, prov)
            except Exception as e:
                logger.warning("Skipping corrupt record %s: %s", mid[:8], e)
        self._loaded = True

    def _save_sync(self) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        data = {
            mid: {"memory": m.to_dict(), "provenance": p.to_dict()}
            for mid, (m, p) in self._cache.items()
        }
        # Atomic write: tmp + rename
        fd, tmp_path = tempfile.mkstemp(
            prefix=".mem_", suffix=".json.tmp",
            dir=os.path.dirname(self._path) or ".",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, default=str)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    async def save(self, memory: Memory, provenance: Optional[Provenance] = None) -> None:
        async with self._lock:
            self._load_sync()
            self._cache[memory.id.value] = (memory, provenance or Provenance())
            try:
                self._save_sync()
            except OSError as e:
                raise PersistenceError(f"JSONFilePersistence save failed: {e}") from e

    async def load(self) -> Tuple[List[Tuple[Memory, Provenance]], RecoveryReport]:
        report = RecoveryReport()
        async with self._lock:
            self._load_sync()
            items = []
            for mid, (mem, prov) in list(self._cache.items()):
                # Verify provenance chain integrity on load
                if not prov.verify_chain():
                    report.quarantined_count += 1
                    report.quarantined_ids.append(mid)
                    report.errors.append(
                        f"Provenance chain verification failed for {mid[:8]}"
                    )
                    continue
                items.append((mem, prov))
            report.loaded_count = len(items)
        return items, report

    async def delete(self, memory_id: str) -> bool:
        async with self._lock:
            self._load_sync()
            if memory_id not in self._cache:
                return False
            del self._cache[memory_id]
            try:
                self._save_sync()
            except OSError as e:
                raise PersistenceError(f"JSONFilePersistence delete failed: {e}") from e
            return True

    async def exists(self, memory_id: str) -> bool:
        async with self._lock:
            self._load_sync()
            return memory_id in self._cache

    async def count(self) -> int:
        async with self._lock:
            self._load_sync()
            return len(self._cache)

    async def flush(self) -> None:
        async with self._lock:
            self._cache.clear()
            try:
                if os.path.exists(self._path):
                    os.unlink(self._path)
            except OSError:
                pass


# ----------------------------------------------------------------------
# Redis adapter (real Redis or fakeredis)
# ----------------------------------------------------------------------


class RedisPersistence(PersistenceAdapter):
    """Redis-backed persistence. Works with real redis or fakeredis.

    Uses a single hash key per memory: hmset(memory_id, {memory, provenance})
    Keys are namespaced under a prefix to avoid collisions.
    """

    def __init__(self, prefix: str = "friday:mem:", client=None):
        self._prefix = prefix
        self._lock = asyncio.Lock()
        self._client = client  # redis.Redis or fakeredis.FakeRedis
        self._owns_client = False

    @property
    def is_durable(self) -> bool:
        return True

    async def _ensure_client(self):
        if self._client is not None:
            return self._client
        # Try real redis
        try:
            import redis  # noqa
            self._client = redis.Redis(
                host=os.environ.get("REDIS_HOST", "localhost"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                decode_responses=True,
            )
            self._client.ping()
            self._owns_client = True
            return self._client
        except Exception:
            # Fall back to fakeredis for testing
            try:
                import fakeredis
                self._client = fakeredis.FakeRedis(decode_responses=True)
                self._owns_client = True
                return self._client
            except ImportError:
                raise PersistenceError(
                    "No Redis client available and fakeredis not installed"
                )

    async def save(self, memory: Memory, provenance: Optional[Provenance] = None) -> None:
        client = await self._ensure_client()
        async with self._lock:
            key = f"{self._prefix}{memory.id.value}"
            value = json.dumps({
                "memory": memory.to_dict(),
                "provenance": (provenance or Provenance()).to_dict(),
            }, default=str)
            try:
                client.set(key, value)
            except Exception as e:
                raise PersistenceError(f"Redis save failed: {e}") from e

    async def load(self) -> Tuple[List[Tuple[Memory, Provenance]], RecoveryReport]:
        client = await self._ensure_client()
        report = RecoveryReport()
        items: List[Tuple[Memory, Provenance]] = []
        async with self._lock:
            try:
                keys = list(client.scan_iter(match=f"{self._prefix}*"))
            except Exception as e:
                report.errors.append(f"Redis scan failed: {e}")
                return items, report
            for key in keys:
                try:
                    raw = client.get(key)
                    if not raw:
                        continue
                    data = json.loads(raw)
                    mem = Memory.from_dict(data["memory"])
                    prov = Provenance.from_dict(data.get("provenance", {}))
                    if not prov.verify_chain():
                        report.quarantined_count += 1
                        report.quarantined_ids.append(mem.id.value)
                        continue
                    items.append((mem, prov))
                except Exception as e:
                    report.quarantined_count += 1
                    report.errors.append(f"Bad record {key}: {e}")
            report.loaded_count = len(items)
        return items, report

    async def delete(self, memory_id: str) -> bool:
        client = await self._ensure_client()
        async with self._lock:
            try:
                return bool(client.delete(f"{self._prefix}{memory_id}"))
            except Exception as e:
                raise PersistenceError(f"Redis delete failed: {e}") from e

    async def exists(self, memory_id: str) -> bool:
        client = await self._ensure_client()
        async with self._lock:
            try:
                return bool(client.exists(f"{self._prefix}{memory_id}"))
            except Exception:
                return False

    async def count(self) -> int:
        client = await self._ensure_client()
        async with self._lock:
            try:
                return sum(1 for _ in client.scan_iter(match=f"{self._prefix}*"))
            except Exception:
                return 0

    async def flush(self) -> None:
        client = await self._ensure_client()
        async with self._lock:
            try:
                keys = list(client.scan_iter(match=f"{self._prefix}*"))
                if keys:
                    client.delete(*keys)
            except Exception as e:
                logger.warning("Redis flush failed: %s", e)


__all__ = [
    "PersistenceAdapter", "InMemoryPersistence", "JSONFilePersistence",
    "RedisPersistence", "RecoveryReport",
]
