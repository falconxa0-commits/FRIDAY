"""Tests for Wave 1 finalization: Engineering Analytics + Quality Intelligence.

These are the two final Age III modules that close out Wave 1:
    - core.engineering_analytics.EngineeringAnalytics
    - core.quality_intelligence.QualityIntelligence

Test strategy:
    - Every test uses ``tmp_path`` so analyzers run on an empty project
      (fast, no real findings to wade through).
    - We assert on return types, shapes, ranges, and ordering rather
      than on specific numeric values, so the tests are robust to
      changes in the underlying analyzers.
    - Resilience is verified implicitly: every method runs even though
      several subsystems have no data on an empty tmp_path.
"""
import asyncio
from pathlib import Path

import pytest

from core.engineering_analytics import (
    EngineeringAnalytics,
    get_engineering_analytics,
)
from core.quality_intelligence import (
    QualityIntelligence,
    get_quality_intelligence,
)


# ===========================================================================
# EngineeringAnalytics
# ===========================================================================
class TestEngineeringAnalytics:
    """Tests for the unified dashboard aggregator."""

    def test_get_dashboard_returns_dict(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        dashboard = asyncio.run(analytics.get_dashboard())
        assert isinstance(dashboard, dict)
        assert "timestamp" in dashboard
        assert "overall_health" in dashboard
        assert "subsystem_status" in dashboard

    def test_dashboard_has_all_subsystems(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        dashboard = asyncio.run(analytics.get_dashboard())
        for key in (
            "task_queue", "knowledge_base", "health",
            "engineering", "architecture", "security",
        ):
            assert key in dashboard, f"Missing subsystem: {key}"
            assert isinstance(dashboard[key], dict)

    def test_dashboard_subsystem_status_keys(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        dashboard = asyncio.run(analytics.get_dashboard())
        status = dashboard["subsystem_status"]
        assert isinstance(status, dict)
        for key in (
            "task_queue", "knowledge_base", "health",
            "engineering", "architecture", "security",
        ):
            assert key in status
            assert status[key] in ("ok", "degraded")

    def test_dashboard_overall_health_is_numeric(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        dashboard = asyncio.run(analytics.get_dashboard())
        assert isinstance(dashboard["overall_health"], (int, float))
        assert 0.0 <= dashboard["overall_health"] <= 100.0

    def test_get_velocity_returns_dict(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        velocity = asyncio.run(analytics.get_velocity())
        assert isinstance(velocity, dict)
        assert "daily" in velocity
        assert "weekly" in velocity
        assert isinstance(velocity["daily"], list)
        assert isinstance(velocity["weekly"], list)

    def test_velocity_daily_has_14_buckets(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        velocity = asyncio.run(analytics.get_velocity())
        assert len(velocity["daily"]) == 14
        for entry in velocity["daily"]:
            assert "date" in entry
            assert "completed" in entry
            assert isinstance(entry["completed"], int)
            assert entry["completed"] >= 0

    def test_velocity_weekly_has_8_buckets(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        velocity = asyncio.run(analytics.get_velocity())
        assert len(velocity["weekly"]) == 8
        for entry in velocity["weekly"]:
            assert "week_start" in entry
            assert "completed" in entry

    def test_velocity_aggregates_present(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        velocity = asyncio.run(analytics.get_velocity())
        assert "total_completed" in velocity
        assert "average_per_day" in velocity
        assert "average_per_week" in velocity
        assert isinstance(velocity["total_completed"], int)

    def test_get_quality_trend_returns_dict(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        trend = asyncio.run(analytics.get_quality_trend())
        assert isinstance(trend, dict)
        assert "snapshots" in trend
        assert "latest" in trend
        assert "trend_direction" in trend
        assert isinstance(trend["snapshots"], list)
        assert trend["trend_direction"] in (
            "improving", "degrading", "stable", "unknown",
        )

    def test_get_risk_assessment_returns_list(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        risks = asyncio.run(analytics.get_risk_assessment())
        assert isinstance(risks, list)

    def test_risk_assessment_items_have_required_fields(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        risks = asyncio.run(analytics.get_risk_assessment())
        for risk in risks:
            assert "rank" in risk
            assert "severity" in risk
            assert "severity_score" in risk
            assert "source" in risk
            assert "title" in risk
            assert isinstance(risk["severity_score"], int)
            assert 0 <= risk["severity_score"] <= 100

    def test_risk_assessment_sorted_by_severity_desc(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        risks = asyncio.run(analytics.get_risk_assessment())
        for i in range(1, len(risks)):
            assert risks[i]["severity_score"] <= risks[i - 1]["severity_score"]

    def test_risk_assessment_ranks_assigned_sequentially(self, tmp_path):
        analytics = EngineeringAnalytics(project_root=tmp_path)
        risks = asyncio.run(analytics.get_risk_assessment())
        for i, risk in enumerate(risks, start=1):
            assert risk["rank"] == i

    def test_singleton_returns_instance(self):
        a1 = get_engineering_analytics()
        a2 = get_engineering_analytics()
        assert a1 is a2


# ===========================================================================
# QualityIntelligence
# ===========================================================================
class TestQualityIntelligence:
    """Tests for the multi-dimensional quality scoring engine."""

    def test_calculate_quality_score_in_range(self, tmp_path):
        qi = QualityIntelligence(project_root=tmp_path)
        score = asyncio.run(qi.calculate_quality_score())
        assert isinstance(score, int)
        assert 0 <= score <= 100

    def test_get_quality_breakdown_returns_dict(self, tmp_path):
        qi = QualityIntelligence(project_root=tmp_path)
        breakdown = asyncio.run(qi.get_quality_breakdown())
        assert isinstance(breakdown, dict)
        assert "timestamp" in breakdown
        assert "dimensions" in breakdown
        assert "overall_score" in breakdown
        assert "grade" in breakdown

    def test_quality_breakdown_has_all_dimensions(self, tmp_path):
        qi = QualityIntelligence(project_root=tmp_path)
        breakdown = asyncio.run(qi.get_quality_breakdown())
        dims = breakdown["dimensions"]
        for key in (
            "test_quality", "code_quality", "architecture_quality",
            "security_quality", "documentation_quality",
        ):
            assert key in dims
            dim = dims[key]
            assert "label" in dim
            assert "score" in dim
            assert "weight" in dim
            assert "details" in dim
            assert isinstance(dim["score"], (int, float))
            assert 0.0 <= dim["score"] <= 100.0

    def test_quality_breakdown_weights_sum_to_one(self, tmp_path):
        qi = QualityIntelligence(project_root=tmp_path)
        breakdown = asyncio.run(qi.get_quality_breakdown())
        total_weight = sum(d["weight"] for d in breakdown["dimensions"].values())
        assert abs(total_weight - 1.0) < 0.001

    def test_quality_breakdown_overall_consistent_with_dimensions(self, tmp_path):
        qi = QualityIntelligence(project_root=tmp_path)
        breakdown = asyncio.run(qi.get_quality_breakdown())
        expected = sum(
            d["score"] * d["weight"]
            for d in breakdown["dimensions"].values()
        )
        assert abs(breakdown["overall_score"] - expected) < 0.5

    def test_quality_overall_score_matches_calculate_score(self, tmp_path):
        qi = QualityIntelligence(project_root=tmp_path)
        breakdown = asyncio.run(qi.get_quality_breakdown())
        score = asyncio.run(qi.calculate_quality_score())
        assert score == int(round(breakdown["overall_score"]))

    def test_grade_boundaries(self, tmp_path):
        qi = QualityIntelligence(project_root=tmp_path)
        breakdown = asyncio.run(qi.get_quality_breakdown())
        score = breakdown["overall_score"]
        grade = breakdown["grade"]
        if score >= 90:
            assert grade == "A"
        elif score >= 80:
            assert grade == "B"
        elif score >= 70:
            assert grade == "C"
        elif score >= 60:
            assert grade == "D"
        else:
            assert grade == "F"

    def test_get_quality_recommendations_returns_list(self, tmp_path):
        qi = QualityIntelligence(project_root=tmp_path)
        recs = asyncio.run(qi.get_quality_recommendations())
        assert isinstance(recs, list)

    def test_quality_recommendations_have_required_fields(self, tmp_path):
        qi = QualityIntelligence(project_root=tmp_path)
        recs = asyncio.run(qi.get_quality_recommendations())
        for r in recs:
            assert "dimension" in r
            assert "label" in r
            assert "priority" in r
            assert "current_score" in r
            assert "target_score" in r
            assert "improvement_potential" in r
            assert "suggestion" in r
            assert r["priority"] in ("high", "medium", "low")

    def test_quality_recommendations_sorted_by_potential_desc(self, tmp_path):
        qi = QualityIntelligence(project_root=tmp_path)
        recs = asyncio.run(qi.get_quality_recommendations())
        for i in range(1, len(recs)):
            assert (
                recs[i]["improvement_potential"]
                <= recs[i - 1]["improvement_potential"]
            )

    def test_quality_recommendations_skip_near_perfect(self, tmp_path):
        """Dimensions with <1.0 improvement potential are filtered out."""
        qi = QualityIntelligence(project_root=tmp_path)
        recs = asyncio.run(qi.get_quality_recommendations())
        for r in recs:
            assert r["improvement_potential"] >= 1.0

    def test_singleton_returns_instance(self):
        q1 = get_quality_intelligence()
        q2 = get_quality_intelligence()
        assert q1 is q2
