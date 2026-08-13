"""Distributed Runtime — unified facade for all distributed subsystems.

Combines:
    - DistributedEventBus
    - DistributedTaskQueue
    - FederationManager
    - WorkerRuntime

Into a single start/stop lifecycle.

When FRIDAY_DISTRIBUTED_RUNTIME=0 (default), operates in local mode
using the Age IV EventBus and TaskQueue.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.runtime.v5.distributed_event_bus import DistributedEventBus
from core.runtime.v5.distributed_task_queue import DistributedTaskQueue
from core.runtime.v5.federation import FederationManager, NodeInfo, NodeStatus
from core.runtime.v5.worker import WorkerRuntime

logger = logging.getLogger("friday.runtime.v5.distributed_runtime")


@dataclass
class DistributedRuntimeStatus:
    """Status of the distributed runtime."""
    started: bool = False
    mode: str = "local"  # "local" or "distributed"
    started_at: str = ""
    components: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started": self.started,
            "mode": self.mode,
            "started_at": self.started_at,
            "components": dict(self.components),
        }


class DistributedRuntime:
    """Unified distributed runtime facade.

    Usage::

        runtime = DistributedRuntime()
        await runtime.start()

        # Access subsystems
        event_bus = runtime.event_bus
        task_queue = runtime.task_queue
        federation = runtime.federation

        await runtime.stop()
    """

    def __init__(
        self,
        node_name: str = "",
        redis_url: str = "",
        enable_distributed: bool = False,
    ):
        self._enable_distributed = enable_distributed or (
            os.environ.get("FRIDAY_DISTRIBUTED_RUNTIME", "0") == "1"
        )
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "")
        self._node_name = node_name
        self._status = DistributedRuntimeStatus()
        self._lock = asyncio.Lock()

        # Subsystems (lazy init)
        self.event_bus: Optional[DistributedEventBus] = None
        self.task_queue: Optional[DistributedTaskQueue] = None
        self.federation: Optional[FederationManager] = None
        self.workers: List[WorkerRuntime] = []

    @property
    def status(self) -> DistributedRuntimeStatus:
        return self._status

    @property
    def is_distributed(self) -> bool:
        return self._status.mode == "distributed"

    async def start(self) -> None:
        """Start the distributed runtime."""
        async with self._lock:
            if self._status.started:
                return

            mode = "distributed" if self._enable_distributed and self._redis_url else "local"

            logger.info(f"Starting DistributedRuntime — mode={mode}")

            # 1. Start event bus
            self.event_bus = DistributedEventBus(
                redis_url=self._redis_url,
                enable_distributed=self._enable_distributed,
            )
            await self.event_bus.start()
            self._status.components["event_bus"] = "started"

            # 2. Start task queue
            self.task_queue = DistributedTaskQueue(
                redis_url=self._redis_url,
                enable_distributed=self._enable_distributed,
            )
            await self.task_queue.start()
            self._status.components["task_queue"] = "started"

            # 3. Start federation
            self.federation = FederationManager(
                node_name=self._node_name or "primary",
                capabilities=["event_bus", "task_queue", "worker"],
            )
            await self.federation.start()
            self._status.components["federation"] = "started"

            self._status.started = True
            self._status.mode = mode
            self._status.started_at = datetime.now(timezone.utc).isoformat()

            await self.event_bus.publish(
                "runtime.distributed.started",
                {"mode": mode, "node": self._node_name},
                source="distributed_runtime",
            )

            logger.info(f"DistributedRuntime started — mode={mode}")

    async def spawn_worker(
        self,
        worker_name: str = "",
        capabilities: Optional[list] = None,
    ) -> WorkerRuntime:
        """Spawn a new worker."""
        worker = WorkerRuntime(
            worker_name=worker_name or f"worker-{len(self.workers)}",
            task_queue=self.task_queue,
            federation=self.federation,
            capabilities=capabilities or [],
        )
        await worker.start()
        self.workers.append(worker)
        self._status.components[f"worker:{worker.name}"] = "started"

        await self.event_bus.publish(
            "worker.spawned",
            {"worker_id": worker.id, "worker_name": worker.name},
            source="distributed_runtime",
        )

        logger.info(f"Spawned worker: {worker.name}")
        return worker

    async def stop(self) -> None:
        """Stop the distributed runtime."""
        async with self._lock:
            if not self._status.started:
                return

            logger.info("Stopping DistributedRuntime...")

            # Stop workers
            for worker in self.workers:
                await worker.stop()
            self.workers.clear()

            # Stop federation
            if self.federation:
                await self.federation.stop()
                self._status.components["federation"] = "stopped"

            # Stop task queue
            if self.task_queue:
                await self.task_queue.stop()
                self._status.components["task_queue"] = "stopped"

            # Stop event bus
            if self.event_bus:
                await self.event_bus.stop()
                self._status.components["event_bus"] = "stopped"

            self._status.started = False
            logger.info("DistributedRuntime stopped")

    async def health_check(self) -> Dict[str, bool]:
        """Check health of all subsystems."""
        results = {}
        if self.event_bus:
            results["event_bus"] = await self.event_bus.is_healthy()
        if self.task_queue:
            results["task_queue"] = await self.task_queue.is_healthy()
        if self.federation:
            results["federation"] = await self.federation.is_healthy()
        for w in self.workers:
            results[f"worker:{w.name}"] = await w.is_healthy()
        return results

    def get_stats(self) -> Dict[str, Any]:
        return {
            "status": self._status.to_dict(),
            "event_bus": self.event_bus.get_stats() if self.event_bus else {},
            "task_queue": self.task_queue.get_stats() if self.task_queue else {},
            "federation": self.federation.get_stats() if self.federation else {},
            "workers": [w.get_status() for w in self.workers],
        }
