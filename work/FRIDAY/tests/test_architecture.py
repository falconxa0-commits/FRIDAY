"""Tests for architecture analysis."""
import pytest
from pathlib import Path

from core.architecture import (
    ArchitectureAnalyzer, ArchitectureReport, Layer, ADR,
    ModuleMetrics, BoundaryViolation, VALID_DEPENDENCIES,
    _module_to_layer,
)


@pytest.fixture()
def project(tmp_path):
    """Create a test project with proper layering."""
    for layer in ["core", "database", "integrations", "api", "cli"]:
        d = tmp_path / layer
        d.mkdir()
        (d / "__init__.py").write_text("")

    # Core module (no deps)
    (tmp_path / "core" / "base.py").write_text("""
class Base:
    pass
""")

    # Database module (depends on core)
    (tmp_path / "database" / "store.py").write_text("""
from core.base import Base

class Store(Base):
    pass
""")

    # API module (depends on core + database)
    (tmp_path / "api" / "routes.py").write_text("""
from core.base import Base
from database.store import Store

def get_route():
    return Store()
""")

    # CLI module (depends on api)
    (tmp_path / "cli" / "main.py").write_text("""
from api.routes import get_route
""")

    return tmp_path


class TestLayerDetection:
    def test_core_layer(self):
        assert _module_to_layer("core/brain.py") == Layer.CORE

    def test_api_layer(self):
        assert _module_to_layer("api/routes/chat.py") == Layer.API

    def test_unknown_layer(self):
        assert _module_to_layer("unknown/module.py") is None


class TestArchitectureAnalysis:
    def test_analyze_returns_report(self, project):
        analyzer = ArchitectureAnalyzer(project_root=project)
        report = analyzer.analyze()
        assert isinstance(report, ArchitectureReport)
        assert report.total_modules > 0

    def test_valid_dependencies_no_violations(self, project):
        analyzer = ArchitectureAnalyzer(project_root=project)
        report = analyzer.analyze()
        # All deps in our test project are valid (lower→higher)
        violations = [v for v in report.violations if not v.valid]
        assert len(violations) == 0

    def test_detects_boundary_violation(self, tmp_path):
        """Core depending on API is a violation."""
        for layer in ["core", "api"]:
            d = tmp_path / layer
            d.mkdir()
            (d / "__init__.py").write_text("")

        # Core imports from API — violation!
        (tmp_path / "core" / "bad.py").write_text("""
from api.routes import something
""")

        analyzer = ArchitectureAnalyzer(project_root=tmp_path)
        report = analyzer.analyze()
        violations = [v for v in report.violations if not v.valid]
        assert len(violations) > 0
        assert violations[0].source_layer == "core"
        assert violations[0].target_layer == "api"

    def test_metrics_calculated(self, project):
        analyzer = ArchitectureAnalyzer(project_root=project)
        report = analyzer.analyze()
        assert len(report.metrics) > 0
        for mod, metrics in report.metrics.items():
            assert isinstance(metrics, ModuleMetrics)
            assert 0 <= metrics.instability <= 1
            assert 0 <= metrics.distance_from_main <= 1

    def test_health_score_in_range(self, project):
        analyzer = ArchitectureAnalyzer(project_root=project)
        report = analyzer.analyze()
        assert 0 <= report.health_score <= 100

    def test_report_to_dict(self, project):
        analyzer = ArchitectureAnalyzer(project_root=project)
        report = analyzer.analyze()
        d = report.to_dict()
        assert "timestamp" in d
        assert "violations" in d
        assert "metrics" in d
        assert "health_score" in d


class TestValidDependencies:
    def test_core_has_no_valid_deps(self):
        assert len(VALID_DEPENDENCIES[Layer.CORE]) == 0

    def test_api_can_depend_on_core(self):
        assert Layer.CORE in VALID_DEPENDENCIES[Layer.API]

    def test_core_cannot_depend_on_api(self):
        assert Layer.API not in VALID_DEPENDENCIES[Layer.CORE]
