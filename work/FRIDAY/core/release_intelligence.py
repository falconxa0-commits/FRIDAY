"""Release Intelligence — automated pre-release review pipeline.

Before every release, automatically executes:
    - Architecture Review
    - Security Review
    - Performance Review
    - Engineering Review
    - Documentation Review
    - Cost Review
    - Risk Review
    - Reliability Review
    - Deployment Review
    - Regression Review

Generates:
    - Production Readiness score
    - Deployment Confidence score
    - Rollback Confidence score
    - Release Confidence score
    - Risk Score
    - Executive Summary

Usage::

    intel = get_release_intelligence()
    report = await intel.assess_release(
        version="3.3.0",
        release_type=ReleaseType.BETA,
    )
    # report.production_readiness → 0-100
    # report.release_confidence → 0-100
    # report.executive_summary → string
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.release_intelligence")


class ReviewType(str, Enum):
    ARCHITECTURE = "architecture"
    SECURITY = "security"
    PERFORMANCE = "performance"
    ENGINEERING = "engineering"
    DOCUMENTATION = "documentation"
    COST = "cost"
    RISK = "risk"
    RELIABILITY = "reliability"
    DEPLOYMENT = "deployment"
    REGRESSION = "regression"


@dataclass
class ReviewResult:
    """Result of a single review dimension."""
    type: ReviewType
    score: int  # 0-100
    status: str  # "pass", "warning", "fail"
    findings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        return d


@dataclass
class ReleaseIntelligenceReport:
    """Full release intelligence report."""
    version: str = ""
    release_type: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reviews: List[ReviewResult] = field(default_factory=list)

    # Computed scores
    production_readiness: int = 0
    deployment_confidence: int = 0
    rollback_confidence: int = 0
    release_confidence: int = 0
    risk_score: int = 0

    executive_summary: str = ""

    @property
    def summary(self) -> str:
        return (
            f"Production Readiness: {self.production_readiness}/100, "
            f"Release Confidence: {self.release_confidence}/100, "
            f"Risk: {self.risk_score}/100"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "release_type": self.release_type,
            "timestamp": self.timestamp,
            "reviews": [r.to_dict() for r in self.reviews],
            "production_readiness": self.production_readiness,
            "deployment_confidence": self.deployment_confidence,
            "rollback_confidence": self.rollback_confidence,
            "release_confidence": self.release_confidence,
            "risk_score": self.risk_score,
            "executive_summary": self.executive_summary,
            "summary": self.summary,
        }


class ReleaseIntelligence:
    """Automated pre-release assessment pipeline."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()

    async def assess_release(
        self,
        version: str,
        release_type: str = "beta",
    ) -> ReleaseIntelligenceReport:
        """Run all pre-release reviews in parallel."""
        report = ReleaseIntelligenceReport(version=version, release_type=release_type)

        # Run all reviews in parallel
        reviews = await asyncio.gather(
            self._review_architecture(),
            self._review_security(),
            self._review_performance(),
            self._review_engineering(),
            self._review_documentation(),
            self._review_cost(),
            self._review_risk(),
            self._review_reliability(),
            self._review_deployment(),
            self._review_regression(),
        )
        report.reviews = list(reviews)

        # Compute aggregate scores
        report = self._compute_scores(report)
        report.executive_summary = self._generate_summary(report)

        return report

    async def _review_architecture(self) -> ReviewResult:
        """Assess architecture health."""
        try:
            from core.architecture import ArchitectureAnalyzer
            analyzer = ArchitectureAnalyzer(project_root=self.project_root)
            arch_report = analyzer.analyze()
            score = int(arch_report.health_score)
            findings = [f"{v.source_layer} → {v.target_layer}: {v.message}" for v in arch_report.violations if not v.valid]
            status = "pass" if score >= 80 else "warning" if score >= 50 else "fail"
            return ReviewResult(
                type=ReviewType.ARCHITECTURE,
                score=score,
                status=status,
                findings=findings[:5],
                recommendations=["Fix layer boundary violations"] if findings else [],
            )
        except Exception as exc:
            return ReviewResult(type=ReviewType.ARCHITECTURE, score=0, status="fail",
                                findings=[f"Error: {exc}"])

    async def _review_security(self) -> ReviewResult:
        """Assess security posture."""
        try:
            from core.security_ops import SecurityOperations
            sec = SecurityOperations(project_root=self.project_root)
            sec_report = sec.scan()
            score = int(sec_report.security_score)
            findings = [f"{f.severity.value}: {f.message}" for f in sec_report.findings[:5]]
            status = "pass" if score >= 90 else "warning" if score >= 70 else "fail"
            return ReviewResult(
                type=ReviewType.SECURITY,
                score=score,
                status=status,
                findings=findings,
                recommendations=["Fix security findings"] if findings else [],
            )
        except Exception as exc:
            return ReviewResult(type=ReviewType.SECURITY, score=0, status="fail",
                                findings=[f"Error: {exc}"])

    async def _review_performance(self) -> ReviewResult:
        """Assess performance benchmarks."""
        try:
            # Check if benchmarks exist
            bench_dir = self.project_root / "benchmarks"
            results_files = list(bench_dir.glob("results_*.json"))
            if not results_files:
                return ReviewResult(
                    type=ReviewType.PERFORMANCE,
                    score=50,
                    status="warning",
                    findings=["No benchmark results found"],
                    recommendations=["Run benchmarks before release"],
                )
            return ReviewResult(
                type=ReviewType.PERFORMANCE,
                score=75,
                status="pass",
                findings=[f"{len(results_files)} benchmark result files found"],
            )
        except Exception as exc:
            return ReviewResult(type=ReviewType.PERFORMANCE, score=0, status="fail",
                                findings=[f"Error: {exc}"])

    async def _review_engineering(self) -> ReviewResult:
        """Assess engineering health."""
        try:
            from core.engineering_intelligence import EngineeringIntelligence
            intel = EngineeringIntelligence(project_root=self.project_root)
            eng_report = intel.analyze()
            score = int(eng_report.health_score)
            status = "pass" if score >= 50 else "warning" if score >= 20 else "fail"
            return ReviewResult(
                type=ReviewType.ENGINEERING,
                score=score,
                status=status,
                findings=[f"{len(eng_report.findings)} engineering findings"],
            )
        except Exception as exc:
            return ReviewResult(type=ReviewType.ENGINEERING, score=0, status="fail",
                                findings=[f"Error: {exc}"])

    async def _review_documentation(self) -> ReviewResult:
        """Assess documentation completeness."""
        try:
            docs_dir = self.project_root / "docs"
            doc_files = list(docs_dir.glob("*.md")) if docs_dir.exists() else []
            required_docs = ["ARCHITECTURE.md", "SECURITY_MODEL.md", "DEPLOYMENT_GUIDE.md",
                             "DEVELOPER_GUIDE.md", "ENGINEERING_PLATFORM.md"]
            found = [d.name for d in doc_files]
            missing = [d for d in required_docs if d not in found]
            score = int((len(required_docs) - len(missing)) / len(required_docs) * 100)
            status = "pass" if not missing else "warning"
            return ReviewResult(
                type=ReviewType.DOCUMENTATION,
                score=score,
                status=status,
                findings=[f"Missing: {', '.join(missing)}"] if missing else ["All required docs present"],
            )
        except Exception as exc:
            return ReviewResult(type=ReviewType.DOCUMENTATION, score=0, status="fail",
                                findings=[f"Error: {exc}"])

    async def _review_cost(self) -> ReviewResult:
        """Assess cost tracking."""
        try:
            from core.cost_tracker import CostTracker
            tracker = CostTracker()
            report = tracker.get_cost_report()
            # Check if cost data is being collected
            has_data = any(p.get("total_calls", 0) > 0 for p in report.get("providers", {}).values())
            score = 80 if has_data else 60
            return ReviewResult(
                type=ReviewType.COST,
                score=score,
                status="pass",
                findings=[f"Cost tracking active: {has_data}"],
            )
        except Exception as exc:
            return ReviewResult(type=ReviewType.COST, score=50, status="warning",
                                findings=[f"Cost tracker error: {exc}"])

    async def _review_risk(self) -> ReviewResult:
        """Assess overall risk."""
        try:
            from core.engineering_intelligence import EngineeringIntelligence
            intel = EngineeringIntelligence(project_root=self.project_root)
            eng_report = intel.analyze()
            critical_count = sum(1 for f in eng_report.findings if f.severity.value == "critical")
            high_count = sum(1 for f in eng_report.findings if f.severity.value == "high")
            score = max(0, 100 - critical_count * 10 - high_count * 5)
            status = "pass" if critical_count == 0 else "warning" if critical_count < 3 else "fail"
            return ReviewResult(
                type=ReviewType.RISK,
                score=score,
                status=status,
                findings=[f"{critical_count} critical, {high_count} high findings"],
            )
        except Exception as exc:
            return ReviewResult(type=ReviewType.RISK, score=50, status="warning",
                                findings=[f"Error: {exc}"])

    async def _review_reliability(self) -> ReviewResult:
        """Assess reliability indicators."""
        try:
            # Check for backup script, health check, monitoring
            backup_script = self.project_root / "scripts" / "backup_restore.py"
            dockerfile = self.project_root / "Dockerfile"
            dockerignore = self.project_root / ".dockerignore"

            checks = {
                "backup_script": backup_script.exists(),
                "dockerfile": dockerfile.exists(),
                "dockerignore": dockerignore.exists(),
                "multi_stage_docker": "AS builder" in dockerfile.read_text() if dockerfile.exists() else False,
            }
            score = int(sum(checks.values()) / len(checks) * 100)
            status = "pass" if score >= 80 else "warning"
            return ReviewResult(
                type=ReviewType.RELIABILITY,
                score=score,
                status=status,
                findings=[f"{k}: {'✓' if v else '✗'}" for k, v in checks.items()],
            )
        except Exception as exc:
            return ReviewResult(type=ReviewType.RELIABILITY, score=50, status="warning",
                                findings=[f"Error: {exc}"])

    async def _review_deployment(self) -> ReviewResult:
        """Assess deployment readiness."""
        try:
            checks = {
                "dockerfile": (self.project_root / "Dockerfile").exists(),
                "dockerignore": (self.project_root / ".dockerignore").exists(),
                "docker_compose": (self.project_root / "docker-compose.yml").exists(),
                "nginx_conf": (self.project_root / "deploy" / "nginx.conf").exists(),
                "systemd": (self.project_root / "deploy" / "systemd").exists(),
            }
            score = int(sum(checks.values()) / len(checks) * 100)
            return ReviewResult(
                type=ReviewType.DEPLOYMENT,
                score=score,
                status="pass" if score >= 80 else "warning",
                findings=[f"{k}: {'✓' if v else '✗'}" for k, v in checks.items()],
            )
        except Exception as exc:
            return ReviewResult(type=ReviewType.DEPLOYMENT, score=50, status="warning",
                                findings=[f"Error: {exc}"])

    async def _review_regression(self) -> ReviewResult:
        """Assess regression risk."""
        try:
            # Run test collection to count tests
            import subprocess, sys
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "--co", "-q"],
                capture_output=True, text=True, timeout=30,
            )
            # Parse test count
            test_count = 0
            for line in result.stdout.split("\n"):
                if "collected" in line:
                    try:
                        test_count = int(line.split()[0])
                    except (ValueError, IndexError):
                        pass
            score = min(100, test_count // 10)  # 1000 tests = 100
            return ReviewResult(
                type=ReviewType.REGRESSION,
                score=score,
                status="pass" if test_count >= 500 else "warning",
                findings=[f"{test_count} tests collected"],
            )
        except Exception as exc:
            return ReviewResult(type=ReviewType.REGRESSION, score=50, status="warning",
                                findings=[f"Error: {exc}"])

    def _compute_scores(self, report: ReleaseIntelligenceReport) -> ReleaseIntelligenceReport:
        """Compute aggregate scores from individual reviews."""
        if not report.reviews:
            return report

        scores = {r.type.value: r.score for r in report.reviews}

        # Production readiness: weighted average
        # Security and reliability are most important
        weights = {
            "security": 0.20,
            "reliability": 0.15,
            "architecture": 0.15,
            "engineering": 0.10,
            "regression": 0.15,
            "deployment": 0.10,
            "documentation": 0.05,
            "performance": 0.05,
            "cost": 0.03,
            "risk": 0.02,
        }
        total_weight = sum(weights.values())
        weighted_sum = sum(scores.get(k, 0) * w for k, w in weights.items())
        report.production_readiness = int(weighted_sum / total_weight)

        # Deployment confidence
        report.deployment_confidence = int(
            (scores.get("deployment", 0) * 0.4 +
             scores.get("reliability", 0) * 0.3 +
             scores.get("regression", 0) * 0.3)
        )

        # Rollback confidence (based on backup + test coverage)
        report.rollback_confidence = int(
            (scores.get("reliability", 0) * 0.5 +
             scores.get("regression", 0) * 0.5)
        )

        # Release confidence (overall)
        report.release_confidence = int(
            (report.production_readiness * 0.5 +
             report.deployment_confidence * 0.25 +
             report.rollback_confidence * 0.25)
        )

        # Risk score (inverse of risk review)
        report.risk_score = 100 - scores.get("risk", 50)

        return report

    def _generate_summary(self, report: ReleaseIntelligenceReport) -> str:
        """Generate executive summary."""
        readiness = report.production_readiness
        if readiness >= 80:
            verdict = "READY for production deployment"
        elif readiness >= 60:
            verdict = "CONDITIONALLY READY — address warnings before deployment"
        elif readiness >= 40:
            verdict = "NOT READY — significant work required"
        else:
            verdict = "CRITICAL ISSUES — do not deploy"

        failing = [r for r in report.reviews if r.status == "fail"]
        warnings = [r for r in report.reviews if r.status == "warning"]

        summary = (
            f"Release v{report.version} ({report.release_type}): {verdict}.\n"
            f"Production Readiness: {report.production_readiness}/100. "
            f"Release Confidence: {report.release_confidence}/100. "
            f"Risk Score: {report.risk_score}/100.\n"
            f"Reviews: {len(report.reviews)} total, "
            f"{len(failing)} failing, {len(warnings)} warnings."
        )
        if failing:
            summary += f"\nFailing: {', '.join(r.type.value for r in failing)}"
        if warnings:
            summary += f"\nWarnings: {', '.join(r.type.value for r in warnings)}"

        return summary


# Singleton
_intelligence: Optional[ReleaseIntelligence] = None


def get_release_intelligence() -> ReleaseIntelligence:
    global _intelligence
    if _intelligence is None:
        _intelligence = ReleaseIntelligence()
    return _intelligence
