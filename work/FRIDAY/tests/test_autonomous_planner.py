"""Tests for the autonomous planner."""
import asyncio
import pytest

from core.autonomous_planner import (
    AutonomousPlanner, Plan, Milestone, EffortEstimate,
)
from core.task_system import TaskStatus, TaskPriority, TaskPhase


@pytest.fixture()
def planner(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    import core.task_system
    core.task_system._queue = None
    p = AutonomousPlanner()
    yield p
    core.task_system._queue = None


class TestGoalClassification:
    def test_classify_security_goal(self, planner):
        assert planner._classify_goal("Add prompt injection defenses") == "security"
        assert planner._classify_goal("Fix auth vulnerability") == "security"

    def test_classify_testing_goal(self, planner):
        assert planner._classify_goal("Add unit tests for voice") == "testing"
        assert planner._classify_goal("Improve coverage") == "testing"

    def test_classify_performance_goal(self, planner):
        assert planner._classify_goal("Optimize vector search") == "performance"
        assert planner._classify_goal("Reduce latency") == "performance"

    def test_classify_refactoring_goal(self, planner):
        assert planner._classify_goal("Refactor brain.py") == "refactoring"
        assert planner._classify_goal("Decompose god class") == "refactoring"

    def test_classify_feature_goal(self, planner):
        assert planner._classify_goal("Add new voice command") == "feature"


class TestPlanCreation:
    def test_create_plan_generates_tasks(self, planner):
        plan = asyncio.run(planner.create_plan(
            goal="Add prompt injection defenses",
            context="User input is not sanitized",
        ))
        assert plan.goal == "Add prompt injection defenses"
        assert len(plan.task_ids) > 0
        assert len(plan.milestones) > 0
        assert plan.estimated_total_hours > 0

    def test_plan_tasks_have_dependencies(self, planner):
        plan = asyncio.run(planner.create_plan(
            goal="Add security feature",
        ))
        # Tasks should be sequential (each depends on previous)
        from core.task_system import get_task_queue
        queue = get_task_queue()
        for i, task_id in enumerate(plan.task_ids):
            task = asyncio.run(queue.get_task(task_id))
            if i > 0:
                assert len(task.dependencies) > 0

    def test_plan_critical_path_includes_all_tasks(self, planner):
        plan = asyncio.run(planner.create_plan(goal="Test feature"))
        assert len(plan.critical_path) == len(plan.task_ids)

    def test_plan_to_dict(self, planner):
        plan = asyncio.run(planner.create_plan(goal="Test"))
        d = plan.to_dict()
        assert "id" in d
        assert "goal" in d
        assert "milestones" in d
        assert "task_ids" in d


class TestReplanning:
    def test_replan_after_failure_creates_recovery_tasks(self, planner):
        plan = asyncio.run(planner.create_plan(goal="Some goal"))
        # Simulate failure of first task
        failed_id = plan.task_ids[0]
        revision = asyncio.run(planner.replan_after_failure(
            plan, failed_id, "Test failure reason"
        ))
        assert len(revision.task_ids) == 3  # diagnose, fix, verify
        assert revision.goal.startswith("Recovery:")

    def test_replan_tasks_are_sequential(self, planner):
        plan = asyncio.run(planner.create_plan(goal="Goal"))
        revision = asyncio.run(planner.replan_after_failure(
            plan, plan.task_ids[0], "Failure"
        ))
        from core.task_system import get_task_queue
        queue = get_task_queue()
        # Fix task depends on diagnose task
        fix_task = asyncio.run(queue.get_task(revision.task_ids[1]))
        assert revision.task_ids[0] in fix_task.dependencies


class TestPlanStatus:
    def test_plan_status_returns_progress(self, planner):
        plan = asyncio.run(planner.create_plan(goal="Status test"))
        status = asyncio.run(planner.get_plan_status(plan))
        assert status["total_tasks"] > 0
        assert status["completed"] == 0
        assert status["progress_pct"] == 0
        assert status["status"] == "active"


class TestEffortEstimate:
    def test_effort_hours(self):
        assert EffortEstimate.TRIVIAL.hours == 0.5
        assert EffortEstimate.SMALL.hours == 2.5
        assert EffortEstimate.MEDIUM.hours == 10
        assert EffortEstimate.LARGE.hours == 28
        assert EffortEstimate.XLARGE.hours == 60
