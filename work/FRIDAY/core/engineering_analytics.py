"""Engineering Analytics — unified dashboard aggregator.

Aggregates metrics from every Age III subsystem into a single
dashboard view, computes velocity and quality trends, and
ranks the top engineering risks.

Design principles:
    - **Unified**: One call returns the whole dashboard.
    - **Resilient**: Each subsystem is wrapped in try/except so
      a single failing subsystem never breaks the dashboard.
    - **Read-only**: Analytics never mutates underlying state.
    - **Parallel**: Subsystem calls run concurrently via asyncio.gather.

Data sources:
    - TaskQueue               — backlog, completion history
    - KnowledgeBase           — institutional memory
    - HealthMonitor           — historical health snapshots
    - EngineeringIntelligence — complexity, debt, risk hotspots
    - ArchitectureAnalyzer    — violations, coupling
    - SecurityOperations      — findings, SBOM

Usage::

    analytics = get_engineering_analytics()
    dashboard = await analytics.get_dashboard()
    velocity = await analytics.get_velocity()
    risks = await analytics.get_risk_assessment()
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.engineering_analytics")


# Severity -> numeric risk score (higher = worse). Used to rank risks
# from heterogeneous sources on a common 0-100 scale.
_SEVERITY_SCORE: Dict[str, int] = {
    "critical": 100,
    "high": 75,
    "medium": 50,
    "low": 25,
    "info": 10,
}


def _severity_score(severity: Any) -> int:
    """Normalize a severity value (enum or str) to a 0-100 risk score."""
    try:
        value = severity.value if hasattr(severity, "value") else str(severity)
    except Exception:
        value = "info"
    return _SEVERITY_SCORE.get(str(value).lower(), 10)


class EngineeringAnalytics:
    """Aggregates engineering metrics into a unified dashboard.

    Every public method is async and resilient — a single subsystem
    failure returns a graceful default rather than raising. The
    dashboard is read-only: no underlying state is mutated.
    """

    # How many days/weeks of velocity history to return.
    VELOCITY_DAILY_WINDOW_DAYS = 14
    VELOCITY_WEEKLY_WINDOW_WEEKS = 8
    # Cap on the number of risks returned by get_risk_assessment.
    RISK_CAP = 50

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = Path(project_root) if project_root else Path.cwd()

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    async def get_dashboard(self) -> Dict[str, Any]:
        """Aggregate all engineering metrics into one dashboard dict.

        Each subsystem is fetched concurrently and individually wrapped
        in try/except so a single failure never breaks the dashboard.
        """
        (
            task_stats,
            kb_stats,
            health_stats,
            eng_summary,
            arch_summary,
            sec_summary,
        ) = await asyncio.gather(
            self._get_task_queue_stats(),
            self._get_knowledge_base_stats(),
            self._get_health_stats(),
            self._get_engineering_summary(),
            self._get_architecture_summary(),
            self._get_security_summary(),
        )

        subsystems = {
            "task_queue": task_stats,
            "knowledge_base": kb_stats,
            "health": health_stats,
            "engineering": eng_summary,
            "architecture": arch_summary,
            "security": sec_summary,
        }

        overall_health = self._average([
            eng_summary.get("health_score"),
            arch_summary.get("health_score"),
            sec_summary.get("security_score"),
            health_stats.get("latest_security"),
            health_stats.get("latest_engineering"),
        ])

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall_health": overall_health,
            "task_queue": task_stats,
            "knowledge_base": kb_stats,
            "health": health_stats,
            "engineering": eng_summary,
            "architecture": arch_summary,
            "security": sec_summary,
            "subsystem_status": {
                name: ("ok" if data else "degraded")
                for name, data in subsystems.items()
            },
        }

    # ------------------------------------------------------------------
    # Velocity
    # ------------------------------------------------------------------
    async def get_velocity(self) -> Dict[str, Any]:
        """Compute task completion velocity (daily + weekly series).

        Returns a dict with ``daily`` (last 14 days) and ``weekly``
        (last 8 ISO weeks) time series, plus aggregate counts.
        """
        tasks = await self._list_completed_tasks()
        today = datetime.now(timezone.utc).date()

        daily_buckets: Dict[str, int] = defaultdict(int)
        for day_offset in range(self.VELOCITY_DAILY_WINDOW_DAYS - 1, -1, -1):
            day = today - timedelta(days=day_offset)
            daily_buckets[day.isoformat()] = 0

        weekly_buckets: Dict[str, int] = defaultdict(int)
        for week_offset in range(self.VELOCITY_WEEKLY_WINDOW_WEEKS - 1, -1, -1):
            week_start = today - timedelta(days=today.weekday()) - timedelta(weeks=week_offset)
            weekly_buckets[week_start.isoformat()] = 0

        for task in tasks:
            completed_at = self._extract_completed_at(task)
            if not completed_at:
                continue
            day = self._parse_date(completed_at)
            if day is None:
                continue
            # Daily bucket
            if (today - day).days < self.VELOCITY_DAILY_WINDOW_DAYS:
                daily_buckets[day.isoformat()] += 1
            # Weekly bucket (ISO week starting Monday)
            week_start = day - timedelta(days=day.weekday())
            weeks_back = (today - week_start).days // 7
            if 0 <= weeks_back < self.VELOCITY_WEEKLY_WINDOW_WEEKS:
                weekly_buckets[week_start.isoformat()] += 1

        daily_series = [
            {"date": d, "completed": c}
            for d, c in sorted(daily_buckets.items())
        ]
        weekly_series = [
            {"week_start": w, "completed": c}
            for w, c in sorted(weekly_buckets.items())
        ]

        total_completed = len(tasks)
        avg_per_day = round(
            total_completed / float(self.VELOCITY_DAILY_WINDOW_DAYS), 2
        ) if total_completed else 0.0
        avg_per_week = round(
            total_completed / float(self.VELOCITY_WEEKLY_WINDOW_WEEKS), 2
        ) if total_completed else 0.0

        return {
            "daily": daily_series,
            "weekly": weekly_series,
            "total_completed": total_completed,
            "average_per_day": avg_per_day,
            "average_per_week": avg_per_week,
            "window_days": self.VELOCITY_DAILY_WINDOW_DAYS,
            "window_weeks": self.VELOCITY_WEEKLY_WINDOW_WEEKS,
        }

    # ------------------------------------------------------------------
    # Quality trend
    # ------------------------------------------------------------------
    async def get_quality_trend(self) -> Dict[str, Any]:
        """Return quality metrics over time from HealthMonitor history."""
        history = await self._get_health_history(limit=30)

        snapshots: List[Dict[str, Any]] = []
        for snap in history:
            snap_dict = snap.to_dict() if hasattr(snap, "to_dict") else dict(snap)
            snapshots.append({
                "timestamp": snap_dict.get("timestamp"),
                "security_score": float(snap_dict.get("security_score", 0.0) or 0.0),
                "architecture_score": float(snap_dict.get("architecture_score", 0.0) or 0.0),
                "engineering_health": float(snap_dict.get("engineering_health", 0.0) or 0.0),
                "debt_score": float(snap_dict.get("debt_score", 0.0) or 0.0),
                "test_count": int(snap_dict.get("test_count", 0) or 0),
                "test_pass_rate": float(snap_dict.get("test_pass_rate", 0.0) or 0.0),
                "finding_count": int(snap_dict.get("finding_count", 0) or 0),
                "violation_count": int(snap_dict.get("violation_count", 0) or 0),
                "alerts": list(snap_dict.get("alerts", []) or []),
            })

        latest = snapshots[-1] if snapshots else None
        trend_direction = self._trend_direction(snapshots, "engineering_health")

        return {
            "snapshots": snapshots,
            "latest": latest,
            "trend_direction": trend_direction,
            "snapshot_count": len(snapshots),
        }

    # ------------------------------------------------------------------
    # Risk assessment
    # ------------------------------------------------------------------
    async def get_risk_assessment(self) -> List[Dict[str, Any]]:
        """Return a ranked list of top engineering risks.

        Combines:
            - Risk hotspots + critical/high findings from EngineeringIntelligence
            - Critical/high security findings from SecurityOperations
            - Layer boundary violations from ArchitectureAnalyzer
            - Blocked tasks from TaskQueue

        Returns a list of risk dicts sorted by severity score (desc).
        """
        eng_findings, sec_findings, arch_violations, blocked_tasks = await asyncio.gather(
            self._get_engineering_findings(),
            self._get_security_findings(),
            self._get_architecture_violations(),
            self._get_blocked_tasks(),
        )

        risks: List[Dict[str, Any]] = []

        for f in eng_findings:
            f_dict = f.to_dict() if hasattr(f, "to_dict") else dict(f)
            sev = f_dict.get("severity", "info")
            risks.append({
                "rank": 0,  # filled after sort
                "severity": sev if isinstance(sev, str) else getattr(sev, "value", str(sev)),
                "severity_score": _severity_score(sev),
                "source": "engineering_intelligence",
                "type": f_dict.get("type", "unknown"),
                "title": str(f_dict.get("message", ""))[:120],
                "file": f_dict.get("file", ""),
                "line": f_dict.get("line", 0),
                "recommendation": f_dict.get("recommendation", ""),
            })

        for f in sec_findings:
            f_dict = f.to_dict() if hasattr(f, "to_dict") else dict(f)
            sev = f_dict.get("severity", "info")
            risks.append({
                "rank": 0,
                "severity": sev if isinstance(sev, str) else getattr(sev, "value", str(sev)),
                "severity_score": _severity_score(sev),
                "source": "security_operations",
                "type": f_dict.get("type", "unknown"),
                "title": str(f_dict.get("message", ""))[:120],
                "file": f_dict.get("file", ""),
                "line": f_dict.get("line", 0),
                "recommendation": f_dict.get("remediation", ""),
            })

        for v in arch_violations:
            v_dict = v.to_dict() if hasattr(v, "to_dict") else dict(v)
            risks.append({
                "rank": 0,
                "severity": "high",
                "severity_score": _severity_score("high"),
                "source": "architecture",
                "type": "layer_violation",
                "title": v_dict.get("message", "Layer boundary violation"),
                "file": v_dict.get("source_module", ""),
                "line": 0,
                "recommendation": "Move shared logic to the appropriate layer or apply dependency inversion.",
            })

        for t in blocked_tasks:
            t_dict = t.to_dict() if hasattr(t, "to_dict") else dict(t)
            risks.append({
                "rank": 0,
                "severity": "medium",
                "severity_score": _severity_score("medium"),
                "source": "task_queue",
                "type": "blocked_task",
                "title": f"Blocked task: {str(t_dict.get('title', ''))[:80]}",
                "file": "",
                "line": 0,
                "recommendation": "Resolve blockers: " + ", ".join(t_dict.get("blockers", []) or []),
            })

        # Sort by severity_score descending, then by title for stable ordering.
        risks.sort(key=lambda r: (-r["severity_score"], r["title"]))

        # Cap to keep the dashboard manageable.
        risks = risks[: self.RISK_CAP]

        # Assign final ranks.
        for i, risk in enumerate(risks, start=1):
            risk["rank"] = i

        return risks

    # ------------------------------------------------------------------
    # Internal: subsystem accessors (each individually resilient)
    # ------------------------------------------------------------------
    async def _get_task_queue_stats(self) -> Dict[str, Any]:
        try:
            from core.task_system import get_task_queue
            queue = get_task_queue()
            return await queue.get_stats()
        except Exception as exc:
            logger.debug(f"TaskQueue stats failed: {exc}")
            return {}

    async def _get_knowledge_base_stats(self) -> Dict[str, Any]:
        try:
            from core.knowledge_base import get_knowledge_base
            kb = get_knowledge_base()
            return await kb.get_stats()
        except Exception as exc:
            logger.debug(f"KnowledgeBase stats failed: {exc}")
            return {}

    async def _get_health_stats(self) -> Dict[str, Any]:
        try:
            from core.health_monitor import get_health_monitor
            monitor = get_health_monitor()
            return await monitor.get_stats()
        except Exception as exc:
            logger.debug(f"HealthMonitor stats failed: {exc}")
            return {}

    async def _get_health_history(self, limit: int = 30) -> List[Any]:
        try:
            from core.health_monitor import get_health_monitor
            monitor = get_health_monitor()
            return await monitor.get_history(limit=limit)
        except Exception as exc:
            logger.debug(f"HealthMonitor history failed: {exc}")
            return []

    async def _get_engineering_summary(self) -> Dict[str, Any]:
        try:
            from core.engineering_intelligence import EngineeringIntelligence
            intel = EngineeringIntelligence(project_root=self.project_root)
            report = intel.analyze()
            return {
                "health_score": float(report.health_score),
                "debt_score": float(report.debt_score),
                "total_files": report.total_files,
                "total_loc": report.total_loc,
                "finding_count": len(report.findings),
                "summary": report.summary,
            }
        except Exception as exc:
            logger.debug(f"EngineeringIntelligence summary failed: {exc}")
            return {}

    async def _get_architecture_summary(self) -> Dict[str, Any]:
        try:
            from core.architecture import ArchitectureAnalyzer
            analyzer = ArchitectureAnalyzer(project_root=self.project_root)
            report = analyzer.analyze()
            return {
                "health_score": float(report.health_score),
                "total_modules": report.total_modules,
                "total_dependencies": report.total_dependencies,
                "violation_count": len(report.violations),
                "adr_count": len(report.adrs),
                "summary": report.summary,
            }
        except Exception as exc:
            logger.debug(f"ArchitectureAnalyzer summary failed: {exc}")
            return {}

    async def _get_security_summary(self) -> Dict[str, Any]:
        try:
            from core.security_ops import SecurityOperations
            sec = SecurityOperations(project_root=self.project_root)
            report = sec.scan()
            return {
                "security_score": float(report.security_score),
                "finding_count": len(report.findings),
                "sbom_count": len(report.sbom),
                "total_files_scanned": report.total_files_scanned,
                "summary": report.summary,
            }
        except Exception as exc:
            logger.debug(f"SecurityOperations summary failed: {exc}")
            return {}

    async def _list_completed_tasks(self) -> List[Any]:
        try:
            from core.task_system import get_task_queue
            queue = get_task_queue()
            return await queue.list_tasks(include_completed=True)
        except Exception as exc:
            logger.debug(f"list_tasks failed: {exc}")
            return []

    async def _get_engineering_findings(self) -> List[Any]:
        try:
            from core.engineering_intelligence import EngineeringIntelligence
            intel = EngineeringIntelligence(project_root=self.project_root)
            report = intel.analyze()
            # Risk hotspots + critical/high findings only.
            risk_findings = [
                f for f in report.findings
                if getattr(f.type, "value", f.type) == "risk_hotspot"
            ]
            other_significant = [
                f for f in report.findings
                if getattr(f.type, "value", f.type) != "risk_hotspot"
                and getattr(f.severity, "value", f.severity) in ("critical", "high")
            ]
            return risk_findings + other_significant[:20]
        except Exception as exc:
            logger.debug(f"Engineering findings failed: {exc}")
            return []

    async def _get_security_findings(self) -> List[Any]:
        try:
            from core.security_ops import SecurityOperations
            sec = SecurityOperations(project_root=self.project_root)
            report = sec.scan()
            return [
                f for f in report.findings
                if getattr(f.severity, "value", f.severity) in ("critical", "high")
            ]
        except Exception as exc:
            logger.debug(f"Security findings failed: {exc}")
            return []

    async def _get_architecture_violations(self) -> List[Any]:
        try:
            from core.architecture import ArchitectureAnalyzer
            analyzer = ArchitectureAnalyzer(project_root=self.project_root)
            report = analyzer.analyze()
            return [v for v in report.violations if not v.valid]
        except Exception as exc:
            logger.debug(f"Architecture violations failed: {exc}")
            return []

    async def _get_blocked_tasks(self) -> List[Any]:
        try:
            from core.task_system import get_task_queue, TaskStatus
            queue = get_task_queue()
            return await queue.list_tasks(status=TaskStatus.BLOCKED)
        except Exception as exc:
            logger.debug(f"Blocked tasks failed: {exc}")
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_completed_at(task: Any) -> Optional[str]:
        if isinstance(task, dict):
            return task.get("completed_at")
        return getattr(task, "completed_at", None)

    @staticmethod
    def _parse_date(iso_str: str) -> Optional[Any]:
        """Parse an ISO 8601 timestamp into a date, tolerant of trailing Z."""
        if not iso_str:
            return None
        try:
            cleaned = iso_str.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned).date()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _average(values: List[Any]) -> float:
        """Average of a list, ignoring None. Returns 0.0 if all None."""
        nums = [float(v) for v in values if v is not None]
        if not nums:
            return 0.0
        return round(sum(nums) / len(nums), 2)

    @staticmethod
    def _trend_direction(snapshots: List[Dict[str, Any]], metric: str) -> str:
        if len(snapshots) < 2:
            return "unknown"
        try:
            current = float(snapshots[-1].get(metric, 0.0) or 0.0)
            previous = float(snapshots[-2].get(metric, 0.0) or 0.0)
        except (TypeError, ValueError):
            return "unknown"
        change = current - previous
        if change > 5:
            return "improving"
        if change < -5:
            return "degrading"
        return "stable"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_analytics: Optional[EngineeringAnalytics] = None


def get_engineering_analytics() -> EngineeringAnalytics:
    """Get the singleton EngineeringAnalytics instance."""
    global _analytics
    if _analytics is None:
        _analytics = EngineeringAnalytics()
    return _analytics
