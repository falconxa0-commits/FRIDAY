"""Dashboard API routes — Mission Control for FRIDAY.

Aggregates data from all engineering subsystems into a unified dashboard.

All endpoints are authenticated via ``core.auth.require_auth`` and follow
the convention of returning ``{"error": "subsystem unavailable"}`` for
any section whose underlying subsystem raises — so a single broken
subsystem never takes down the whole dashboard.

Endpoints
---------
    GET /api/dashboard              - main summary (everything)
    GET /api/dashboard/health       - health scores only (fast polling)
    GET /api/dashboard/tasks        - task overview
    GET /api/dashboard/findings     - engineering findings
    GET /api/dashboard/architecture - architecture overview
    GET /api/dashboard/security     - security overview
    GET /api/dashboard/performance  - performance / benchmarks
    GET /api/dashboard/releases     - release overview

Performance
-----------
All endpoints target <100 ms response time. The expensive analyzers
(``EngineeringIntelligence.analyze``, ``ArchitectureAnalyzer.analyze``,
``SecurityOperations.scan``) run in worker threads via
``asyncio.to_thread`` and are cached for 60 seconds using a simple
in-memory TTL cache. Each cache entry is computed once and reused
until the TTL expires, regardless of which endpoint requested it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends

from core.auth import require_auth

logger = logging.getLogger("friday.api.dashboard")

router = APIRouter(tags=["dashboard"])


# ---------------------------------------------------------------------------
# Simple in-memory TTL cache
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 60  # 1 minute

_cache: Dict[str, Tuple[float, Any]] = {}
_cache_lock: Optional[asyncio.Lock] = None


def _get_cache_lock() -> asyncio.Lock:
    """Lazy-create the cache lock (must be created inside a running loop)."""
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


async def _cached(key: str, factory):
    """Return cached value if fresh, otherwise call ``factory()`` and cache it.

    ``factory`` may be sync or async; both are supported. The factory runs
    OUTSIDE the cache lock so concurrent requests don't serialize on
    computation — the (tiny) race window where two requests recompute the
    same key is acceptable.
    """
    now = time.time()
    lock = _get_cache_lock()
    async with lock:
        if key in _cache:
            ts, value = _cache[key]
            if now - ts < _CACHE_TTL_SECONDS:
                return value

    # Compute outside the lock so other requests aren't blocked.
    if asyncio.iscoroutinefunction(factory):
        value = await factory()
    else:
        value = await asyncio.to_thread(factory)

    async with lock:
        _cache[key] = (time.time(), value)
    return value


def clear_cache() -> None:
    """Clear the dashboard cache (for tests)."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _enum_value(x: Any) -> str:
    """Return ``.value`` for enums, else ``str(x)``."""
    if hasattr(x, "value"):
        return x.value
    return str(x)


# Severity ordering for sorting findings (critical → info)
_SEVERITY_ORDER: Dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def _severity_rank(sev: Any) -> int:
    return _SEVERITY_ORDER.get(_enum_value(sev), 99)


def _finding_to_dict(f) -> Dict[str, Any]:
    """Serialise a Finding/SecurityFinding to a JSON-safe dict."""
    if hasattr(f, "to_dict"):
        d = f.to_dict()
        # Ensure enums are strings (to_dict() on dataclasses with asdict
        # leaves enum objects in place — coerce them).
        for k, v in list(d.items()):
            if hasattr(v, "value"):
                d[k] = v.value
            elif isinstance(v, dict):
                for kk, vv in list(v.items()):
                    if hasattr(vv, "value"):
                        v[kk] = vv.value
        return d
    return {
        "type": _enum_value(getattr(f, "type", "")),
        "severity": _enum_value(getattr(f, "severity", "")),
        "file": getattr(f, "file", ""),
        "line": getattr(f, "line", 0),
        "message": getattr(f, "message", ""),
        "recommendation": getattr(f, "recommendation", getattr(f, "remediation", "")),
        "metadata": getattr(f, "metadata", {}) or {},
    }


# ---------------------------------------------------------------------------
# Subsystem collectors — each returns either a real result or
# {"error": "subsystem unavailable"} so callers can merge freely.
# ---------------------------------------------------------------------------
async def _collect_engineering_intelligence() -> Dict[str, Any]:
    try:
        from core.engineering_intelligence import get_engineering_intelligence

        def _analyze():
            return get_engineering_intelligence().analyze()

        report = await _cached("eng_intel_report", _analyze)

        findings = list(report.findings)
        findings_sorted = sorted(findings, key=lambda f: _severity_rank(f.severity))
        top_findings = [_finding_to_dict(f) for f in findings_sorted[:10]]

        findings_by_type: Dict[str, int] = {}
        for f in findings:
            t = _enum_value(f.type)
            findings_by_type[t] = findings_by_type.get(t, 0) + 1

        risk_hotspots = [
            _finding_to_dict(f)
            for f in findings
            if _enum_value(f.type) == "risk_hotspot"
        ][:10]

        return {
            "available": True,
            "health_score": round(float(report.health_score), 1),
            "debt_score": round(float(report.debt_score), 1),
            "total_files": report.total_files,
            "total_loc": report.total_loc,
            "total_findings": len(findings),
            "top_findings": top_findings,
            "findings_by_type": findings_by_type,
            "risk_hotspots": risk_hotspots,
            "summary": report.summary,
            "timestamp": report.timestamp,
        }
    except Exception as exc:
        logger.warning(f"Engineering intelligence unavailable: {exc}", exc_info=True)
        return {"available": False, "error": "subsystem unavailable"}


async def _collect_architecture() -> Dict[str, Any]:
    try:
        from core.architecture import get_architecture_analyzer

        def _analyze():
            return get_architecture_analyzer().analyze()

        report = await _cached("arch_report", _analyze)

        # Most coupled modules (top 5 by efferent coupling)
        modules_sorted = sorted(
            report.metrics.values(),
            key=lambda m: getattr(m, "efferent_coupling", 0),
            reverse=True,
        )
        most_coupled = [
            {
                "name": m.name,
                "layer": m.layer,
                "afferent_coupling": m.afferent_coupling,
                "efferent_coupling": m.efferent_coupling,
                "instability": m.instability,
            }
            for m in modules_sorted[:5]
        ]

        return {
            "available": True,
            "health_score": round(float(report.health_score), 1),
            "total_modules": report.total_modules,
            "total_dependencies": report.total_dependencies,
            "violation_count": len(report.violations),
            "violations": [
                {
                    "source_module": v.source_module,
                    "source_layer": v.source_layer,
                    "target_module": v.target_module,
                    "target_layer": v.target_layer,
                    "message": v.message,
                }
                for v in report.violations[:20]
            ],
            "most_coupled_modules": most_coupled,
            "adr_count": len(report.adrs),
            "adrs": [
                {
                    "id": a.id,
                    "title": a.title,
                    "status": a.status,
                    "date": a.date,
                }
                for a in report.adrs[:10]
            ],
            "summary": report.summary,
            "timestamp": report.timestamp,
        }
    except Exception as exc:
        logger.warning(f"Architecture analyzer unavailable: {exc}", exc_info=True)
        return {"available": False, "error": "subsystem unavailable"}


async def _collect_security() -> Dict[str, Any]:
    try:
        from core.security_ops import get_security_operations

        def _scan():
            return get_security_operations().scan()

        report = await _cached("sec_report", _scan)

        by_severity: Dict[str, int] = {}
        for f in report.findings:
            sev = _enum_value(f.severity)
            by_severity[sev] = by_severity.get(sev, 0) + 1

        findings_sorted = sorted(report.findings, key=lambda f: _severity_rank(f.severity))
        top_findings = [_finding_to_dict(f) for f in findings_sorted[:10]]

        return {
            "available": True,
            "security_score": round(float(report.security_score), 1),
            "total_findings": len(report.findings),
            "findings_by_severity": by_severity,
            "sbom_count": len(report.sbom),
            "files_scanned": report.total_files_scanned,
            "top_findings": top_findings,
            "summary": report.summary,
            "timestamp": report.timestamp,
        }
    except Exception as exc:
        logger.warning(f"Security ops unavailable: {exc}", exc_info=True)
        return {"available": False, "error": "subsystem unavailable"}


async def _collect_task_queue() -> Dict[str, Any]:
    try:
        from core.task_system import get_task_queue, TaskStatus

        queue = get_task_queue()
        stats = await queue.get_stats()

        # Recently completed (last 10)
        all_tasks = await queue.list_tasks(include_completed=True)
        completed = [
            t for t in all_tasks if t.status == TaskStatus.COMPLETED
        ]
        completed.sort(
            key=lambda t: t.completed_at or t.updated_at,
            reverse=True,
        )
        recent_completed = [
            {
                "id": t.id,
                "title": t.title,
                "phase": _enum_value(t.phase),
                "priority": _enum_value(t.priority),
                "completed_at": t.completed_at,
                "duration_seconds": t.metadata.get("duration_seconds", 0)
                if isinstance(t.metadata, dict)
                else 0,
            }
            for t in completed[:10]
        ]

        return {
            "available": True,
            "active": stats.get("active", 0),
            "completed": stats.get("completed", 0),
            "by_status": stats.get("by_status", {}),
            "by_priority": stats.get("by_priority", {}),
            "by_phase": stats.get("by_phase", {}),
            "recently_completed": recent_completed,
            "receipts": stats.get("receipts", 0),
        }
    except Exception as exc:
        logger.warning(f"Task queue unavailable: {exc}", exc_info=True)
        return {"available": False, "error": "subsystem unavailable"}


async def _collect_knowledge_base() -> Dict[str, Any]:
    try:
        from core.knowledge_base import get_knowledge_base

        kb = get_knowledge_base()
        stats = await kb.get_stats()
        return {
            "available": True,
            "total": stats.get("total", 0),
            "by_type": stats.get("by_type", {}),
            "by_status": stats.get("by_status", {}),
        }
    except Exception as exc:
        logger.warning(f"Knowledge base unavailable: {exc}", exc_info=True)
        return {"available": False, "error": "subsystem unavailable"}


async def _collect_releases() -> Dict[str, Any]:
    try:
        from core.release_pipeline import (
            get_release_pipeline,
            ReleaseStatus,
        )

        pipeline = get_release_pipeline()
        all_releases = await pipeline.list_releases()
        published = [r for r in all_releases if r.status == ReleaseStatus.PUBLISHED]
        drafts = [r for r in all_releases if r.status == ReleaseStatus.DRAFT]

        latest = published[0] if published else None

        def _release_summary(r):
            return {
                "version": r.version,
                "release_type": _enum_value(r.release_type),
                "status": _enum_value(r.status),
                "release_date": r.release_date,
                "predecessor": r.predecessor,
                "task_count": len(r.completed_task_ids),
            }

        return {
            "available": True,
            "latest": _release_summary(latest) if latest else None,
            "history": [_release_summary(r) for r in all_releases[:5]],
            "drafts": [_release_summary(r) for r in drafts],
            "total": len(all_releases),
            "published_count": len(published),
            "draft_count": len(drafts),
        }
    except Exception as exc:
        logger.warning(f"Release pipeline unavailable: {exc}", exc_info=True)
        return {"available": False, "error": "subsystem unavailable"}


async def _collect_engineering_org() -> Dict[str, Any]:
    try:
        from core.engineering_org import get_engineering_org

        org = get_engineering_org()
        status = await org.get_org_status()
        leads = status.get("leads", {})
        return {
            "available": True,
            "lead_count": len(leads),
            "leads": [
                {
                    "role": role,
                    "name": lead.get("name", role),
                    "tasks_completed": lead.get("tasks_completed", 0),
                    "tasks_failed": lead.get("tasks_failed", 0),
                    "worker_count": lead.get("worker_count", 0),
                }
                for role, lead in leads.items()
            ],
            "active_assignments": status.get("active_assignments", 0),
        }
    except Exception as exc:
        logger.warning(f"Engineering org unavailable: {exc}", exc_info=True)
        return {"available": False, "error": "subsystem unavailable"}


async def _collect_validation() -> Dict[str, Any]:
    try:
        from core.validation_pipeline import get_validation_pipeline, CheckStatus

        async def _run_validation():
            pipeline = get_validation_pipeline()
            # Skip tests for fast dashboard polling
            return await pipeline.run(skip_tests=True)

        report = await _cached("validation_report", _run_validation)

        checks = [
            {
                "name": c.name,
                "status": _enum_value(c.status),
                "duration_seconds": c.duration_seconds,
                "message": c.message,
            }
            for c in report.checks
        ]
        return {
            "available": True,
            "overall_status": _enum_value(report.overall_status),
            "summary": report.summary,
            "checks": checks,
            "timestamp": report.timestamp,
        }
    except Exception as exc:
        logger.warning(f"Validation pipeline unavailable: {exc}", exc_info=True)
        return {"available": False, "error": "subsystem unavailable"}


def _read_benchmark(name: str) -> Optional[Dict[str, Any]]:
    """Read a benchmark results JSON from disk (best-effort)."""
    try:
        # Locate benchmarks dir relative to this file
        repo_root = Path(__file__).resolve().parent.parent.parent
        path = repo_root / "benchmarks" / f"results_{name}.json"
        if not path.is_file():
            return None
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


async def _collect_performance() -> Dict[str, Any]:
    """Collect benchmark results and compute trends."""
    try:
        names = ["startup", "chat_latency", "memory", "vector_search"]
        latest: Dict[str, Any] = {}
        for name in names:
            data = await asyncio.to_thread(_read_benchmark, name)
            if data is not None:
                latest[name] = data

        # Trends are not stored historically in the repo (each benchmark
        # file is overwritten on each run), so we compare to the cached
        # snapshot from the previous poll if available. If no previous
        # snapshot exists, mark trend as "unknown".
        previous = _cache.get("performance_previous")
        trends: Dict[str, str] = {}
        if previous is not None:
            _, prev_data = previous
            for name in names:
                if name in latest and name in prev_data:
                    trends[name] = _compare_benchmarks(prev_data[name], latest[name])
                else:
                    trends[name] = "unknown"
        else:
            for name in names:
                trends[name] = "unknown"

        # Stash the current snapshot for next poll (only the lightweight
        # summary stats, not the full raw_runs).
        snapshot: Dict[str, Any] = {}
        for name, data in latest.items():
            snapshot[name] = _summarise_benchmark(data)
        _cache["performance_previous"] = (time.time(), snapshot)

        return {
            "available": True,
            "benchmarks": {
                name: _summarise_benchmark(data) for name, data in latest.items()
            },
            "trends": trends,
            "benchmark_count": len(latest),
        }
    except Exception as exc:
        logger.warning(f"Performance collection unavailable: {exc}", exc_info=True)
        return {"available": False, "error": "subsystem unavailable"}


def _summarise_benchmark(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract a small summary from a benchmark results dict."""
    summary: Dict[str, Any] = {
        "benchmark": data.get("benchmark", "unknown"),
        "iterations": data.get("iterations", 0),
    }
    # Startup benchmark: pull script_to_brain_ready median
    if data.get("benchmark") == "startup":
        totals = data.get("totals_ms", {})
        brain_ready = totals.get("script_to_brain_ready", {})
        summary["script_to_brain_ready_ms"] = brain_ready.get("median")
        first_chunk = totals.get("script_to_first_chunk", {})
        summary["script_to_first_chunk_ms"] = first_chunk.get("median")
    elif data.get("benchmark") == "chat_latency":
        brain_direct = data.get("brain_direct", {})
        # Find the smallest message-length bucket's full_ms p50
        buckets = brain_direct.get("message_lengths", {})
        if buckets:
            first_key = next(iter(buckets))
            bucket = buckets[first_key]
            full_ms = bucket.get("full_ms", {})
            summary["message_length_bucket"] = first_key
            summary["full_ms_p50"] = full_ms.get("p50")
            summary["full_ms_p99"] = full_ms.get("p99")
    elif data.get("benchmark") == "memory":
        # Pull top-level memory stats if available
        summary["mean_mb"] = data.get("mean_mb") or data.get("mean")
        summary["peak_mb"] = data.get("peak_mb") or data.get("peak")
    elif data.get("benchmark") == "vector_search":
        summary["mean_ms"] = data.get("mean_ms") or data.get("mean")
        summary["p99_ms"] = data.get("p99_ms") or data.get("p99")
    return summary


def _compare_benchmarks(prev: Dict[str, Any], curr: Dict[str, Any]) -> str:
    """Compare two benchmark summaries and return a trend string.

    Returns 'improving', 'regressing', or 'stable'. Looks for the first
    available numeric metric and compares them.
    """
    metric_keys = [
        "script_to_brain_ready_ms",
        "script_to_first_chunk_ms",
        "full_ms_p50",
        "mean_mb",
        "peak_mb",
        "mean_ms",
        "p99_ms",
    ]
    for key in metric_keys:
        prev_val = prev.get(key) if isinstance(prev, dict) else None
        curr_val = curr.get(key) if isinstance(curr, dict) else None
        if isinstance(prev_val, (int, float)) and isinstance(curr_val, (int, float)):
            if prev_val == 0:
                return "stable"
            delta = (curr_val - prev_val) / prev_val
            if delta < -0.05:  # >5% faster
                return "improving"
            elif delta > 0.05:  # >5% slower
                return "regressing"
            return "stable"
    return "unknown"


# ---------------------------------------------------------------------------
# Health score helpers
# ---------------------------------------------------------------------------
async def _compute_health_scores() -> Dict[str, Any]:
    """Compute the four health scores + overall weighted average."""
    ei, arch, sec, tasks = await asyncio.gather(
        _collect_engineering_intelligence(),
        _collect_architecture(),
        _collect_security(),
        _collect_task_queue(),
    )

    eng_health = ei.get("health_score") if ei.get("available") else None
    arch_health = arch.get("health_score") if arch.get("available") else None
    sec_score = sec.get("security_score") if sec.get("available") else None

    # Test pass rate from task queue (completed / (completed + failed))
    test_pass_rate: Optional[float] = None
    if tasks.get("available"):
        by_status = tasks.get("by_status", {})
        completed = by_status.get("completed", 0)
        failed = by_status.get("failed", 0)
        if completed + failed > 0:
            test_pass_rate = round(completed / (completed + failed), 4)

    # Overall: weighted average of available scores
    weights = {
        "engineering": 0.35,
        "architecture": 0.25,
        "security": 0.25,
        "tests": 0.15,
    }
    total_weight = 0.0
    total_score = 0.0
    if eng_health is not None:
        total_score += eng_health * weights["engineering"]
        total_weight += weights["engineering"]
    if arch_health is not None:
        total_score += arch_health * weights["architecture"]
        total_weight += weights["architecture"]
    if sec_score is not None:
        total_score += sec_score * weights["security"]
        total_weight += weights["security"]
    if test_pass_rate is not None:
        total_score += (test_pass_rate * 100.0) * weights["tests"]
        total_weight += weights["tests"]

    overall = round(total_score / total_weight, 1) if total_weight > 0 else None

    return {
        "engineering_health": eng_health,
        "architecture_health": arch_health,
        "security_score": sec_score,
        "test_pass_rate": test_pass_rate,
        "overall": overall,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def dashboard_summary(_auth: str = Depends(require_auth)) -> Dict[str, Any]:
    """Main dashboard — aggregates every subsystem.

    Each section is collected in parallel. A failure in any one section
    is isolated and surfaced as ``{"error": "subsystem unavailable"}``
    so the rest of the dashboard still renders.
    """
    start = time.perf_counter()

    ei, arch, sec, tasks, kb, releases, org, validation = await asyncio.gather(
        _collect_engineering_intelligence(),
        _collect_architecture(),
        _collect_security(),
        _collect_task_queue(),
        _collect_knowledge_base(),
        _collect_releases(),
        _collect_engineering_org(),
        _collect_validation(),
    )

    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_ms": elapsed_ms,
        "engineering_intelligence": ei,
        "architecture": arch,
        "security": sec,
        "tasks": tasks,
        "knowledge_base": kb,
        "engineering_org": org,
        "releases": releases,
        "validation": validation,
    }


@router.get("/health")
async def dashboard_health(_auth: str = Depends(require_auth)) -> Dict[str, Any]:
    """Fast health-score polling endpoint.

    Returns the four key health scores and a weighted overall score.
    All values are 0-100 (or ``None`` if the underlying subsystem is
    unavailable). ``test_pass_rate`` is 0.0-1.0.
    """
    start = time.perf_counter()
    scores = await _compute_health_scores()
    scores["response_ms"] = round((time.perf_counter() - start) * 1000, 1)
    scores["timestamp"] = datetime.now(timezone.utc).isoformat()
    return scores


@router.get("/tasks")
async def dashboard_tasks(_auth: str = Depends(require_auth)) -> Dict[str, Any]:
    """Task overview — counts by status/priority/phase + recent completions."""
    start = time.perf_counter()
    tasks = await _collect_task_queue()
    org = await _collect_engineering_org()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_ms": round((time.perf_counter() - start) * 1000, 1),
        "tasks": tasks,
        "engineering_org": org,
    }


@router.get("/findings")
async def dashboard_findings(_auth: str = Depends(require_auth)) -> Dict[str, Any]:
    """Engineering findings — top 10 by severity, by type, and risk hotspots."""
    start = time.perf_counter()
    ei = await _collect_engineering_intelligence()
    sec = await _collect_security()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_ms": round((time.perf_counter() - start) * 1000, 1),
        "engineering_findings": {
            "top_findings": ei.get("top_findings", []) if ei.get("available") else [],
            "findings_by_type": ei.get("findings_by_type", {}) if ei.get("available") else {},
            "risk_hotspots": ei.get("risk_hotspots", []) if ei.get("available") else [],
            "total": ei.get("total_findings", 0) if ei.get("available") else 0,
        },
        "security_findings": {
            "top_findings": sec.get("top_findings", []) if sec.get("available") else [],
            "findings_by_severity": sec.get("findings_by_severity", {}) if sec.get("available") else {},
            "total": sec.get("total_findings", 0) if sec.get("available") else 0,
        },
    }


@router.get("/architecture")
async def dashboard_architecture(_auth: str = Depends(require_auth)) -> Dict[str, Any]:
    """Architecture overview — violations, modules, coupling, ADRs."""
    start = time.perf_counter()
    arch = await _collect_architecture()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_ms": round((time.perf_counter() - start) * 1000, 1),
        "architecture": arch,
    }


@router.get("/security")
async def dashboard_security(_auth: str = Depends(require_auth)) -> Dict[str, Any]:
    """Security overview — score, finding counts, SBOM size, top findings."""
    start = time.perf_counter()
    sec = await _collect_security()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_ms": round((time.perf_counter() - start) * 1000, 1),
        "security": sec,
    }


@router.get("/performance")
async def dashboard_performance(_auth: str = Depends(require_auth)) -> Dict[str, Any]:
    """Performance overview — latest benchmark results + trend vs previous poll."""
    start = time.perf_counter()
    perf = await _collect_performance()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_ms": round((time.perf_counter() - start) * 1000, 1),
        "performance": perf,
    }


@router.get("/releases")
async def dashboard_releases(_auth: str = Depends(require_auth)) -> Dict[str, Any]:
    """Release overview — latest published, history (last 5), drafts."""
    start = time.perf_counter()
    releases = await _collect_releases()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_ms": round((time.perf_counter() - start) * 1000, 1),
        "releases": releases,
    }


__all__ = ["router", "clear_cache"]
