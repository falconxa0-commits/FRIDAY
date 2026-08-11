"""Quality Intelligence — multi-dimensional quality scoring.

Scores the codebase across five quality dimensions and rolls them
up into a single 0-100 quality score. Generates prioritized
recommendations for improving the weakest dimensions.

Design principles:
    - **Multi-dimensional**: Quality is broken down into 5 orthogonal
      dimensions, each scored 0-100.
    - **Weighted**: Dimensions are weighted to compute the overall
      score (test/code quality weigh most; docs weigh least).
    - **Resilient**: Each dimension is independently wrapped in
      try/except so a failing analyzer never breaks the whole score.
    - **Actionable**: Recommendations target the lowest-scoring
      dimensions where improvement has the highest weighted impact.

Quality dimensions (weights):
    - Test quality          25%
    - Code quality          25%
    - Architecture quality  20%
    - Security quality      20%
    - Documentation quality 10%

Usage::

    qi = get_quality_intelligence()
    score = await qi.calculate_quality_score()        # 0-100 int
    breakdown = await qi.get_quality_breakdown()      # per-dimension
    recs = await qi.get_quality_recommendations()     # improvements
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.quality_intelligence")


# Dimension weights — must sum to 1.0.
_DIMENSION_WEIGHTS: Dict[str, float] = {
    "test_quality": 0.25,
    "code_quality": 0.25,
    "architecture_quality": 0.20,
    "security_quality": 0.20,
    "documentation_quality": 0.10,
}

_DIMENSION_LABELS: Dict[str, str] = {
    "test_quality": "Test Quality",
    "code_quality": "Code Quality",
    "architecture_quality": "Architecture Quality",
    "security_quality": "Security Quality",
    "documentation_quality": "Documentation Quality",
}

# Suggestions shown when a dimension is flagged for improvement.
_DIMENSION_SUGGESTIONS: Dict[str, str] = {
    "test_quality": (
        "Increase test coverage and fix failing tests. Target untested "
        "modules first — see 'source_module_count' vs 'tested_module_count' "
        "in the dimension details."
    ),
    "code_quality": (
        "Reduce technical debt and refactor high-complexity functions. "
        "Resolve TODO/FIXME markers and break up god classes."
    ),
    "architecture_quality": (
        "Fix layer boundary violations and reduce coupling. Apply "
        "dependency inversion where efferent coupling is high."
    ),
    "security_quality": (
        "Move hardcoded secrets to environment variables and patch "
        "vulnerable dependencies. Address critical findings first."
    ),
    "documentation_quality": (
        "Add missing docs (README, API_REFERENCE, ARCHITECTURE, "
        "SECURITY_MODEL, DEPLOYMENT_GUIDE) and ensure .env.example "
        "documents all required environment variables."
    ),
}


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Clamp a float to [lo, hi]."""
    return max(lo, min(hi, value))


class QualityIntelligence:
    """Multi-dimensional quality scoring engine.

    Each dimension is scored independently and resiliently; failures
    in a single analyzer fall back to a neutral 50.0 score rather
    than raising.
    """

    # Thresholds for filtering trivial recommendations.
    MIN_IMPROVEMENT_POTENTIAL = 1.0
    HIGH_PRIORITY_THRESHOLD = 10.0
    MEDIUM_PRIORITY_THRESHOLD = 3.0

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = Path(project_root) if project_root else Path.cwd()

    # ------------------------------------------------------------------
    # Overall score
    # ------------------------------------------------------------------
    async def calculate_quality_score(self) -> int:
        """Return the overall quality score (0-100, int)."""
        breakdown = await self.get_quality_breakdown()
        return int(round(breakdown["overall_score"]))

    # ------------------------------------------------------------------
    # Per-dimension breakdown
    # ------------------------------------------------------------------
    async def get_quality_breakdown(self) -> Dict[str, Any]:
        """Return per-dimension scores with details and roll-up.

        Returns a dict of the form::

            {
                "timestamp": "...",
                "dimensions": {
                    "test_quality": {
                        "label": "Test Quality",
                        "score": 0-100,
                        "weight": 0.25,
                        "details": {...},
                    },
                    ...
                },
                "overall_score": 0.0,
                "grade": "A" | "B" | ...,
            }
        """
        (
            test_q,
            code_q,
            arch_q,
            sec_q,
            doc_q,
        ) = await asyncio.gather(
            self._score_test_quality(),
            self._score_code_quality(),
            self._score_architecture_quality(),
            self._score_security_quality(),
            self._score_documentation_quality(),
        )

        dimensions = {
            "test_quality": test_q,
            "code_quality": code_q,
            "architecture_quality": arch_q,
            "security_quality": sec_q,
            "documentation_quality": doc_q,
        }

        # Weighted overall score.
        overall = 0.0
        for key, dim in dimensions.items():
            weight = _DIMENSION_WEIGHTS[key]
            overall += dim["score"] * weight

        overall = _clamp(overall)
        grade = self._grade(overall)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dimensions": dimensions,
            "overall_score": round(overall, 2),
            "grade": grade,
        }

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------
    async def get_quality_recommendations(self) -> List[Dict[str, Any]]:
        """Return prioritized recommendations to improve quality.

        Focuses on the lowest-scoring dimensions where improvement
        has the highest weighted impact on the overall score.
        """
        breakdown = await self.get_quality_breakdown()
        dimensions = breakdown["dimensions"]

        # Improvement potential = (100 - score) * weight.
        candidates = []
        for key, dim in dimensions.items():
            score = dim["score"]
            weight = _DIMENSION_WEIGHTS[key]
            potential = (100.0 - score) * weight
            candidates.append({
                "dimension": key,
                "label": dim["label"],
                "current_score": score,
                "weight": weight,
                "improvement_potential": round(potential, 2),
                "details": dim["details"],
            })

        # Sort by improvement potential descending.
        candidates.sort(key=lambda c: -c["improvement_potential"])

        recommendations: List[Dict[str, Any]] = []
        for c in candidates:
            if c["improvement_potential"] < self.MIN_IMPROVEMENT_POTENTIAL:
                continue  # Skip dimensions already near-perfect.
            potential = c["improvement_potential"]
            if potential >= self.HIGH_PRIORITY_THRESHOLD:
                priority = "high"
            elif potential >= self.MEDIUM_PRIORITY_THRESHOLD:
                priority = "medium"
            else:
                priority = "low"
            recommendations.append({
                "dimension": c["dimension"],
                "label": c["label"],
                "priority": priority,
                "current_score": c["current_score"],
                "target_score": 100,
                "improvement_potential": potential,
                "suggestion": _DIMENSION_SUGGESTIONS.get(
                    c["dimension"],
                    "Investigate and improve this dimension.",
                ),
            })

        return recommendations

    # ------------------------------------------------------------------
    # Per-dimension scorers (each resilient)
    # ------------------------------------------------------------------
    async def _score_test_quality(self) -> Dict[str, Any]:
        """Score test quality from pass rate + coverage proxy.

        Score = 60% pass rate + 40% coverage proxy (ratio of source
        modules that have a corresponding test file).
        """
        details: Dict[str, Any] = {}
        pass_rate = 100.0
        coverage_proxy = 0.0

        # Pass rate from the latest health snapshot.
        try:
            from core.health_monitor import get_health_monitor
            monitor = get_health_monitor()
            history = await monitor.get_history(limit=1)
            if history:
                latest = history[-1]
                raw_rate = getattr(latest, "test_pass_rate", None)
                if raw_rate is not None:
                    pass_rate = float(raw_rate)
                details["test_count"] = int(getattr(latest, "test_count", 0) or 0)
            details["pass_rate"] = round(pass_rate, 2)
        except Exception as exc:
            logger.debug(f"Test pass-rate lookup failed: {exc}")

        # Coverage proxy: ratio of source modules with a test file.
        try:
            source_files = set()
            test_files = set()
            for path in self.project_root.rglob("*.py"):
                if any(p in path.parts for p in (
                    ".venv", "__pycache__", ".git", ".friday", "node_modules",
                )):
                    continue
                rel = path.relative_to(self.project_root)
                if rel.parts and rel.parts[0] == "tests":
                    stem = path.stem
                    if stem.startswith("test_"):
                        test_files.add(stem[len("test_"):])
                else:
                    source_files.add(path.stem)
            source_files.discard("__init__")
            if source_files:
                covered = source_files & test_files
                coverage_proxy = (len(covered) / len(source_files)) * 100.0
            else:
                coverage_proxy = 100.0  # no source -> vacuously covered
            details["source_module_count"] = len(source_files)
            details["tested_module_count"] = (
                len(source_files & test_files) if source_files else 0
            )
            details["coverage_proxy"] = round(coverage_proxy, 2)
        except Exception as exc:
            logger.debug(f"Coverage proxy failed: {exc}")
            coverage_proxy = 50.0  # neutral default

        score = _clamp(0.6 * pass_rate + 0.4 * coverage_proxy)
        return {
            "label": _DIMENSION_LABELS["test_quality"],
            "score": round(score, 2),
            "weight": _DIMENSION_WEIGHTS["test_quality"],
            "details": details,
        }

    async def _score_code_quality(self) -> Dict[str, Any]:
        """Score code quality from complexity + debt findings.

        Score = 60% engineering health + 30% (100 - debt) + 10% baseline,
        minus a penalty for critical/high complexity findings.
        """
        details: Dict[str, Any] = {}
        score = 50.0  # neutral default
        try:
            from core.engineering_intelligence import EngineeringIntelligence
            intel = EngineeringIntelligence(project_root=self.project_root)
            report = intel.analyze()

            health = float(report.health_score)
            debt = float(report.debt_score)

            complexity_findings = [
                f for f in report.findings
                if getattr(f.type, "value", f.type) == "complexity"
            ]
            critical_complexity = [
                f for f in complexity_findings
                if getattr(f.severity, "value", f.severity) in ("critical", "high")
            ]

            details["health_score"] = health
            details["debt_score"] = debt
            details["total_findings"] = len(report.findings)
            details["complexity_findings"] = len(complexity_findings)
            details["critical_complexity_findings"] = len(critical_complexity)
            details["total_loc"] = report.total_loc

            score = 0.6 * health + 0.3 * (100.0 - debt) + 0.1 * 100.0
            if report.total_loc > 0:
                penalty = min(20.0, len(critical_complexity) * 2.0)
                score -= penalty
            score = _clamp(score)
        except Exception as exc:
            logger.debug(f"Code quality scoring failed: {exc}")
            score = 50.0
        return {
            "label": _DIMENSION_LABELS["code_quality"],
            "score": round(score, 2),
            "weight": _DIMENSION_WEIGHTS["code_quality"],
            "details": details,
        }

    async def _score_architecture_quality(self) -> Dict[str, Any]:
        """Score architecture quality from violations + coupling.

        Score = architecture health minus a penalty for layer
        violations and high-coupling modules.
        """
        details: Dict[str, Any] = {}
        score = 50.0
        try:
            from core.architecture import ArchitectureAnalyzer
            analyzer = ArchitectureAnalyzer(project_root=self.project_root)
            report = analyzer.analyze()

            health = float(report.health_score)
            violation_count = len([v for v in report.violations if not v.valid])

            high_coupling = [
                m for m in report.metrics.values()
                if m.efferent_coupling > 8
            ]

            details["health_score"] = health
            details["violation_count"] = violation_count
            details["module_count"] = report.total_modules
            details["high_coupling_modules"] = len(high_coupling)

            penalty = min(30.0, violation_count * 2.0 + len(high_coupling) * 1.0)
            score = _clamp(health - penalty)
        except Exception as exc:
            logger.debug(f"Architecture quality scoring failed: {exc}")
            score = 50.0
        return {
            "label": _DIMENSION_LABELS["architecture_quality"],
            "score": round(score, 2),
            "weight": _DIMENSION_WEIGHTS["architecture_quality"],
            "details": details,
        }

    async def _score_security_quality(self) -> Dict[str, Any]:
        """Score security quality from SecurityOperations scan.

        Score = security score minus a small additional penalty for
        critical/high findings.
        """
        details: Dict[str, Any] = {}
        score = 50.0
        try:
            from core.security_ops import SecurityOperations, SecuritySeverity
            sec = SecurityOperations(project_root=self.project_root)
            report = sec.scan()

            sec_score = float(report.security_score)
            critical = [f for f in report.findings if f.severity == SecuritySeverity.CRITICAL]
            high = [f for f in report.findings if f.severity == SecuritySeverity.HIGH]

            details["security_score"] = sec_score
            details["total_findings"] = len(report.findings)
            details["critical_findings"] = len(critical)
            details["high_findings"] = len(high)
            details["sbom_count"] = len(report.sbom)

            # 80% security score, 20% additional critical/high penalty.
            penalty = min(40.0, len(critical) * 10.0 + len(high) * 4.0)
            score = _clamp(sec_score - penalty * 0.2)
        except Exception as exc:
            logger.debug(f"Security quality scoring failed: {exc}")
            score = 50.0
        return {
            "label": _DIMENSION_LABELS["security_quality"],
            "score": round(score, 2),
            "weight": _DIMENSION_WEIGHTS["security_quality"],
            "details": details,
        }

    async def _score_documentation_quality(self) -> Dict[str, Any]:
        """Score documentation quality from DocValidator pass rate."""
        details: Dict[str, Any] = {}
        score = 50.0
        try:
            from core.doc_validator import DocValidator
            validator = DocValidator(project_root=self.project_root)
            summary = await validator.get_summary()

            pass_rate = float(summary.get("pass_rate", 0.0) or 0.0)
            details["pass_rate"] = round(pass_rate, 2)
            details["total_checks"] = int(summary.get("total_checks", 0) or 0)
            details["passed_checks"] = int(summary.get("passed", 0) or 0)
            details["failed_checks"] = int(summary.get("failed", 0) or 0)

            score = _clamp(pass_rate)
        except Exception as exc:
            logger.debug(f"Documentation quality scoring failed: {exc}")
            score = 50.0
        return {
            "label": _DIMENSION_LABELS["documentation_quality"],
            "score": round(score, 2),
            "weight": _DIMENSION_WEIGHTS["documentation_quality"],
            "details": details,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _grade(score: float) -> str:
        """Convert a 0-100 score to a letter grade."""
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_quality: Optional[QualityIntelligence] = None


def get_quality_intelligence() -> QualityIntelligence:
    """Get the singleton QualityIntelligence instance."""
    global _quality
    if _quality is None:
        _quality = QualityIntelligence()
    return _quality
