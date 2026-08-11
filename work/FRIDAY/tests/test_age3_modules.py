"""Tests for Wave 1 Age III modules."""
import asyncio
import pytest
from pathlib import Path

# --- Recommendation Engine ---
from core.recommendation_engine import (
    RecommendationEngine, Recommendation, RecommendationType, Priority,
)


class TestRecommendationEngine:
    def test_generate_recommendations(self, tmp_path):
        engine = RecommendationEngine(project_root=tmp_path)
        recs = asyncio.run(engine.generate_recommendations())
        assert isinstance(recs, list)

    def test_recommendation_to_dict(self):
        rec = Recommendation(
            title="Test",
            type=RecommendationType.SECURITY,
            priority=Priority.HIGH,
        )
        d = rec.to_dict()
        assert d["type"] == "security"
        assert d["priority"] == "high"

    def test_priority_weight(self):
        assert Priority.CRITICAL.weight < Priority.HIGH.weight
        assert Priority.HIGH.weight < Priority.MEDIUM.weight


# --- Health Monitor ---
from core.health_monitor import HealthMonitor, HealthSnapshot


class TestHealthMonitor:
    def test_snapshot_to_dict(self):
        s = HealthSnapshot(security_score=90.0, architecture_score=50.0)
        d = s.to_dict()
        assert d["security_score"] == 90.0
        assert "timestamp" in d

    def test_check_thresholds(self, tmp_path):
        monitor = HealthMonitor(project_root=tmp_path)
        snapshot = HealthSnapshot(
            security_score=50.0,
            architecture_score=30.0,
            engineering_health=10.0,
        )
        alerts = monitor._check_thresholds(snapshot)
        assert len(alerts) == 3  # All below thresholds


# --- Regression Detector ---
from core.regression_detector import RegressionDetector, Regression, RegressionSeverity


class TestRegressionDetector:
    def test_regression_to_dict(self):
        r = Regression(
            metric="security_score",
            previous_value=100.0,
            current_value=80.0,
            delta=-20.0,
            severity=RegressionSeverity.CRITICAL,
            message="Security regressed",
        )
        d = r.to_dict()
        assert d["severity"] == "critical"
        assert d["delta"] == -20.0


# --- Doc Validator ---
from core.doc_validator import DocValidator, DocValidationResult


class TestDocValidator:
    def test_validate_returns_results(self, tmp_path):
        validator = DocValidator(project_root=tmp_path)
        results = asyncio.run(validator.validate())
        assert isinstance(results, list)
        assert len(results) > 0

    def test_readme_check(self, tmp_path):
        (tmp_path / "README.md").write_text("# Test")
        validator = DocValidator(project_root=tmp_path)
        result = asyncio.run(validator._check_readme_exists())
        assert result.passed is True


# --- Benchmark Runner ---
from core.benchmark_runner import BenchmarkRunner, BenchmarkResult


class TestBenchmarkRunner:
    def test_result_to_dict(self):
        r = BenchmarkResult(name="test", duration_seconds=1.5, status="pass")
        d = r.to_dict()
        assert d["name"] == "test"
        assert d["duration_seconds"] == 1.5

    def test_run_nonexistent_benchmark(self, tmp_path):
        runner = BenchmarkRunner(project_root=tmp_path)
        result = asyncio.run(runner.run_benchmark("nonexistent.py"))
        assert result.status == "skip"


# --- Auto-Fix Pipeline ---
from core.auto_fix import AutoFixPipeline, FixProposal, FixType, FixRisk


class TestAutoFixPipeline:
    def test_proposal_to_dict(self):
        p = FixProposal(
            title="Test fix",
            type=FixType.MISSING_INIT,
            risk=FixRisk.SAFE,
        )
        d = p.to_dict()
        assert d["type"] == "missing_init"
        assert d["risk"] == "safe"

    def test_detect_missing_init(self, tmp_path):
        (tmp_path / "mypkg").mkdir()
        (tmp_path / "mypkg" / "mod1.py").write_text("x = 1")
        (tmp_path / "mypkg" / "mod2.py").write_text("y = 2")
        pipeline = AutoFixPipeline(project_root=tmp_path)
        proposals = asyncio.run(pipeline._detect_missing_init_files())
        assert any("mypkg" in p.file for p in proposals)

    def test_apply_missing_init_fix(self, tmp_path):
        (tmp_path / "mypkg").mkdir()
        (tmp_path / "mypkg" / "mod1.py").write_text("x = 1")
        (tmp_path / "mypkg" / "mod2.py").write_text("y = 2")
        pipeline = AutoFixPipeline(project_root=tmp_path)
        # Use generate_proposals so proposals are stored in the pipeline
        proposals = asyncio.run(pipeline.generate_proposals())
        init_proposals = [p for p in proposals if p.type == FixType.MISSING_INIT]
        assert len(init_proposals) > 0

        # Apply the fix
        result = asyncio.run(pipeline.apply_fix(init_proposals[0].id, approved_by="test"))
        assert result["success"] is True
        assert (tmp_path / "mypkg" / "__init__.py").exists()
