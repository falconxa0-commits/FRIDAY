"""Engineering Intelligence — continuous codebase analysis.

Monitors the repository for technical debt, architecture smells,
complexity hotspots, and dependency issues. Generates engineering
recommendations that flow into the task system.

Design principles:
    - **Static analysis only**: no code execution, safe to run anytime.
    - **Incremental**: can analyze a single file or the whole repo.
    - **Actionable**: every finding includes a recommendation.
    - **Quantified**: every finding has a severity score (0-10).

Analyzers:
    1. Complexity scorer (cyclomatic complexity via AST)
    2. Technical debt detector (TODOs, FIXMEs, stubs, dead code)
    3. Dependency analyzer (import graph, circular deps)
    4. Code ownership map (which module owns what)
    5. Architecture smell detector (god classes, shotgun surgery)
    6. Risk predictor (hotspots likely to break)
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("friday.eng_intelligence")

_PROJECT_ROOT = Path(os.environ.get("FRIDAY_PROJECT_ROOT", str(Path.cwd())))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def score(self) -> int:
        return {"info": 1, "low": 3, "medium": 5, "high": 7, "critical": 9}[self.value]


class FindingType(str, Enum):
    COMPLEXITY = "complexity"
    TODO = "todo"
    FIXME = "fixme"
    STUB = "stub"
    DEAD_CODE = "dead_code"
    CIRCULAR_DEP = "circular_dependency"
    GOD_CLASS = "god_class"
    SHOTGUN_SURGERY = "shotgun_surgery"
    DUPLICATE_CODE = "duplicate_code"
    MISSING_TEST = "missing_test"
    HARDCODED_SECRET = "hardcoded_secret"
    RISK_HOTSPOT = "risk_hotspot"


@dataclass
class Finding:
    """A single engineering finding."""
    type: FindingType
    severity: Severity
    file: str
    line: int
    message: str
    recommendation: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ModuleInfo:
    """Information about a single Python module."""
    path: str
    loc: int
    classes: int
    functions: int
    imports: List[str]
    imported_by: List[str]
    complexity: float
    finding_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EngineeringReport:
    """Full engineering intelligence report."""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    project_root: str = ""
    total_files: int = 0
    total_loc: int = 0
    findings: List[Finding] = field(default_factory=list)
    modules: Dict[str, ModuleInfo] = field(default_factory=dict)
    health_score: float = 0.0  # 0-100, higher is better
    debt_score: float = 0.0   # 0-100, higher is worse

    @property
    def summary(self) -> str:
        by_sev = {}
        for f in self.findings:
            by_sev[f.severity.value] = by_sev.get(f.severity.value, 0) + 1
        parts = [f"{v} {k}" for k, v in by_sev.items()]
        return f"{len(self.findings)} findings ({', '.join(parts)}), health={self.health_score:.0f}/100"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "project_root": self.project_root,
            "total_files": self.total_files,
            "total_loc": self.total_loc,
            "health_score": self.health_score,
            "debt_score": self.debt_score,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "modules": {k: v.to_dict() for k, v in self.modules.items()},
        }


# ---------------------------------------------------------------------------
# Analyzers
# ---------------------------------------------------------------------------
class ComplexityAnalyzer:
    """Measures cyclomatic complexity using AST."""

    @staticmethod
    def analyze_file(filepath: Path, project_root: Optional[Path] = None) -> Tuple[int, List[Finding]]:
        """Return (max_complexity, findings) for a file."""
        root = project_root or _PROJECT_ROOT
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except Exception:
            return 0, []

        findings = []
        max_complexity = 0

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                complexity = ComplexityAnalyzer._function_complexity(node)
                max_complexity = max(max_complexity, complexity)

                if complexity > 15:
                    sev = Severity.CRITICAL if complexity > 25 else Severity.HIGH
                    try:
                        rel_path = str(filepath.relative_to(root))
                    except ValueError:
                        rel_path = str(filepath)
                    findings.append(Finding(
                        type=FindingType.COMPLEXITY,
                        severity=sev,
                        file=rel_path,
                        line=node.lineno,
                        message=f"{node.name}() has complexity {complexity}",
                        recommendation="Break into smaller functions. High complexity increases bug risk.",
                        metadata={"function": node.name, "complexity": complexity},
                    ))
                elif complexity > 10:
                    try:
                        rel_path = str(filepath.relative_to(root))
                    except ValueError:
                        rel_path = str(filepath)
                    findings.append(Finding(
                        type=FindingType.COMPLEXITY,
                        severity=Severity.MEDIUM,
                        file=rel_path,
                        line=node.lineno,
                        message=f"{node.name}() has complexity {complexity}",
                        recommendation="Consider refactoring to reduce branching.",
                        metadata={"function": node.name, "complexity": complexity},
                    ))

        return max_complexity, findings

    @staticmethod
    def _function_complexity(node: ast.FunctionDef) -> int:
        """Calculate cyclomatic complexity of a function."""
        complexity = 1  # base path
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor)):
                complexity += 1
            elif isinstance(child, ast.ExceptHandler):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
            elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                complexity += 1
        return complexity


class TechnicalDebtAnalyzer:
    """Detects TODOs, FIXMEs, stubs, and dead code."""

    TODO_PATTERN = re.compile(r"#\s*(TODO|FIXME|HACK|XXX|BUG)", re.IGNORECASE)
    STUB_PATTERNS = [
        re.compile(r"pass\s*$"),
        re.compile(r"NotImplementedError"),
        re.compile(r"raise\s+NotImplemented"),
        re.compile(r'"Not implemented'),
        re.compile(r"'Not implemented"),
    ]

    @staticmethod
    def analyze_file(filepath: Path, project_root: Optional[Path] = None) -> List[Finding]:
        root = project_root or _PROJECT_ROOT
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
            lines = source.split("\n")
        except Exception:
            return []

        findings = []
        try:
            rel_path = str(filepath.relative_to(root))
        except ValueError:
            rel_path = str(filepath)

        for i, line in enumerate(lines, 1):
            # TODO/FIXME detection
            match = TechnicalDebtAnalyzer.TODO_PATTERN.search(line)
            if match:
                tag = match.group(1).upper()
                sev = Severity.HIGH if tag in ("FIXME", "BUG", "XXX") else Severity.MEDIUM
                findings.append(Finding(
                    type=FindingType.TODO if tag == "TODO" else FindingType.FIXME,
                    severity=sev,
                    file=rel_path,
                    line=i,
                    message=f"{tag}: {line.strip()[:80]}",
                    recommendation="Resolve or convert to a tracked task.",
                ))

        # Stub detection
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = node.body
                    if len(body) == 1:
                        stmt = body[0]
                        if isinstance(stmt, ast.Pass):
                            findings.append(Finding(
                                type=FindingType.STUB,
                                severity=Severity.MEDIUM,
                                file=rel_path,
                                line=node.lineno,
                                message=f"{node.name}() is a stub (pass only)",
                                recommendation="Implement or mark as abstract.",
                            ))
                        elif isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call):
                            if isinstance(stmt.exc.func, ast.Name) and stmt.exc.func.id == "NotImplementedError":
                                findings.append(Finding(
                                    type=FindingType.STUB,
                                    severity=Severity.MEDIUM,
                                    file=rel_path,
                                    line=node.lineno,
                                    message=f"{node.name}() raises NotImplementedError",
                                    recommendation="Implement or mark as abstract.",
                                ))
        except SyntaxError:
            pass

        return findings


class DependencyAnalyzer:
    """Builds import graph and detects circular dependencies."""

    @staticmethod
    def analyze(project_root: Path) -> Tuple[Dict[str, ModuleInfo], List[Finding]]:
        """Analyze all Python files and build dependency graph."""
        project_root = Path(project_root)
        modules: Dict[str, ModuleInfo] = {}
        findings: List[Finding] = []

        # Collect all Python files
        py_files = []
        for path in project_root.rglob("*.py"):
            if any(part in path.parts for part in (".venv", "__pycache__", ".git", "node_modules")):
                continue
            py_files.append(path)

        # Build module info
        for filepath in py_files:
            try:
                source = filepath.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source)
            except Exception:
                continue

            rel_path = str(filepath.relative_to(project_root))
            module_name = rel_path.replace("/", ".").replace(".py", "")

            imports = []
            classes = 0
            functions = 0

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.append(node.module)
                elif isinstance(node, ast.ClassDef):
                    classes += 1
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions += 1

            loc = len(source.split("\n"))
            modules[module_name] = ModuleInfo(
                path=rel_path,
                loc=loc,
                classes=classes,
                functions=functions,
                imports=imports,
                imported_by=[],
                complexity=0.0,
                finding_count=0,
            )

        # Build reverse imports
        for mod_name, mod_info in modules.items():
            for imp in mod_info.imports:
                # Find which local module this import corresponds to
                for other_name in modules:
                    if imp == other_name or imp.startswith(other_name + "."):
                        modules[other_name].imported_by.append(mod_name)
                        break

        # Detect circular dependencies
        cycles = DependencyAnalyzer._find_cycles(modules)
        for cycle in cycles:
            findings.append(Finding(
                type=FindingType.CIRCULAR_DEP,
                severity=Severity.HIGH,
                file=cycle[0].replace(".", "/") + ".py",
                line=0,
                message=f"Circular dependency: {' → '.join(cycle)} → {cycle[0]}",
                recommendation="Break the cycle by extracting shared logic to a lower-level module.",
                metadata={"cycle": cycle},
            ))

        return modules, findings

    @staticmethod
    def _find_cycles(modules: Dict[str, ModuleInfo]) -> List[List[str]]:
        """Detect circular dependencies using DFS."""
        cycles = []
        visited: Set[str] = set()
        rec_stack: List[str] = []

        def dfs(node: str) -> None:
            if node in rec_stack:
                # Found a cycle
                idx = rec_stack.index(node)
                cycle = rec_stack[idx:] + [node]
                cycles.append(cycle)
                return
            if node in visited:
                return

            visited.add(node)
            rec_stack.append(node)

            mod = modules.get(node)
            if mod:
                for imp in mod.imports:
                    # Only follow local imports
                    for other in modules:
                        if imp == other or (imp.startswith(other + ".") and other != node):
                            dfs(other)

            rec_stack.pop()

        for mod_name in modules:
            if mod_name not in visited:
                dfs(mod_name)

        # Deduplicate cycles
        unique = []
        seen = set()
        for cycle in cycles:
            key = tuple(sorted(cycle))
            if key not in seen:
                seen.add(key)
                unique.append(cycle)

        return unique


class ArchitectureSmellDetector:
    """Detects god classes and other architecture smells."""

    GOD_CLASS_THRESHOLD = 500  # LOC
    GOD_CLASS_METHODS = 20     # number of methods

    @staticmethod
    def analyze(modules: Dict[str, ModuleInfo]) -> List[Finding]:
        findings = []

        for mod_name, mod_info in modules.items():
            # God class detection
            if mod_info.loc > ArchitectureSmellDetector.GOD_CLASS_THRESHOLD and mod_info.classes > 0:
                findings.append(Finding(
                    type=FindingType.GOD_CLASS,
                    severity=Severity.HIGH if mod_info.loc > 1000 else Severity.MEDIUM,
                    file=mod_info.path,
                    line=0,
                    message=f"{mod_info.path} is a god class ({mod_info.loc} LOC, {mod_info.classes} classes)",
                    recommendation="Decompose into smaller, focused modules with single responsibilities.",
                    metadata={"loc": mod_info.loc, "classes": mod_info.classes},
                ))

            # Shotgun surgery: module imported by many others
            if len(mod_info.imported_by) > 10:
                findings.append(Finding(
                    type=FindingType.SHOTGUN_SURGERY,
                    severity=Severity.MEDIUM,
                    file=mod_info.path,
                    line=0,
                    message=f"{mod_info.path} is imported by {len(mod_info.imported_by)} modules",
                    recommendation="High fan-in. Consider if this module's responsibilities are too broad.",
                    metadata={"imported_by_count": len(mod_info.imported_by)},
                ))

        return findings


class RiskPredictor:
    """Predicts which files are most likely to break."""

    @staticmethod
    def predict(findings: List[Finding], modules: Dict[str, ModuleInfo]) -> List[Finding]:
        """Generate risk hotspot findings based on finding density + complexity."""
        # Count findings per file
        file_findings: Dict[str, List[Finding]] = {}
        for f in findings:
            file_findings.setdefault(f.file, []).append(f)

        risk_findings = []
        for file, file_fs in file_findings.items():
            if len(file_fs) < 3:
                continue

            # Calculate risk score
            critical_count = sum(1 for f in file_fs if f.severity == Severity.CRITICAL)
            high_count = sum(1 for f in file_fs if f.severity == Severity.HIGH)
            risk_score = critical_count * 3 + high_count * 2 + len(file_fs)

            if risk_score >= 10:
                risk_findings.append(Finding(
                    type=FindingType.RISK_HOTSPOT,
                    severity=Severity.CRITICAL if risk_score >= 20 else Severity.HIGH,
                    file=file,
                    line=0,
                    message=f"Risk hotspot: {len(file_fs)} findings (risk score {risk_score})",
                    recommendation="Prioritize refactoring this file. High finding density indicates structural problems.",
                    metadata={"risk_score": risk_score, "finding_count": len(file_fs)},
                ))

        return risk_findings


# ---------------------------------------------------------------------------
# Main Engineering Intelligence engine
# ---------------------------------------------------------------------------
class EngineeringIntelligence:
    """Runs all analyzers and generates a report."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or _PROJECT_ROOT

    def analyze(self) -> EngineeringReport:
        """Run full analysis and return report."""
        report = EngineeringReport(project_root=str(self.project_root))

        # Build dependency graph
        modules, dep_findings = DependencyAnalyzer.analyze(self.project_root)
        report.modules = modules
        report.findings.extend(dep_findings)

        # Run per-file analyzers
        py_files = [self.project_root / m.path for m in modules.values()]
        report.total_files = len(py_files)

        for filepath in py_files:
            if not filepath.exists():
                continue

            # Complexity
            max_complexity, complexity_findings = ComplexityAnalyzer.analyze_file(
                filepath, project_root=self.project_root
            )
            report.findings.extend(complexity_findings)

            # Technical debt
            debt_findings = TechnicalDebtAnalyzer.analyze_file(
                filepath, project_root=self.project_root
            )
            report.findings.extend(debt_findings)

            # Update module info
            mod_name = str(filepath.relative_to(self.project_root)).replace("/", ".").replace(".py", "")
            if mod_name in modules:
                modules[mod_name].complexity = float(max_complexity)
                modules[mod_name].finding_count = len(complexity_findings) + len(debt_findings)

            report.total_loc += len(filepath.read_text(encoding="utf-8", errors="replace").split("\n"))

        # Architecture smells
        arch_findings = ArchitectureSmellDetector.analyze(modules)
        report.findings.extend(arch_findings)

        # Risk prediction
        risk_findings = RiskPredictor.predict(report.findings, modules)
        report.findings.extend(risk_findings)

        # Calculate scores
        report.health_score = self._calculate_health(report)
        report.debt_score = self._calculate_debt(report)

        return report

    @staticmethod
    def _calculate_health(report: EngineeringReport) -> float:
        """Calculate health score (0-100, higher is better)."""
        if report.total_files == 0:
            return 100.0

        # Start at 100, subtract for findings
        score = 100.0
        for f in report.findings:
            score -= f.severity.score * 0.5

        # Normalize by file count
        score = score / max(report.total_files / 50, 1)

        return max(0.0, min(100.0, score))

    @staticmethod
    def _calculate_debt(report: EngineeringReport) -> float:
        """Calculate debt score (0-100, higher is worse)."""
        if report.total_loc == 0:
            return 0.0

        # Debt = sum of finding severities / 1000 LOC
        total_severity = sum(f.severity.score for f in report.findings)
        debt = (total_severity / report.total_loc) * 1000

        return min(100.0, debt)


# ---------------------------------------------------------------------------
# Singleton
# -*-
_intelligence: Optional[EngineeringIntelligence] = None


def get_engineering_intelligence(instance=None) -> EngineeringIntelligence:
    """Get the singleton EngineeringIntelligence instance.

    Args:
        instance: Optional instance to inject. When provided, the
            singleton is replaced with this instance. This is primarily
            intended for tests to substitute mock/fake instances.
    """
    global _intelligence
    if instance is not None:
        _intelligence = instance
    if _intelligence is None:
        _intelligence = EngineeringIntelligence()
    return _intelligence
