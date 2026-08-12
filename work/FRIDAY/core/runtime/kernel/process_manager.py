"""Process Manager — subprocess lifecycle management.

Tracks and manages subprocess execution for plugin sandboxing,
tool execution, and isolated task execution.

This is the interface layer for future subprocess-based sandboxing
(Age V). Currently tracks processes but does not isolate them.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.kernel.process_manager")


class ProcessState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"
    TIMEOUT = "timeout"


@dataclass
class ManagedProcess:
    """A tracked subprocess."""
    id: str
    command: List[str]
    state: ProcessState = ProcessState.PENDING
    pid: int = 0
    started_at: str = ""
    completed_at: str = ""
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    timeout: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "state": self.state.value,
            "pid": self.pid,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "returncode": self.returncode,
        }


class ProcessManager:
    """Manages subprocess lifecycle.

    Provides:
        - spawn: Start a subprocess with tracking
        - kill: Terminate a running process
        - wait: Wait for completion
        - list: List all tracked processes
    """

    def __init__(self):
        self._processes: Dict[str, ManagedProcess] = {}
        self._subprocesses: Dict[str, asyncio.subprocess.Process] = {}
        self._lock = asyncio.Lock()

    async def spawn(
        self,
        process_id: str,
        command: List[str],
        timeout: float = 30.0,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ManagedProcess:
        """Spawn a tracked subprocess."""
        proc = ManagedProcess(
            id=process_id,
            command=command,
            timeout=timeout,
            started_at=datetime.now(timezone.utc).isoformat(),
            state=ProcessState.RUNNING,
        )

        try:
            subprocess_proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            proc.pid = subprocess_proc.pid

            async with self._lock:
                self._processes[process_id] = proc
                self._subprocesses[process_id] = subprocess_proc

            logger.info(f"Spawned process {process_id} (pid={proc.pid})")
            return proc

        except Exception as exc:
            proc.state = ProcessState.FAILED
            proc.stderr = str(exc)
            logger.error(f"Failed to spawn process {process_id}: {exc}")
            return proc

    async def wait(self, process_id: str) -> ManagedProcess:
        """Wait for a process to complete."""
        proc = self._processes.get(process_id)
        subproc = self._subprocesses.get(process_id)

        if not proc or not subproc:
            raise ValueError(f"Process {process_id} not found")

        try:
            stdout, stderr = await asyncio.wait_for(
                subproc.communicate(),
                timeout=proc.timeout if proc.timeout > 0 else None,
            )
            proc.returncode = subproc.returncode
            proc.stdout = stdout.decode("utf-8", errors="replace") if stdout else ""
            proc.stderr = stderr.decode("utf-8", errors="replace") if stderr else ""
            proc.state = ProcessState.COMPLETED if proc.returncode == 0 else ProcessState.FAILED
        except asyncio.TimeoutError:
            proc.state = ProcessState.TIMEOUT
            await self.kill(process_id)
        except Exception as exc:
            proc.state = ProcessState.FAILED
            proc.stderr = str(exc)

        proc.completed_at = datetime.now(timezone.utc).isoformat()
        return proc

    async def kill(self, process_id: str) -> bool:
        """Kill a running process."""
        subproc = self._subprocesses.get(process_id)
        proc = self._processes.get(process_id)

        if not subproc or not proc:
            return False

        try:
            subproc.kill()
            await subproc.wait()
            proc.state = ProcessState.KILLED
            proc.completed_at = datetime.now(timezone.utc).isoformat()
            logger.info(f"Killed process {process_id}")
            return True
        except Exception as exc:
            logger.error(f"Failed to kill process {process_id}: {exc}")
            return False

    async def spawn_and_wait(
        self,
        process_id: str,
        command: List[str],
        timeout: float = 30.0,
    ) -> ManagedProcess:
        """Spawn a process and wait for it to complete."""
        await self.spawn(process_id, command, timeout)
        return await self.wait(process_id)

    def get_process(self, process_id: str) -> Optional[ManagedProcess]:
        return self._processes.get(process_id)

    def list_processes(self, state: Optional[ProcessState] = None) -> List[ManagedProcess]:
        procs = list(self._processes.values())
        if state:
            procs = [p for p in procs if p.state == state]
        return procs

    def get_stats(self) -> Dict[str, Any]:
        by_state = {}
        for p in self._processes.values():
            by_state[p.state.value] = by_state.get(p.state.value, 0) + 1
        return {
            "total_processes": len(self._processes),
            "by_state": by_state,
            "running": by_state.get("running", 0),
        }

    async def is_healthy(self) -> bool:
        running = sum(1 for p in self._processes.values() if p.state == ProcessState.RUNNING)
        return running < 100  # unhealthy if too many processes

    async def stop(self) -> None:
        """Kill all running processes."""
        for pid in list(self._subprocesses.keys()):
            await self.kill(pid)
        logger.info("Process manager stopped")
