"""Tests for the dashboard (Mission Control) API routes.

Covers all 8 endpoints:
    GET /api/dashboard
    GET /api/dashboard/health
    GET /api/dashboard/tasks
    GET /api/dashboard/findings
    GET /api/dashboard/architecture
    GET /api/dashboard/security
    GET /api/dashboard/performance
    GET /api/dashboard/releases

Plus: all endpoints require authentication (401 without token).

Slow subsystems (engineering intelligence, architecture, security ops,
validation pipeline) are mocked so the tests run in <1 second.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from core.engineering_intelligence import (
    EngineeringReport, Finding, FindingType, Severity,
)
from core.architecture import (
    ArchitectureReport, BoundaryViolation, ModuleMetrics, ADR,
)
from core.security_ops import (
    SecurityReport, SecurityFinding, SecurityFindingType, SecuritySeverity,
    SBOMEntry,
)
from core.validation_pipeline import ValidationReport, CheckStatus


# ---------------------------------------------------------------------------
# Fake report builders — return fully-populated reports so the dashboard
# has something to summarise.
# ---------------------------------------------------------------------------
def build_fake_eng_report() -> EngineeringReport:
    return EngineeringReport(
        project_root="/test",
        total_files=10,
        total_loc=500,
        health_score=85.0,
        debt_score=15.0,
        findings=[
            Finding(
                type=FindingType.COMPLEXITY,
                severity=Severity.HIGH,
                file="core/example.py",
                line=42,
                message="complex_function() has complexity 18",
                recommendation="Break into smaller functions",
                metadata={"function": "complex_function", "complexity": 18},
            ),
            Finding(
                type=FindingType.RISK_HOTSPOT,
                severity=Severity.CRITICAL,
                file="core/risk.py",
                line=0,
                message="Risk hotspot: 5 findings (risk score 12)",
                recommendation="Prioritize refactoring this file.",
                metadata={"risk_score": 12, "finding_count": 5},
            ),
            Finding(
                type=FindingType.TODO,
                severity=Severity.MEDIUM,
                file="core/todo.py",
                line=1,
                message="TODO: This needs to be implemented",
                recommendation="Resolve or convert to a tracked task.",
            ),
        ],
    )


def build_fake_arch_report() -> ArchitectureReport:
    return ArchitectureReport(
        total_modules=20,
        total_dependencies=45,
        violations=[
            BoundaryViolation(
                source_module="core.something",
                source_layer="core",
                target_module="api.routes.chat",
                target_layer="api",
                valid=False,
                message="core layer cannot depend on api layer",
            ),
        ],
        metrics={
            "core.brain": ModuleMetrics(
                name="core.brain",
                layer="core",
                afferent_coupling=5,
                efferent_coupling=2,
                instability=0.286,
                abstractness=0.5,
                distance_from_main=0.214,
            ),
            "api.routes.chat": ModuleMetrics(
                name="api.routes.chat",
                layer="api",
                afferent_coupling=1,
                efferent_coupling=8,
                instability=0.889,
                abstractness=0.5,
                distance_from_main=0.389,
            ),
        },
        adrs=[
            ADR(
                id="ADR-001",
                title="Use FastAPI",
                status="accepted",
                date="2024-01-01T00:00:00+00:00",
                context="Need web framework",
                decision="Use FastAPI",
                consequences="Async-first",
            ),
        ],
        health_score=92.0,
    )


def build_fake_sec_report() -> SecurityReport:
    return SecurityReport(
        project_root="/test",
        findings=[
            SecurityFinding(
                type=SecurityFindingType.HARDCODED_SECRET,
                severity=SecuritySeverity.HIGH,
                file="core/config.py",
                line=10,
                message="Possible API key (32+ chars)",
                remediation="Move to environment variable.",
            ),
            SecurityFinding(
                type=SecurityFindingType.VULNERABLE_DEP,
                severity=SecuritySeverity.CRITICAL,
                file="requirements.txt",
                line=0,
                message="urllib3==1.26.0 has CVE-2023-43804",
                remediation="Upgrade urllib3 to latest version.",
            ),
        ],
        sbom=[
            SBOMEntry(name="fastapi", version="0.110.0", source="pyproject.toml"),
            SBOMEntry(name="rich", version="13.0.0", source="pyproject.toml"),
            SBOMEntry(name="pydantic", version="2.0.0", source="pyproject.toml"),
        ],
        total_files_scanned=50,
        total_deps=3,
        security_score=88.0,
    )


class FastValidationPipeline:
    """Validation pipeline stub that doesn't run subprocesses."""

    async def run(self, skip_tests: bool = True) -> ValidationReport:
        report = ValidationReport()
        report.overall_status = CheckStatus.PASSED
        report.checks = []
        return report


# ---------------------------------------------------------------------------
# Singletons to reset before/after each test
# ---------------------------------------------------------------------------
_SINGLETONS = [
    "core.task_system",
    "core.knowledge_base",
    "core.release_pipeline",
    "core.engineering_intelligence",
    "core.architecture",
    "core.security_ops",
    "core.engineering_org",
    "core.validation_pipeline",
]


def _reset_singletons() -> None:
    import importlib
    for mod_name in _SINGLETONS:
        mod = importlib.import_module(mod_name)
        # Reset common singleton attribute names
        for attr in ("_queue", "_kb", "_pipeline", "_intelligence",
                     "_analyzer", "_sec_ops", "_org"):
            if hasattr(mod, attr):
                setattr(mod, attr, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with all slow subsystems mocked and isolated to tmp_path."""
    # Isolate engineering subsystems to tmp_path
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    _reset_singletons()

    # Mock engineering intelligence analyzer
    fake_intel = MagicMock()
    fake_intel.analyze.return_value = build_fake_eng_report()
    monkeypatch.setattr(
        "core.engineering_intelligence.get_engineering_intelligence",
        lambda: fake_intel,
    )

    # Mock architecture analyzer
    fake_arch = MagicMock()
    fake_arch.analyze.return_value = build_fake_arch_report()
    monkeypatch.setattr(
        "core.architecture.get_architecture_analyzer",
        lambda: fake_arch,
    )

    # Mock security operations
    fake_sec = MagicMock()
    fake_sec.scan.return_value = build_fake_sec_report()
    monkeypatch.setattr(
        "core.security_ops.get_security_operations",
        lambda: fake_sec,
    )

    # Fast validation pipeline — set the module-level singleton so both
    # core.validation_pipeline.get_validation_pipeline() and
    # core.engineering_org.get_validation_pipeline() (which calls through
    # to the same function) return our stub.
    fast_pipe = FastValidationPipeline()
    monkeypatch.setattr("core.validation_pipeline._pipeline", fast_pipe)

    # Clear the dashboard's TTL cache so each test sees fresh data
    from api.routes.dashboard import clear_cache
    clear_cache()

    from api.main import app
    with TestClient(app) as c:
        yield c

    clear_cache()
    _reset_singletons()


@pytest.fixture()
def auth_disabled():
    """Confirm auth is disabled in dev mode (set by conftest)."""
    import core.auth
    return bool(core.auth.FRIDAY_DEV_MODE and not core.auth.FRIDAY_API_TOKEN)


# ---------------------------------------------------------------------------
# Tests — each endpoint returns 200 with expected fields
# ---------------------------------------------------------------------------

class TestMainDashboard:
    """GET /api/dashboard — main summary."""

    def test_returns_200(self, client):
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200

    def test_returns_all_expected_sections(self, client):
        resp = client.get("/api/dashboard")
        data = resp.json()
        expected_keys = {
            "timestamp", "response_ms",
            "engineering_intelligence", "architecture", "security",
            "tasks", "knowledge_base", "engineering_org",
            "releases", "validation",
        }
        assert expected_keys.issubset(data.keys()), (
            f"Missing keys: {expected_keys - set(data.keys())}"
        )

    def test_engineering_intelligence_section(self, client):
        resp = client.get("/api/dashboard")
        ei = resp.json()["engineering_intelligence"]
        assert ei["available"] is True
        assert ei["health_score"] == 85.0
        assert ei["debt_score"] == 15.0
        assert ei["total_findings"] == 3
        assert "top_findings" in ei
        assert "findings_by_type" in ei
        assert "risk_hotspots" in ei

    def test_architecture_section(self, client):
        resp = client.get("/api/dashboard")
        arch = resp.json()["architecture"]
        assert arch["available"] is True
        assert arch["health_score"] == 92.0
        assert arch["violation_count"] == 1
        assert arch["total_modules"] == 20
        assert arch["adr_count"] == 1

    def test_security_section(self, client):
        resp = client.get("/api/dashboard")
        sec = resp.json()["security"]
        assert sec["available"] is True
        assert sec["security_score"] == 88.0
        assert sec["sbom_count"] == 3
        assert sec["total_findings"] == 2

    def test_response_ms_present(self, client):
        resp = client.get("/api/dashboard")
        assert "response_ms" in resp.json()
        assert isinstance(resp.json()["response_ms"], (int, float))


class TestHealthEndpoint:
    """GET /api/dashboard/health — fast health polling."""

    def test_returns_200(self, client):
        resp = client.get("/api/dashboard/health")
        assert resp.status_code == 200

    def test_returns_health_scores(self, client):
        resp = client.get("/api/dashboard/health")
        data = resp.json()
        for key in ("engineering_health", "architecture_health",
                    "security_score", "test_pass_rate", "overall"):
            assert key in data, f"Missing {key}"

    def test_scores_in_valid_range(self, client):
        resp = client.get("/api/dashboard/health")
        data = resp.json()
        # 0-100 for scores
        assert 0 <= data["engineering_health"] <= 100
        assert 0 <= data["architecture_health"] <= 100
        assert 0 <= data["security_score"] <= 100
        assert 0 <= data["overall"] <= 100
        # test_pass_rate is 0.0-1.0 (or None)
        tpr = data["test_pass_rate"]
        assert tpr is None or 0.0 <= tpr <= 1.0

    def test_overall_is_weighted_average(self, client):
        """overall should be between min and max of component scores."""
        resp = client.get("/api/dashboard/health")
        data = resp.json()
        scores = [
            data["engineering_health"],
            data["architecture_health"],
            data["security_score"],
        ]
        if data["test_pass_rate"] is not None:
            scores.append(data["test_pass_rate"] * 100)
        assert min(scores) <= data["overall"] <= max(scores)


class TestTasksEndpoint:
    """GET /api/dashboard/tasks — task overview."""

    def test_returns_200(self, client):
        resp = client.get("/api/dashboard/tasks")
        assert resp.status_code == 200

    def test_returns_task_counts(self, client):
        resp = client.get("/api/dashboard/tasks")
        data = resp.json()
        tasks = data["tasks"]
        assert tasks["available"] is True
        assert "by_status" in tasks
        assert "by_priority" in tasks
        assert "by_phase" in tasks
        # by_status should be a dict (possibly empty)
        assert isinstance(tasks["by_status"], dict)
        assert isinstance(tasks["by_priority"], dict)
        assert isinstance(tasks["by_phase"], dict)

    def test_recently_completed_is_list(self, client):
        resp = client.get("/api/dashboard/tasks")
        data = resp.json()
        assert "recently_completed" in data["tasks"]
        assert isinstance(data["tasks"]["recently_completed"], list)

    def test_includes_engineering_org(self, client):
        resp = client.get("/api/dashboard/tasks")
        data = resp.json()
        assert "engineering_org" in data
        assert data["engineering_org"]["available"] is True
        assert data["engineering_org"]["lead_count"] > 0


class TestFindingsEndpoint:
    """GET /api/dashboard/findings — engineering findings."""

    def test_returns_200(self, client):
        resp = client.get("/api/dashboard/findings")
        assert resp.status_code == 200

    def test_returns_findings_list(self, client):
        resp = client.get("/api/dashboard/findings")
        data = resp.json()
        eng = data["engineering_findings"]
        assert "top_findings" in eng
        assert isinstance(eng["top_findings"], list)
        assert len(eng["top_findings"]) > 0
        # Top finding should have expected fields
        top = eng["top_findings"][0]
        assert "severity" in top
        assert "file" in top
        assert "message" in top

    def test_returns_findings_by_type(self, client):
        resp = client.get("/api/dashboard/findings")
        data = resp.json()
        by_type = data["engineering_findings"]["findings_by_type"]
        assert isinstance(by_type, dict)
        # Fake report has complexity + risk_hotspot + todo findings
        assert "complexity" in by_type or "risk_hotspot" in by_type or "todo" in by_type

    def test_returns_risk_hotspots(self, client):
        resp = client.get("/api/dashboard/findings")
        data = resp.json()
        hotspots = data["engineering_findings"]["risk_hotspots"]
        assert isinstance(hotspots, list)
        # Fake report includes one risk_hotspot finding
        assert len(hotspots) >= 1

    def test_security_findings_section(self, client):
        resp = client.get("/api/dashboard/findings")
        data = resp.json()
        sec = data["security_findings"]
        assert "top_findings" in sec
        assert "findings_by_severity" in sec
        assert isinstance(sec["top_findings"], list)


class TestArchitectureEndpoint:
    """GET /api/dashboard/architecture — architecture overview."""

    def test_returns_200(self, client):
        resp = client.get("/api/dashboard/architecture")
        assert resp.status_code == 200

    def test_returns_architecture_metrics(self, client):
        resp = client.get("/api/dashboard/architecture")
        arch = resp.json()["architecture"]
        assert arch["available"] is True
        assert arch["total_modules"] == 20
        assert arch["total_dependencies"] == 45
        assert arch["violation_count"] == 1
        assert arch["health_score"] == 92.0
        assert arch["adr_count"] == 1

    def test_returns_most_coupled_modules(self, client):
        resp = client.get("/api/dashboard/architecture")
        arch = resp.json()["architecture"]
        assert "most_coupled_modules" in arch
        assert isinstance(arch["most_coupled_modules"], list)
        # Should be sorted by efferent coupling descending
        coupled = arch["most_coupled_modules"]
        if len(coupled) >= 2:
            assert coupled[0]["efferent_coupling"] >= coupled[1]["efferent_coupling"]

    def test_returns_violations(self, client):
        resp = client.get("/api/dashboard/architecture")
        arch = resp.json()["architecture"]
        assert "violations" in arch
        assert isinstance(arch["violations"], list)
        if arch["violations"]:
            v = arch["violations"][0]
            assert "source_module" in v
            assert "target_module" in v
            assert "message" in v


class TestSecurityEndpoint:
    """GET /api/dashboard/security — security overview."""

    def test_returns_200(self, client):
        resp = client.get("/api/dashboard/security")
        assert resp.status_code == 200

    def test_returns_security_score(self, client):
        resp = client.get("/api/dashboard/security")
        sec = resp.json()["security"]
        assert sec["available"] is True
        assert sec["security_score"] == 88.0
        assert 0 <= sec["security_score"] <= 100

    def test_returns_finding_counts_by_severity(self, client):
        resp = client.get("/api/dashboard/security")
        sec = resp.json()["security"]
        assert "findings_by_severity" in sec
        # Fake report has 1 HIGH + 1 CRITICAL
        by_sev = sec["findings_by_severity"]
        assert by_sev.get("high", 0) + by_sev.get("critical", 0) >= 2

    def test_returns_sbom_count(self, client):
        resp = client.get("/api/dashboard/security")
        sec = resp.json()["security"]
        assert sec["sbom_count"] == 3

    def test_returns_top_findings(self, client):
        resp = client.get("/api/dashboard/security")
        sec = resp.json()["security"]
        assert isinstance(sec["top_findings"], list)
        # Top finding should be the critical one (sorted by severity)
        if sec["top_findings"]:
            assert sec["top_findings"][0]["severity"] == "critical"


class TestPerformanceEndpoint:
    """GET /api/dashboard/performance — benchmark results + trends."""

    def test_returns_200(self, client):
        resp = client.get("/api/dashboard/performance")
        assert resp.status_code == 200

    def test_returns_performance_section(self, client):
        resp = client.get("/api/dashboard/performance")
        data = resp.json()
        assert "performance" in data
        perf = data["performance"]
        assert "available" in perf
        assert "benchmarks" in perf
        assert "trends" in perf

    def test_benchmarks_is_dict(self, client):
        resp = client.get("/api/dashboard/performance")
        perf = resp.json()["performance"]
        assert isinstance(perf["benchmarks"], dict)

    def test_trends_is_dict(self, client):
        resp = client.get("/api/dashboard/performance")
        perf = resp.json()["performance"]
        assert isinstance(perf["trends"], dict)


class TestReleasesEndpoint:
    """GET /api/dashboard/releases — release overview."""

    def test_returns_200(self, client):
        resp = client.get("/api/dashboard/releases")
        assert resp.status_code == 200

    def test_returns_release_list(self, client):
        resp = client.get("/api/dashboard/releases")
        releases = resp.json()["releases"]
        assert releases["available"] is True
        assert "history" in releases
        assert "drafts" in releases
        assert "latest" in releases
        assert isinstance(releases["history"], list)
        assert isinstance(releases["drafts"], list)

    def test_release_counts(self, client):
        resp = client.get("/api/dashboard/releases")
        releases = resp.json()["releases"]
        assert "total" in releases
        assert "published_count" in releases
        assert "draft_count" in releases
        assert releases["total"] == releases["published_count"] + releases["draft_count"]


# ---------------------------------------------------------------------------
# Auth tests — all endpoints require authentication
# ---------------------------------------------------------------------------

ENDPOINTS = [
    "/api/dashboard",
    "/api/dashboard/health",
    "/api/dashboard/tasks",
    "/api/dashboard/findings",
    "/api/dashboard/architecture",
    "/api/dashboard/security",
    "/api/dashboard/performance",
    "/api/dashboard/releases",
]


class TestAuthRequired:
    """All dashboard endpoints must reject unauthenticated requests with 401."""

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_returns_401_without_token(self, client, monkeypatch, endpoint):
        """When auth is enabled, requests without a Bearer token get 401."""
        # Enable auth by flipping FRIDAY_DEV_MODE off in core.auth.
        # (conftest sets DEV_MODE=1 + empty TOKEN, which disables auth.
        #  Setting DEV_MODE=False here forces require_auth to enforce the
        #  token check, which rejects empty credentials.)
        import core.auth
        monkeypatch.setattr(core.auth, "FRIDAY_DEV_MODE", False)
        # Leave FRIDAY_API_TOKEN="" (from conftest) — empty token means
        # no credential can match, so all unauthenticated requests 401.

        resp = client.get(endpoint)
        assert resp.status_code == 401, (
            f"{endpoint} returned {resp.status_code}, expected 401"
        )

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_returns_200_with_dev_mode(self, client, endpoint):
        """In dev mode (default), all endpoints are accessible without token."""
        resp = client.get(endpoint)
        assert resp.status_code == 200, (
            f"{endpoint} returned {resp.status_code} in dev mode"
        )


# ---------------------------------------------------------------------------
# Resilience tests — failed subsystems don't break the whole dashboard
# ---------------------------------------------------------------------------

class TestSubsystemFailureIsolation:
    """A failing subsystem should return {"error": ...}, not crash the API."""

    def test_engineering_intelligence_failure_isolated(self, client, monkeypatch):
        # Replace the analyzer with one that raises
        def boom():
            raise RuntimeError("boom")
        broken = MagicMock()
        broken.analyze.side_effect = boom
        monkeypatch.setattr(
            "core.engineering_intelligence.get_engineering_intelligence",
            lambda: broken,
        )
        from api.routes.dashboard import clear_cache
        clear_cache()

        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        ei = resp.json()["engineering_intelligence"]
        assert ei.get("available") is False
        assert ei.get("error") == "subsystem unavailable"

    def test_security_failure_isolated(self, client, monkeypatch):
        broken = MagicMock()
        broken.scan.side_effect = RuntimeError("boom")
        monkeypatch.setattr(
            "core.security_ops.get_security_operations",
            lambda: broken,
        )
        from api.routes.dashboard import clear_cache
        clear_cache()

        resp = client.get("/api/dashboard/security")
        assert resp.status_code == 200
        sec = resp.json()["security"]
        assert sec.get("available") is False
        assert sec.get("error") == "subsystem unavailable"
