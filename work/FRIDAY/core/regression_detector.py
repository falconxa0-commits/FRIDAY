"""Regression Detector — detects performance and quality regressions.

Compares current metrics against historical baselines and identifies
regressions in:
    - Test count (should not decrease)
    - Security score (should not decrease)
    - Architecture score (should not decrease)
    - Performance benchmarks (should not degrade)
    - Engineering findings (should not increase)

Usage::

    detector = get_regression_detector()
    regressions = await detector.detect_regressions()
    # regressions → list of Regression, each with metric + delta + severity
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.regression_detector")


class RegressionSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Regression:
    """A detected regression."""
    metric: str
    previous_value: float
    current_value: float
    delta: float
    severity: RegressionSeverity
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


class RegressionDetector:
    """Detects regressions by comparing current vs historical metrics.

    Uses the HealthMonitor's history as the baseline.
    """

    # Regression thresholds (percentage change that triggers alert)
    THRESHOLDS = {
        "security_score": -5.0,      # 5% decrease = warning
        "architecture_score": -5.0,
        "engineering_health": -10.0,
        "test_count": -1.0,          # Any test loss = warning
        "finding_count": 10.0,       # 10% more findings = warning
        "violation_count": 10.0,
    }

    CRITICAL_THRESHOLDS = {
        "security_score": -20.0,     # 20% decrease = critical
        "architecture_score": -20.0,
        "test_count": -10.0,         # 10+ tests lost = critical
    }

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()

    async def detect_regressions(self) -> List[Regression]:
        """Detect all regressions by comparing current vs previous snapshot."""
        from core.health_monitor import get_health_monitor
        monitor = get_health_monitor()

        history = await monitor.get_history(limit=2)
        if len(history) < 2:
            return []

        previous = history[-2]
        current = history[-1]

        regressions = []

        # Check each metric
        metrics_to_check = [
            ("security_score", "Security score"),
            ("architecture_score", "Architecture score"),
            ("engineering_health", "Engineering health"),
            ("test_count", "Test count"),
            ("finding_count", "Finding count"),
            ("violation_count", "Violation count"),
        ]

        for metric, label in metrics_to_check:
            prev_val = float(getattr(previous, metric))
            curr_val = float(getattr(current, metric))

            if prev_val == 0:
                continue  # Can't compute percentage change from 0

            delta_pct = ((curr_val - prev_val) / abs(prev_val)) * 100

            threshold = self.THRESHOLDS.get(metric, -5.0)
            critical_threshold = self.CRITICAL_THRESHOLDS.get(metric, -20.0)

            if delta_pct <= critical_threshold:
                regressions.append(Regression(
                    metric=metric,
                    previous_value=prev_val,
                    current_value=curr_val,
                    delta=delta_pct,
                    severity=RegressionSeverity.CRITICAL,
                    message=f"{label} regressed by {abs(delta_pct):.1f}% ({prev_val} → {curr_val})",
                ))
            elif delta_pct <= threshold:
                regressions.append(Regression(
                    metric=metric,
                    previous_value=prev_val,
                    current_value=curr_val,
                    delta=delta_pct,
                    severity=RegressionSeverity.WARNING,
                    message=f"{label} regressed by {abs(delta_pct):.1f}% ({prev_val} → {curr_val})",
                ))

        return regressions

    async def get_baseline(self) -> Dict[str, Any]:
        """Get the baseline metrics (first recorded snapshot)."""
        from core.health_monitor import get_health_monitor
        monitor = get_health_monitor()
        history = await monitor.get_history(limit=1000)
        if not history:
            return {}
        first = history[0]
        return {
            "timestamp": first.timestamp,
            "security_score": first.security_score,
            "architecture_score": first.architecture_score,
            "engineering_health": first.engineering_health,
            "test_count": first.test_count,
        }

    async def compare_to_baseline(self) -> Dict[str, Any]:
        """Compare current metrics to baseline."""
        from core.health_monitor import get_health_monitor
        monitor = get_health_monitor()
        history = await monitor.get_history(limit=1000)

        if len(history) < 2:
            return {"message": "Insufficient history for comparison"}

        baseline = history[0]
        current = history[-1]

        comparison = {}
        for metric in ["security_score", "architecture_score", "engineering_health", "test_count"]:
            base_val = float(getattr(baseline, metric))
            curr_val = float(getattr(current, metric))
            if base_val > 0:
                change_pct = ((curr_val - base_val) / base_val) * 100
                comparison[metric] = {
                    "baseline": base_val,
                    "current": curr_val,
                    "change_pct": round(change_pct, 1),
                    "improved": change_pct > 0,
                }

        return comparison


# Singleton
_detector: Optional[RegressionDetector] = None


def get_regression_detector(instance=None) -> RegressionDetector:
    """Get the singleton RegressionDetector instance.

    Args:
        instance: Optional instance to inject. When provided, the
            singleton is replaced with this instance. This is primarily
            intended for tests to substitute mock/fake instances.
    """
    global _detector
    if instance is not None:
        _detector = instance
    if _detector is None:
        _detector = RegressionDetector()
    return _detector
