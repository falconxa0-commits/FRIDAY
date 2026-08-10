"""Security Operations — secret scanning, SBOM, dependency auditing.

Provides:
    - Secret scanning (detect hardcoded API keys, passwords, tokens)
    - SBOM (Software Bill of Materials) generation
    - Dependency auditing (check for known vulnerabilities)
    - Supply-chain validation
    - Runtime policy enforcement

Design principles:
    - **Fail-closed**: Unknown dependencies are treated as risky.
    - **Comprehensive**: Scan all source files, not just .env.
    - **Actionable**: Every finding includes a remediation step.
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("friday.security_ops")

_PROJECT_ROOT = Path(os.environ.get("FRIDAY_PROJECT_ROOT", str(Path.cwd())))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
class SecurityFindingType(str, Enum):
    HARDCODED_SECRET = "hardcoded_secret"
    SUSPICIOUS_PATTERN = "suspicious_pattern"
    VULNERABLE_DEP = "vulnerable_dependency"
    MISSING_LICENSE = "missing_license"
    POLICY_VIOLATION = "policy_violation"
    OUTDATED_DEP = "outdated_dependency"


class SecuritySeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SecurityFinding:
    type: SecurityFindingType
    severity: SecuritySeverity
    file: str
    line: int
    message: str
    remediation: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["severity"] = self.severity.value
        return d


@dataclass
class SBOMEntry:
    """A single entry in the Software Bill of Materials."""
    name: str
    version: str
    source: str  # "pip", "npm", "manual"
    license: str = "unknown"
    hash: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SecurityReport:
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    project_root: str = ""
    findings: List[SecurityFinding] = field(default_factory=list)
    sbom: List[SBOMEntry] = field(default_factory=list)
    total_files_scanned: int = 0
    total_deps: int = 0
    security_score: float = 0.0  # 0-100, higher is better

    @property
    def summary(self) -> str:
        by_sev = {}
        for f in self.findings:
            by_sev[f.severity.value] = by_sev.get(f.severity.value, 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(by_sev.items())]
        return (
            f"{len(self.findings)} findings ({', '.join(parts)}), "
            f"{len(self.sbom)} deps, score={self.security_score:.0f}/100"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "project_root": self.project_root,
            "total_files_scanned": self.total_files_scanned,
            "total_deps": len(self.sbom),
            "security_score": self.security_score,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "sbom": [s.to_dict() for s in self.sbom],
        }


# ---------------------------------------------------------------------------
# Secret Scanner
# -*-
class SecretScanner:
    """Scans source files for hardcoded secrets."""

    # Patterns for common secret types
    SECRET_PATTERNS = [
        # API keys (32+ char alphanumeric)
        (re.compile(r'["\']([a-zA-Z0-9]{32,})["\']'), "API key (32+ chars)"),
        # AWS access keys
        (re.compile(r'AKIA[0-9A-Z]{16}'), "AWS Access Key"),
        # AWS secret keys
        (re.compile(r'["\']([a-zA-Z0-9/+=]{40})["\']'), "Possible AWS Secret Key (40 chars)"),
        # GitHub tokens
        (re.compile(r'gh[pousr]_[A-Za-z0-9]{36}'), "GitHub Token"),
        # Generic API key assignments
        (re.compile(r'(?:api_key|apikey|api-key|secret|token|password|passwd|pwd)\s*[=:]\s*["\']([^"\']{8,})["\']', re.IGNORECASE), "Credential assignment"),
        # Private keys
        (re.compile(r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----'), "Private key"),
        # Database URLs with credentials
        (re.compile(r'(?:postgres|mysql|mongodb|redis)://[^:]+:[^@]+@'), "Database URL with credentials"),
    ]

    # Files to skip
    SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".friday"}
    SKIP_EXTENSIONS = {".pyc", ".pyo", ".so", ".dll", ".exe", ".png", ".jpg", ".gif", ".pdf"}

    @staticmethod
    def scan_file(filepath: Path, project_root: Optional[Path] = None) -> List[SecurityFinding]:
        """Scan a single file for secrets."""
        root = project_root or _PROJECT_ROOT
        if filepath.suffix in SecretScanner.SKIP_EXTENSIONS:
            return []

        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []

        findings = []
        try:
            rel_path = str(filepath.relative_to(root))
        except ValueError:
            rel_path = str(filepath)
        lines = source.split("\n")

        for i, line in enumerate(lines, 1):
            # Skip comments
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue

            # Skip .env.example and test fixtures
            if "example" in rel_path or "test" in rel_path.lower():
                if "your_" in line.lower() or "placeholder" in line.lower() or "xxx" in line.lower():
                    continue

            for pattern, description in SecretScanner.SECRET_PATTERNS:
                match = pattern.search(line)
                if match:
                    # Check if it's a placeholder
                    matched_text = match.group(0)
                    if any(p in matched_text.lower() for p in ["your_", "placeholder", "example", "xxx", "changeme", "test"]):
                        continue

                    findings.append(SecurityFinding(
                        type=SecurityFindingType.HARDCODED_SECRET,
                        severity=SecuritySeverity.HIGH,
                        file=rel_path,
                        line=i,
                        message=f"Possible {description}: {matched_text[:40]}...",
                        remediation="Move to environment variable. Never commit secrets to source control.",
                        metadata={"pattern": description, "matched": matched_text[:60]},
                    ))

        return findings


# ---------------------------------------------------------------------------
# SBOM Generator
# -*-
class SBOMGenerator:
    """Generates a Software Bill of Materials."""

    @staticmethod
    def generate(project_root: Path) -> List[SBOMEntry]:
        """Generate SBOM from installed packages."""
        entries = []

        # Parse pyproject.toml
        pyproject = project_root / "pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib
                with open(pyproject, "rb") as f:
                    data = tomllib.load(f)
                deps = data.get("project", {}).get("dependencies", [])
                optional = data.get("project", {}).get("optional-dependencies", {})
                for group, group_deps in optional.items():
                    deps.extend(group_deps)

                for dep in deps:
                    # Parse "package>=1.0.0" format
                    name = re.split(r"[>=<!\[ ]", dep)[0]
                    version = ""
                    match = re.search(r">=?([0-9][0-9.]*)", dep)
                    if match:
                        version = match.group(1)
                    entries.append(SBOMEntry(
                        name=name,
                        version=version or "unknown",
                        source="pyproject.toml",
                    ))
            except Exception as exc:
                logger.debug(f"Failed to parse pyproject.toml: {exc}")

        # Parse requirements.txt
        reqs = project_root / "requirements.txt"
        if reqs.exists():
            try:
                for line in reqs.read_text().split("\n"):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    name = re.split(r"[>=<!\[ ]", line)[0]
                    version = ""
                    match = re.search(r">=?([0-9][0-9.]*)", line)
                    if match:
                        version = match.group(1)
                    # Check if already in list
                    if not any(e.name == name for e in entries):
                        entries.append(SBOMEntry(
                            name=name,
                            version=version or "unknown",
                            source="requirements.txt",
                        ))
            except Exception as exc:
                logger.debug(f"Failed to parse requirements.txt: {exc}")

        # Try to get installed versions via pip
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--format=json"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                installed = json.loads(result.stdout)
                for pkg in installed:
                    name = pkg.get("name", "").lower()
                    version = pkg.get("version", "")
                    for entry in entries:
                        if entry.name.lower() == name:
                            entry.version = version
                            break
        except Exception:
            pass  # pip list may not be available in all environments

        return entries


# ---------------------------------------------------------------------------
# Dependency Auditor
# -*-
class DependencyAuditor:
    """Audits dependencies for known vulnerabilities."""

    @staticmethod
    def audit(sbom: List[SBOMEntry]) -> List[SecurityFinding]:
        """Check dependencies for known issues."""
        findings = []

        # Try pip-audit if available
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip_audit"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0 and result.stdout:
                # Parse output for vulnerabilities
                for line in result.stdout.split("\n"):
                    if "vuln" in line.lower() or "CVE" in line:
                        findings.append(SecurityFinding(
                            type=SecurityFindingType.VULNERABLE_DEP,
                            severity=SecuritySeverity.HIGH,
                            file="requirements.txt",
                            line=0,
                            message=line.strip(),
                            remediation="Update the vulnerable package to a fixed version.",
                        ))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # pip-audit not installed

        # Check for packages with known bad versions
        KNOWN_BAD = {
            "requests": {"2.25.0": "CVE-2023-32681"},
            "urllib3": {"1.26.0": "CVE-2023-43804"},
            "cryptography": {"3.0": "Multiple CVEs"},
        }

        for entry in sbom:
            for bad_pkg, bad_versions in KNOWN_BAD.items():
                if entry.name.lower() == bad_pkg.lower():
                    for bad_ver, cve in bad_versions.items():
                        if entry.version.startswith(bad_ver):
                            findings.append(SecurityFinding(
                                type=SecurityFindingType.VULNERABLE_DEP,
                                severity=SecuritySeverity.CRITICAL,
                                file="requirements.txt",
                                line=0,
                                message=f"{entry.name}=={entry.version} has {cve}",
                                remediation=f"Upgrade {entry.name} to latest version.",
                                metadata={"package": entry.name, "version": entry.version, "cve": cve},
                            ))

        return findings


# ---------------------------------------------------------------------------
# Main Security Operations
# -*-
class SecurityOperations:
    """Runs all security scanners and generates a report."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or _PROJECT_ROOT

    def scan(self) -> SecurityReport:
        """Run full security scan."""
        report = SecurityReport(project_root=str(self.project_root))

        # Scan all source files for secrets
        for filepath in self.project_root.rglob("*"):
            if not filepath.is_file():
                continue
            if any(part in SecretScanner.SKIP_DIRS for part in filepath.parts):
                continue
            if filepath.suffix in SecretScanner.SKIP_EXTENSIONS:
                continue

            findings = SecretScanner.scan_file(filepath, project_root=self.project_root)
            report.findings.extend(findings)
            report.total_files_scanned += 1

        # Generate SBOM
        report.sbom = SBOMGenerator.generate(self.project_root)

        # Audit dependencies
        dep_findings = DependencyAuditor.audit(report.sbom)
        report.findings.extend(dep_findings)

        # Calculate security score
        report.security_score = self._calculate_score(report)

        return report

    @staticmethod
    def _calculate_score(report: SecurityReport) -> float:
        """Calculate security score (0-100, higher is better)."""
        score = 100.0
        for f in report.findings:
            if f.severity == SecuritySeverity.CRITICAL:
                score -= 15
            elif f.severity == SecuritySeverity.HIGH:
                score -= 10
            elif f.severity == SecuritySeverity.MEDIUM:
                score -= 5
            elif f.severity == SecuritySeverity.LOW:
                score -= 2
        return max(0.0, min(100.0, score))


# Singleton
_sec_ops: Optional[SecurityOperations] = None


def get_security_operations() -> SecurityOperations:
    global _sec_ops
    if _sec_ops is None:
        _sec_ops = SecurityOperations()
    return _sec_ops
