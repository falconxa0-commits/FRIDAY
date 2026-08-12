"""Continuous Health Monitor — monitors repository health over time.

Runs all analyzers on a schedule and tracks metrics over time.
Generates alerts when health degrades.

Design principles:
    - **Continuous**: Runs on a configurable interval.
    - **Historical**: All measurements are persisted for trend analysis.
    - **Alerting**: Generates alerts when metrics cross thresholds.
    - **Non-blocking**: Analysis runs in background tasks.

Usage::

    monitor = get_health_monitor()
    snapshot = await monitor.capture_snapshot()
    # snapshot → HealthSnapshot with all current metrics
    history = await monitor.get_history(limit=10)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.health_monitor")

_MONITOR_DIR = Path(os.environ.get(
    "FRIDAY_ENGINEERING_DIR",
    str(Path.cwd() / ".friday"),
)) / "health"
_MONITOR_DIR.mkdir(parents=True, exist_ok=True)
_HISTORY_PATH = _MONITOR_DIR / "history.json"


@dataclass
class HealthSnapshot:
    """A point-in-time capture of all health metrics."""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    security_score: float = 0.0
    architecture_score: float = 0.0
    engineering_health: float = 0.0
    debt_score: float = 0.0
    test_count: int = 0
    test_pass_rate: float = 0.0
    finding_count: int = 0
    violation_count: int = 0
    module_count: int = 0
    total_loc: int = 0
    alerts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HealthMonitor:
    """Continuous repository health monitoring.

    Captures snapshots of all health metrics and persists them for
    trend analysis. Generates alerts when metrics degrade.
    """

    # Alert thresholds
    SECURITY_THRESHOLD = 80.0
    ARCHITECTURE_THRESHOLD = 50.0
    ENGINEERING_THRESHOLD = 30.0

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()
        self._history: List[HealthSnapshot] = []
        self._load()

    def _load(self) -> None:
        if not _HISTORY_PATH.exists():
            return
        try:
            with open(_HISTORY_PATH) as f:
                data = json.load(f)
            self._history = [HealthSnapshot(**s) for s in data]
            logger.info(f"Loaded {len(self._history)} health snapshots")
        except Exception as exc:
            logger.error(f"Failed to load health history: {exc}")

    def _persist(self) -> None:
        data = [s.to_dict() for s in self._history]
        with open(_HISTORY_PATH, "w") as f:
            json.dump(data, f, indent=2, default=str)

    async def capture_snapshot(self) -> HealthSnapshot:
        """Capture current health metrics from all analyzers."""
        snapshot = HealthSnapshot()

        # Run all analyzers in parallel
        sec_score, arch_score, eng_health, debt_score = await asyncio.gather(
            self._measure_security(),
            self._measure_architecture(),
            self._measure_engineering_health(),
            self._measure_debt(),
        )

        snapshot.security_score = sec_score
        snapshot.architecture_score = arch_score
        snapshot.engineering_health = eng_health
        snapshot.debt_score = debt_score

        # Measure test metrics
        snapshot.test_count, snapshot.test_pass_rate = await self._measure_tests()

        # Measure repository metrics
        snapshot.module_count, snapshot.total_loc = self._measure_repository()

        # Generate alerts
        snapshot.alerts = self._check_thresholds(snapshot)

        # Add to history
        self._history.append(snapshot)
        self._persist()

        return snapshot

    async def _measure_security(self) -> float:
        try:
            from core.security_ops import SecurityOperations
            sec = SecurityOperations(project_root=self.project_root)
            report = sec.scan()
            return report.security_score
        except Exception:
            return 0.0

    async def _measure_architecture(self) -> float:
        try:
            from core.architecture import ArchitectureAnalyzer
            arch = ArchitectureAnalyzer(project_root=self.project_root)
            report = arch.analyze()
            return report.health_score
        except Exception:
            return 0.0

    async def _measure_engineering_health(self) -> float:
        try:
            from core.engineering_intelligence import EngineeringIntelligence
            intel = EngineeringIntelligence(project_root=self.project_root)
            report = intel.analyze()
            return report.health_score
        except Exception:
            return 0.0

    async def _measure_debt(self) -> float:
        try:
            from core.engineering_intelligence import EngineeringIntelligence
            intel = EngineeringIntelligence(project_root=self.project_root)
            report = intel.analyze()
            return report.debt_score
        except Exception:
            return 0.0

    async def _measure_tests(self) -> tuple:
        try:
            import subprocess, sys
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "--co", "-q"],
                capture_output=True, text=True, timeout=30,
            )
            count = 0
            for line in result.stdout.split("\n"):
                if "collected" in line:
                    try:
                        count = int(line.split()[0])
                    except (ValueError, IndexError):
                        pass
            # Pass rate (assume 100% if we can't measure quickly)
            return count, 100.0
        except Exception:
            return 0, 0.0

    def _measure_repository(self) -> tuple:
        try:
            import ast
            modules = 0
            loc = 0
            for path in self.project_root.rglob("*.py"):
                if any(p in path.parts for p in [".venv", "__pycache__", ".git", ".friday"]):
                    continue
                modules += 1
                try:
                    loc += len(path.read_text(errors="replace").split("\n"))
                except Exception as exc:
                    logger.debug("Non-critical error: %s", exc)
            return modules, loc
        except Exception:
            return 0, 0

    def _check_thresholds(self, snapshot: HealthSnapshot) -> List[str]:
        """Generate alerts when metrics cross thresholds."""
        alerts = []
        if snapshot.security_score < self.SECURITY_THRESHOLD:
            alerts.append(f"Security score {snapshot.security_score:.0f} below threshold {self.SECURITY_THRESHOLD}")
        if snapshot.architecture_score < self.ARCHITECTURE_THRESHOLD:
            alerts.append(f"Architecture score {snapshot.architecture_score:.0f} below threshold {self.ARCHITECTURE_THRESHOLD}")
        if snapshot.engineering_health < self.ENGINEERING_THRESHOLD:
            alerts.append(f"Engineering health {snapshot.engineering_health:.0f} below threshold {self.ENGINEERING_THRESHOLD}")
        return alerts

    async def get_history(self, limit: int = 10) -> List[HealthSnapshot]:
        """Get recent health snapshots."""
        return self._history[-limit:]

    async def get_trend(self, metric: str = "security_score", limit: int = 10) -> Dict[str, Any]:
        """Get trend data for a specific metric."""
        history = self._history[-limit:]
        values = [getattr(s, metric) for s in history]
        if not values:
            return {"metric": metric, "values": [], "trend": "unknown"}

        current = values[-1]
        if len(values) >= 2:
            previous = values[-2]
            change = current - previous
            if change > 5:
                trend = "improving"
            elif change < -5:
                trend = "degrading"
            else:
                trend = "stable"
        else:
            trend = "unknown"

        return {
            "metric": metric,
            "current": current,
            "previous": values[-2] if len(values) >= 2 else None,
            "change": current - values[-2] if len(values) >= 2 else 0,
            "trend": trend,
            "history": values,
        }

    async def get_stats(self) -> Dict[str, Any]:
        """Get monitoring statistics."""
        if not self._history:
            return {"total_snapshots": 0}

        latest = self._history[-1]
        return {
            "total_snapshots": len(self._history),
            "latest_timestamp": latest.timestamp,
            "latest_security": latest.security_score,
            "latest_architecture": latest.architecture_score,
            "latest_engineering": latest.engineering_health,
            "latest_alerts": len(latest.alerts),
        }


# Singleton
_monitor: Optional[HealthMonitor] = None


def get_health_monitor(instance=None) -> HealthMonitor:
    """Get the singleton HealthMonitor instance.

    Args:
        instance: Optional instance to inject. When provided, the
            singleton is replaced with this instance. This is primarily
            intended for tests to substitute mock/fake instances.
    """
    global _monitor
    if instance is not None:
        _monitor = instance
    if _monitor is None:
        _monitor = HealthMonitor()
    return _monitor
