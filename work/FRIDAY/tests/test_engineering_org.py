"""Tests for the engineering organization."""
import asyncio
import pytest

from core.engineering_org import (
    EngineeringOrg, Lead, LeadRole, get_engineering_org,
)
from core.task_system import TaskStatus, TaskPriority, TaskPhase


@pytest.fixture()
def org(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    import core.task_system
    import core.knowledge_base
    import core.validation_pipeline
    import core.engineering_org
    core.task_system._queue = None
    core.knowledge_base._kb = None
    core.validation_pipeline._pipeline = None
    core.engineering_org._org = None

    # Patch the validation pipeline to skip slow checks during tests
    from core.validation_pipeline import ValidationPipeline, ValidationReport, CheckStatus
    class FastPipeline(ValidationPipeline):
        async def run(self, skip_tests=True):
            report = ValidationReport()
            report.overall_status = CheckStatus.PASSED
            return report

    monkeypatch.setattr("core.engineering_org.get_validation_pipeline", lambda: FastPipeline())

    org = EngineeringOrg()
    yield org

    core.task_system._queue = None
    core.knowledge_base._kb = None
    core.validation_pipeline._pipeline = None
    core.engineering_org._org = None


class TestLeadInit:
    def test_all_leads_present(self, org):
        assert LeadRole.EXECUTIVE_ORCHESTRATOR in org.leads
        assert LeadRole.CHIEF_ARCHITECT in org.leads
        assert LeadRole.SECURITY_LEAD in org.leads
        assert LeadRole.TESTING_LEAD in org.leads
        assert LeadRole.DOCUMENTATION_LEAD in org.leads
        assert LeadRole.PERFORMANCE_LEAD in org.leads
        assert LeadRole.RESEARCH_LEAD in org.leads
        assert LeadRole.RELEASE_MANAGER in org.leads

    def test_leads_have_correct_phases(self, org):
        sec = org.leads[LeadRole.SECURITY_LEAD]
        assert TaskPhase.SECURITY in sec.phases

        test = org.leads[LeadRole.TESTING_LEAD]
        assert TaskPhase.VALIDATION in test.phases
        assert TaskPhase.QUALITY in test.phases


class TestTaskCreationAndAssignment:
    def test_create_and_assign_task_sets_assigned_to(self, org):
        task = asyncio.run(org.create_and_assign_task(
            title="Security fix",
            description="Fix MCP auth",
            phase=TaskPhase.SECURITY,
            priority=TaskPriority.CRITICAL,
        ))
        assert task.assigned_to == "security_lead"
        assert task.phase == TaskPhase.SECURITY
        assert task.priority == TaskPriority.CRITICAL

    def test_get_lead_for_task_returns_correct_lead(self, org):
        task = asyncio.run(org.create_and_assign_task(
            title="Test task",
            description="Description",
            phase=TaskPhase.PERFORMANCE,
        ))
        lead = asyncio.run(org.get_lead_for_task(task.id))
        assert lead is not None
        assert lead.role == LeadRole.PERFORMANCE_LEAD


class TestTaskExecution:
    def test_execute_task_completes_successfully(self, org):
        task = asyncio.run(org.create_and_assign_task(
            title="Simple task",
            description="Do something simple",
            phase=TaskPhase.QUALITY,
        ))

        async def simple_work(t):
            pass

        receipt = asyncio.run(org.execute_task(task.id, work_fn=simple_work))
        assert receipt is not None
        assert receipt.task_id == task.id

    def test_execute_task_with_work_error_fails(self, org):
        task = asyncio.run(org.create_and_assign_task(
            title="Failing task",
            description="This will fail",
            phase=TaskPhase.QUALITY,
            max_retries=1,
        ))

        async def failing_work(t):
            raise RuntimeError("Intentional failure")

        receipt = asyncio.run(org.execute_task(task.id, work_fn=failing_work))
        assert receipt is None  # task failed, no receipt

    def test_completed_task_increments_lead_counter(self, org):
        task = asyncio.run(org.create_and_assign_task(
            title="Counted task",
            description="Test counter",
            phase=TaskPhase.QUALITY,
        ))

        async def work(t):
            pass

        lead_before = org.leads[LeadRole.TESTING_LEAD].tasks_completed
        asyncio.run(org.execute_task(task.id, work_fn=work))
        lead_after = org.leads[LeadRole.TESTING_LEAD].tasks_completed
        assert lead_after == lead_before + 1


class TestReporting:
    def test_org_status_includes_all_sections(self, org):
        status = asyncio.run(org.get_org_status())
        assert "leads" in status
        assert "task_queue" in status
        assert "knowledge_base" in status
        assert "active_assignments" in status

    def test_org_status_has_all_leads(self, org):
        status = asyncio.run(org.get_org_status())
        assert len(status["leads"]) >= 8

    def test_report_completion_creates_kb_entry(self, org):
        from core.task_system import TaskReceipt
        receipt = TaskReceipt(
            task_id="test-id",
            title="Test task",
            phase="quality",
            completed_at="2026-01-01T00:00:00Z",
            duration_seconds=1.0,
            tests_passed=5,
            tests_failed=0,
            hash="test-hash",
        )
        asyncio.run(org.report_completion(receipt))

        from core.knowledge_base import EntryType
        entries = asyncio.run(org.knowledge_base.list_entries(type=EntryType.LESSON))
        assert any("Test task" in e.title for e in entries)
