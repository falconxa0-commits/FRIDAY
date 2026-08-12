"""Runtime Kernel — unified facade for all kernel subsystems.

The RuntimeKernel provides a single entry point to all kernel services:
    - KernelScheduler (fair task scheduling)
    - ProcessManager (subprocess lifecycle)
    - IPCManager (inter-process communication)
    - KernelEventLoop (unified event loop)

This is the "kernel" of the FRIDAY runtime — it provides the
primitives that the RuntimeContext and all runtime services use.

Design:
    - NOT an OS kernel (no hardware, no memory management, no syscalls)
    - Userspace primitives for task scheduling and process management
    - Foundation for future Age V sandboxing
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.runtime.kernel.kernel_scheduler import KernelScheduler, TaskPriority
from core.runtime.kernel.process_manager import ProcessManager, ProcessState
from core.runtime.kernel.ipc_manager import IPCManager, IPCChannel
from core.runtime.kernel.event_loop import KernelEventLoop

logger = logging.getLogger("friday.runtime.kernel")


@dataclass
class KernelHealth:
    """Health status of the runtime kernel."""
    healthy: bool = False
    scheduler_healthy: bool = False
    process_manager_healthy: bool = False
    ipc_healthy: bool = False
    event_loop_healthy: bool = False
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "healthy": self.healthy,
            "scheduler_healthy": self.scheduler_healthy,
            "process_manager_healthy": self.process_manager_healthy,
            "ipc_healthy": self.ipc_healthy,
            "event_loop_healthy": self.event_loop_healthy,
            "checked_at": self.checked_at,
        }


@dataclass
class KernelMetrics:
    """Aggregated metrics from all kernel subsystems."""
    scheduler_stats: Dict[str, Any] = field(default_factory=dict)
    process_stats: Dict[str, Any] = field(default_factory=dict)
    ipc_stats: Dict[str, Any] = field(default_factory=dict)
    event_loop_stats: Dict[str, Any] = field(default_factory=dict)
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scheduler": self.scheduler_stats,
            "process_manager": self.process_stats,
            "ipc": self.ipc_stats,
            "event_loop": self.event_loop_stats,
            "collected_at": self.collected_at,
        }


class RuntimeKernel:
    """The FRIDAY runtime kernel facade.

    Provides unified access to all kernel services. This is the
    lowest layer of the FRIDAY runtime — everything above uses
    these primitives.

    Usage::

        kernel = get_runtime_kernel()
        await kernel.start()

        # Submit a high-priority task
        await kernel.scheduler.submit("task1", my_func, TaskPriority.HIGH)

        # Spawn a subprocess
        proc = await kernel.process_manager.spawn_and_wait("proc1", ["ls", "-la"])

        # Create an IPC channel
        ch = await kernel.ipc.create_channel("events")

        await kernel.stop()
    """

    def __init__(self):
        self.scheduler = KernelScheduler()
        self.process_manager = ProcessManager()
        self.ipc = IPCManager()
        self.event_loop = KernelEventLoop()
        self._started = False

    async def start(self) -> None:
        """Start all kernel subsystems."""
        if self._started:
            return
        await self.event_loop.start()
        self._started = True
        logger.info("Runtime kernel started")

    async def stop(self) -> None:
        """Stop all kernel subsystems."""
        if not self._started:
            return
        await self.event_loop.stop()
        await self.scheduler.stop()
        await self.process_manager.stop()
        await self.ipc.stop()
        self._started = False
        logger.info("Runtime kernel stopped")

    async def health_check(self) -> KernelHealth:
        """Check health of all kernel subsystems."""
        sched_h, proc_h, ipc_h, loop_h = await asyncio.gather(
            self.scheduler.is_healthy(),
            self.process_manager.is_healthy(),
            self.ipc.is_healthy(),
            self.event_loop.is_healthy(),
        )
        return KernelHealth(
            healthy=all([sched_h, proc_h, ipc_h, loop_h]),
            scheduler_healthy=sched_h,
            process_manager_healthy=proc_h,
            ipc_healthy=ipc_h,
            event_loop_healthy=loop_h,
        )

    def get_metrics(self) -> KernelMetrics:
        """Collect metrics from all kernel subsystems."""
        return KernelMetrics(
            scheduler_stats=self.scheduler.get_stats(),
            process_stats=self.process_manager.get_stats(),
            ipc_stats=self.ipc.get_stats(),
            event_loop_stats=self.event_loop.get_stats(),
        )

    def get_status(self) -> Dict[str, Any]:
        """Get kernel status summary."""
        return {
            "started": self._started,
            "scheduler_queues": self.scheduler.get_queue_depths(),
            "processes": len(self.process_manager._processes),
            "ipc_channels": len(self.ipc._channels),
            "active_tasks": len(self.event_loop.get_active_tasks()),
        }


# Singleton
_kernel: Optional[RuntimeKernel] = None


def get_runtime_kernel() -> RuntimeKernel:
    global _kernel
    if _kernel is None:
        _kernel = RuntimeKernel()
    return _kernel
