"""IPC Manager — inter-process communication via async channels.

Provides typed communication channels between runtime components.
Each channel is an async queue with optional message typing.

Usage::

    ipc = IPCManager()
    ch = ipc.create_channel("tasks", maxsize=100)
    await ch.send({"task": "compute"})
    msg = await ch.receive()
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.kernel.ipc")


@dataclass
class IPCChannel:
    """A typed communication channel."""
    name: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    maxsize: int = 100
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sent_count: int = 0
    received_count: int = 0
    closed: bool = False

    async def send(self, message: Any) -> bool:
        """Send a message. Returns False if channel is closed or full."""
        if self.closed:
            return False
        try:
            self.queue.put_nowait(message)
            self.sent_count += 1
            return True
        except asyncio.QueueFull:
            logger.warning(f"Channel {self.name} is full")
            return False

    async def receive(self, timeout: float = 0) -> Optional[Any]:
        """Receive a message. Returns None if timeout or closed."""
        if self.closed and self.queue.empty():
            return None
        try:
            if timeout > 0:
                return await asyncio.wait_for(self.queue.get(), timeout=timeout)
            else:
                return await self.queue.get()
            self.received_count += 1
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None

    def close(self) -> None:
        self.closed = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "maxsize": self.maxsize,
            "queue_size": self.queue.qsize(),
            "sent_count": self.sent_count,
            "received_count": self.received_count,
            "closed": self.closed,
        }


class IPCManager:
    """Manages IPC channels between runtime components."""

    def __init__(self):
        self._channels: Dict[str, IPCChannel] = {}
        self._lock = asyncio.Lock()

    async def create_channel(self, name: str, maxsize: int = 100) -> IPCChannel:
        """Create a new IPC channel."""
        async with self._lock:
            if name in self._channels:
                return self._channels[name]
            channel = IPCChannel(name=name, maxsize=maxsize)
            # Recreate queue with proper maxsize
            channel.queue = asyncio.Queue(maxsize=maxsize)
            self._channels[name] = channel
            logger.info(f"Created IPC channel: {name}")
            return channel

    def get_channel(self, name: str) -> Optional[IPCChannel]:
        return self._channels.get(name)

    async def send(self, channel_name: str, message: Any) -> bool:
        """Send a message on a channel."""
        ch = self._channels.get(channel_name)
        if not ch:
            return False
        return await ch.send(message)

    async def receive(self, channel_name: str, timeout: float = 0) -> Optional[Any]:
        """Receive a message from a channel."""
        ch = self._channels.get(channel_name)
        if not ch:
            return None
        return await ch.receive(timeout)

    def list_channels(self) -> List[IPCChannel]:
        return list(self._channels.values())

    def close_channel(self, name: str) -> bool:
        ch = self._channels.get(name)
        if ch:
            ch.close()
            return True
        return False

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_channels": len(self._channels),
            "channels": {n: c.to_dict() for n, c in self._channels.items()},
        }

    async def is_healthy(self) -> bool:
        return True

    async def stop(self) -> None:
        for ch in self._channels.values():
            ch.close()
        logger.info("IPC manager stopped")
