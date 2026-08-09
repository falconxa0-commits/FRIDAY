"""Tests for the validation pipeline."""
import asyncio
import pytest

from core.validation_pipeline import (
    ValidationPipeline, ValidationReport, CheckResult, CheckStatus,
    get_validation_pipeline,
)


@pytest.fixture()
def pipeline():
    return ValidationPipeline()


class TestCheckResult:
    def test_passed_check(self):
        result = CheckResult(
            name="test",
            status=CheckStatus.PASSED,
            duration_seconds=1.0,
            message="All good",
        )
        assert result.status == CheckStatus.PASSED

    def test_failed_check(self):
        result = CheckResult(
            name="test",
            status=CheckStatus.FAILED,
            duration_seconds=1.0,
            message="Failed",
        )
        assert result.status == CheckStatus.FAILED


class TestValidationReport:
    def test_empty_report_passes(self):
        report = ValidationReport()
        assert report.passed is True
        assert report.overall_status == CheckStatus.PASSED

    def test_failed_check_fails_report(self):
        report = ValidationReport()
        report.add(CheckResult("test", CheckStatus.PASSED, 1.0))
        report.add(CheckResult("test2", CheckStatus.FAILED, 1.0, "Error"))
        assert report.passed is False
        assert report.overall_status == CheckStatus.FAILED

    def test_summary_string(self):
        report = ValidationReport()
        report.add(CheckResult("a", CheckStatus.PASSED, 1.0))
        report.add(CheckResult("b", CheckStatus.FAILED, 1.0))
        report.add(CheckResult("c", CheckStatus.SKIPPED, 1.0))
        summary = report.summary
        assert "1 passed" in summary
        assert "1 failed" in summary
        assert "1 skipped" in summary


class TestValidationPipeline:
    def test_check_ledger_chain(self, pipeline):
        result = asyncio.run(pipeline._check_ledger_chain())
        assert result.status in (CheckStatus.PASSED, CheckStatus.FAILED, CheckStatus.ERROR)
        assert result.duration_seconds >= 0

    def test_check_imports(self, pipeline):
        result = asyncio.run(pipeline._check_imports())
        # Should pass — all modules importable
        assert result.status == CheckStatus.PASSED

    def test_run_returns_report(self, pipeline):
        report = asyncio.run(pipeline.run(skip_tests=True))
        assert isinstance(report, ValidationReport)
        assert len(report.checks) > 0
        assert report.total_duration_seconds > 0
