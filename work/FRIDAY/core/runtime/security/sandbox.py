"""Runtime Sandbox — isolation interfaces for plugin/tool execution."""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.security.sandbox")


@dataclass
class SandboxConfig:
    """Configuration for a sandboxed execution."""
    capabilities: List[str] = field(default_factory=list)
    max_memory_mb: int = 256
    max_cpu_seconds: int = 30
    max_filesystem_paths: List[str] = field(default_factory=list)
    network_allowed: bool = False
    env_vars: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capabilities": self.capabilities,
            "max_memory_mb": self.max_memory_mb,
            "max_cpu_seconds": self.max_cpu_seconds,
            "network_allowed": self.network_allowed,
        }


@dataclass
class SandboxResult:
    """Result of a sandboxed execution."""
    status: str = "pending"
    output: Any = None
    error: str = ""
    exit_code: int = 0
    duration_seconds: float = 0.0
    memory_used_mb: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "error": self.error,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "memory_used_mb": self.memory_used_mb,
            "timestamp": self.timestamp,
        }


class SandboxInterface(ABC):
    """Abstract interface for sandbox implementations."""

    @abstractmethod
    async def execute(
        self,
        func: Any,
        args: tuple = (),
        kwargs: Optional[Dict] = None,
        config: Optional[SandboxConfig] = None,
    ) -> SandboxResult: ...

    @abstractmethod
    async def is_available(self) -> bool: ...


class CapabilitySandbox(SandboxInterface):
    """Capability-scoped sandbox (no real isolation)."""

    def __init__(self, policy_engine=None):
        self.policy_engine = policy_engine

    async def execute(
        self,
        func: Any,
        args: tuple = (),
        kwargs: Optional[Dict] = None,
        config: Optional[SandboxConfig] = None,
    ) -> SandboxResult:
        kwargs = kwargs or {}
        config = config or SandboxConfig()
        result = SandboxResult()

        if self.policy_engine and config.capabilities:
            for cap in config.capabilities:
                decision = self.policy_engine.evaluate(cap)
                if not decision.allowed:
                    result.status = "failed"
                    result.error = f"Capability denied: {cap}"
                    return result

        start = time.perf_counter()
        try:
            if asyncio.iscoroutinefunction(func):
                result.output = await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=config.max_cpu_seconds,
                )
            else:
                result.output = await asyncio.to_thread(func, *args, **kwargs)
            result.status = "success"
        except asyncio.TimeoutError:
            result.status = "timeout"
            result.error = f"Exceeded {config.max_cpu_seconds}s timeout"
        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)

        result.duration_seconds = time.perf_counter() - start
        return result

    async def is_available(self) -> bool:
        return True


class SubprocessSandbox(SandboxInterface):
    """Subprocess-based sandbox (interface only, not implemented)."""

    async def execute(
        self,
        func: Any,
        args: tuple = (),
        kwargs: Optional[Dict] = None,
        config: Optional[SandboxConfig] = None,
    ) -> SandboxResult:
        raise NotImplementedError(
            "SubprocessSandbox requires seccomp + namespace isolation (Age V). "
            "Use CapabilitySandbox for now."
        )

    async def is_available(self) -> bool:
        return False


def create_sandbox(policy_engine=None, use_subprocess: bool = False) -> SandboxInterface:
    """Create a sandbox instance."""
    if use_subprocess:
        sandbox = SubprocessSandbox()
        if asyncio.run(sandbox.is_available()):
            return sandbox
        logger.warning("Subprocess sandbox not available, falling back to capability sandbox")

    return CapabilitySandbox(policy_engine=policy_engine)
