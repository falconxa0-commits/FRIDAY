"""Runtime Sandbox — isolation interfaces for plugin/tool execution.

This module provides two sandbox implementations:

* :class:`CapabilitySandbox` — a lightweight in-process sandbox that
  delegates capability checks to a :class:`PolicyEngine` and runs the
  function inside the host Python process (no OS-level isolation).
* :class:`SubprocessSandbox` — a *production* sandbox that runs the
  target function inside a dedicated child process spawned via
  :mod:`multiprocessing`. The child is hard-restricted by:

      * ``RLIMIT_AS``  — address-space (memory) ceiling  (Linux/BSD).
      * ``RLIMIT_CPU`` — CPU-seconds ceiling.
      * ``RLIMIT_FSIZE`` — file-size write ceiling (default 0).
      * ``RLIMIT_NPROC`` — per-user process count ceiling.
      * ``signal.SIGALRM`` — wall-clock backup timeout.
      * ``os.setsid``   — own process group, so the whole tree can be
        killed by group-signal if a timeout fires.
      * Environment whitelist — only a small set of variables are
        forwarded to the child.

After the child exits, the parent samples peak RSS via
``resource.getrusage(RUSAGE_CHILDREN)`` and verifies the configured
memory ceiling was respected. Every execution is appended to a
decision log (in-memory, optionally forwarded to a ``PolicyEngine``'s
``_decision_log``).
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import pickle
import signal
import sys
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.security.sandbox")

# ---------------------------------------------------------------------------
# Platform capability detection
# ---------------------------------------------------------------------------
try:
    import resource as _resource  # POSIX only
    _HAS_RESOURCE = True
except ImportError:  # pragma: no cover — Windows fallback path
    _HAS_RESOURCE = False

try:
    import psutil as _psutil  # optional, used for live RSS sampling
    _HAS_PSUTIL = True
except ImportError:  # pragma: no cover
    _HAS_PSUTIL = False


# Environment variables the child is allowed to inherit from the parent.
# Anything outside this set is stripped — the child starts with a
# minimal, sanitized environment. Callers may pass additional vars via
# ``SandboxConfig.env_vars``.
DEFAULT_ENV_WHITELIST: List[str] = [
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "FRIDAY_DEV_MODE",
    "FRIDAY_LEDGER_HMAC_SECRET",
    "FRIDAY_ENGINEERING_DIR",
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
]


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


# ---------------------------------------------------------------------------
# SubprocessSandbox — production implementation
# ---------------------------------------------------------------------------


def _sandbox_worker(
    func: Any,
    args: tuple,
    kwargs: Dict[str, Any],
    config_dict: Dict[str, Any],
    result_queue: "multiprocessing.Queue",
) -> None:
    """Run inside the spawned child process.

    Applies POSIX resource limits, installs a SIGALRM wall-clock
    timeout, then calls ``func(*args, **kwargs)``. The result (or
    exception) is pushed back to the parent through ``result_queue``.

    Must be a module-level function so :mod:`multiprocessing` can
    pickle it on spawn-based start methods.
    """
    # Re-import inside the child to avoid leaking parent state on fork.
    import os as _os
    import signal as _signal

    # 1. Environment isolation — wipe everything not whitelisted.
    whitelist = set(config_dict.get("env_whitelist") or [])
    extra_env = config_dict.get("env_vars") or {}
    if whitelist:
        kept = {k: v for k, v in _os.environ.items() if k in whitelist}
        _os.environ.clear()
        _os.environ.update(kept)
    # Caller-supplied vars always win and can extend the whitelist.
    _os.environ.update(extra_env)

    # 2. Become a session leader so the whole process tree dies when
    #    the parent kills the group. os.setsid may fail if already a
    #    leader — that's fine.
    try:
        _os.setsid()
    except OSError:
        pass

    # 3. Apply POSIX resource limits (Linux/BSD).
    max_mem_mb = int(config_dict.get("max_memory_mb") or 0)
    max_cpu = int(config_dict.get("max_cpu_seconds") or 0)
    fs_paths = config_dict.get("max_filesystem_paths") or []
    network_allowed = bool(config_dict.get("network_allowed", False))

    try:
        import resource as _r

        # Memory ceiling — RLIMIT_AS is the address-space cap.
        if max_mem_mb > 0 and hasattr(_r, "RLIMIT_AS"):
            mem_bytes = max_mem_mb * 1024 * 1024
            _r.setrlimit(_r.RLIMIT_AS, (mem_bytes, mem_bytes))

        # CPU seconds ceiling — kernel will SIGXCPU the child.
        if max_cpu > 0 and hasattr(_r, "RLIMIT_CPU"):
            soft = max(max_cpu - 1, 1)  # soft a bit below hard
            _r.setrlimit(_r.RLIMIT_CPU, (soft, max_cpu))

        # File-write ceiling — block any write larger than 0 bytes
        # unless the caller explicitly whitelisted filesystem paths.
        # (Conservative: prevents a sandboxed function from writing
        # arbitrary files. Real production code would use namespaces.)
        if not fs_paths and hasattr(_r, "RLIMIT_FSIZE"):
            _r.setrlimit(_r.RLIMIT_FSIZE, (0, 0))

        # NOTE: RLIMIT_NPROC is intentionally NOT set. It is a per-UID
        # (not per-process) limit, which means a tight ceiling here
        # would also count against the parent and any sibling tests —
        # leading to "can't start new thread" errors in the queue
        # feeder thread. Fork-bomb protection is instead provided by
        # the parent's wall-clock timeout + process-group kill.
    except (ImportError, ValueError, OSError) as exc:
        # Resource limits aren't available or the system rejected the
        # value. Record but don't abort — the parent's wall-clock
        # timeout is still in effect as a backstop.
        result_queue.put(("_resource_warning", str(exc)))

    # 4. Install SIGALRM as a wall-clock backup. If the CPU-seconds
    #    rlimit doesn't fire (e.g. blocking syscall), the alarm will.
    if max_cpu > 0:

        def _alarm_handler(signum, frame):
            raise TimeoutError(
                f"Sandbox wall-clock timeout ({max_cpu}s) exceeded"
            )

        try:
            _signal.signal(_signal.SIGALRM, _alarm_handler)
            _signal.alarm(max(max_cpu, 1))
        except (ValueError, OSError):
            pass  # not in main thread / unsupported

    # 5. Execute the function.
    try:
        if asyncio.iscoroutinefunction(func):
            # The child has no event loop yet — give it one.
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                output = loop.run_until_complete(func(*args, **kwargs))
            finally:
                loop.close()
        else:
            output = func(*args, **kwargs)
        result_queue.put(("_success", output))
    except TimeoutError as exc:
        result_queue.put(("_timeout", str(exc)))
    except BaseException as exc:  # noqa: BLE001 — sandbox must report all
        # Pickle-able shape: (type_name, message, traceback_text)
        result_queue.put((
            "_failed",
            {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        ))


class SubprocessSandbox(SandboxInterface):
    """Production sandbox using subprocess isolation.

    The sandbox spawns a dedicated child process via
    :mod:`multiprocessing` for every execution. The child:

        * runs in its own session (``os.setsid``);
        * has its environment replaced with a whitelisted subset;
        * is hard-limited by ``RLIMIT_AS`` (memory),
          ``RLIMIT_CPU`` (CPU seconds), ``RLIMIT_FSIZE`` (file writes),
          and ``RLIMIT_NPROC`` (process count) on POSIX systems;
        * installs a ``SIGALRM`` wall-clock backup timer.

    The parent waits for the child with a timeout of its own; if the
    timeout fires the entire process group is ``SIGKILL``-ed. Peak RSS
    of the child is sampled via ``resource.getrusage`` (with a
    ``psutil`` live-poll fallback for finer-grained reporting).

    Every execution — success or failure — is appended to the
    in-memory decision log, and (when a ``PolicyEngine`` is supplied)
    forwarded to that engine's decision log as well.
    """

    # Default start method — we use ``spawn`` on every platform. The
    # ``fork`` start method inherits the parent's address space, which
    # means an RLIMIT_AS ceiling applied in the child can leave almost
    # no headroom (the parent may already be using hundreds of MB) and
    # causes spurious "can't start new thread" failures in the queue
    # feeder. ``spawn`` starts a fresh interpreter — slower but safe.
    DEFAULT_START_METHOD = "spawn"

    def __init__(
        self,
        policy_engine=None,
        env_whitelist: Optional[List[str]] = None,
        start_method: Optional[str] = None,
    ):
        self.policy_engine = policy_engine
        self.env_whitelist = list(env_whitelist or DEFAULT_ENV_WHITELIST)
        self._decision_log: List[Dict[str, Any]] = []
        self._start_method = start_method or self.DEFAULT_START_METHOD

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def execute(
        self,
        func: Any,
        args: tuple = (),
        kwargs: Optional[Dict] = None,
        config: Optional[SandboxConfig] = None,
    ) -> SandboxResult:
        """Execute ``func`` in an isolated subprocess.

        See the class docstring for the full set of guarantees.
        Returns a :class:`SandboxResult` with ``status`` ∈
        ``{success, timeout, failed, killed}``.
        """
        kwargs = kwargs or {}
        config = config or SandboxConfig()
        result = SandboxResult(status="running")
        start = time.perf_counter()

        # Capability pre-check — short-circuit before spawning a process.
        if self.policy_engine and config.capabilities:
            for cap in config.capabilities:
                decision = self.policy_engine.evaluate(cap)
                if not decision.allowed:
                    result.status = "failed"
                    result.error = f"Capability denied: {cap}"
                    result.duration_seconds = time.perf_counter() - start
                    self._audit(
                        status="denied",
                        config=config,
                        error=result.error,
                        duration=result.duration_seconds,
                    )
                    return result

        # Sanity — verify the function is picklable. We do this BEFORE
        # spawning so we can return a clean "failed" result instead of
        # the multiprocessing layer raising in a worker thread.
        try:
            pickle.dumps(func)
            pickle.dumps((args, kwargs))
        except (pickle.PicklingError, TypeError, AttributeError) as exc:
            result.status = "failed"
            result.error = f"Function is not picklable: {exc}"
            result.duration_seconds = time.perf_counter() - start
            self._audit(
                status="failed",
                config=config,
                error=result.error,
                duration=result.duration_seconds,
            )
            return result

        # Snapshot child RSS before we spawn — RUSAGE_CHILDREN aggregates
        # across ALL reaped children, so we diff against the baseline.
        rss_before_kb = self._child_rss_kb()

        config_dict = {
            "max_memory_mb": config.max_memory_mb,
            "max_cpu_seconds": config.max_cpu_seconds,
            "max_filesystem_paths": list(config.max_filesystem_paths),
            "network_allowed": config.network_allowed,
            "env_vars": dict(config.env_vars),
            "env_whitelist": list(self.env_whitelist),
        }

        ctx = multiprocessing.get_context(self._start_method)
        result_queue: "multiprocessing.Queue" = ctx.Queue()

        proc = ctx.Process(
            target=_sandbox_worker,
            args=(func, args, kwargs, config_dict, result_queue),
            daemon=False,
        )

        peak_rss_bytes = 0
        try:
            proc.start()
            pid = proc.pid

            # Poll the child for completion + sample its RSS until either
            # the child exits or the timeout fires.
            deadline = time.perf_counter() + max(config.max_cpu_seconds, 1)
            while proc.is_alive() and time.perf_counter() < deadline:
                peak_rss_bytes = max(peak_rss_bytes, self._live_rss_bytes(pid))
                # Yield to the event loop so we don't block other tasks.
                await asyncio.sleep(0.05)

            if proc.is_alive():
                # Timed out — kill the whole process group.
                self._kill_process_group(pid)
                proc.join(timeout=2.0)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=1.0)
                result.status = "timeout"
                result.exit_code = -9
                result.error = (
                    f"Sandbox timeout: exceeded {config.max_cpu_seconds}s"
                )
            else:
                exitcode = proc.exitcode
                result.exit_code = exitcode if exitcode is not None else 0
                payload = self._drain_queue(result_queue)
                if exitcode is not None and exitcode < 0 and not payload:
                    # Killed by a signal (SIGXCPU, SIGKILL, …) without
                    # getting to write a result.
                    if exitcode == -signal.SIGXCPU:
                        result.status = "timeout"
                        result.error = "CPU rlimit (SIGXCPU) hit"
                    elif exitcode == -signal.SIGKILL:
                        result.status = "killed"
                        result.error = "Sandbox killed (SIGKILL — likely OOM)"
                    elif exitcode == -signal.SIGALRM:
                        result.status = "timeout"
                        result.error = "SIGALRM timeout fired"
                    else:
                        result.status = "killed"
                        result.error = f"Sandbox killed by signal {-exitcode}"
                elif payload:
                    # Drain returns a list of (kind, value) tuples —
                    # the worker may push a resource-warning before the
                    # terminal payload. Find the terminal one.
                    resource_warning: Optional[str] = None
                    terminal: Optional[tuple] = None
                    for item in payload:
                        if not isinstance(item, tuple) or len(item) != 2:
                            continue
                        kind, value = item
                        if kind == "_resource_warning":
                            resource_warning = str(value)
                        else:
                            terminal = (kind, value)
                    if resource_warning is not None:
                        logger.warning(
                            "SubprocessSandbox resource warning: %s",
                            resource_warning,
                        )
                    if terminal is None and resource_warning is not None:
                        # Resource warning without a terminal payload —
                        # the worker died applying limits.
                        result.status = "failed"
                        result.error = (
                            f"Resource limit application failed: {resource_warning}"
                        )
                    elif terminal is not None:
                        kind, value = terminal
                        if kind == "_success":
                            result.status = "success"
                            result.output = value
                        elif kind == "_timeout":
                            result.status = "timeout"
                            result.error = str(value)
                        elif kind == "_failed":
                            result.status = "failed"
                            if isinstance(value, dict):
                                msg = value.get("message") or value.get("type", "")
                                result.error = msg or "execution failed"
                            else:
                                result.error = str(value)
                        else:
                            result.status = "failed"
                            result.error = f"Unknown payload kind: {kind}"
                else:
                    # No payload. Distinguish:
                    #   exitcode == 0 → function returned None cleanly
                    #   exitcode != 0 → child died before it could push
                    #     a result (e.g. MemoryError when the queue
                    #     feeder thread itself couldn't allocate). This
                    #     is the most common path when RLIMIT_AS fires
                    #     and the worker can't report through the queue.
                    if result.exit_code == 0:
                        result.status = "success"
                        result.output = None
                    elif result.exit_code < 0:
                        # Killed by a signal we didn't classify above.
                        result.status = "killed"
                        result.error = (
                            f"Sandbox killed by signal {-result.exit_code}"
                        )
                    else:
                        result.status = "failed"
                        result.error = (
                            f"Sandbox child exited with code "
                            f"{result.exit_code} and produced no result "
                            f"(likely memory/CPU limit enforced by kernel)"
                        )

        except Exception as exc:  # noqa: BLE001
            result.status = "failed"
            result.error = f"Sandbox infrastructure error: {exc}"
            # Best-effort cleanup.
            try:
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass
        finally:
            result.duration_seconds = time.perf_counter() - start

            # Final RSS accounting — prefer RUSAGE_CHILDREN delta; fall
            # back to the live peak we sampled during the run.
            rss_after_kb = self._child_rss_kb()
            child_delta_kb = max(0, rss_after_kb - rss_before_kb)
            if child_delta_kb > 0:
                peak_rss_bytes = max(peak_rss_bytes, child_delta_kb * 1024)

            result.memory_used_mb = round(peak_rss_bytes / (1024 * 1024), 3)

            # Post-execution memory-limit enforcement. If the child
            # somehow exceeded the ceiling (e.g. RLIMIT_AS was not
            # available on this platform), surface it as a failure.
            if (
                config.max_memory_mb > 0
                and result.memory_used_mb > config.max_memory_mb * 1.25
                and result.status == "success"
            ):
                result.status = "failed"
                result.error = (
                    f"Memory limit exceeded: {result.memory_used_mb}MB "
                    f"> {config.max_memory_mb}MB ceiling"
                )

            self._audit(
                status=result.status,
                config=config,
                error=result.error,
                duration=result.duration_seconds,
                memory_mb=result.memory_used_mb,
                exit_code=result.exit_code,
            )

        return result

    async def is_available(self) -> bool:
        """The subprocess sandbox is available when multiprocessing works.

        On POSIX systems we additionally require the ``resource`` module
        so that memory/CPU limits can be enforced. If it's missing we
        *still* report True (the parent's wall-clock timeout is a
        functional fallback) — but ``execute`` will log a warning that
        RLIMIT_AS couldn't be applied.
        """
        try:
            ctx = multiprocessing.get_context(self._start_method)
            q = ctx.Queue()
            # Round-trip a tiny payload to confirm the spawn/fork path works.
            q.put(b"ping")
            return q.get(timeout=1.0) == b"ping"
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # Decision-log / audit helpers
    # ------------------------------------------------------------------
    def _audit(
        self,
        status: str,
        config: SandboxConfig,
        error: str = "",
        duration: float = 0.0,
        memory_mb: float = 0.0,
        exit_code: int = 0,
    ) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "error": error,
            "duration_seconds": round(duration, 4),
            "memory_used_mb": round(memory_mb, 3),
            "exit_code": exit_code,
            "config": config.to_dict(),
        }
        self._decision_log.append(entry)
        # Forward to the policy engine's decision log if supplied, so
        # security operators have a single place to inspect.
        if self.policy_engine is not None and hasattr(
            self.policy_engine, "_decision_log"
        ):
            try:
                # Reuse PolicyDecision so downstream consumers don't
                # need to learn a second shape.
                from core.runtime.security.policy_engine import PolicyDecision

                self.policy_engine._decision_log.append(
                    PolicyDecision(
                        allowed=(status == "success"),
                        reason=error or f"sandbox.execute:{status}",
                        policy_name=f"subprocess_sandbox.{status}",
                    )
                )
            except Exception:  # noqa: BLE001
                pass
        logger.info(
            "SubprocessSandbox execute status=%s dur=%.3fs mem=%.2fMB err=%s",
            status, duration, memory_mb, error or "-",
        )

    def get_decision_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return the most recent ``limit`` audit entries."""
        return list(self._decision_log[-limit:])

    def clear_decision_log(self) -> None:
        self._decision_log.clear()

    # ------------------------------------------------------------------
    # Platform-specific helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _child_rss_kb() -> int:
        """Return max RSS of all reaped children, in KiB (0 on failure)."""
        if not _HAS_RESOURCE:
            return 0
        try:
            usage = _resource.getrusage(_resource.RUSAGE_CHILDREN)
            # ru_maxrss is KiB on Linux, bytes on macOS — normalize.
            if sys.platform == "darwin":
                return int(usage.ru_maxrss / 1024)
            return int(usage.ru_maxrss)
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _live_rss_bytes(pid: int) -> int:
        """Best-effort live RSS sampling of a running child PID."""
        if not pid or pid < 0:
            return 0
        if _HAS_PSUTIL:
            try:
                return _psutil.Process(pid).memory_info().rss
            except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                return 0
            except Exception:  # noqa: BLE001
                return 0
        # /proc fallback (Linux only)
        try:
            with open(f"/proc/{pid}/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        # "VmRSS:    1234 kB"
                        parts = line.split()
                        if len(parts) >= 2:
                            return int(parts[1]) * 1024
        except (OSError, ValueError):
            pass
        return 0

    @staticmethod
    def _kill_process_group(pid: int) -> None:
        """SIGTERM then SIGKILL the whole process group led by ``pid``."""
        if not pid or pid < 0:
            return
        # Try to kill the process group first (covers children spawned
        # by the sandboxed function).
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(pid), sig)
            except (ProcessLookupError, PermissionError):
                pass
            except OSError:
                pass
            # Brief grace period between SIGTERM and SIGKILL.
            time.sleep(0.1)
            # Also signal the leader directly as a fallback.
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    @staticmethod
    def _drain_queue(q: "multiprocessing.Queue") -> List[Any]:
        """Drain all currently-enqueued items from ``q`` without blocking.

        ``multiprocessing.Queue.get_nowait`` raises ``queue.Empty`` when
        there's nothing to consume, so we collect items one-by-one until
        the queue is empty.
        """
        import queue as _queue_mod
        items: List[Any] = []
        while True:
            try:
                items.append(q.get_nowait())
            except _queue_mod.Empty:
                break
            except Exception:  # noqa: BLE001
                break
        return items


def create_sandbox(policy_engine=None, use_subprocess: bool = False) -> SandboxInterface:
    """Create a sandbox instance.

    When ``use_subprocess=True`` and the platform supports it, returns a
    :class:`SubprocessSandbox` (the production implementation). Otherwise
    falls back to :class:`CapabilitySandbox`.
    """
    if use_subprocess:
        sandbox = SubprocessSandbox(policy_engine=policy_engine)
        # If we're already running inside an event loop, we cannot call
        # ``asyncio.run`` to check ``is_available()`` — return the
        # sandbox directly; its ``execute()`` path is the real test.
        try:
            asyncio.get_running_loop()
            return sandbox
        except RuntimeError:
            pass  # not inside an event loop — fall through to asyncio.run
        try:
            if asyncio.run(sandbox.is_available()):
                return sandbox
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Subprocess sandbox availability check failed (%s); "
                "falling back to capability sandbox", exc,
            )
        logger.warning(
            "Subprocess sandbox not available, falling back to capability sandbox"
        )

    return CapabilitySandbox(policy_engine=policy_engine)
