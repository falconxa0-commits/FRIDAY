"""Runtime Observability — unified metrics for all runtime subsystems.

Collects metrics from every runtime subsystem and exposes them through
a single interface. Integrates with the existing Prometheus metrics
in core/observability.py.

Design principles:
    - **Unified**: All runtime metrics in one place.
    - **Real-time**: Metrics reflect current state, not cached.
    - **Structured**: Every metric has a name, value, unit, and timestamp.
    - **Queryable**: Can filter by subsystem or metric type.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.observability")


@dataclass
class Metric:
    """A single runtime metric."""
    name: str
    value: float
    unit: str = ""
    subsystem: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "subsystem": self.subsystem,
            "timestamp": self.timestamp,
        }


class RuntimeObservability:
    """Collects and exposes runtime metrics from all subsystems.

    Usage::

        obs = RuntimeObservability(context=ctx)
        metrics = await obs.collect_all()
        # metrics → list of Metric
        dashboard = await obs.get_dashboard()
        # dashboard → unified dict for display
    """

    def __init__(self, context=None):
        self.context = context

    async def collect_all(self) -> List[Metric]:
        """Collect metrics from all runtime subsystems."""
        metrics: List[Metric] = []

        if not self.context:
            return metrics

        # Collect from each subsystem
        collectors = [
            ("event_bus", self._collect_event_bus_metrics),
            ("scheduler", self._collect_scheduler_metrics),
            ("execution_graph", self._collect_execution_graph_metrics),
            ("resource_manager", self._collect_resource_manager_metrics),
            ("capability_registry", self._collect_capability_metrics),
            ("agent_runtime", self._collect_agent_metrics),
            ("plugin_runtime", self._collect_plugin_metrics),
            ("lifecycle_manager", self._collect_lifecycle_metrics),
            ("memory_runtime", self._collect_memory_metrics),
            ("workflow_runtime", self._collect_workflow_metrics),
            ("session_runtime", self._collect_session_metrics),
            ("context_runtime", self._collect_context_metrics),
            ("state_runtime", self._collect_state_metrics),
            ("executor", self._collect_executor_metrics),
        ]

        for name, collector in collectors:
            try:
                subsystem_metrics = await collector()
                metrics.extend(subsystem_metrics)
            except Exception as exc:
                logger.debug(f"Failed to collect {name} metrics: {exc}")
                metrics.append(Metric(
                    name=f"{name}.error",
                    value=1,
                    unit="bool",
                    subsystem=name,
                ))

        return metrics

    def _get_service(self, name: str) -> Optional[Any]:
        if self.context:
            return self.context.get_service(name)
        return None

    async def _collect_event_bus_metrics(self) -> List[Metric]:
        bus = self._get_service("event_bus")
        if not bus:
            return []
        stats = bus.get_stats()
        return [
            Metric(name="event_bus.total_events", value=stats.get("total_events", 0), unit="events", subsystem="event_bus"),
            Metric(name="event_bus.subscriber_count", value=stats.get("subscriber_count", 0), unit="subscribers", subsystem="event_bus"),
        ]

    async def _collect_scheduler_metrics(self) -> List[Metric]:
        sched = self._get_service("scheduler")
        if not sched:
            return []
        stats = sched.get_stats()
        return [
            Metric(name="scheduler.total_jobs", value=stats.get("total_jobs", 0), unit="jobs", subsystem="scheduler"),
            Metric(name="scheduler.running", value=1 if stats.get("running") else 0, unit="bool", subsystem="scheduler"),
        ]

    async def _collect_execution_graph_metrics(self) -> List[Metric]:
        graph = self._get_service("execution_graph")
        if not graph:
            return []
        stats = graph.get_stats()
        return [
            Metric(name="execution_graph.total_tasks", value=stats.get("total_tasks", 0), unit="tasks", subsystem="execution_graph"),
            Metric(name="execution_graph.has_cycles", value=1 if stats.get("has_cycles") else 0, unit="bool", subsystem="execution_graph"),
        ]

    async def _collect_resource_manager_metrics(self) -> List[Metric]:
        rm = self._get_service("resource_manager")
        if not rm:
            return []
        stats = rm.get_stats()
        usage = stats.get("usage", {})
        return [
            Metric(name="resource.memory_mb", value=usage.get("memory_mb", 0), unit="MB", subsystem="resource_manager"),
            Metric(name="resource.cpu_percent", value=usage.get("cpu_percent", 0), unit="%", subsystem="resource_manager"),
            Metric(name="resource.concurrent_requests", value=usage.get("concurrent_requests", 0), unit="requests", subsystem="resource_manager"),
            Metric(name="resource.alerts", value=len(stats.get("alerts", [])), unit="alerts", subsystem="resource_manager"),
        ]

    async def _collect_capability_metrics(self) -> List[Metric]:
        reg = self._get_service("capability_registry")
        if not reg:
            return []
        stats = reg.get_stats()
        return [
            Metric(name="capability.total", value=stats.get("total_capabilities", 0), unit="capabilities", subsystem="capability_registry"),
        ]

    async def _collect_agent_metrics(self) -> List[Metric]:
        ar = self._get_service("agent_runtime")
        if not ar:
            return []
        stats = ar.get_stats()
        return [
            Metric(name="agent.total", value=stats.get("total_agents", 0), unit="agents", subsystem="agent_runtime"),
            Metric(name="agent.executions", value=stats.get("total_executions", 0), unit="executions", subsystem="agent_runtime"),
            Metric(name="agent.failures", value=stats.get("total_failures", 0), unit="failures", subsystem="agent_runtime"),
        ]

    async def _collect_plugin_metrics(self) -> List[Metric]:
        pr = self._get_service("plugin_runtime")
        if not pr:
            return []
        stats = pr.get_stats()
        return [
            Metric(name="plugin.total", value=stats.get("total_plugins", 0), unit="plugins", subsystem="plugin_runtime"),
            Metric(name="plugin.executions", value=stats.get("total_executions", 0), unit="executions", subsystem="plugin_runtime"),
        ]

    async def _collect_lifecycle_metrics(self) -> List[Metric]:
        lm = self._get_service("lifecycle_manager")
        if not lm:
            return []
        stats = lm.get_stats()
        return [
            Metric(name="lifecycle.total_components", value=stats.get("total_components", 0), unit="components", subsystem="lifecycle_manager"),
        ]

    async def _collect_memory_metrics(self) -> List[Metric]:
        mr = self._get_service("memory_runtime")
        if not mr:
            return []
        stats = mr.get_stats()
        return [
            Metric(name="memory.total_pools", value=stats.get("total_pools", 0), unit="pools", subsystem="memory_runtime"),
            Metric(name="memory.total_allocated", value=stats.get("total_allocated_bytes", 0), unit="bytes", subsystem="memory_runtime"),
        ]

    async def _collect_workflow_metrics(self) -> List[Metric]:
        wr = self._get_service("workflow_runtime")
        if not wr:
            return []
        stats = wr.get_stats()
        return [
            Metric(name="workflow.total", value=stats.get("total_workflows", 0), unit="workflows", subsystem="workflow_runtime"),
        ]

    async def _collect_session_metrics(self) -> List[Metric]:
        sr = self._get_service("session_runtime")
        if not sr:
            return []
        count = await sr.get_session_count()
        return [
            Metric(name="session.active", value=count, unit="sessions", subsystem="session_runtime"),
        ]

    async def _collect_context_metrics(self) -> List[Metric]:
        cr = self._get_service("context_runtime")
        if not cr:
            return []
        stats = cr.get_stats()
        return [
            Metric(name="context.total", value=stats.get("total_contexts", 0), unit="contexts", subsystem="context_runtime"),
        ]

    async def _collect_state_metrics(self) -> List[Metric]:
        sr = self._get_service("state_runtime")
        if not sr:
            return []
        stats = sr.get_stats()
        return [
            Metric(name="state.total_machines", value=stats.get("total_machines", 0), unit="machines", subsystem="state_runtime"),
        ]

    async def _collect_executor_metrics(self) -> List[Metric]:
        ex = self._get_service("executor")
        if not ex:
            return []
        stats = ex.get_stats()
        return [
            Metric(name="executor.total_executions", value=stats.get("total_executions", 0), unit="executions", subsystem="executor"),
            Metric(name="executor.failures", value=stats.get("total_failures", 0), unit="failures", subsystem="executor"),
            Metric(name="executor.failure_rate", value=stats.get("failure_rate", 0), unit="%", subsystem="executor"),
        ]

    async def get_dashboard(self) -> Dict[str, Any]:
        """Get a unified dashboard of all runtime metrics."""
        metrics = await self.collect_all()

        # Group by subsystem
        by_subsystem: Dict[str, List[Dict]] = {}
        for m in metrics:
            by_subsystem.setdefault(m.subsystem, []).append(m.to_dict())

        # Health check
        health = {}
        if self.context:
            health = await self.context.health_check()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_metrics": len(metrics),
            "subsystems": list(by_subsystem.keys()),
            "healthy_subsystems": sum(1 for v in health.values() if v),
            "total_subsystems": len(health),
            "metrics_by_subsystem": by_subsystem,
            "health": health,
        }

    async def get_health_summary(self) -> Dict[str, Any]:
        """Get a quick health summary."""
        if not self.context:
            return {"status": "no_context"}

        health = await self.context.health_check()
        total = len(health)
        healthy = sum(1 for v in health.values() if v)

        return {
            "status": "healthy" if healthy == total else "degraded" if healthy > 0 else "unhealthy",
            "healthy": healthy,
            "total": total,
            "health_pct": (healthy / total * 100) if total > 0 else 0,
        }
