"""FRIDAY Age V — Distributed Runtime.

This package provides distributed execution capabilities that extend
the Age IV runtime without modifying it. All modules use adapter
patterns and feature flags for backward compatibility.

Modules:
    - distributed_event_bus: Redis-backed EventBus with local fallback
    - distributed_task_queue: Durable task queue with retry/DLQ
    - federation: Node identity, heartbeat, discovery
    - worker: Worker runtime with task claiming and recovery
    - distributed_runtime: Unified facade

Feature flag: FRIDAY_DISTRIBUTED_RUNTIME=1 enables distributed mode.
When disabled (default), all operations fall back to Age IV local mode.
"""
from core.runtime.v5.distributed_event_bus import DistributedEventBus
from core.runtime.v5.distributed_task_queue import DistributedTaskQueue
from core.runtime.v5.federation import FederationManager, NodeInfo, NodeStatus
from core.runtime.v5.worker import WorkerRuntime, WorkerStatus
from core.runtime.v5.distributed_runtime import DistributedRuntime

__all__ = [
    "DistributedEventBus",
    "DistributedTaskQueue",
    "FederationManager", "NodeInfo", "NodeStatus",
    "WorkerRuntime", "WorkerStatus",
    "DistributedRuntime",
]
