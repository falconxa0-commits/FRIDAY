"""Tests for FRIDAY Wave 2 runtime subsystems.

Covers:
    - ContextRuntime (create/get/update/delete/chain/resolve)
    - StateRuntime (create/transition/invalid transition/history/rules)
    - SessionRuntime (create/get/update/end/list/count)
    - WorkflowRuntime (create/execute/status/cancel/list/invalid steps)
"""
import asyncio

import pytest

from core.runtime.context_runtime import Context, ContextRuntime
from core.runtime.state_runtime import StateMachine, StateRuntime, StateTransition
from core.runtime.session_runtime import Session, SessionRuntime
from core.runtime.workflow_runtime import (
    Workflow,
    WorkflowRuntime,
    WorkflowStatus,
    WorkflowStatusEnum,
    WorkflowStep,
)


# ---------------------------------------------------------------------------
# ContextRuntime
# ---------------------------------------------------------------------------
class TestContextRuntime:
    @pytest.mark.asyncio
    async def test_create_context_returns_context_with_id(self):
        rt = ContextRuntime()
        ctx = await rt.create_context("ingest", {"source": "api"})
        assert isinstance(ctx, Context)
        assert ctx.id
        assert ctx.name == "ingest"
        assert ctx.data == {"source": "api"}
        assert ctx.parent_id is None
        assert ctx.children == []

    @pytest.mark.asyncio
    async def test_get_context_returns_created_context(self):
        rt = ContextRuntime()
        ctx = await rt.create_context("c", {"k": 1})
        fetched = await rt.get_context(ctx.id)
        assert fetched is ctx

    @pytest.mark.asyncio
    async def test_get_context_unknown_returns_none(self):
        rt = ContextRuntime()
        assert await rt.get_context("does-not-exist") is None

    @pytest.mark.asyncio
    async def test_update_context_merges_data(self):
        rt = ContextRuntime()
        ctx = await rt.create_context("c", {"a": 1})
        updated = await rt.update_context(ctx.id, {"b": 2})
        assert updated.data == {"a": 1, "b": 2}
        # Original ctx object is mutated in place
        assert ctx.data == {"a": 1, "b": 2}
        assert updated.updated_at >= ctx.created_at

    @pytest.mark.asyncio
    async def test_update_context_unknown_returns_none(self):
        rt = ContextRuntime()
        assert await rt.update_context("missing", {"x": 1}) is None

    @pytest.mark.asyncio
    async def test_delete_context_removes_it(self):
        rt = ContextRuntime()
        ctx = await rt.create_context("c")
        assert await rt.delete_context(ctx.id) is True
        assert await rt.get_context(ctx.id) is None
        assert await rt.delete_context(ctx.id) is False

    @pytest.mark.asyncio
    async def test_chain_contexts_inherits_parent_data(self):
        rt = ContextRuntime()
        parent = await rt.create_context("p", {"env": "prod"})
        child = await rt.create_context("c", {"region": "us"})
        assert await rt.chain_contexts(parent.id, child.id) is True

        assert child.parent_id == parent.id
        assert parent.id in child.parent_id
        assert child.id in parent.children
        # Child should now inherit parent data
        assert child.data["env"] == "prod"
        assert child.data["region"] == "us"

    @pytest.mark.asyncio
    async def test_chain_contexts_rejects_unknown_ids(self):
        rt = ContextRuntime()
        a = await rt.create_context("a")
        assert await rt.chain_contexts("nope", a.id) is False
        assert await rt.chain_contexts(a.id, "nope") is False
        assert await rt.chain_contexts(a.id, a.id) is False  # self-cycle

    @pytest.mark.asyncio
    async def test_chain_contexts_prevents_cycles(self):
        rt = ContextRuntime()
        a = await rt.create_context("a")
        b = await rt.create_context("b")
        c = await rt.create_context("c")
        await rt.chain_contexts(a.id, b.id)  # a → b
        await rt.chain_contexts(b.id, c.id)  # b → c
        # Now try to make c the parent of a (would create cycle a→b→c→a)
        assert await rt.chain_contexts(c.id, a.id) is False

    @pytest.mark.asyncio
    async def test_resolve_context_walks_parent_chain(self):
        rt = ContextRuntime()
        root = await rt.create_context("root", {"a": 1, "b": 2})
        mid = await rt.create_context("mid", {"b": 99, "c": 3})
        leaf = await rt.create_context("leaf", {"d": 4})
        await rt.chain_contexts(root.id, mid.id)
        await rt.chain_contexts(mid.id, leaf.id)

        resolved = await rt.resolve_context(leaf.id)
        # Leaf should inherit a=1, b=99 (mid overrides root), c=3, d=4
        assert resolved == {"a": 1, "b": 99, "c": 3, "d": 4}

    @pytest.mark.asyncio
    async def test_list_contexts_returns_all(self):
        rt = ContextRuntime()
        await rt.create_context("a")
        await rt.create_context("b")
        ctxs = await rt.list_contexts()
        assert len(ctxs) == 2

    @pytest.mark.asyncio
    async def test_delete_context_orphans_children(self):
        rt = ContextRuntime()
        parent = await rt.create_context("p")
        child = await rt.create_context("c")
        await rt.chain_contexts(parent.id, child.id)
        await rt.delete_context(parent.id)

        # Child still exists, but parent_id is now None
        orphan = await rt.get_context(child.id)
        assert orphan is not None
        assert orphan.parent_id is None

    @pytest.mark.asyncio
    async def test_context_get_stats(self):
        rt = ContextRuntime()
        a = await rt.create_context("a")
        b = await rt.create_context("b")
        await rt.chain_contexts(a.id, b.id)
        stats = rt.get_stats()
        assert stats["total_contexts"] == 2
        assert stats["root_contexts"] == 1
        assert stats["chained_contexts"] == 1


# ---------------------------------------------------------------------------
# StateRuntime
# ---------------------------------------------------------------------------
class TestStateRuntime:
    @pytest.mark.asyncio
    async def test_create_state_machine(self):
        rt = StateRuntime()
        sm = await rt.create_state_machine("ingest", initial_state="idle")
        assert isinstance(sm, StateMachine)
        assert sm.current_state == "idle"
        assert sm.name == "ingest"
        assert sm.history == []

    @pytest.mark.asyncio
    async def test_get_state(self):
        rt = StateRuntime()
        sm = await rt.create_state_machine("sm", "idle")
        assert await rt.get_state(sm.id) == "idle"

    @pytest.mark.asyncio
    async def test_get_state_unknown_returns_none(self):
        rt = StateRuntime()
        assert await rt.get_state("nope") is None

    @pytest.mark.asyncio
    async def test_add_transition_rule_and_transition(self):
        rt = StateRuntime()
        sm = await rt.create_state_machine("sm", "idle")
        assert await rt.add_transition_rule(sm.id, "idle", "running") is True
        assert await rt.add_transition_rule(sm.id, "running", "done") is True

        assert await rt.transition(sm.id, "running") is True
        assert await rt.get_state(sm.id) == "running"
        assert await rt.transition(sm.id, "done") is True
        assert await rt.get_state(sm.id) == "done"

    @pytest.mark.asyncio
    async def test_invalid_transition_returns_false(self):
        rt = StateRuntime()
        sm = await rt.create_state_machine("sm", "idle")
        await rt.add_transition_rule(sm.id, "idle", "running")
        # No rule idle → done
        assert await rt.transition(sm.id, "done") is False
        assert await rt.get_state(sm.id) == "idle"

    @pytest.mark.asyncio
    async def test_transition_unknown_machine_returns_false(self):
        rt = StateRuntime()
        assert await rt.transition("nope", "x") is False

    @pytest.mark.asyncio
    async def test_transition_same_state_is_noop(self):
        rt = StateRuntime()
        sm = await rt.create_state_machine("sm", "idle")
        assert await rt.transition(sm.id, "idle") is True
        # Recorded as a no-op transition
        history = await rt.get_history(sm.id)
        assert len(history) == 1
        assert history[0].from_state == "idle"
        assert history[0].to_state == "idle"

    @pytest.mark.asyncio
    async def test_get_history_records_all_transitions(self):
        rt = StateRuntime()
        sm = await rt.create_state_machine("sm", "idle")
        await rt.add_transition_rule(sm.id, "idle", "running")
        await rt.add_transition_rule(sm.id, "running", "done")
        await rt.transition(sm.id, "running")
        await rt.transition(sm.id, "done")

        history = await rt.get_history(sm.id)
        assert len(history) == 2
        assert isinstance(history[0], StateTransition)
        assert history[0].from_state == "idle"
        assert history[0].to_state == "running"
        assert history[1].from_state == "running"
        assert history[1].to_state == "done"

    @pytest.mark.asyncio
    async def test_get_history_unknown_machine_returns_empty(self):
        rt = StateRuntime()
        assert await rt.get_history("nope") == []

    @pytest.mark.asyncio
    async def test_add_transition_rule_unknown_machine(self):
        rt = StateRuntime()
        assert await rt.add_transition_rule("nope", "a", "b") is False

    @pytest.mark.asyncio
    async def test_state_get_stats(self):
        rt = StateRuntime()
        sm = await rt.create_state_machine("sm", "idle")
        await rt.add_transition_rule(sm.id, "idle", "running")
        await rt.transition(sm.id, "running")
        stats = rt.get_stats()
        assert stats["total_machines"] == 1
        assert stats["total_transitions_recorded"] == 1
        assert stats["total_rules"] == 1


# ---------------------------------------------------------------------------
# SessionRuntime
# ---------------------------------------------------------------------------
class TestSessionRuntime:
    @pytest.mark.asyncio
    async def test_create_session(self):
        rt = SessionRuntime()
        s = await rt.create_session(user_id="alice", data={"topic": "x"})
        assert isinstance(s, Session)
        assert s.user_id == "alice"
        assert s.data == {"topic": "x"}
        assert s.active is True
        assert s.created_at
        assert s.last_active

    @pytest.mark.asyncio
    async def test_create_session_defaults(self):
        rt = SessionRuntime()
        s = await rt.create_session()
        assert s.user_id == "default"
        assert s.data == {}

    @pytest.mark.asyncio
    async def test_get_session_touches_last_active(self):
        rt = SessionRuntime()
        s = await rt.create_session("alice")
        old_ts = s.last_active
        await asyncio.sleep(0.001)
        fetched = await rt.get_session(s.id)
        assert fetched is s
        assert fetched.last_active >= old_ts

    @pytest.mark.asyncio
    async def test_get_session_unknown_returns_none(self):
        rt = SessionRuntime()
        assert await rt.get_session("nope") is None

    @pytest.mark.asyncio
    async def test_update_session(self):
        rt = SessionRuntime()
        s = await rt.create_session("alice")
        updated = await rt.update_session(s.id, {"topic": "deploy"})
        assert updated.data == {"topic": "deploy"}

    @pytest.mark.asyncio
    async def test_update_session_unknown(self):
        rt = SessionRuntime()
        assert await rt.update_session("nope", {"x": 1}) is None

    @pytest.mark.asyncio
    async def test_end_session(self):
        rt = SessionRuntime()
        s = await rt.create_session("alice")
        assert await rt.end_session(s.id) is True
        assert s.active is False
        # Ending again is a no-op but still returns True (idempotent)
        assert await rt.end_session(s.id) is True

    @pytest.mark.asyncio
    async def test_end_session_unknown(self):
        rt = SessionRuntime()
        assert await rt.end_session("nope") is False

    @pytest.mark.asyncio
    async def test_list_active_sessions(self):
        rt = SessionRuntime()
        a = await rt.create_session("alice")
        b = await rt.create_session("bob")
        await rt.end_session(a.id)
        active = await rt.list_active_sessions()
        assert len(active) == 1
        assert active[0].id == b.id

    @pytest.mark.asyncio
    async def test_get_session_count(self):
        rt = SessionRuntime()
        await rt.create_session("alice")
        await rt.create_session("bob")
        await rt.create_session("carol")
        assert await rt.get_session_count() == 3
        assert await rt.get_active_session_count() == 3
        await rt.end_session((await rt.list_active_sessions())[0].id)
        assert await rt.get_active_session_count() == 2
        assert await rt.get_session_count() == 3  # ended sessions retained

    @pytest.mark.asyncio
    async def test_session_get_stats(self):
        rt = SessionRuntime()
        await rt.create_session("alice")
        await rt.create_session("alice")
        await rt.create_session("bob")
        stats = rt.get_stats()
        assert stats["total_sessions"] == 3
        assert stats["active_sessions"] == 3
        assert stats["unique_users"] == 2


# ---------------------------------------------------------------------------
# WorkflowRuntime
# ---------------------------------------------------------------------------
class TestWorkflowRuntime:
    @pytest.mark.asyncio
    async def test_create_workflow(self):
        rt = WorkflowRuntime()
        wf = await rt.create_workflow(
            "etl",
            steps=[
                WorkflowStep(id="a", name="A", func=None),
                WorkflowStep(id="b", name="B", func=None, depends_on=["a"]),
            ],
        )
        assert isinstance(wf, Workflow)
        assert wf.name == "etl"
        assert len(wf.steps) == 2
        assert wf.status.status == WorkflowStatusEnum.PENDING
        assert wf.graph is not None

    @pytest.mark.asyncio
    async def test_create_workflow_rejects_duplicate_step_ids(self):
        rt = WorkflowRuntime()
        with pytest.raises(ValueError, match="unique IDs"):
            await rt.create_workflow(
                "bad",
                steps=[
                    WorkflowStep(id="a", name="A"),
                    WorkflowStep(id="a", name="A2"),
                ],
            )

    @pytest.mark.asyncio
    async def test_create_workflow_rejects_unknown_dependency(self):
        rt = WorkflowRuntime()
        with pytest.raises(ValueError, match="unknown step"):
            await rt.create_workflow(
                "bad",
                steps=[
                    WorkflowStep(id="a", name="A", depends_on=["ghost"]),
                ],
            )

    @pytest.mark.asyncio
    async def test_execute_linear_workflow(self):
        rt = WorkflowRuntime()
        order: List[str] = []

        async def step_a():
            order.append("a")
            return 1

        async def step_b(deps):
            order.append("b")
            assert deps["a"] == 1
            return deps["a"] + 1

        async def step_c(deps):
            order.append("c")
            return deps["b"] + 1

        wf = await rt.create_workflow(
            "linear",
            steps=[
                WorkflowStep(id="a", name="A", func=step_a),
                WorkflowStep(id="b", name="B", func=step_b, depends_on=["a"]),
                WorkflowStep(id="c", name="C", func=step_c, depends_on=["b"]),
            ],
        )
        results = await rt.execute_workflow(wf.id)
        assert results == {"a": 1, "b": 2, "c": 3}
        assert order == ["a", "b", "c"]

        status = await rt.get_workflow_status(wf.id)
        assert status.status == WorkflowStatusEnum.COMPLETED
        assert status.step_states["a"] == "completed"
        assert status.step_states["b"] == "completed"
        assert status.step_states["c"] == "completed"

    @pytest.mark.asyncio
    async def test_execute_parallel_workflow(self):
        rt = WorkflowRuntime()

        async def step_a():
            await asyncio.sleep(0.01)
            return "a"

        async def step_b():
            await asyncio.sleep(0.01)
            return "b"

        async def step_c(deps):
            return deps["a"] + deps["b"]

        wf = await rt.create_workflow(
            "parallel",
            steps=[
                WorkflowStep(id="a", name="A", func=step_a),
                WorkflowStep(id="b", name="B", func=step_b),
                WorkflowStep(id="c", name="C", func=step_c,
                             depends_on=["a", "b"]),
            ],
        )
        results = await rt.execute_workflow(wf.id)
        assert results == {"a": "a", "b": "b", "c": "ab"}
        status = await rt.get_workflow_status(wf.id)
        assert status.status == WorkflowStatusEnum.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_workflow_with_failing_step(self):
        rt = WorkflowRuntime()

        async def good_step():
            return "ok"

        async def bad_step():
            raise RuntimeError("boom")

        async def dependent(deps):
            return "should-not-run"

        wf = await rt.create_workflow(
            "fail",
            steps=[
                WorkflowStep(id="good", name="Good", func=good_step),
                WorkflowStep(id="bad", name="Bad", func=bad_step,
                             depends_on=["good"]),
                WorkflowStep(id="downstream", name="Down",
                             func=dependent, depends_on=["bad"]),
            ],
        )
        results = await rt.execute_workflow(wf.id)
        # The good step still produced a result
        assert results.get("good") == "ok"
        assert "bad" not in results

        status = await rt.get_workflow_status(wf.id)
        assert status.status == WorkflowStatusEnum.FAILED
        assert "boom" in status.step_errors.get("bad", "")

    @pytest.mark.asyncio
    async def test_execute_workflow_unknown_returns_empty(self):
        rt = WorkflowRuntime()
        assert await rt.execute_workflow("nope") == {}

    @pytest.mark.asyncio
    async def test_get_workflow_status_unknown(self):
        rt = WorkflowRuntime()
        status = await rt.get_workflow_status("nope")
        assert status.status == WorkflowStatusEnum.FAILED
        assert "unknown workflow" in status.error

    @pytest.mark.asyncio
    async def test_cancel_pending_workflow(self):
        rt = WorkflowRuntime()
        wf = await rt.create_workflow(
            "w",
            steps=[WorkflowStep(id="a", name="A", func=None)],
        )
        assert await rt.cancel_workflow(wf.id) is True
        status = await rt.get_workflow_status(wf.id)
        assert status.status == WorkflowStatusEnum.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_unknown_workflow(self):
        rt = WorkflowRuntime()
        assert await rt.cancel_workflow("nope") is False

    @pytest.mark.asyncio
    async def test_cancel_completed_workflow_returns_false(self):
        rt = WorkflowRuntime()

        async def step_a():
            return 1

        wf = await rt.create_workflow(
            "w",
            steps=[WorkflowStep(id="a", name="A", func=step_a)],
        )
        await rt.execute_workflow(wf.id)
        # Already completed — cannot cancel
        assert await rt.cancel_workflow(wf.id) is False

    @pytest.mark.asyncio
    async def test_list_workflows(self):
        rt = WorkflowRuntime()
        await rt.create_workflow("a", steps=[WorkflowStep(id="s1", name="S1")])
        await rt.create_workflow("b", steps=[WorkflowStep(id="s1", name="S1")])
        wfs = await rt.list_workflows()
        assert len(wfs) == 2

    @pytest.mark.asyncio
    async def test_workflow_get_stats(self):
        rt = WorkflowRuntime()
        await rt.create_workflow("a", steps=[WorkflowStep(id="s1", name="S1")])
        stats = rt.get_stats()
        assert stats["total_workflows"] == 1
        assert stats["by_status"]["pending"] == 1

    @pytest.mark.asyncio
    async def test_workflow_sync_function_supported(self):
        """Sync callables should also work via the runner fallback."""
        rt = WorkflowRuntime()

        def sync_step():
            return 42

        wf = await rt.create_workflow(
            "sync",
            steps=[WorkflowStep(id="a", name="A", func=sync_step)],
        )
        results = await rt.execute_workflow(wf.id)
        assert results == {"a": 42}
