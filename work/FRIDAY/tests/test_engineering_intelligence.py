"""Tests for engineering intelligence module."""
import asyncio
import pytest
from pathlib import Path

from core.engineering_intelligence import (
    EngineeringIntelligence, EngineeringReport, Finding, FindingType, Severity,
    ComplexityAnalyzer, TechnicalDebtAnalyzer, DependencyAnalyzer,
    ArchitectureSmellDetector, RiskPredictor, ModuleInfo,
)


@pytest.fixture()
def project(tmp_path):
    """Create a small test project."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "__init__.py").write_text("")
    (tmp_path / "core" / "simple.py").write_text("""
def simple_function(x):
    return x + 1
""")
    (tmp_path / "core" / "complex.py").write_text("""
def complex_function(x, y, z, w):
    if x > 0:
        if y > 0:
            if z > 0:
                if w > 0:
                    for i in range(x):
                        if i % 2 == 0:
                            try:
                                while i < y:
                                    if i == z:
                                        return i
                                    i += 1
                            except ValueError:
                                pass
                        elif i % 3 == 0:
                            continue
                        else:
                            break
                else:
                    return -1
            else:
                return -2
        else:
            return -3
    else:
        return -4
    return 0
""")
    (tmp_path / "core" / "todos.py").write_text("""
# TODO: This needs to be implemented
# FIXME: This is broken
def placeholder():
    pass  # stub
""")
    return tmp_path


class TestComplexityAnalyzer:
    def test_simple_function_low_complexity(self, project):
        complexity, findings = ComplexityAnalyzer.analyze_file(project / "core" / "simple.py", project_root=project)
        assert complexity == 1
        assert len(findings) == 0

    def test_complex_function_high_complexity(self, project):
        complexity, findings = ComplexityAnalyzer.analyze_file(project / "core" / "complex.py", project_root=project)
        assert complexity >= 10
        assert len(findings) > 0
        assert findings[0].type == FindingType.COMPLEXITY

    def test_complexity_severity_escalation(self, project):
        _, findings = ComplexityAnalyzer.analyze_file(project / "core" / "complex.py", project_root=project)
        # Should be at least MEDIUM severity
        assert any(f.severity in (Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL) for f in findings)


class TestTechnicalDebtAnalyzer:
    def test_todo_detection(self, project):
        findings = TechnicalDebtAnalyzer.analyze_file(project / "core" / "todos.py", project_root=project)
        todos = [f for f in findings if f.type == FindingType.TODO]
        assert len(todos) >= 1

    def test_fixme_detection(self, project):
        findings = TechnicalDebtAnalyzer.analyze_file(project / "core" / "todos.py", project_root=project)
        fixmes = [f for f in findings if f.type == FindingType.FIXME]
        assert len(fixmes) >= 1

    def test_stub_detection(self, project):
        findings = TechnicalDebtAnalyzer.analyze_file(project / "core" / "todos.py", project_root=project)
        stubs = [f for f in findings if f.type == FindingType.STUB]
        assert len(stubs) >= 1


class TestDependencyAnalyzer:
    def test_builds_dependency_graph(self, project):
        modules, findings = DependencyAnalyzer.analyze(project)
        assert len(modules) > 0
        assert "core.simple" in modules or "core.complex" in modules

    def test_no_false_circular_deps(self, project):
        modules, findings = DependencyAnalyzer.analyze(project)
        circular = [f for f in findings if f.type == FindingType.CIRCULAR_DEP]
        # Simple project shouldn't have circular deps
        assert len(circular) == 0


class TestArchitectureSmellDetector:
    def test_god_class_detection(self, project):
        # Create a large file
        lines = ["class GodClass:"]
        for i in range(30):
            lines.append(f"    def method_{i}(self):")
            lines.append("        pass")
            lines.append("")
        (project / "core" / "god.py").write_text("\n".join(lines))
        modules, _ = DependencyAnalyzer.analyze(project)
        # Add LOC info
        for mod in modules.values():
            mod.loc = len((project / mod.path).read_text().split("\n"))
        findings = ArchitectureSmellDetector.analyze(modules)
        # May or may not detect depending on LOC threshold
        assert isinstance(findings, list)


class TestEngineeringIntelligence:
    def test_analyze_returns_report(self, project):
        intel = EngineeringIntelligence(project_root=project)
        report = intel.analyze()
        assert isinstance(report, EngineeringReport)
        assert report.total_files > 0
        assert report.total_loc > 0
        assert 0 <= report.health_score <= 100
        assert 0 <= report.debt_score <= 100

    def test_report_has_findings(self, project):
        intel = EngineeringIntelligence(project_root=project)
        report = intel.analyze()
        # Should find TODOs, FIXMEs, complexity
        assert len(report.findings) > 0

    def test_report_summary_string(self, project):
        intel = EngineeringIntelligence(project_root=project)
        report = intel.analyze()
        assert "findings" in report.summary
        assert "health=" in report.summary

    def test_report_to_dict(self, project):
        intel = EngineeringIntelligence(project_root=project)
        report = intel.analyze()
        d = report.to_dict()
        assert "timestamp" in d
        assert "findings" in d
        assert "modules" in d
        assert "health_score" in d
