"""Autonomous Recommendation Engine — generates engineering recommendations.

Analyzes the repository using all available intelligence modules and
generates prioritized, actionable recommendations that flow into the
task system.

Design principles:
    - **Evidence-based**: Every recommendation cites specific findings.
    - **Prioritized**: Recommendations are ranked by impact × effort.
    - **Actionable**: Each recommendation includes a concrete next step.
    - **Non-destructive**: Recommendations are proposals, not auto-execution.

Usage::

    engine = get_recommendation_engine()
    recs = await engine.generate_recommendations()
    # recs → list of Recommendation, sorted by priority
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.recommendations")


class RecommendationType(str, Enum):
    SECURITY = "security"
    ARCHITECTURE = "architecture"
    PERFORMANCE = "performance"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    REFACTORING = "refactoring"
    DEPENDENCY = "dependency"
    COMPLEXITY = "complexity"
    DEBT = "technical_debt"
    RISK = "risk"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def weight(self) -> int:
        return {"critical": 0, "high": 1, "medium": 2, "low": 3}[self.value]


@dataclass
class Recommendation:
    """A single engineering recommendation."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: RecommendationType = RecommendationType.DEBT
    priority: Priority = Priority.MEDIUM
    title: str = ""
    description: str = ""
    evidence: str = ""
    impact: str = ""
    effort: str = ""
    suggested_action: str = ""
    affected_files: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confidence: int = 50  # 0-100

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["priority"] = self.priority.value
        return d


class RecommendationEngine:
    """Generates engineering recommendations from repository analysis.

    Pulls data from:
        - EngineeringIntelligence (complexity, debt, TODOs)
        - ArchitectureAnalyzer (violations, coupling)
        - SecurityOperations (findings, SBOM)
        - TaskQueue (backlog, blockers)
        - KnowledgeBase (lessons, ADRs)
    """

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()

    async def generate_recommendations(self) -> List[Recommendation]:
        """Run all analyzers and generate prioritized recommendations."""
        recs: List[Recommendation] = []

        # Run all analyzers in parallel
        eng_recs, arch_recs, sec_recs, test_recs, doc_recs = await asyncio.gather(
            self._analyze_engineering(),
            self._analyze_architecture(),
            self._analyze_security(),
            self._analyze_testing(),
            self._analyze_documentation(),
        )

        recs.extend(eng_recs)
        recs.extend(arch_recs)
        recs.extend(sec_recs)
        recs.extend(test_recs)
        recs.extend(doc_recs)

        # Sort by priority
        recs.sort(key=lambda r: (r.priority.weight, -r.confidence))

        return recs

    async def _analyze_engineering(self) -> List[Recommendation]:
        """Generate recommendations from engineering intelligence."""
        recs = []
        try:
            from core.engineering_intelligence import EngineeringIntelligence
            intel = EngineeringIntelligence(project_root=self.project_root)
            report = intel.analyze()

            # High-complexity functions
            complexity_findings = [f for f in report.findings if f.type.value == "complexity" and f.severity.value in ("critical", "high")]
            if complexity_findings:
                top_5 = complexity_findings[:5]
                recs.append(Recommendation(
                    type=RecommendationType.COMPLEXITY,
                    priority=Priority.HIGH,
                    title=f"Refactor {len(complexity_findings)} high-complexity functions",
                    description=f"{len(complexity_findings)} functions have cyclomatic complexity >15. "
                                f"Top offender: {top_5[0].message}",
                    evidence=f"Engineering Intelligence found {len(complexity_findings)} complexity findings",
                    impact="High complexity correlates with 3x bug rate (McCabe)",
                    effort="1-2 weeks",
                    suggested_action="Break into smaller functions with single responsibilities",
                    affected_files=list(set(f.file for f in top_5)),
                    confidence=85,
                ))

            # TODOs and FIXMEs
            todo_findings = [f for f in report.findings if f.type.value in ("todo", "fixme")]
            if todo_findings:
                recs.append(Recommendation(
                    type=RecommendationType.DEBT,
                    priority=Priority.MEDIUM,
                    title=f"Resolve {len(todo_findings)} TODO/FIXME comments",
                    description=f"{len(todo_findings)} TODO/FIXME markers found in source code",
                    evidence=f"Engineering Intelligence found {len(todo_findings)} debt markers",
                    impact="Unresolved TODOs indicate incomplete work",
                    effort="1-3 days",
                    suggested_action="Convert to tracked tasks or resolve immediately",
                    affected_files=list(set(f.file for f in todo_findings[:10])),
                    confidence=70,
                ))

            # God classes
            god_findings = [f for f in report.findings if f.type.value == "god_class"]
            if god_findings:
                recs.append(Recommendation(
                    type=RecommendationType.REFACTORING,
                    priority=Priority.HIGH,
                    title=f"Decompose {len(god_findings)} god classes",
                    description=f"{len(god_findings)} modules exceed 500 LOC with multiple classes",
                    evidence=f"Architecture smell detector found {len(god_findings)} god classes",
                    impact="God classes are hard to maintain and test",
                    effort="2-3 weeks",
                    suggested_action="Split into focused modules with single responsibilities",
                    affected_files=list(set(f.file for f in god_findings)),
                    confidence=80,
                ))

        except Exception as exc:
            logger.error(f"Engineering analysis failed: {exc}")

        return recs

    async def _analyze_architecture(self) -> List[Recommendation]:
        """Generate recommendations from architecture analysis."""
        recs = []
        try:
            from core.architecture import ArchitectureAnalyzer
            analyzer = ArchitectureAnalyzer(project_root=self.project_root)
            report = analyzer.analyze()

            # Layer violations
            violations = [v for v in report.violations if not v.valid]
            if violations:
                recs.append(Recommendation(
                    type=RecommendationType.ARCHITECTURE,
                    priority=Priority.HIGH,
                    title=f"Fix {len(violations)} layer boundary violations",
                    description=f"{len(violations)} modules depend on incorrect layers. "
                                f"Example: {violations[0].source_module} ({violations[0].source_layer}) "
                                f"→ {violations[0].target_module} ({violations[0].target_layer})",
                    evidence=f"Architecture Analyzer found {len(violations)} violations",
                    impact="Layer violations make the system harder to test and deploy independently",
                    effort="1 week",
                    suggested_action="Move shared logic to the appropriate layer or use dependency inversion",
                    affected_files=list(set(v.source_module for v in violations)),
                    confidence=85,
                ))

            # High coupling
            high_coupling = [m for m in report.metrics.values() if m.efferent_coupling > 8]
            if high_coupling:
                recs.append(Recommendation(
                    type=RecommendationType.ARCHITECTURE,
                    priority=Priority.MEDIUM,
                    title=f"Reduce coupling in {len(high_coupling)} highly-coupled modules",
                    description=f"{len(high_coupling)} modules have efferent coupling >8",
                    evidence=f"Architecture Analyzer found high coupling in {len(high_coupling)} modules",
                    impact="High coupling increases change ripple effects",
                    effort="1-2 weeks",
                    suggested_action="Apply dependency inversion and interface segregation",
                    affected_files=[m.name for m in high_coupling[:5]],
                    confidence=70,
                ))

        except Exception as exc:
            logger.error(f"Architecture analysis failed: {exc}")

        return recs

    async def _analyze_security(self) -> List[Recommendation]:
        """Generate recommendations from security analysis."""
        recs = []
        try:
            from core.security_ops import SecurityOperations
            sec = SecurityOperations(project_root=self.project_root)
            report = sec.scan()

            if report.findings:
                critical = [f for f in report.findings if f.severity.value == "critical"]
                high = [f for f in report.findings if f.severity.value == "high"]

                if critical or high:
                    recs.append(Recommendation(
                        type=RecommendationType.SECURITY,
                        priority=Priority.CRITICAL if critical else Priority.HIGH,
                        title=f"Fix {len(critical)} critical and {len(high)} high security findings",
                        description=f"Security scan found {len(critical)} critical and {len(high)} high severity findings",
                        evidence=f"Security Operations scan: {len(report.findings)} total findings",
                        impact="Security vulnerabilities can lead to data breaches",
                        effort="1-3 days",
                        suggested_action="Move secrets to environment variables, fix vulnerabilities",
                        affected_files=list(set(f.file for f in critical + high)),
                        confidence=90,
                    ))

            # SBOM analysis
            if report.sbom:
                recs.append(Recommendation(
                    type=RecommendationType.DEPENDENCY,
                    priority=Priority.LOW,
                    title=f"Audit {len(report.sbom)} dependencies for vulnerabilities",
                    description=f"SBOM contains {len(report.sbom)} packages — verify all are current",
                    evidence=f"SBOM generated with {len(report.sbom)} entries",
                    impact="Outdated dependencies may contain known CVEs",
                    effort="2 hours",
                    suggested_action="Run pip-audit and update outdated packages",
                    confidence=60,
                ))

        except Exception as exc:
            logger.error(f"Security analysis failed: {exc}")

        return recs

    async def _analyze_testing(self) -> List[Recommendation]:
        """Generate recommendations from test coverage analysis."""
        recs = []
        try:
            # Check for modules without tests
            import ast
            source_files = set()
            test_files = set()

            for path in self.project_root.rglob("*.py"):
                if any(p in path.parts for p in [".venv", "__pycache__", ".git", ".friday", "tests"]):
                    continue
                source_files.add(path.stem)

            for path in (self.project_root / "tests").glob("test_*.py"):
                test_files.add(path.stem.replace("test_", ""))

            untested = source_files - test_files - {"__init__"}
            if len(untested) > 20:
                recs.append(Recommendation(
                    type=RecommendationType.TESTING,
                    priority=Priority.HIGH,
                    title=f"Add tests for {len(untested)} untested modules",
                    description=f"{len(untested)} source modules have no corresponding test file",
                    evidence=f"Test coverage analysis: {len(untested)}/{len(source_files)} modules untested",
                    impact="Untested code is more likely to contain bugs",
                    effort="2-3 weeks",
                    suggested_action="Prioritize critical-path modules: agents, API routes, core/brain",
                    confidence=80,
                ))

        except Exception as exc:
            logger.error(f"Testing analysis failed: {exc}")

        return recs

    async def _analyze_documentation(self) -> List[Recommendation]:
        """Generate recommendations from documentation analysis."""
        recs = []
        try:
            docs_dir = self.project_root / "docs"
            if not docs_dir.exists():
                recs.append(Recommendation(
                    type=RecommendationType.DOCUMENTATION,
                    priority=Priority.HIGH,
                    title="Create docs/ directory",
                    description="No documentation directory found",
                    evidence="docs/ directory does not exist",
                    impact="Without docs, onboarding is harder",
                    effort="1 day",
                    suggested_action="Create docs/ with ARCHITECTURE.md, API_REFERENCE.md, DEPLOYMENT_GUIDE.md",
                    confidence=90,
                ))
            else:
                doc_files = list(docs_dir.glob("*.md"))
                required = ["ARCHITECTURE.md", "SECURITY_MODEL.md", "DEPLOYMENT_GUIDE.md", "DEVELOPER_GUIDE.md"]
                missing = [r for r in required if not (docs_dir / r).exists()]
                if missing:
                    recs.append(Recommendation(
                        type=RecommendationType.DOCUMENTATION,
                        priority=Priority.MEDIUM,
                        title=f"Create {len(missing)} missing documentation files",
                        description=f"Missing: {', '.join(missing)}",
                        evidence=f"Documentation check: {len(missing)} required docs missing",
                        impact="Incomplete documentation hinders adoption",
                        effort="2-3 days",
                        suggested_action=f"Create: {', '.join(missing)}",
                        confidence=75,
                    ))

        except Exception as exc:
            logger.error(f"Documentation analysis failed: {exc}")

        return recs

    async def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all recommendations."""
        recs = await self.generate_recommendations()
        by_priority = {}
        by_type = {}
        for r in recs:
            by_priority[r.priority.value] = by_priority.get(r.priority.value, 0) + 1
            by_type[r.type.value] = by_type.get(r.type.value, 0) + 1

        return {
            "total": len(recs),
            "by_priority": by_priority,
            "by_type": by_type,
            "top_3": [r.to_dict() for r in recs[:3]],
        }


# Singleton
_engine: Optional[RecommendationEngine] = None


def get_recommendation_engine(instance=None) -> RecommendationEngine:
    """Get the singleton RecommendationEngine instance.

    Args:
        instance: Optional instance to inject. When provided, the
            singleton is replaced with this instance. This is primarily
            intended for tests to substitute mock/fake instances.
    """
    global _engine
    if instance is not None:
        _engine = instance
    if _engine is None:
        _engine = RecommendationEngine()
    return _engine
