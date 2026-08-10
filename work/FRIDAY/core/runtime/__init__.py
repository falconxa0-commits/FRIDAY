"""FRIDAY AI Runtime — infrastructure for autonomous execution.

This package provides the runtime layer that FRIDAY uses to:
    - Schedule and execute tasks
    - Manage resources (memory, CPU, tokens)
    - Route events between subsystems
    - Track capabilities and services
    - Execute dependency-aware work graphs

The runtime does NOT replace FridayBrain — it provides the
infrastructure that the brain and agents run on.
"""
from core.runtime.runtime_manager import (
    RuntimeManager, RuntimeState, RuntimeStatus, get_runtime_manager,
)

__all__ = [
    "RuntimeManager", "RuntimeState", "RuntimeStatus", "get_runtime_manager",
]
