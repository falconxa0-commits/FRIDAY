"""Continuous Architecture — ADR management + structural analysis.

Makes architecture a first-class subsystem with:
    - ADR (Architecture Decision Record) management
    - Module dependency graph
    - Coupling and cohesion analysis
    - Layer validation
    - Boundary enforcement

Design principles:
    - **Preserved**: Every architectural decision is recorded.
    - **Validated**: Layer boundaries are enforced automatically.
    - **Measured**: Coupling and cohesion are quantified.
    - **Actionable**: Violations generate engineering tasks.
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

logger = logging.getLogger("friday.architecture")


# ---------------------------------------------------------------------------
# Layer definitions
# ---------------------------------------------------------------------------
class Layer(str, Enum):
    """Architectural layers, ordered from low to high."""
    CORE = "core"           # Lowest — no dependencies on higher layers
    DATABASE = "database"   # Depends on core
    INTEGRATIONS = "integrations"  # Depends on core
    AGENTS = "agents"       # Depends on core, integrations
    API = "api"             # Depends on core, integrations, agents
    CLI = "cli"             # Depends on everything
    APPS = "apps"           # Highest — can depend on anything
    TESTS = "tests"         # Can import anything
    SCRIPTS = "scripts"     # Can import anything


# Valid dependency directions (lower → higher is OK, higher → lower is not)
VALID_DEPENDENCIES: Dict[Layer, Set[Layer]] = {
    Layer.CORE: set(),  # Core depends on nothing internal
    Layer.DATABASE: {Layer.CORE},
    Layer.INTEGRATIONS: {Layer.CORE},
    Layer.AGENTS: {Layer.CORE, Layer.INTEGRATIONS},
    Layer.API: {Layer.CORE, Layer.DATABASE, Layer.INTEGRATIONS, Layer.AGENTS},
    Layer.CLI: {Layer.CORE, Layer.INTEGRATIONS, Layer.AGENTS, Layer.API, Layer.DATABASE},
    Layer.APPS: {Layer.CORE, Layer.API, Layer.CLI},
    Layer.TESTS: set(),  # Tests can import anything
    Layer.SCRIPTS: set(), # Scripts can import anything
}


def _module_to_layer(module_path: str) -> Optional[Layer]:
    """Determine which layer a module belongs to."""
    parts = module_path.replace("/", ".").replace("\\", ".").split(".")
    if not parts:
        return None
    first = parts[0]
    for layer in Layer:
        if first == layer.value or first.startswith(layer.value):
            return layer
    return None


# ---------------------------------------------------------------------------
# Data classes
# -*-
@dataclass
class ADR:
    """Architecture Decision Record."""
    id: str = ""
    title: str = ""
    status: str = "proposed"  # proposed, accepted, deprecated, superseded
    date: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    context: str = ""
    decision: str = ""
    consequences: str = ""
    alternatives: str = ""
    related_adrs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ModuleMetrics:
    """Coupling and cohesion metrics for a module."""
    name: str
    layer: str
    afferent_coupling: int = 0   # how many modules depend on this
    efferent_coupling: int = 0   # how many modules this depends on
    instability: float = 0.0     # 0 = stable, 1 = unstable
    abstractness: float = 0.0    # 0 = concrete, 1 = abstract
    distance_from_main: float = 0.0  # 0 = optimal, 1 = far from optimal

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BoundaryViolation:
    """A layer boundary violation."""
    source_module: str
    source_layer: str
    target_module: str
    target_layer: str
    valid: bool
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ArchitectureReport:
    """Full architecture analysis report."""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_modules: int = 0
    total_dependencies: int = 0
    violations: List[BoundaryViolation] = field(default_factory=list)
    metrics: Dict[str, ModuleMetrics] = field(default_factory=dict)
    adrs: List[ADR] = field(default_factory=list)
    health_score: float = 0.0

    @property
    def summary(self) -> str:
        return (
            f"{self.total_modules} modules, {self.total_dependencies} deps, "
            f"{len(self.violations)} violations, health={self.health_score:.0f}/100"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_modules": self.total_modules,
            "total_dependencies": self.total_dependencies,
            "health_score": self.health_score,
            "summary": self.summary,
            "violations": [v.to_dict() for v in self.violations],
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
            "adrs": [a.to_dict() for a in self.adrs],
        }


# ---------------------------------------------------------------------------
# Architecture Analyzer
# -*-
class ArchitectureAnalyzer:
    """Analyzes project architecture: layers, coupling, cohesion."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()

    def analyze(self) -> ArchitectureReport:
        """Run full architecture analysis."""
        report = ArchitectureReport()

        # Collect all Python modules
        modules = self._collect_modules()
        report.total_modules = len(modules)

        # Build dependency graph
        dep_graph = self._build_dependency_graph(modules)
        report.total_dependencies = sum(len(deps) for deps in dep_graph.values())

        # Check layer boundaries
        report.violations = self._check_boundaries(modules, dep_graph)

        # Calculate coupling metrics
        report.metrics = self._calculate_metrics(modules, dep_graph)

        # Load ADRs from knowledge base
        report.adrs = self._load_adrs()

        # Calculate health score
        report.health_score = self._calculate_health(report)

        return report

    def _collect_modules(self) -> Dict[str, Layer]:
        """Collect all Python modules and their layers."""
        modules = {}
        for path in self.project_root.rglob("*.py"):
            if any(part in path.parts for part in (".venv", "__pycache__", ".git", "node_modules")):
                continue
            rel = str(path.relative_to(self.project_root))
            module_path = rel.replace("/", ".").replace("\\", ".").replace(".py", "")
            layer = _module_to_layer(rel)
            if layer:
                modules[module_path] = layer
        return modules

    def _build_dependency_graph(self, modules: Dict[str, Layer]) -> Dict[str, Set[str]]:
        """Build a dependency graph from imports."""
        graph = {mod: set() for mod in modules}

        for mod_path in modules:
            file_path = self.project_root / (mod_path.replace(".", "/") + ".py")
            if not file_path.exists():
                continue

            try:
                source = file_path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source)
            except Exception:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self._add_dependency(graph, mod_path, alias.name, modules)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        self._add_dependency(graph, mod_path, node.module, modules)

        return graph

    def _add_dependency(
        self,
        graph: Dict[str, Set[str]],
        source: str,
        import_name: str,
        modules: Dict[str, Layer],
    ) -> None:
        """Add a dependency if it's a local module."""
        # Try exact match
        if import_name in modules and import_name != source:
            graph[source].add(import_name)
            return

        # Try prefix match (e.g., "core.brain" matches "core")
        # Also match package imports: "api.routes" matches "api" package
        for mod in modules:
            if mod == source:
                continue
            # Check if import starts with this module (e.g., "api.routes" starts with "api.")
            if import_name.startswith(mod + "."):
                graph[source].add(mod)
                return
            # Also check the reverse: module "api.__init__" represents the "api" package
            # so "api" import should match "api.__init__"
            pkg_name = mod.rsplit(".__init__", 1)[0] if mod.endswith(".__init__") else None
            if pkg_name and (import_name == pkg_name or import_name.startswith(pkg_name + ".")):
                graph[source].add(mod)
                return

    def _check_boundaries(
        self,
        modules: Dict[str, Layer],
        graph: Dict[str, Set[str]],
    ) -> List[BoundaryViolation]:
        """Check for layer boundary violations."""
        violations = []

        for source, deps in graph.items():
            source_layer = modules.get(source)
            if not source_layer:
                continue

            for dep in deps:
                target_layer = modules.get(dep)
                if not target_layer:
                    continue

                if source_layer == target_layer:
                    continue  # same layer is always OK

                # Check if this dependency is valid
                valid_deps = VALID_DEPENDENCIES.get(source_layer, set())
                is_valid = target_layer in valid_deps or source_layer in (Layer.TESTS, Layer.SCRIPTS)

                if not is_valid:
                    violations.append(BoundaryViolation(
                        source_module=source,
                        source_layer=source_layer.value,
                        target_module=dep,
                        target_layer=target_layer.value,
                        valid=False,
                        message=f"{source_layer.value} layer cannot depend on {target_layer.value} layer",
                    ))

        return violations

    def _calculate_metrics(
        self,
        modules: Dict[str, Layer],
        graph: Dict[str, Set[str]],
    ) -> Dict[str, ModuleMetrics]:
        """Calculate coupling and cohesion metrics."""
        # Calculate afferent (incoming) and efferent (outgoing) coupling
        afferent: Dict[str, int] = {m: 0 for m in modules}
        efferent: Dict[str, int] = {m: 0 for m in modules}

        for source, deps in graph.items():
            efferent[source] = len(deps)
            for dep in deps:
                if dep in afferent:
                    afferent[dep] += 1

        metrics = {}
        for mod, layer in modules.items():
            ca = afferent.get(mod, 0)  # afferent coupling
            ce = efferent.get(mod, 0)  # efferent coupling
            total = ca + ce

            # Instability: I = Ce / (Ca + Ce), 0 = stable, 1 = unstable
            instability = ce / total if total > 0 else 0.0

            # Abstractness (simplified: count abstract classes / total classes)
            # For now, use 0.5 as placeholder
            abstractness = 0.5

            # Distance from main sequence: D = |A + I - 1|
            # 0 = optimal, 1 = far from optimal
            distance = abs(abstractness + instability - 1.0)

            metrics[mod] = ModuleMetrics(
                name=mod,
                layer=layer.value,
                afferent_coupling=ca,
                efferent_coupling=ce,
                instability=round(instability, 3),
                abstractness=round(abstractness, 3),
                distance_from_main=round(distance, 3),
            )

        return metrics

    def _load_adrs(self) -> List[ADR]:
        """Load ADRs from the knowledge base (synchronous read from disk)."""
        try:
            import os as _os
            from core.knowledge_base import _KB_ENTRIES_DIR, EntryType
            adrs = []
            if not _KB_ENTRIES_DIR.exists():
                return adrs
            for entry_path in _KB_ENTRIES_DIR.glob("*.json"):
                try:
                    with open(entry_path) as f:
                        data = json.load(f)
                    if data.get("type") == EntryType.ADR.value:
                        adrs.append(ADR(
                            id=data.get("id", ""),
                            title=data.get("title", ""),
                            status=data.get("status", "accepted"),
                            date=data.get("created_at", ""),
                            context=data.get("summary", ""),
                            decision=data.get("content", "")[:500],
                        ))
                except Exception:
                    continue
            return adrs
        except Exception as exc:
            logger.debug(f"Could not load ADRs: {exc}")
            return []

    def _calculate_health(self, report: ArchitectureReport) -> float:
        """Calculate architecture health score (0-100)."""
        if report.total_modules == 0:
            return 100.0

        # Start at 100, subtract for violations
        score = 100.0
        for v in report.violations:
            score -= 5.0

        # Penalize high instability in core modules
        for mod, metrics in report.metrics.items():
            if metrics.layer == "core" and metrics.instability > 0.5:
                score -= 2.0

        return max(0.0, min(100.0, score))


# ---------------------------------------------------------------------------
# Singleton
# -*-
_analyzer: Optional[ArchitectureAnalyzer] = None


def get_architecture_analyzer(instance=None) -> ArchitectureAnalyzer:
    """Get the singleton ArchitectureAnalyzer instance.

    Args:
        instance: Optional instance to inject. When provided, the
            singleton is replaced with this instance. This is primarily
            intended for tests to substitute mock/fake instances.
    """
    global _analyzer
    if instance is not None:
        _analyzer = instance
    if _analyzer is None:
        _analyzer = ArchitectureAnalyzer()
    return _analyzer
