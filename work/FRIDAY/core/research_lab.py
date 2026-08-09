"""Research Laboratory — track experiments, hypotheses, and findings.

Separates researched ideas from implemented features. Research
becomes institutional knowledge that informs future engineering.

Design principles:
    - **Hypothesis-driven**: Every experiment starts with a hypothesis.
    - **Reproducible**: Experiments record their setup and results.
    - **Comparable**: Results can be compared across experiments.
    - **Preserved**: Failures are recorded, not hidden.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.research_lab")

_RESEARCH_DIR = Path(os.environ.get(
    "FRIDAY_ENGINEERING_DIR",
    str(Path.cwd() / ".friday"),
)) / "research"
_RESEARCH_DIR.mkdir(parents=True, exist_ok=True)


class ExperimentStatus(str, Enum):
    PROPOSED = "proposed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class ExperimentType(str, Enum):
    BENCHMARK = "benchmark"       # Performance comparison
    ABLATION = "ablation"         # Remove a component, measure impact
    ALGORITHM = "algorithm"       # Compare algorithms
    ARCHITECTURE = "architecture" # Compare architectural approaches
    HYPERPARAMETER = "hyperparameter"  # Tune a parameter
    FEASIBILITY = "feasibility"   # Is this even possible?


@dataclass
class Hypothesis:
    """A testable hypothesis."""
    statement: str = ""
    null_hypothesis: str = ""
    expected_result: str = ""
    metrics: List[str] = field(default_factory=list)


@dataclass
class ExperimentResult:
    """Results of an experiment."""
    metrics: Dict[str, float] = field(default_factory=dict)
    observations: str = ""
    conclusion: str = ""
    supports_hypothesis: Optional[bool] = None


@dataclass
class Experiment:
    """A research experiment."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    type: ExperimentType = ExperimentType.FEASIBILITY
    status: ExperimentStatus = ExperimentStatus.PROPOSED
    hypothesis: Hypothesis = field(default_factory=Hypothesis)
    setup: str = ""               # How to reproduce
    parameters: Dict[str, Any] = field(default_factory=dict)
    result: Optional[ExperimentResult] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str = ""
    related_experiments: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Experiment":
        if "type" in data and isinstance(data["type"], str):
            data["type"] = ExperimentType(data["type"])
        if "status" in data and isinstance(data["status"], str):
            data["status"] = ExperimentStatus(data["status"])
        if "hypothesis" in data and isinstance(data["hypothesis"], dict):
            data["hypothesis"] = Hypothesis(**data["hypothesis"])
        if "result" in data and isinstance(data["result"], dict):
            data["result"] = ExperimentResult(**data["result"])
        return cls(**data)


class ResearchLab:
    """Manages research experiments.

    Usage::

        lab = get_research_lab()
        exp = await lab.create_experiment(
            title="ANN vs brute-force vector search",
            type=ExperimentType.ALGORITHM,
            hypothesis=Hypothesis(
                statement="ANN (hnswlib) is 100x faster than brute-force at 10k vectors",
                expected_result="100x speedup at p99",
                metrics=["latency_p99", "recall"],
            ),
            setup="Populate 10k vectors, query 100 times, measure p99",
        )
        # ... run experiment ...
        await lab.record_result(exp.id, ExperimentResult(
            metrics={"latency_p99": 0.5, "recall": 0.98},
            conclusion="ANN is 100x faster with 98% recall",
            supports_hypothesis=True,
        ))
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or _RESEARCH_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._experiments: Dict[str, Experiment] = {}
        self._load()

    def _load(self) -> None:
        """Load experiments from disk."""
        for path in self.base_dir.glob("*.json"):
            try:
                with open(path) as f:
                    data = json.load(f)
                exp = Experiment.from_dict(data)
                self._experiments[exp.id] = exp
            except Exception as exc:
                logger.error(f"Failed to load experiment {path}: {exc}")

    def _persist(self, exp: Experiment) -> None:
        path = self.base_dir / f"{exp.id}.json"
        with open(path, "w") as f:
            json.dump(exp.to_dict(), f, indent=2, default=str)

    async def create_experiment(
        self,
        title: str,
        type: ExperimentType = ExperimentType.FEASIBILITY,
        hypothesis: Optional[Hypothesis] = None,
        setup: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> Experiment:
        exp = Experiment(
            title=title,
            type=type,
            hypothesis=hypothesis or Hypothesis(),
            setup=setup,
            parameters=parameters or {},
            tags=tags or [],
        )
        self._experiments[exp.id] = exp
        self._persist(exp)
        logger.info(f"Created experiment {exp.id[:8]}: {title}")
        return exp

    async def start_experiment(self, exp_id: str) -> Optional[Experiment]:
        exp = self._experiments.get(exp_id)
        if not exp:
            return None
        exp.status = ExperimentStatus.RUNNING
        self._persist(exp)
        return exp

    async def record_result(
        self,
        exp_id: str,
        result: ExperimentResult,
    ) -> Optional[Experiment]:
        exp = self._experiments.get(exp_id)
        if not exp:
            return None
        exp.result = result
        exp.status = ExperimentStatus.COMPLETED if result.supports_hypothesis is not None else ExperimentStatus.COMPLETED
        exp.completed_at = datetime.now(timezone.utc).isoformat()
        self._persist(exp)
        logger.info(f"Recorded result for experiment {exp_id[:8]}: {result.conclusion[:60]}")
        return exp

    async def abandon_experiment(self, exp_id: str, reason: str = "") -> Optional[Experiment]:
        exp = self._experiments.get(exp_id)
        if not exp:
            return None
        exp.status = ExperimentStatus.ABANDONED
        exp.completed_at = datetime.now(timezone.utc).isoformat()
        if reason:
            exp.result = ExperimentResult(observations=f"Abandoned: {reason}")
        self._persist(exp)
        return exp

    async def get_experiment(self, exp_id: str) -> Optional[Experiment]:
        return self._experiments.get(exp_id)

    async def list_experiments(
        self,
        status: Optional[ExperimentStatus] = None,
        type: Optional[ExperimentType] = None,
    ) -> List[Experiment]:
        exps = list(self._experiments.values())
        if status:
            exps = [e for e in exps if e.status == status]
        if type:
            exps = [e for e in exps if e.type == type]
        exps.sort(key=lambda e: e.created_at, reverse=True)
        return exps

    async def compare_experiments(self, exp_ids: List[str]) -> Dict[str, Any]:
        """Compare results across multiple experiments."""
        experiments = []
        for eid in exp_ids:
            exp = self._experiments.get(eid)
            if exp and exp.result:
                experiments.append(exp)

        if len(experiments) < 2:
            return {"error": "Need at least 2 completed experiments to compare"}

        # Collect all metrics
        all_metrics: Set[str] = set()
        for exp in experiments:
            if exp.result:
                all_metrics.update(exp.result.metrics.keys())

        comparison = {}
        for metric in all_metrics:
            values = {}
            for exp in experiments:
                if exp.result and metric in exp.result.metrics:
                    values[exp.title] = exp.result.metrics[metric]
            comparison[metric] = values

        return {
            "experiments": [e.title for e in experiments],
            "metrics": comparison,
            "best": {
                metric: max(vals.items(), key=lambda x: x[1])[0]
                for metric, vals in comparison.items()
                if vals
            },
        }

    async def get_stats(self) -> Dict[str, Any]:
        exps = list(self._experiments.values())
        return {
            "total": len(exps),
            "by_status": {
                s.value: sum(1 for e in exps if e.status == s)
                for s in ExperimentStatus
            },
            "by_type": {
                t.value: sum(1 for e in exps if e.type == t)
                for t in ExperimentType
            },
        }


# Singleton
_lab: Optional[ResearchLab] = None


def get_research_lab() -> ResearchLab:
    global _lab
    if _lab is None:
        _lab = ResearchLab()
    return _lab
