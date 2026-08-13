"""Federation — node identity, heartbeat, and discovery.

Manages a cluster of FRIDAY runtime nodes:
    - Node registration (identity, capabilities, address)
    - Heartbeat (liveness monitoring)
    - Discovery (find other nodes)
    - Leader election (simple, for single-primary mode)
    - Graceful deregistration

Nodes are NOT trusted by default. Each node must:
    1. Register with a valid node ID
    2. Advertise capabilities
    3. Maintain heartbeat
    4. Be authorized by the governance layer

Usage::

    fed = FederationManager(node_id="node-1", node_name="primary")
    await fed.start()
    other_nodes = await fed.discover_nodes()
    await fed.stop()
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.v5.federation")


class NodeStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DEGRADED = "degraded"
    SHUTTING_DOWN = "shutting_down"
    OFFLINE = "offline"
    BANNED = "banned"  # security violation


@dataclass
class NodeInfo:
    """Information about a federation node."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    address: str = ""
    port: int = 8000
    capabilities: List[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    is_primary: bool = False
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_heartbeat: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "port": self.port,
            "capabilities": self.capabilities,
            "status": self.status.value,
            "is_primary": self.is_primary,
            "registered_at": self.registered_at,
            "last_heartbeat": self.last_heartbeat,
            "metadata": self.metadata,
        }

    @property
    def is_stale(self) -> bool:
        """Check if heartbeat is stale (>30s since last)."""
        try:
            hb = datetime.fromisoformat(self.last_heartbeat)
            elapsed = (datetime.now(timezone.utc) - hb).total_seconds()
            return elapsed > 30
        except Exception:
            return True


class FederationManager:
    """Manages runtime federation.

    Features:
        - Node registration and deregistration
        - Heartbeat monitoring
        - Node discovery
        - Leader election (simple last-man-standing)
        - Stale node detection
        - Capability advertisement
    """

    HEARTBEAT_INTERVAL = 10  # seconds
    STALE_THRESHOLD = 30  # seconds without heartbeat = stale

    def __init__(
        self,
        node_id: str = "",
        node_name: str = "",
        node_address: str = "",
        node_port: int = 8000,
        capabilities: Optional[List[str]] = None,
    ):
        self._self = NodeInfo(
            id=node_id or str(uuid.uuid4()),
            name=node_name or f"node-{node_id[:8]}" if node_id else "node",
            address=node_address,
            port=node_port,
            capabilities=capabilities or [],
            status=NodeStatus.PENDING,
        )
        self._nodes: Dict[str, NodeInfo] = {}  # node_id → info
        self._lock = asyncio.Lock()
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._stale_check_task: Optional[asyncio.Task] = None
        self._running = False
        self._primary_id: Optional[str] = None

    @property
    def self_info(self) -> NodeInfo:
        return self._self

    @property
    def is_primary(self) -> bool:
        return self._self.is_primary

    async def start(self) -> None:
        """Start the federation manager."""
        self._running = True
        self._self.status = NodeStatus.ACTIVE
        self._self.is_primary = True  # first node is primary by default
        self._primary_id = self._self.id
        self._nodes[self._self.id] = self._self

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._stale_check_task = asyncio.create_task(self._stale_check_loop())

        logger.info(
            f"Federation started — node={self._self.name} "
            f"id={self._self.id[:8]} primary={self._self.is_primary}"
        )

    async def stop(self) -> None:
        """Stop the federation manager and deregister."""
        self._running = False
        self._self.status = NodeStatus.SHUTTING_DOWN

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._stale_check_task:
            self._stale_check_task.cancel()
            try:
                await self._stale_check_task
            except asyncio.CancelledError:
                pass

        # Deregister self
        async with self._lock:
            self._nodes.pop(self._self.id, None)
            self._self.status = NodeStatus.OFFLINE

            # If we were primary, elect a new one
            if self._primary_id == self._self.id:
                await self._elect_new_primary()

        logger.info("Federation stopped")

    async def register_node(
        self,
        node_info: NodeInfo,
    ) -> bool:
        """Register a new node in the federation.

        Args:
            node_info: Information about the node to register.

        Returns:
            True if registration was accepted.
        """
        if node_info.status == NodeStatus.BANNED:
            return False

        async with self._lock:
            node_info.status = NodeStatus.ACTIVE
            node_info.registered_at = datetime.now(timezone.utc).isoformat()
            node_info.last_heartbeat = node_info.registered_at
            self._nodes[node_info.id] = node_info

        logger.info(
            f"Registered node: {node_info.name} ({node_info.id[:8]}) "
            f"capabilities={len(node_info.capabilities)}"
        )
        return True

    async def deregister_node(self, node_id: str) -> bool:
        """Deregister a node from the federation."""
        async with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                return False
            node.status = NodeStatus.OFFLINE
            del self._nodes[node_id]

            if self._primary_id == node_id:
                await self._elect_new_primary()

        logger.info(f"Deregistered node: {node_id[:8]}")
        return True

    async def heartbeat(self, node_id: str) -> bool:
        """Update a node's heartbeat.

        Returns:
            True if heartbeat was recorded.
        """
        async with self._lock:
            node = self._nodes.get(node_id)
            if not node or node.status == NodeStatus.BANNED:
                return False
            node.last_heartbeat = datetime.now(timezone.utc).isoformat()
            if node.status == NodeStatus.DEGRADED:
                node.status = NodeStatus.ACTIVE
        return True

    async def discover_nodes(
        self,
        status: Optional[NodeStatus] = None,
    ) -> List[NodeInfo]:
        """Discover nodes in the federation.

        Args:
            status: Filter by status (default: ACTIVE only).

        Returns:
            List of matching nodes.
        """
        async with self._lock:
            nodes = list(self._nodes.values())
        if status is None:
            status = NodeStatus.ACTIVE
        return [n for n in nodes if n.status == status]

    async def get_primary(self) -> Optional[NodeInfo]:
        """Get the current primary node."""
        if not self._primary_id:
            return None
        async with self._lock:
            return self._nodes.get(self._primary_id)

    async def ban_node(self, node_id: str) -> bool:
        """Ban a node (security violation)."""
        async with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                return False
            node.status = NodeStatus.BANNED

            if self._primary_id == node_id:
                await self._elect_new_primary()

        logger.warning(f"Banned node: {node_id[:8]}")
        return True

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats for self."""
        while self._running:
            try:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                self._self.last_heartbeat = datetime.now(timezone.utc).isoformat()
            except asyncio.CancelledError:
                break

    async def _stale_check_loop(self) -> None:
        """Periodically check for stale nodes and mark them degraded."""
        while self._running:
            try:
                await asyncio.sleep(self.STALE_THRESHOLD)
                await self._check_stale_nodes()
            except asyncio.CancelledError:
                break

    async def _check_stale_nodes(self) -> None:
        """Mark stale nodes as degraded."""
        async with self._lock:
            for node in self._nodes.values():
                if node.id == self._self.id:
                    continue
                if node.status == NodeStatus.ACTIVE and node.is_stale:
                    node.status = NodeStatus.DEGRADED
                    logger.warning(
                        f"Node {node.name} ({node.id[:8]}) marked DEGRADED — "
                        f"heartbeat stale"
                    )

    async def _elect_new_primary(self) -> None:
        """Elect a new primary node (simple: first active node)."""
        for node in self._nodes.values():
            if node.status == NodeStatus.ACTIVE:
                node.is_primary = True
                self._primary_id = node.id
                logger.info(f"Elected new primary: {node.name} ({node.id[:8]})")
                return
        self._primary_id = None
        logger.warning("No active nodes available for primary election")

    def get_stats(self) -> Dict[str, Any]:
        by_status = {}
        for node in self._nodes.values():
            by_status[node.status.value] = by_status.get(node.status.value, 0) + 1
        return {
            "total_nodes": len(self._nodes),
            "by_status": by_status,
            "has_primary": self._primary_id is not None,
            "self_id": self._self.id,
            "self_is_primary": self.is_primary,
        }

    async def is_healthy(self) -> bool:
        return self._running and self._self.status == NodeStatus.ACTIVE
