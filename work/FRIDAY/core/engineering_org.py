"""Engineering Organization — structured team of specialist agents.

Defines the engineering org structure with leads who coordinate
specialist workers. Each lead owns a domain (security, testing, etc.)
and is responsible for tasks in that domain.

Design principles:
    - **Clear ownership**: Every task has an assigned lead.
    - **Delegation**: Leads can spawn specialist workers for subtasks.
    - **Reporting**: Leads report completion with structured receipts.
    - **Accountability**: Every decision is logged to the knowledge base.

Org structure::

    ExecutiveOrchestrator
    ├── ChiefArchitect       — architecture, module boundaries
    ├── SecurityLead         — auth, authz, secrets, sandboxing
    ├── TestingLead          — unit/integration/e2e tests
    ├── DocumentationLead    — docs, ADRs, guides
    ├── PerformanceLead      — benchmarks, profiling, optimization
    ├── ResearchLead         — research pipeline, experiments
    ├── ReleaseManager       — releases, changelogs, versioning
    └── DevOpsLead           — CI/CD, observability, deployment

Each lead is a Python class that:
    1. Accepts a Task from the queue
    2. Validates it can perform the task (has the right tools)
    3. Delegates to specialist workers if needed
    4. Runs the validation pipeline
    5. Reports completion with a receipt (or failure with reason)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.task_system import (
    TaskQueue, Task, TaskStatus, TaskPriority, TaskPhase,
    TaskReceipt, get_task_queue,
)
from core.knowledge_base import (
    KnowledgeBase, EntryType, EntryStatus, get_knowledge_base,
)
from core.validation_pipeline import (
    ValidationPipeline, ValidationReport, CheckStatus, get_validation_pipeline,
)

logger = logging.getLogger("friday.engineering_org")


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
class LeadRole(str, Enum):
    EXECUTIVE_ORCHESTRATOR = "executive_orchestrator"
    CHIEF_ARCHITECT = "chief_architect"
    SECURITY_LEAD = "security_lead"
    TESTING_LEAD = "testing_lead"
    DOCUMENTATION_LEAD = "documentation_lead"
    PERFORMANCE_LEAD = "performance_lead"
    RESEARCH_LEAD = "research_lead"
    RELEASE_MANAGER = "release_manager"
    DEVOPS_LEAD = "devops_lead"
    REFACTORING_LEAD = "refactoring_lead"


# Map phases to leads
PHASE_TO_LEAD: Dict[TaskPhase, LeadRole] = {
    TaskPhase.ORGANIZATION: LeadRole.EXECUTIVE_ORCHESTRATOR,
    TaskPhase.WORK_MANAGEMENT: LeadRole.EXECUTIVE_ORCHESTRATOR,
    TaskPhase.KNOWLEDGE: LeadRole.DOCUMENTATION_LEAD,
    TaskPhase.VALIDATION: LeadRole.TESTING_LEAD,
    TaskPhase.DX: LeadRole.CHIEF_ARCHITECT,
    TaskPhase.RELEASE: LeadRole.RELEASE_MANAGER,
    TaskPhase.RESEARCH: LeadRole.RESEARCH_LEAD,
    TaskPhase.PERFORMANCE: LeadRole.PERFORMANCE_LEAD,
    TaskPhase.SECURITY: LeadRole.SECURITY_LEAD,
    TaskPhase.QUALITY: LeadRole.TESTING_LEAD,
}


# ---------------------------------------------------------------------------
# Lead
# ---------------------------------------------------------------------------
@dataclass
class Lead:
    """An engineering lead who coordinates work in a domain.

    Attributes:
        role: The lead's role enum.
        name: Human-readable name.
        phases: Task phases this lead owns.
        specialties: List of specialty areas.
        worker_count: Number of specialist workers available.
        tasks_completed: Lifetime count of tasks completed.
        tasks_failed: Lifetime count of tasks failed.
    """
    role: LeadRole
    name: str
    phases: List[TaskPhase]
    specialties: List[str] = field(default_factory=list)
    worker_count: int = 1
    tasks_completed: int = 0
    tasks_failed: int = 0

    def can_handle(self, task: Task) -> bool:
        """Check if this lead can handle a task."""
        return task.phase in self.phases

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role.value,
            "name": self.name,
            "phases": [p.value for p in self.phases],
            "specialties": self.specialties,
            "worker_count": self.worker_count,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
        }


# ---------------------------------------------------------------------------
# Engineering Organization
# ---------------------------------------------------------------------------
class EngineeringOrg:
    """The engineering organization that coordinates all leads.

    Usage::

        org = get_engineering_org()
        await org.create_and_assign_task(
            title="Fix MCP auth",
            phase=TaskPhase.SECURITY,
            priority=TaskPriority.CRITICAL,
            description="Add handshake token to MCP server",
        )
        # Lead picks it up, validates, completes with receipt
    """

    def __init__(
        self,
        task_queue: Optional[TaskQueue] = None,
        knowledge_base: Optional[KnowledgeBase] = None,
        validation_pipeline: Optional[ValidationPipeline] = None,
    ):
        self.task_queue = task_queue or get_task_queue()
        self.knowledge_base = knowledge_base or get_knowledge_base()
        self.validation = validation_pipeline or get_validation_pipeline()

        # Initialize leads
        self.leads: Dict[LeadRole, Lead] = self._init_leads()

        # Track active work
        self._active_assignments: Dict[str, LeadRole] = {}  # task_id → lead

    def _init_leads(self) -> Dict[LeadRole, Lead]:
        return {
            LeadRole.EXECUTIVE_ORCHESTRATOR: Lead(
                role=LeadRole.EXECUTIVE_ORCHESTRATOR,
                name="Executive Orchestrator",
                phases=[TaskPhase.ORGANIZATION, TaskPhase.WORK_MANAGEMENT],
                specialties=["coordination", "planning", "delegation"],
                worker_count=1,
            ),
            LeadRole.CHIEF_ARCHITECT: Lead(
                role=LeadRole.CHIEF_ARCHITECT,
                name="Chief Architect",
                phases=[TaskPhase.DX],
                specialties=["architecture", "module-boundaries", "refactoring"],
                worker_count=2,
            ),
            LeadRole.SECURITY_LEAD: Lead(
                role=LeadRole.SECURITY_LEAD,
                name="Security Lead",
                phases=[TaskPhase.SECURITY],
                specialties=["auth", "authz", "secrets", "sandboxing", "threat-modeling"],
                worker_count=2,
            ),
            LeadRole.TESTING_LEAD: Lead(
                role=LeadRole.TESTING_LEAD,
                name="Testing Lead",
                phases=[TaskPhase.VALIDATION, TaskPhase.QUALITY],
                specialties=["unit-tests", "integration-tests", "e2e", "regression"],
                worker_count=3,
            ),
            LeadRole.DOCUMENTATION_LEAD: Lead(
                role=LeadRole.DOCUMENTATION_LEAD,
                name="Documentation Lead",
                phases=[TaskPhase.KNOWLEDGE],
                specialties=["api-docs", "guides", "adrs", "runbooks"],
                worker_count=1,
            ),
            LeadRole.PERFORMANCE_LEAD: Lead(
                role=LeadRole.PERFORMANCE_LEAD,
                name="Performance Lead",
                phases=[TaskPhase.PERFORMANCE],
                specialties=["benchmarking", "profiling", "optimization"],
                worker_count=1,
            ),
            LeadRole.RESEARCH_LEAD: Lead(
                role=LeadRole.RESEARCH_LEAD,
                name="Research Lead",
                phases=[TaskPhase.RESEARCH],
                specialties=["research", "experimentation", "evaluation"],
                worker_count=1,
            ),
            LeadRole.RELEASE_MANAGER: Lead(
                role=LeadRole.RELEASE_MANAGER,
                name="Release Manager",
                phases=[TaskPhase.RELEASE],
                specialties=["versioning", "changelog", "release-notes"],
                worker_count=1,
            ),
            LeadRole.DEVOPS_LEAD: Lead(
                role=LeadRole.DEVOPS_LEAD,
                name="DevOps Lead",
                phases=[],  # DevOps supports all phases but doesn't own any
                specialties=["ci-cd", "observability", "deployment"],
                worker_count=1,
            ),
            LeadRole.REFACTORING_LEAD: Lead(
                role=LeadRole.REFACTORING_LEAD,
                name="Refactoring Lead",
                phases=[],  # Supports architect
                specialties=["dead-code-removal", "naming", "type-safety"],
                worker_count=1,
            ),
        }

    # ------------------------------------------------------------------
    # Task delegation
    # ------------------------------------------------------------------

    async def create_and_assign_task(
        self,
        title: str,
        description: str,
        phase: TaskPhase,
        priority: TaskPriority = TaskPriority.MEDIUM,
        dependencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Task:
        """Create a task and assign it to the appropriate lead."""
        task = await self.task_queue.create_task(
            title=title,
            description=description,
            phase=phase,
            priority=priority,
            dependencies=dependencies,
            assigned_to=PHASE_TO_LEAD.get(phase, LeadRole.EXECUTIVE_ORCHESTRATOR).value,
            metadata=metadata or {},
            max_retries=max_retries,
        )

        # Assign to lead
        lead_role = PHASE_TO_LEAD.get(phase, LeadRole.EXECUTIVE_ORCHESTRATOR)
        self._active_assignments[task.id] = lead_role
        logger.info(f"Task {task.id[:8]} assigned to {lead_role.value}: {title}")
        return task

    async def get_lead_for_task(self, task_id: str) -> Optional[Lead]:
        """Get the lead assigned to a task."""
        role = self._active_assignments.get(task_id)
        if role:
            return self.leads.get(role)
        # Infer from task phase
        task = await self.task_queue.get_task(task_id)
        if task:
            role = PHASE_TO_LEAD.get(task.phase, LeadRole.EXECUTIVE_ORCHESTRATOR)
            return self.leads.get(role)
        return None

    # ------------------------------------------------------------------
    # Task execution (with validation)
    # ------------------------------------------------------------------

    async def execute_task(
        self,
        task_id: str,
        work_fn: Optional[Callable] = None,
        skip_validation: bool = False,
    ) -> Optional[TaskReceipt]:
        """Execute a task: start, run work, validate, complete.

        Args:
            task_id: The task to execute.
            work_fn: Optional async callable that does the actual work.
                     If None, the task is marked complete without work
                     (useful for manual tasks).
            skip_validation: If True, skip the validation pipeline
                             (NOT recommended — should only be used
                             for documentation-only tasks).

        Returns:
            TaskReceipt if completed successfully, None otherwise.
        """
        lead = await self.get_lead_for_task(task_id)
        lead_name = lead.name if lead else "Unknown"

        # Start the task
        task = await self.task_queue.start_task(task_id, actor=lead_name)
        if not task:
            logger.error(f"Task {task_id[:8]} not found")
            return None

        if task.status == TaskStatus.BLOCKED:
            logger.warning(f"Task {task_id[:8]} is blocked, cannot execute")
            return None

        if task.status == TaskStatus.FAILED:
            logger.error(f"Task {task_id[:8]} has failed (max retries exceeded)")
            return None

        start_time = time.perf_counter()

        # Run the work function
        work_error = None
        if work_fn:
            try:
                await work_fn(task)
            except Exception as exc:
                work_error = str(exc)
                logger.error(f"Task {task_id[:8]} work failed: {exc}")

        if work_error:
            await self.task_queue.fail_task(
                task_id, actor=lead_name, error=work_error, can_retry=True
            )
            if lead:
                lead.tasks_failed += 1
            return None

        # Run validation
        tests_passed = 0
        tests_failed = 0
        if not skip_validation:
            try:
                report = await self.validation.run()
                tests_passed = sum(
                    1 for c in report.checks
                    if c.name == "unit_tests" and c.status == CheckStatus.PASSED
                )
                tests_failed = sum(
                    1 for c in report.checks
                    if c.status in (CheckStatus.FAILED, CheckStatus.ERROR)
                )
                if not report.passed:
                    await self.task_queue.fail_task(
                        task_id,
                        actor=lead_name,
                        error=f"Validation failed: {report.summary}",
                        can_retry=True,
                    )
                    if lead:
                        lead.tasks_failed += 1
                    return None
            except Exception as exc:
                logger.error(f"Validation pipeline error: {exc}")

        duration = time.perf_counter() - start_time

        # Complete the task
        receipt = await self.task_queue.complete_task(
            task_id,
            actor=lead_name,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            duration_seconds=duration,
            metadata={"lead": lead_name},
        )

        if receipt and lead:
            lead.tasks_completed += 1

        return receipt

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    async def get_org_status(self) -> Dict[str, Any]:
        """Get the full organization status report."""
        queue_stats = await self.task_queue.get_stats()
        kb_stats = await self.knowledge_base.get_stats()
        return {
            "leads": {
                role.value: lead.to_dict()
                for role, lead in self.leads.items()
            },
            "task_queue": queue_stats,
            "knowledge_base": kb_stats,
            "active_assignments": len(self._active_assignments),
        }

    async def get_backlog(self) -> List[Task]:
        """Get all ready tasks sorted by priority."""
        return await self.task_queue.get_ready_tasks()

    async def report_completion(self, receipt: TaskReceipt) -> None:
        """Record a task completion in the knowledge base."""
        await self.knowledge_base.create_entry(
            type=EntryType.LESSON,
            title=f"Task completed: {receipt.title}",
            summary=f"Phase: {receipt.phase}, Duration: {receipt.duration_seconds:.1f}s, "
                    f"Tests: {receipt.tests_passed} passed / {receipt.tests_failed} failed",
            content=f"Task {receipt.task_id} was completed by the engineering org.\n\n"
                    f"Receipt hash: {receipt.hash[:32]}...\n"
                    f"Completed at: {receipt.completed_at}\n"
                    f"Duration: {receipt.duration_seconds:.2f}s\n"
                    f"Tests passed: {receipt.tests_passed}\n"
                    f"Tests failed: {receipt.tests_failed}\n",
            tags=["task-completion", receipt.phase],
            related_tasks=[receipt.task_id],
            metadata={"receipt_hash": receipt.hash},
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_org: Optional[EngineeringOrg] = None


def get_engineering_org(instance=None) -> EngineeringOrg:
    """Get the singleton EngineeringOrg instance.

    Args:
        instance: Optional instance to inject. When provided, the
            singleton is replaced with this instance. This is primarily
            intended for tests to substitute mock/fake instances.
    """
    global _org
    if instance is not None:
        _org = instance
    if _org is None:
        _org = EngineeringOrg()
    return _org
