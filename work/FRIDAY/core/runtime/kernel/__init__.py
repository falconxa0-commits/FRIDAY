"""Runtime Kernel — the scheduling and process management core.

This is NOT an operating system kernel. It is a userspace scheduler
and process manager that provides:
    - Fair task scheduling (priority + round-robin)
    - Process lifecycle management (subprocess tracking)
    - Inter-process communication (IPC via queues)
    - Unified event loop integration

The kernel sits BELOW the RuntimeContext and provides the primitives
that the runtime services use.
"""
from core.runtime.kernel.kernel_scheduler import KernelScheduler, TaskPriority
from core.runtime.kernel.process_manager import ProcessManager, ProcessState
from core.runtime.kernel.ipc_manager import IPCManager, IPCChannel
from core.runtime.kernel.event_loop import KernelEventLoop
from core.runtime.kernel.runtime_kernel import RuntimeKernel, KernelHealth, KernelMetrics

__all__ = [
    "KernelScheduler", "TaskPriority",
    "ProcessManager", "ProcessState",
    "IPCManager", "IPCChannel",
    "KernelEventLoop",
    "RuntimeKernel", "KernelHealth", "KernelMetrics",
]
