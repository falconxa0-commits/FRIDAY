"""Tests for LifecycleManager and MemoryRuntime (SWARM5-WAVE1B).

Covers:

- LifecycleManager: register, duplicate register, start, stop, restart,
  state transitions, invalid transitions, health check, stats, stop-all,
  FAILED handling, mark_degraded, unknown-component safety (13+ tests).
- MemoryRuntime: create pool, duplicate/invalid create, allocate,
  retrieve, deallocate, capacity limit, capacity-refusal on overwrite,
  usage stats, list pools, clear pool, delete pool, non-bytes refusal,
  health, stop, stats (12+ tests).
"""
from __future__ import annotations

import asyncio

import pytest

from core.runtime.lifecycle_manager import (
    ComponentHandle,
    ComponentState,
    LifecycleManager,
)
from core.runtime.memory_runtime import MemoryPool, MemoryRuntime


# ---------------------------------------------------------------------------
# Helper components
# ---------------------------------------------------------------------------
class _StubComponent:
    """Minimal component with start/stop/is_healthy hooks."""

    def __init__(self, *, healthy: bool = True, fail_start: bool = False,
                 fail_stop: bool = False, fail_health: bool = False):
        self.healthy = healthy
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.fail_health = fail_health
        self.started = False
        self.stopped = False

    async def start(self):
        if self.fail_start:
            raise RuntimeError("start failed")
        self.started = True

    async def stop(self):
        if self.fail_stop:
            raise RuntimeError("stop failed")
        self.stopped = True

    async def is_healthy(self):
        if self.fail_health:
            raise RuntimeError("health failed")
        return self.healthy


class _SyncComponent:
    """Component with sync (non-async) hooks — must still work."""

    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def is_healthy(self):
        return True


class _BareComponent:
    """Component with no hooks at all — must be tolerated."""


# ===========================================================================
# LifecycleManager
# ===========================================================================
class TestLifecycleManagerRegister:
    @pytest.mark.asyncio
    async def test_register_returns_initialized_handle(self):
        lm = LifecycleManager()
        handle = await lm.register_component("comp", _StubComponent())
        assert isinstance(handle, ComponentHandle)
        assert handle.name == "comp"
        assert handle.state is ComponentState.INITIALIZED
        assert handle.restart_count == 0
        assert handle.failure_count == 0
        assert handle.started_at == ""
        assert handle.created_at != ""

    @pytest.mark.asyncio
    async def test_register_duplicate_raises_value_error(self):
        lm = LifecycleManager()
        await lm.register_component("dup", _StubComponent())
        with pytest.raises(ValueError, match="already registered"):
            await lm.register_component("dup", _StubComponent())


class TestLifecycleManagerStartStop:
    @pytest.mark.asyncio
    async def test_start_moves_to_running_and_records_started_at(self):
        lm = LifecycleManager()
        comp = _StubComponent()
        await lm.register_component("c", comp)
        ok = await lm.start_component("c")
        assert ok is True
        state = await lm.get_component_state("c")
        assert state is ComponentState.RUNNING
        assert comp.started is True
        handle = lm._components["c"]
        assert handle.started_at != ""

    @pytest.mark.asyncio
    async def test_start_unknown_returns_false(self):
        lm = LifecycleManager()
        assert await lm.start_component("ghost") is False

    @pytest.mark.asyncio
    async def test_start_idempotent_when_running(self):
        lm = LifecycleManager()
        comp = _StubComponent()
        await lm.register_component("c", comp)
        assert await lm.start_component("c") is True
        # Second start: should be a no-op returning True (already RUNNING)
        assert await lm.start_component("c") is True

    @pytest.mark.asyncio
    async def test_stop_moves_to_stopped_and_records_stopped_at(self):
        lm = LifecycleManager()
        comp = _StubComponent()
        await lm.register_component("c", comp)
        await lm.start_component("c")
        ok = await lm.stop_component("c")
        assert ok is True
        state = await lm.get_component_state("c")
        assert state is ComponentState.STOPPED
        assert comp.stopped is True
        handle = lm._components["c"]
        assert handle.stopped_at != ""

    @pytest.mark.asyncio
    async def test_stop_unknown_returns_false(self):
        lm = LifecycleManager()
        assert await lm.stop_component("ghost") is False

    @pytest.mark.asyncio
    async def test_stop_idempotent_when_stopped(self):
        lm = LifecycleManager()
        comp = _StubComponent()
        await lm.register_component("c", comp)
        await lm.start_component("c")
        assert await lm.stop_component("c") is True
        assert await lm.stop_component("c") is True


class TestLifecycleManagerRestart:
    @pytest.mark.asyncio
    async def test_restart_increments_restart_count_and_reruns_hooks(self):
        lm = LifecycleManager()
        comp = _StubComponent()
        await lm.register_component("c", comp)
        await lm.start_component("c")
        comp.started = False  # reset
        comp.stopped = False
        ok = await lm.restart_component("c")
        assert ok is True
        handle = lm._components["c"]
        assert handle.restart_count == 1
        assert handle.state is ComponentState.RUNNING
        assert comp.stopped is True
        assert comp.started is True

    @pytest.mark.asyncio
    async def test_restart_unknown_returns_false(self):
        lm = LifecycleManager()
        assert await lm.restart_component("ghost") is False

    @pytest.mark.asyncio
    async def test_restart_works_from_stopped_state(self):
        lm = LifecycleManager()
        comp = _StubComponent()
        await lm.register_component("c", comp)
        await lm.start_component("c")
        await lm.stop_component("c")
        ok = await lm.restart_component("c")
        assert ok is True
        assert await lm.get_component_state("c") is ComponentState.RUNNING
        assert lm._components["c"].restart_count == 1


class TestLifecycleManagerTransitions:
    @pytest.mark.asyncio
    async def test_start_failure_transitions_to_failed(self):
        lm = LifecycleManager()
        comp = _StubComponent(fail_start=True)
        await lm.register_component("c", comp)
        ok = await lm.start_component("c")
        assert ok is False
        state = await lm.get_component_state("c")
        assert state is ComponentState.FAILED
        handle = lm._components["c"]
        assert handle.failure_count == 1
        assert handle.last_error != ""

    @pytest.mark.asyncio
    async def test_stop_failure_keeps_stopped_but_records_failure(self):
        lm = LifecycleManager()
        comp = _StubComponent(fail_stop=True)
        await lm.register_component("c", comp)
        await lm.start_component("c")
        ok = await lm.stop_component("c")
        # Stop still succeeds (we can't keep running it), but failure is recorded
        assert ok is True
        assert await lm.get_component_state("c") is ComponentState.STOPPED
        assert lm._components["c"].failure_count >= 1

    @pytest.mark.asyncio
    async def test_mark_degraded_from_running(self):
        lm = LifecycleManager()
        await lm.register_component("c", _StubComponent())
        await lm.start_component("c")
        assert await lm.mark_degraded("c") is True
        assert await lm.get_component_state("c") is ComponentState.DEGRADED

    @pytest.mark.asyncio
    async def test_mark_degraded_invalid_from_uninitialized(self):
        lm = LifecycleManager()
        await lm.register_component("c", _StubComponent())
        # INITIALIZED → DEGRADED is not allowed
        assert await lm.mark_degraded("c") is False
        assert await lm.get_component_state("c") is ComponentState.INITIALIZED

    @pytest.mark.asyncio
    async def test_mark_failed_from_any_state(self):
        lm = LifecycleManager()
        await lm.register_component("c", _StubComponent())
        # From INITIALIZED
        assert await lm.mark_failed("c", "boom") is True
        assert await lm.get_component_state("c") is ComponentState.FAILED
        assert lm._components["c"].last_error == "boom"


class TestLifecycleManagerHealth:
    @pytest.mark.asyncio
    async def test_health_check_all_healthy(self):
        lm = LifecycleManager()
        await lm.register_component("a", _StubComponent(healthy=True))
        await lm.register_component("b", _StubComponent(healthy=True))
        await lm.start_component("a")
        await lm.start_component("b")
        results = await lm.health_check_all()
        assert results == {"a": True, "b": True}
        assert await lm.is_healthy() is True

    @pytest.mark.asyncio
    async def test_health_check_all_detects_unhealthy(self):
        lm = LifecycleManager()
        await lm.register_component("good", _StubComponent(healthy=True))
        await lm.register_component("bad", _StubComponent(healthy=False))
        await lm.start_component("good")
        await lm.start_component("bad")
        results = await lm.health_check_all()
        assert results == {"good": True, "bad": False}
        assert await lm.is_healthy() is False

    @pytest.mark.asyncio
    async def test_health_check_all_handles_health_hook_exception(self):
        lm = LifecycleManager()
        await lm.register_component("c", _StubComponent(fail_health=True))
        await lm.start_component("c")
        results = await lm.health_check_all()
        assert results == {"c": False}

    @pytest.mark.asyncio
    async def test_health_check_all_unstarted_component_unhealthy(self):
        lm = LifecycleManager()
        await lm.register_component("c", _StubComponent())
        # Never started — state is INITIALIZED, should report False
        results = await lm.health_check_all()
        assert results == {"c": False}

    @pytest.mark.asyncio
    async def test_health_check_all_bare_component_healthy_when_running(self):
        # Bare component without is_healthy hook → assumed healthy when RUNNING
        lm = LifecycleManager()
        await lm.register_component("c", _BareComponent())
        await lm.start_component("c")
        results = await lm.health_check_all()
        assert results == {"c": True}

    @pytest.mark.asyncio
    async def test_is_healthy_true_when_no_components(self):
        lm = LifecycleManager()
        assert await lm.is_healthy() is True


class TestLifecycleManagerSyncAndBare:
    @pytest.mark.asyncio
    async def test_sync_hooks_supported(self):
        lm = LifecycleManager()
        comp = _SyncComponent()
        await lm.register_component("c", comp)
        assert await lm.start_component("c") is True
        assert comp.started is True
        assert await lm.stop_component("c") is True
        assert comp.stopped is True
        assert await lm.health_check_all() == {"c": False}  # stopped → False


class TestLifecycleManagerGetAllStates:
    @pytest.mark.asyncio
    async def test_get_all_states_returns_map(self):
        lm = LifecycleManager()
        await lm.register_component("a", _StubComponent())
        await lm.register_component("b", _StubComponent())
        await lm.start_component("a")
        states = await lm.get_all_states()
        assert states == {
            "a": ComponentState.RUNNING,
            "b": ComponentState.INITIALIZED,
        }

    @pytest.mark.asyncio
    async def test_get_component_state_unknown_returns_none(self):
        lm = LifecycleManager()
        assert await lm.get_component_state("ghost") is None


class TestLifecycleManagerStopAll:
    @pytest.mark.asyncio
    async def test_stop_stops_all_components(self):
        lm = LifecycleManager()
        c1 = _StubComponent()
        c2 = _StubComponent()
        await lm.register_component("a", c1)
        await lm.register_component("b", c2)
        await lm.start_component("a")
        await lm.start_component("b")
        await lm.stop()
        assert c1.stopped is True
        assert c2.stopped is True
        all_states = await lm.get_all_states()
        assert all(s is ComponentState.STOPPED for s in all_states.values())


class TestLifecycleManagerStats:
    @pytest.mark.asyncio
    async def test_stats_shape_and_counters(self):
        lm = LifecycleManager()
        await lm.register_component("a", _StubComponent())
        await lm.register_component("b", _StubComponent())
        await lm.start_component("a")
        await lm.stop_component("a")
        await lm.restart_component("a")  # restart_count → 1
        stats = lm.get_stats()
        assert stats["total_components"] == 2
        assert stats["total_starts"] == 2  # initial start + restart's start
        assert stats["total_stops"] == 1  # restart's stop
        assert stats["total_restarts"] == 1
        assert "state_counts" in stats
        assert isinstance(stats["components"], list)
        assert all("name" in c for c in stats["components"])


# ===========================================================================
# MemoryRuntime
# ===========================================================================
class TestMemoryRuntimePool:
    @pytest.mark.asyncio
    async def test_create_pool_returns_pool(self):
        mr = MemoryRuntime()
        pool = await mr.create_pool("ctx", 1024)
        assert isinstance(pool, MemoryPool)
        assert pool.name == "ctx"
        assert pool.max_size_bytes == 1024
        assert pool.current_size_bytes == 0
        assert pool.items == {}
        assert pool.created_at != ""

    @pytest.mark.asyncio
    async def test_create_pool_duplicate_raises(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1024)
        with pytest.raises(ValueError, match="already exists"):
            await mr.create_pool("ctx", 2048)

    @pytest.mark.asyncio
    async def test_create_pool_non_positive_max_raises(self):
        mr = MemoryRuntime()
        with pytest.raises(ValueError):
            await mr.create_pool("zero", 0)
        with pytest.raises(ValueError):
            await mr.create_pool("neg", -1)

    @pytest.mark.asyncio
    async def test_get_pool_returns_created_pool(self):
        mr = MemoryRuntime()
        pool = await mr.create_pool("ctx", 1024)
        fetched = await mr.get_pool("ctx")
        assert fetched is pool

    @pytest.mark.asyncio
    async def test_get_pool_unknown_returns_none(self):
        mr = MemoryRuntime()
        assert await mr.get_pool("ghost") is None

    @pytest.mark.asyncio
    async def test_list_pools_returns_all(self):
        mr = MemoryRuntime()
        await mr.create_pool("a", 100)
        await mr.create_pool("b", 200)
        pools = await mr.list_pools()
        assert len(pools) == 2
        names = {p.name for p in pools}
        assert names == {"a", "b"}


class TestMemoryRuntimeAllocate:
    @pytest.mark.asyncio
    async def test_allocate_stores_bytes_and_updates_size(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1024)
        ok = await mr.allocate("ctx", "k1", b"hello")
        assert ok is True
        pool = await mr.get_pool("ctx")
        assert pool.current_size_bytes == 5
        assert pool.items["k1"] == b"hello"

    @pytest.mark.asyncio
    async def test_allocate_unknown_pool_returns_false(self):
        mr = MemoryRuntime()
        assert await mr.allocate("ghost", "k", b"x") is False

    @pytest.mark.asyncio
    async def test_allocate_non_bytes_returns_false(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1024)
        assert await mr.allocate("ctx", "k", "not-bytes") is False
        assert await mr.allocate("ctx", "k2", 12345) is False

    @pytest.mark.asyncio
    async def test_allocate_exceeding_capacity_refused(self):
        mr = MemoryRuntime()
        await mr.create_pool("small", 10)
        assert await mr.allocate("small", "a", b"12345") is True  # 5/10
        assert await mr.allocate("small", "b", b"123456") is False  # would be 11/10
        # Pool unchanged after refusal
        pool = await mr.get_pool("small")
        assert pool.current_size_bytes == 5
        assert "b" not in pool.items

    @pytest.mark.asyncio
    async def test_allocate_exact_fit_succeeds(self):
        mr = MemoryRuntime()
        await mr.create_pool("exact", 5)
        assert await mr.allocate("exact", "k", b"abcde") is True
        assert (await mr.get_pool("exact")).current_size_bytes == 5

    @pytest.mark.asyncio
    async def test_allocate_overwrite_existing_within_capacity(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 100)
        await mr.allocate("ctx", "k", b"aaaa")  # 4 bytes
        ok = await mr.allocate("ctx", "k", b"bb")  # replace with 2 bytes
        assert ok is True
        pool = await mr.get_pool("ctx")
        assert pool.current_size_bytes == 2
        assert pool.items["k"] == b"bb"

    @pytest.mark.asyncio
    async def test_allocate_overwrite_exceeding_capacity_refused(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 10)
        await mr.allocate("ctx", "k", b"abc")  # 3/10
        # Replacing 3-byte value with 11-byte value would push to 11/10
        ok = await mr.allocate("ctx", "k", b"x" * 11)
        assert ok is False
        # Original value preserved
        pool = await mr.get_pool("ctx")
        assert pool.items["k"] == b"abc"
        assert pool.current_size_bytes == 3

    @pytest.mark.asyncio
    async def test_allocate_bytearray_accepted(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 100)
        ok = await mr.allocate("ctx", "k", bytearray(b"hi"))
        assert ok is True
        assert (await mr.retrieve("ctx", "k")) == b"hi"


class TestMemoryRuntimeRetrieve:
    @pytest.mark.asyncio
    async def test_retrieve_returns_stored_bytes(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1024)
        await mr.allocate("ctx", "k", b"value")
        assert await mr.retrieve("ctx", "k") == b"value"

    @pytest.mark.asyncio
    async def test_retrieve_unknown_key_returns_none(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1024)
        assert await mr.retrieve("ctx", "ghost") is None

    @pytest.mark.asyncio
    async def test_retrieve_unknown_pool_returns_none(self):
        mr = MemoryRuntime()
        assert await mr.retrieve("ghost", "k") is None


class TestMemoryRuntimeDeallocate:
    @pytest.mark.asyncio
    async def test_deallocate_removes_key_and_updates_size(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1024)
        await mr.allocate("ctx", "k", b"hello")
        ok = await mr.deallocate("ctx", "k")
        assert ok is True
        pool = await mr.get_pool("ctx")
        assert pool.current_size_bytes == 0
        assert "k" not in pool.items

    @pytest.mark.asyncio
    async def test_deallocate_unknown_key_returns_false(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1024)
        assert await mr.deallocate("ctx", "ghost") is False

    @pytest.mark.asyncio
    async def test_deallocate_unknown_pool_returns_false(self):
        mr = MemoryRuntime()
        assert await mr.deallocate("ghost", "k") is False


class TestMemoryRuntimeUsage:
    @pytest.mark.asyncio
    async def test_get_pool_usage_shape(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1000)
        await mr.allocate("ctx", "a", b"hello")  # 5 bytes
        await mr.allocate("ctx", "b", b"world")  # 5 bytes
        usage = await mr.get_pool_usage("ctx")
        assert usage["size_bytes"] == 10
        assert usage["max_bytes"] == 1000
        assert usage["item_count"] == 2
        assert usage["usage_pct"] == 1.0  # 10/1000 * 100

    @pytest.mark.asyncio
    async def test_get_pool_usage_unknown_returns_none(self):
        mr = MemoryRuntime()
        assert await mr.get_pool_usage("ghost") is None

    @pytest.mark.asyncio
    async def test_get_pool_usage_empty_pool(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1000)
        usage = await mr.get_pool_usage("ctx")
        assert usage == {
            "size_bytes": 0,
            "max_bytes": 1000,
            "usage_pct": 0.0,
            "item_count": 0,
        }


class TestMemoryRuntimeClearAndDelete:
    @pytest.mark.asyncio
    async def test_clear_pool_returns_count_and_empties(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1024)
        await mr.allocate("ctx", "a", b"1")
        await mr.allocate("ctx", "b", b"2")
        await mr.allocate("ctx", "c", b"3")
        cleared = await mr.clear_pool("ctx")
        assert cleared == 3
        pool = await mr.get_pool("ctx")
        assert pool.items == {}
        assert pool.current_size_bytes == 0

    @pytest.mark.asyncio
    async def test_clear_pool_unknown_returns_zero(self):
        mr = MemoryRuntime()
        assert await mr.clear_pool("ghost") == 0

    @pytest.mark.asyncio
    async def test_clear_pool_already_empty_returns_zero(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1024)
        assert await mr.clear_pool("ctx") == 0

    @pytest.mark.asyncio
    async def test_delete_pool_removes_it(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 1024)
        assert await mr.delete_pool("ctx") is True
        assert await mr.get_pool("ctx") is None
        # Second delete is a no-op
        assert await mr.delete_pool("ctx") is False


class TestMemoryRuntimeHealth:
    @pytest.mark.asyncio
    async def test_is_healthy_when_within_limits(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 100)
        await mr.allocate("ctx", "k", b"hello")
        assert await mr.is_healthy() is True

    @pytest.mark.asyncio
    async def test_is_healthy_when_no_pools(self):
        mr = MemoryRuntime()
        assert await mr.is_healthy() is True

    @pytest.mark.asyncio
    async def test_stop_clears_all_pools_and_marks_unhealthy(self):
        mr = MemoryRuntime()
        await mr.create_pool("a", 100)
        await mr.create_pool("b", 100)
        await mr.allocate("a", "k", b"hello")
        await mr.allocate("b", "k", b"world")
        await mr.stop()
        assert await mr.is_healthy() is False  # _running=False
        # Pools themselves are emptied
        assert (await mr.get_pool("a")).current_size_bytes == 0
        assert (await mr.get_pool("b")).current_size_bytes == 0


class TestMemoryRuntimeStats:
    @pytest.mark.asyncio
    async def test_stats_shape_and_counters(self):
        mr = MemoryRuntime()
        await mr.create_pool("ctx", 100)
        await mr.create_pool("embed", 200)
        await mr.allocate("ctx", "a", b"hello")  # 5 bytes
        await mr.allocate("ctx", "b", b"world")  # 5 bytes
        await mr.retrieve("ctx", "a")             # 1 retrieval
        await mr.deallocate("ctx", "b")           # 1 dealloc
        # Refuse one to bump refusals
        await mr.allocate("ctx", "c", b"x" * 200)
        stats = mr.get_stats()
        assert stats["pool_count"] == 2
        assert stats["total_allocated_bytes"] == 5  # only "a" remains (5 bytes)
        assert stats["total_capacity_bytes"] == 300
        assert stats["total_allocations"] == 2
        assert stats["total_retrievals"] == 1
        assert stats["total_deallocations"] == 1
        assert stats["total_refusals"] == 1
        assert len(stats["pools"]) == 2
