"""Autonomous Planner — breaks goals into executable work.

Takes a high-level goal and decomposes it into milestones, tasks,
and dependency graphs. Estimates effort, detects blockers, and
recommends execution order.

Design principles:
    - **Goal-driven**: Start with a goal, end with executable tasks.
    - **Dependency-aware**: Tasks are ordered by dependencies.
    - **Effort-estimated**: Each task has a time estimate.
    - **Re-plannable**: When a task fails, the plan can be revised.
    - **Critical-path aware**: Identifies the longest dependency chain.

Usage::

    planner = AutonomousPlanner()
    plan = await planner.create_plan(
        goal="Add prompt injection defenses",
        context="User input is not sanitized before LLM calls",
    )
    # plan.milestones → list of Milestone
    # plan.tasks → list of Task (created in the task queue)
    # plan.critical_path → list of task IDs on the critical path
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from core.task_system import (
    TaskQueue, Task, TaskStatus, TaskPriority, TaskPhase,
    get_task_queue,
)

logger = logging.getLogger("friday.planner")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
class EffortEstimate(str, Enum):
    """T-shirt sizing for effort estimation."""
    TRIVIAL = "trivial"      # < 1 hour
    SMALL = "small"          # 1-4 hours
    MEDIUM = "medium"        # 4-16 hours (1-2 days)
    LARGE = "large"          # 16-40 hours (1 week)
    XLARGE = "xlarge"        # 40+ hours (1+ weeks)

    @property
    def hours(self) -> float:
        return {
            "trivial": 0.5, "small": 2.5, "medium": 10,
            "large": 28, "xlarge": 60,
        }[self.value]


@dataclass
class Milestone:
    """A milestone in a plan — a group of related tasks."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    task_ids: List[str] = field(default_factory=list)
    effort: EffortEstimate = EffortEstimate.MEDIUM
    target_date: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["effort"] = self.effort.value
        return d


@dataclass
class Plan:
    """A complete execution plan for a goal."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal: str = ""
    context: str = ""
    milestones: List[Milestone] = field(default_factory=list)
    task_ids: List[str] = field(default_factory=list)
    critical_path: List[str] = field(default_factory=list)
    estimated_total_hours: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "context": self.context,
            "milestones": [m.to_dict() for m in self.milestones],
            "task_ids": self.task_ids,
            "critical_path": self.critical_path,
            "estimated_total_hours": self.estimated_total_hours,
            "created_at": self.created_at,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------
class AutonomousPlanner:
    """Decomposes goals into executable plans.

    The planner uses a knowledge-based approach:
    1. Match the goal against known patterns (security, testing, etc.)
    2. Generate milestones based on the pattern
    3. Generate tasks for each milestone
    4. Build dependency graph
    5. Calculate critical path
    6. Estimate total effort
    """

    # Pattern-based task templates
    TASK_TEMPLATES = {
        "security": [
            ("Analyze attack surface", TaskPhase.SECURITY, EffortEstimate.SMALL),
            ("Implement defense", TaskPhase.SECURITY, EffortEstimate.LARGE),
            ("Add security tests", TaskPhase.VALIDATION, EffortEstimate.MEDIUM),
            ("Update threat model", TaskPhase.KNOWLEDGE, EffortEstimate.SMALL),
            ("Security review", TaskPhase.SECURITY, EffortEstimate.MEDIUM),
        ],
        "testing": [
            ("Identify untested paths", TaskPhase.VALIDATION, EffortEstimate.SMALL),
            ("Write unit tests", TaskPhase.VALIDATION, EffortEstimate.MEDIUM),
            ("Write integration tests", TaskPhase.VALIDATION, EffortEstimate.MEDIUM),
            ("Add regression tests", TaskPhase.VALIDATION, EffortEstimate.SMALL),
            ("Verify coverage", TaskPhase.QUALITY, EffortEstimate.SMALL),
        ],
        "performance": [
            ("Profile current performance", TaskPhase.PERFORMANCE, EffortEstimate.SMALL),
            ("Identify bottlenecks", TaskPhase.PERFORMANCE, EffortEstimate.SMALL),
            ("Implement optimization", TaskPhase.PERFORMANCE, EffortEstimate.LARGE),
            ("Benchmark before/after", TaskPhase.PERFORMANCE, EffortEstimate.SMALL),
            ("Document findings", TaskPhase.KNOWLEDGE, EffortEstimate.SMALL),
        ],
        "refactoring": [
            ("Analyze current structure", TaskPhase.DX, EffortEstimate.SMALL),
            ("Design new structure", TaskPhase.DX, EffortEstimate.MEDIUM),
            ("Implement refactoring", TaskPhase.DX, EffortEstimate.LARGE),
            ("Update tests", TaskPhase.VALIDATION, EffortEstimate.MEDIUM),
            ("Update documentation", TaskPhase.KNOWLEDGE, EffortEstimate.SMALL),
        ],
        "feature": [
            ("Design feature", TaskPhase.DX, EffortEstimate.MEDIUM),
            ("Implement core logic", TaskPhase.DX, EffortEstimate.LARGE),
            ("Add tests", TaskPhase.VALIDATION, EffortEstimate.MEDIUM),
            ("Update documentation", TaskPhase.KNOWLEDGE, EffortEstimate.SMALL),
            ("Add to changelog", TaskPhase.RELEASE, EffortEstimate.TRIVIAL),
        ],
    }

    def __init__(self, task_queue: Optional[TaskQueue] = None):
        self.task_queue = task_queue or get_task_queue()

    def _classify_goal(self, goal: str) -> str:
        """Classify a goal into a pattern type."""
        goal_lower = goal.lower()
        if any(w in goal_lower for w in ["security", "auth", "vulnerability", "injection", "sandbox"]):
            return "security"
        if any(w in goal_lower for w in ["test", "coverage", "qa", "regression"]):
            return "testing"
        if any(w in goal_lower for w in ["performance", "latency", "memory", "speed", "optimize"]):
            return "performance"
        if any(w in goal_lower for w in ["refactor", "restructure", "decompose", "clean"]):
            return "refactoring"
        return "feature"

    async def create_plan(
        self,
        goal: str,
        context: str = "",
        priority: TaskPriority = TaskPriority.HIGH,
    ) -> Plan:
        """Create an execution plan for a goal."""
        pattern = self._classify_goal(goal)
        templates = self.TASK_TEMPLATES.get(pattern, self.TASK_TEMPLATES["feature"])

        plan = Plan(goal=goal, context=context)
        prev_task_id: Optional[str] = None
        milestone = Milestone(
            name=f"{pattern.title()} Implementation",
            description=f"Milestone for: {goal}",
        )

        # Create tasks with sequential dependencies
        for title, phase, effort in templates:
            dependencies = [prev_task_id] if prev_task_id else []
            task = await self.task_queue.create_task(
                title=f"{goal[:40]}: {title}" if len(goal) > 40 else f"{goal}: {title}",
                description=f"Goal: {goal}\nContext: {context}\nStep: {title}",
                phase=phase,
                priority=priority,
                dependencies=dependencies,
                metadata={"plan_id": plan.id, "effort": effort.value, "step": title},
            )
            plan.task_ids.append(task.id)
            milestone.task_ids.append(task.id)
            milestone.effort = max(milestone.effort, effort, key=lambda e: e.hours)
            prev_task_id = task.id

        plan.milestones.append(milestone)

        # Calculate critical path (sequential tasks are all on critical path)
        plan.critical_path = list(plan.task_ids)

        # Estimate total effort
        plan.estimated_total_hours = sum(
            self.TASK_TEMPLATES[pattern][i][2].hours
            for i in range(len(templates))
        )

        logger.info(
            f"Created plan {plan.id[:8]} for goal '{goal[:40]}': "
            f"{len(plan.task_ids)} tasks, ~{plan.estimated_total_hours}h estimated"
        )
        return plan

    async def replan_after_failure(
        self,
        plan: Plan,
        failed_task_id: str,
        failure_reason: str,
    ) -> Plan:
        """Re-plan after a task failure.

        Creates a revised plan with:
        1. A diagnostic task to understand the failure
        2. A fix task
        3. A verification task
        """
        revision = Plan(
            goal=f"Recovery: {plan.goal}",
            context=f"Task {failed_task_id[:8]} failed: {failure_reason}",
        )

        # Diagnostic task
        diag_task = await self.task_queue.create_task(
            title=f"Diagnose failure: {failure_reason[:60]}",
            description=f"Investigate why task {failed_task_id} failed: {failure_reason}",
            phase=TaskPhase.VALIDATION,
            priority=TaskPriority.HIGH,
            metadata={"plan_id": plan.id, "recovery": True},
        )
        revision.task_ids.append(diag_task.id)

        # Fix task (depends on diagnostic)
        fix_task = await self.task_queue.create_task(
            title=f"Fix: {failure_reason[:60]}",
            description=f"Fix the issue identified in {diag_task.id[:8]}",
            phase=TaskPhase.QUALITY,
            priority=TaskPriority.HIGH,
            dependencies=[diag_task.id],
            metadata={"plan_id": plan.id, "recovery": True},
        )
        revision.task_ids.append(fix_task.id)

        # Verification task
        verify_task = await self.task_queue.create_task(
            title=f"Verify fix for: {failure_reason[:60]}",
            description=f"Verify the fix in {fix_task.id[:8]} resolves the failure",
            phase=TaskPhase.VALIDATION,
            priority=TaskPriority.HIGH,
            dependencies=[fix_task.id],
            metadata={"plan_id": plan.id, "recovery": True},
        )
        revision.task_ids.append(verify_task.id)

        revision.critical_path = [diag_task.id, fix_task.id, verify_task.id]
        revision.estimated_total_hours = EffortEstimate.SMALL.hours + EffortEstimate.MEDIUM.hours + EffortEstimate.SMALL.hours

        logger.info(f"Created recovery plan {revision.id[:8]} for failure of {failed_task_id[:8]}")
        return revision

    async def get_plan_status(self, plan: Plan) -> Dict[str, Any]:
        """Get the current status of a plan."""
        tasks = []
        for task_id in plan.task_ids:
            task = await self.task_queue.get_task(task_id)
            if task:
                tasks.append(task)

        completed = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)
        in_progress = sum(1 for t in tasks if t.status == TaskStatus.IN_PROGRESS)
        blocked = sum(1 for t in tasks if t.status == TaskStatus.BLOCKED)
        pending = sum(1 for t in tasks if t.status == TaskStatus.PENDING)

        return {
            "plan_id": plan.id,
            "goal": plan.goal,
            "total_tasks": len(tasks),
            "completed": completed,
            "failed": failed,
            "in_progress": in_progress,
            "blocked": blocked,
            "pending": pending,
            "progress_pct": (completed / len(tasks) * 100) if tasks else 0,
            "estimated_hours": plan.estimated_total_hours,
            "status": "completed" if completed == len(tasks) else "active" if failed == 0 else "at_risk",
        }

    def detect_blockers(self, plan: Plan, tasks: List[Task]) -> List[str]:
        """Detect blockers in a plan."""
        blockers = []
        task_map = {t.id: t for t in tasks}

        for task in tasks:
            if task.status == TaskStatus.FAILED:
                blockers.append(f"Task {task.id[:8]} ({task.title}) has failed")
            elif task.status == TaskStatus.BLOCKED:
                # Check which dependency is blocking
                for dep_id in task.dependencies:
                    dep = task_map.get(dep_id)
                    if dep and dep.status != TaskStatus.COMPLETED:
                        blockers.append(
                            f"Task {task.id[:8]} blocked by {dep_id[:8]} ({dep.status.value})"
                        )

        return blockers


# ---------------------------------------------------------------------------
# Singleton
# -*-
_planner: Optional[AutonomousPlanner] = None


def get_planner() -> AutonomousPlanner:
    global _planner
    if _planner is None:
        _planner = AutonomousPlanner()
    return _planner
