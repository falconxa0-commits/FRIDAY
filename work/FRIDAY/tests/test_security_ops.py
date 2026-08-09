"""Tests for security operations."""
import asyncio
import pytest
from pathlib import Path

from core.security_ops import (
    SecurityOperations, SecurityReport, SecurityFinding, SecurityFindingType,
    SecuritySeverity, SecretScanner, SBOMGenerator, DependencyAuditor, SBOMEntry,
)


@pytest.fixture()
def project(tmp_path):
    """Create a project with various security issues."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "__init__.py").write_text("")

    # File with hardcoded secret
    (tmp_path / "core" / "config.py").write_text("""
API_KEY = "sk-1234567890abcdef1234567890abcdef"
PASSWORD = "supersecretpassword123"
""")

    # Clean file
    (tmp_path / "core" / "clean.py").write_text("""
import os
api_key = os.environ.get("API_KEY")
""")

    # requirements.txt
    (tmp_path / "requirements.txt").write_text("""
fastapi>=0.110.0
rich>=13.0.0
zhipuai>=2.1.0
""")

    # pyproject.toml
    (tmp_path / "pyproject.toml").write_text("""
[project]
name = "test-project"
dependencies = [
    "fastapi>=0.110.0",
    "rich>=13.0.0",
]
""")

    return tmp_path


class TestSecretScanner:
    def test_detects_hardcoded_api_key(self, project):
        findings = SecretScanner.scan_file(project / "core" / "config.py")
        secrets = [f for f in findings if f.type == SecurityFindingType.HARDCODED_SECRET]
        assert len(secrets) >= 1

    def test_does_not_flag_env_vars(self, project):
        findings = SecretScanner.scan_file(project / "core" / "clean.py")
        secrets = [f for f in findings if f.type == SecurityFindingType.HARDCODED_SECRET]
        assert len(secrets) == 0

    def test_skips_placeholders(self, tmp_path):
        (tmp_path / "test_config.py").write_text("""
API_KEY = "your_api_key_here"
TOKEN = "xxxxxxxxxxxxxxxx"
""")
        findings = SecretScanner.scan_file(tmp_path / "test_config.py")
        secrets = [f for f in findings if f.type == SecurityFindingType.HARDCODED_SECRET]
        assert len(secrets) == 0

    def test_skips_binary_files(self, tmp_path):
        (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02\x03")
        findings = SecretScanner.scan_file(tmp_path / "data.bin")
        assert len(findings) == 0


class TestSBOMGenerator:
    def test_generates_sbom_from_pyproject(self, project):
        sbom = SBOMGenerator.generate(project)
        names = [e.name for e in sbom]
        assert "fastapi" in names
        assert "rich" in names

    def test_sbom_entries_have_required_fields(self, project):
        sbom = SBOMGenerator.generate(project)
        for entry in sbom:
            assert isinstance(entry, SBOMEntry)
            assert entry.name
            assert entry.source

    def test_sbom_deduplicates(self, project):
        sbom = SBOMGenerator.generate(project)
        names = [e.name for e in sbom]
        # fastapi appears in both pyproject and requirements, should be deduped
        assert names.count("fastapi") == 1


class TestSecurityOperations:
    def test_scan_returns_report(self, project):
        sec_ops = SecurityOperations(project_root=project)
        report = sec_ops.scan()
        assert isinstance(report, SecurityReport)
        assert report.total_files_scanned > 0

    def test_scan_finds_secrets(self, project):
        sec_ops = SecurityOperations(project_root=project)
        report = sec_ops.scan()
        secrets = [f for f in report.findings if f.type == SecurityFindingType.HARDCODED_SECRET]
        assert len(secrets) >= 1

    def test_security_score_decreases_with_findings(self, project):
        sec_ops = SecurityOperations(project_root=project)
        report = sec_ops.scan()
        # Should have findings, so score < 100
        if report.findings:
            assert report.security_score < 100

    def test_report_to_dict(self, project):
        sec_ops = SecurityOperations(project_root=project)
        report = sec_ops.scan()
        d = report.to_dict()
        assert "findings" in d
        assert "sbom" in d
        assert "security_score" in d

    def test_report_summary(self, project):
        sec_ops = SecurityOperations(project_root=project)
        report = sec_ops.scan()
        assert "findings" in report.summary
        assert "score=" in report.summary
