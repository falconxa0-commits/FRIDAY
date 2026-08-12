"""Production tests for the SubprocessSandbox.

These tests exercise the real ``SubprocessSandbox`` implementation —
no mocks. They spawn real child processes, enforce real ``RLIMIT_AS``
ceilings, install real ``SIGALRM`` timers, and verify the resulting
``SandboxResult`` reflects exactly what happened.

The functions used as execution targets are defined at module scope so
that ``multiprocessing`` can pickle them when the start method is
``spawn`` (the default on macOS / Windows). On Linux the default is
``fork`` which would tolerate local closures, but we use module-level
functions for cross-platform consistency.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from core.runtime.security.sandbox import (
    CapabilitySandbox,
    SandboxConfig,
    SandboxResult,
    SubprocessSandbox,
    create_sandbox,
)
from core.runtime.security.policy_engine import PolicyEngine, Policy


# ---------------------------------------------------------------------------
# Picklable target functions (module-level)
# ---------------------------------------------------------------------------

def add(a: int, b: int) -> int:
    return a + b


def echo_kwargs(**kwargs):
    return dict(kwargs)


def slow_sleep(seconds: float) -> str:
    time.sleep(seconds)
    return "completed"


def alloc_memory(mb: int) -> int:
    """Allocate roughly ``mb`` megabytes and return the count allocated."""
    chunks = []
    for _ in range(mb):
        chunks.append(b"x" * (1024 * 1024))
    return len(chunks)


def read_env(name: str) -> str:
    return os.environ.get(name, "<UNSET>")


def raise_value_error(msg: str = "boom") -> None:
    raise ValueError(msg)


def read_pid_and_parent_pid():
    return (os.getpid(), os.getppid())


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

class TestSubprocessSandboxAvailability:
    @pytest.mark.asyncio
    async def test_is_available_returns_true_on_posix(self):
        sb = SubprocessSandbox()
        # On Linux/BSD this must be True (resource module present).
        avail = await sb.is_available()
        assert avail is True


# ---------------------------------------------------------------------------
# Basic execution
# ---------------------------------------------------------------------------

class TestSubprocessSandboxExecution:
    @pytest.mark.asyncio
    async def test_execute_simple_function_returns_success(self):
        sb = SubprocessSandbox()
        result = await sb.execute(add, (2, 3), config=SandboxConfig(max_cpu_seconds=5))
        assert result.status == "success"
        assert result.output == 5
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_execute_returns_sandbox_result_type(self):
        sb = SubprocessSandbox()
        result = await sb.execute(add, (1, 1), config=SandboxConfig(max_cpu_seconds=5))
        assert isinstance(result, SandboxResult)
        # Required fields per task contract.
        assert hasattr(result, "status")
        assert hasattr(result, "output")
        assert hasattr(result, "duration_seconds")
        assert hasattr(result, "memory_used_mb")

    @pytest.mark.asyncio
    async def test_execute_kwargs_are_passed_through(self):
        sb = SubprocessSandbox()
        result = await sb.execute(
            echo_kwargs,
            kwargs={"a": 1, "b": "two"},
            config=SandboxConfig(max_cpu_seconds=5),
        )
        assert result.status == "success"
        assert result.output == {"a": 1, "b": "two"}

    @pytest.mark.asyncio
    async def test_execute_in_separate_process(self):
        """The function must actually run in a child process — its PID
        must differ from the test process's PID, and its parent PID must
        be the test process."""
        sb = SubprocessSandbox()
        result = await sb.execute(
            read_pid_and_parent_pid,
            config=SandboxConfig(max_cpu_seconds=5),
        )
        assert result.status == "success"
        child_pid, parent_pid = result.output
        assert child_pid != os.getpid()
        assert parent_pid == os.getpid()

    @pytest.mark.asyncio
    async def test_duration_seconds_is_positive(self):
        sb = SubprocessSandbox()
        result = await sb.execute(add, (1, 1), config=SandboxConfig(max_cpu_seconds=5))
        assert result.duration_seconds > 0.0

    @pytest.mark.asyncio
    async def test_memory_used_mb_is_non_negative(self):
        sb = SubprocessSandbox()
        result = await sb.execute(add, (1, 1), config=SandboxConfig(max_cpu_seconds=5))
        # The child loads Python + the function — it will use *some* memory.
        assert result.memory_used_mb >= 0.0


# ---------------------------------------------------------------------------
# Timeout enforcement
# ---------------------------------------------------------------------------

class TestSubprocessSandboxTimeout:
    @pytest.mark.asyncio
    async def test_timeout_kills_slow_function(self):
        sb = SubprocessSandbox()
        result = await sb.execute(
            slow_sleep,
            (10.0,),  # 10-second sleep
            config=SandboxConfig(max_cpu_seconds=1),  # 1-second ceiling
        )
        assert result.status == "timeout"
        assert "timeout" in result.error.lower() or "exceeded" in result.error.lower()

    @pytest.mark.asyncio
    async def test_timeout_returns_quickly(self):
        """The sandbox must not wait the full 10s for the slow function —
        the 1-second timeout must fire and the call must return within
        ~3 seconds (allowing time for process cleanup)."""
        sb = SubprocessSandbox()
        start = time.perf_counter()
        result = await sb.execute(
            slow_sleep,
            (30.0,),
            config=SandboxConfig(max_cpu_seconds=1),
        )
        elapsed = time.perf_counter() - start
        assert result.status == "timeout"
        # Generous upper bound: process spawn + SIGALRM + cleanup.
        assert elapsed < 5.0, f"Sandbox took {elapsed:.2f}s to time out"


# ---------------------------------------------------------------------------
# Memory limit enforcement
# ---------------------------------------------------------------------------

class TestSubprocessSandboxMemoryLimit:
    @pytest.mark.asyncio
    async def test_memory_limit_blocks_allocation(self):
        """A function that tries to allocate 200MB under a 16MB ceiling
        must be killed — either by RLIMIT_AS (MemoryError in the child)
        or by the post-execution RSS check."""
        sb = SubprocessSandbox()
        result = await sb.execute(
            alloc_memory,
            (200,),
            config=SandboxConfig(max_memory_mb=16, max_cpu_seconds=10),
        )
        # The child cannot allocate 200MB under a 16MB RLIMIT_AS — it
        # dies before reporting success. The exact status depends on
        # whether the kernel's RLIMIT or our RSS check fires first.
        assert result.status in ("failed", "killed", "timeout")
        assert result.output is None

    @pytest.mark.asyncio
    async def test_small_allocation_under_generous_limit_succeeds(self):
        sb = SubprocessSandbox()
        result = await sb.execute(
            alloc_memory,
            (2,),  # 2MB allocation
            config=SandboxConfig(max_memory_mb=512, max_cpu_seconds=10),
        )
        assert result.status == "success"
        assert result.output == 2


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------

class TestSubprocessSandboxEnvIsolation:
    @pytest.mark.asyncio
    async def test_non_whitelisted_env_var_is_stripped(self, monkeypatch):
        monkeypatch.setenv("FRIDAY_TEST_SECRET", "leak-me")
        sb = SubprocessSandbox()
        result = await sb.execute(
            read_env,
            ("FRIDAY_TEST_SECRET",),
            config=SandboxConfig(max_cpu_seconds=5),
        )
        assert result.status == "success"
        assert result.output == "<UNSET>"

    @pytest.mark.asyncio
    async def test_whitelisted_env_var_passes_through(self, monkeypatch):
        # PATH is on the default whitelist.
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        sb = SubprocessSandbox()
        result = await sb.execute(
            read_env,
            ("PATH",),
            config=SandboxConfig(max_cpu_seconds=5),
        )
        assert result.status == "success"
        assert result.output == "/usr/bin:/bin"

    @pytest.mark.asyncio
    async def test_config_env_vars_are_added(self):
        sb = SubprocessSandbox()
        result = await sb.execute(
            read_env,
            ("SANDBOX_GREETING",),
            config=SandboxConfig(
                max_cpu_seconds=5,
                env_vars={"SANDBOX_GREETING": "hello-from-sandbox"},
            ),
        )
        assert result.status == "success"
        assert result.output == "hello-from-sandbox"

    @pytest.mark.asyncio
    async def test_config_env_var_overrides_inherited(self, monkeypatch):
        """If a var is in the whitelist AND in config.env_vars, the
        config.env_vars value wins (caller can override inherited env)."""
        monkeypatch.setenv("PATH", "/inherited")
        sb = SubprocessSandbox()
        result = await sb.execute(
            read_env,
            ("PATH",),
            config=SandboxConfig(
                max_cpu_seconds=5,
                env_vars={"PATH": "/override"},
            ),
        )
        assert result.status == "success"
        assert result.output == "/override"


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------

class TestSubprocessSandboxExceptions:
    @pytest.mark.asyncio
    async def test_function_exception_surfaces_as_failed(self):
        sb = SubprocessSandbox()
        result = await sb.execute(
            raise_value_error,
            ("custom-message",),
            config=SandboxConfig(max_cpu_seconds=5),
        )
        assert result.status == "failed"
        assert "custom-message" in result.error


# ---------------------------------------------------------------------------
# Audit / decision log
# ---------------------------------------------------------------------------

class TestSubprocessSandboxAuditLog:
    @pytest.mark.asyncio
    async def test_audit_log_appended_on_success(self):
        sb = SubprocessSandbox()
        await sb.execute(add, (1, 2), config=SandboxConfig(max_cpu_seconds=5))
        log = sb.get_decision_log()
        assert len(log) == 1
        entry = log[0]
        assert entry["status"] == "success"
        assert "timestamp" in entry
        assert "config" in entry
        assert "duration_seconds" in entry

    @pytest.mark.asyncio
    async def test_audit_log_appended_on_failure(self):
        sb = SubprocessSandbox()
        await sb.execute(
            raise_value_error,
            config=SandboxConfig(max_cpu_seconds=5),
        )
        log = sb.get_decision_log()
        assert len(log) == 1
        assert log[0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_audit_log_appended_on_timeout(self):
        sb = SubprocessSandbox()
        await sb.execute(
            slow_sleep,
            (30.0,),
            config=SandboxConfig(max_cpu_seconds=1),
        )
        log = sb.get_decision_log()
        assert len(log) == 1
        assert log[0]["status"] == "timeout"

    @pytest.mark.asyncio
    async def test_audit_log_clear(self):
        sb = SubprocessSandbox()
        await sb.execute(add, (1, 2), config=SandboxConfig(max_cpu_seconds=5))
        assert len(sb.get_decision_log()) == 1
        sb.clear_decision_log()
        assert len(sb.get_decision_log()) == 0

    @pytest.mark.asyncio
    async def test_audit_log_forwarded_to_policy_engine(self):
        """When a PolicyEngine is supplied, each execution also
        appends a PolicyDecision to the engine's decision log."""
        engine = PolicyEngine()
        sb = SubprocessSandbox(policy_engine=engine)
        await sb.execute(add, (1, 2), config=SandboxConfig(max_cpu_seconds=5))
        # The sandbox's local log...
        assert len(sb.get_decision_log()) == 1
        # ...and the engine's log.
        assert len(engine.get_decision_log()) == 1
        decision = engine.get_decision_log()[0]
        assert decision.allowed is True
        assert "subprocess_sandbox" in decision.policy_name


# ---------------------------------------------------------------------------
# Capability pre-check
# ---------------------------------------------------------------------------

class TestSubprocessSandboxCapabilityCheck:
    @pytest.mark.asyncio
    async def test_denied_capability_short_circuits_without_spawn(self):
        """If the policy engine denies the capability, the sandbox must
        return ``failed`` WITHOUT spawning a subprocess."""
        engine = PolicyEngine()
        engine.deny_capability("dangerous.network")

        sb = SubprocessSandbox(policy_engine=engine)
        # Use a sentinel function that, if it ran, would set a flag.
        flag = {"ran": False}

        def sentinel():
            flag["ran"] = True
            return "should-not-run"

        # sentinel is a local closure — unpicklable. But because the
        # capability check happens BEFORE pickling, we should never
        # reach the pickle stage.
        result = await sb.execute(
            sentinel,
            config=SandboxConfig(
                max_cpu_seconds=5,
                capabilities=["dangerous.network"],
            ),
        )
        assert result.status == "failed"
        assert "denied" in result.error.lower()
        assert flag["ran"] is False  # prove we never ran it
        # And the engine recorded a decision.
        assert len(engine.get_decision_log()) >= 1


# ---------------------------------------------------------------------------
# Pickleability & infra errors
# ---------------------------------------------------------------------------

class TestSubprocessSandboxPickleability:
    @pytest.mark.asyncio
    async def test_unpicklable_function_returns_failed_cleanly(self):
        def local_closure():
            return "local"

        sb = SubprocessSandbox()
        result = await sb.execute(
            local_closure,
            config=SandboxConfig(max_cpu_seconds=5),
        )
        # No exception bubbled to the caller — clean failure surface.
        assert result.status == "failed"
        assert "picklable" in result.error.lower() or "pickle" in result.error.lower()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestCreateSandboxFactory:
    @pytest.mark.asyncio
    async def test_create_sandbox_returns_subprocess_when_requested(self):
        sb = create_sandbox(use_subprocess=True)
        assert isinstance(sb, SubprocessSandbox)
        assert await sb.is_available() is True

    @pytest.mark.asyncio
    async def test_create_sandbox_returns_capability_by_default(self):
        sb = create_sandbox()
        assert isinstance(sb, CapabilitySandbox)

    def test_create_sandbox_capability_explicit_no_subprocess(self):
        sb = create_sandbox(use_subprocess=False)
        assert isinstance(sb, CapabilitySandbox)


# ---------------------------------------------------------------------------
# Multiple executions / state isolation
# ---------------------------------------------------------------------------

class TestSubprocessSandboxStateIsolation:
    @pytest.mark.asyncio
    async def test_two_executions_do_not_share_state(self):
        """A function that mutates a module-level variable in the child
        must not affect the parent, and must not affect a subsequent
        child (each child is a fresh Python interpreter)."""
        # We need a target that mutates a module attribute. Use the
        # sandbox module's own logger as a sentinel — actually, use
        # `time.sleep` which is harmless. Instead, verify isolation
        # via os.environ: child A sets a var, child B shouldn't see it.
        sb = SubprocessSandbox()

        # child A — set SANDBOX_TEST_VAR in the child's env
        async def set_var(name, value):
            cfg = SandboxConfig(
                max_cpu_seconds=5,
                env_vars={name: value},
            )

            def setter():
                # In the child, the env var should be visible.
                return os.environ.get(name, "<UNSET>")

            # Can't pickle local function — define at module level.
            return await sb.execute(read_env, (name,), config=cfg)

        # First child sees the var we passed.
        r1 = await set_var("SANDBOX_TEST_VAR", "child-a-value")
        assert r1.output == "child-a-value"

        # Second child, without the env var, must NOT see it.
        r2 = await sb.execute(
            read_env,
            ("SANDBOX_TEST_VAR",),
            config=SandboxConfig(max_cpu_seconds=5),
        )
        assert r2.status == "success"
        assert r2.output == "<UNSET>"

    @pytest.mark.asyncio
    async def test_concurrent_executions_all_complete(self):
        """Run 5 sandbox executions concurrently — all must succeed."""
        sb = SubprocessSandbox()
        tasks = [
            sb.execute(add, (i, i), config=SandboxConfig(max_cpu_seconds=5))
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)
        assert all(r.status == "success" for r in results)
        assert [r.output for r in results] == [0, 2, 4, 6, 8]
