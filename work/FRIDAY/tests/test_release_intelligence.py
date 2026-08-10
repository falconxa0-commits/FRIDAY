"""Tests for release intelligence."""
import asyncio
import pytest

from core.release_intelligence import (
    ReleaseIntelligence, ReleaseIntelligenceReport,
    ReviewResult, ReviewType, get_release_intelligence,
)


@pytest.fixture()
def intel(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_PROJECT_ROOT", str(tmp_path))
    return ReleaseIntelligence(project_root=tmp_path)


class TestReviewResult:
    def test_to_dict(self):
        result = ReviewResult(
            type=ReviewType.SECURITY,
            score=90,
            status="pass",
            findings=["No issues"],
        )
        d = result.to_dict()
        assert d["type"] == "security"
        assert d["score"] == 90
        assert d["status"] == "pass"


class TestReleaseIntelligence:
    def test_assess_release_returns_report(self, intel):
        report = asyncio.run(intel.assess_release(
            version="3.3.0",
            release_type="beta",
        ))
        assert isinstance(report, ReleaseIntelligenceReport)
        assert report.version == "3.3.0"
        assert len(report.reviews) == 10

    def test_all_review_types_present(self, intel):
        report = asyncio.run(intel.assess_release("1.0.0"))
        review_types = {r.type for r in report.reviews}
        assert ReviewType.ARCHITECTURE in review_types
        assert ReviewType.SECURITY in review_types
        assert ReviewType.PERFORMANCE in review_types
        assert ReviewType.REGRESSION in review_types

    def test_scores_computed(self, intel):
        report = asyncio.run(intel.assess_release("1.0.0"))
        assert 0 <= report.production_readiness <= 100
        assert 0 <= report.deployment_confidence <= 100
        assert 0 <= report.rollback_confidence <= 100
        assert 0 <= report.release_confidence <= 100
        assert 0 <= report.risk_score <= 100

    def test_executive_summary_generated(self, intel):
        report = asyncio.run(intel.assess_release("1.0.0"))
        assert len(report.executive_summary) > 0
        assert "Production Readiness" in report.executive_summary

    def test_report_to_dict(self, intel):
        report = asyncio.run(intel.assess_release("1.0.0"))
        d = report.to_dict()
        assert "version" in d
        assert "reviews" in d
        assert "production_readiness" in d
        assert "executive_summary" in d

    def test_report_summary(self, intel):
        report = asyncio.run(intel.assess_release("1.0.0"))
        assert "Production Readiness" in report.summary
        assert "Release Confidence" in report.summary
