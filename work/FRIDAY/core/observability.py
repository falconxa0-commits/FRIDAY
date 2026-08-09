"""Unified observability stack for FRIDAY.

Single source of truth for structured logging, Prometheus metrics,
request-scoped correlation IDs, and optional Sentry integration.

Components
----------
* :class:`StructuredLogger` — emits one JSON object per log line with the
  fields ``timestamp``, ``level``, ``logger``, ``message``,
  ``correlation_id`` and an ``extra`` object. Safe to use in production
  code paths; replaces ad-hoc ``print()`` calls.
* :class:`PrometheusMetrics` — holds every Counter / Histogram / Gauge
  used across the codebase. Exposed as the module-level singleton
  :data:`metrics`.
* :func:`get_correlation_id` / :func:`set_correlation_id` — contextvar
  backed request tracing. :class:`CorrelationIdMiddleware` populates it
  on every HTTP request from the ``X-Request-ID`` header (or a fresh
  UUID4) so log lines and Sentry events can be correlated back to the
  request that produced them.
* :func:`init_sentry` — idempotent Sentry SDK initialisation that no-ops
  gracefully when ``SENTRY_DSN`` is not set.

Design notes
------------
* Prometheus metrics are defined ONCE at module import. Re-importing the
  module does not re-register them (Python module cache), so tests can
  import freely without hitting ``Duplicated timeseries`` errors.
* Sentry init is wrapped in try/except so a misconfigured DSN never
  breaks app startup.
* The structured logger writes to ``stdout`` so it works in container
  environments where stdout is aggregated by the platform.
* :class:`CorrelationIdMiddleware` is a pure-ASGI middleware (not
  ``BaseHTTPMiddleware``) because the pure-ASGI form correctly propagates
  ``ContextVar`` values into the route handler's task — a known pitfall
  with ``BaseHTTPMiddleware``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Prometheus client — imported eagerly because the dependency is required
# for the /metrics endpoint. We still degrade gracefully if it is missing
# (e.g. minimal install) so the rest of the app keeps working.
# ---------------------------------------------------------------------------
try:
    from prometheus_client import (  # type: ignore
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover — defensive, dependency is installed
    _PROMETHEUS_AVAILABLE = False
    Counter = Gauge = Histogram = None  # type: ignore
    generate_latest = None  # type: ignore
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


# ---------------------------------------------------------------------------
# Correlation ID — propagated via contextvars so it survives across awaits
# within a single request, but is isolated between concurrent requests.
# ---------------------------------------------------------------------------
correlation_id_var: ContextVar[Optional[str]] = ContextVar(
    "friday_correlation_id", default=None
)


def get_correlation_id() -> Optional[str]:
    """Return the current request's correlation ID, or ``None``.

    Returns ``None`` when no request is in scope — e.g. background tasks,
    CLI invocations, or unit tests that have not called
    :func:`set_correlation_id`.
    """
    return correlation_id_var.get()


def set_correlation_id(value: Optional[str]) -> Any:
    """Set the correlation ID for the current async context.

    Returns the token that must be passed to :func:`reset_correlation_id`
    once the request completes — mirrors ``ContextVar.set`` semantics.
    """
    return correlation_id_var.set(value)


def reset_correlation_id(token: Any) -> None:
    """Reset the correlation ID to its previous value."""
    correlation_id_var.reset(token)


# ---------------------------------------------------------------------------
# Prometheus metrics registry
# ---------------------------------------------------------------------------
class PrometheusMetrics:
    """Container for every FRIDAY Prometheus metric.

    Metrics are constructed once and register themselves with the default
    ``prometheus_client`` ``REGISTRY`` — that is what
    :func:`prometheus_client.generate_latest` serialises on the
    ``/metrics`` endpoint.

    Attributes are exposed directly (no property wrappers) so callers can
    do ``metrics.chat_requests_total.labels(...).inc()`` concisely.
    """

    def __init__(self) -> None:
        if not _PROMETHEUS_AVAILABLE:  # pragma: no cover — defensive
            return

        # ---- Chat traffic ------------------------------------------------
        self.chat_requests_total = Counter(
            "friday_chat_requests_total",
            "Total chat requests handled, labelled by provider and outcome.",
            ["provider", "status"],
        )
        self.chat_latency_seconds = Histogram(
            "friday_chat_latency_seconds",
            "Wall-clock latency of chat requests in seconds.",
            ["provider"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
        )
        self.tokens_used_total = Counter(
            "friday_tokens_used_total",
            "Tokens consumed by the LLM, labelled by provider and direction.",
            ["provider", "direction"],
        )
        self.active_conversations = Gauge(
            "friday_active_conversations",
            "Number of currently active conversation sessions.",
        )
        self.cost_usd_total = Counter(
            "friday_cost_usd_total",
            "Cumulative USD cost incurred, labelled by provider.",
            ["provider"],
        )

        # ---- Ledger / audit ----------------------------------------------
        self.ledger_actions_total = Counter(
            "friday_ledger_actions_total",
            "Action ledger events, labelled by component, action, status.",
            ["component", "action", "status"],
        )
        self.ledger_chain_valid = Gauge(
            "friday_ledger_chain_valid",
            "1 if the audit hash chain verifies, 0 if it is broken.",
        )

        # ---- Memory ------------------------------------------------------
        self.memory_count = Gauge(
            "friday_memory_count",
            "Number of memories currently stored.",
        )

        # ---- Integrations ------------------------------------------------
        self.integration_status = Gauge(
            "friday_integration_status",
            "1 if the integration is live, 0 if offline.",
            ["integration_name"],
        )

        # ---- Errors ------------------------------------------------------
        self.errors_total = Counter(
            "friday_errors_total",
            "Errors raised, labelled by module and error_type.",
            ["module", "error_type"],
        )

    # ---- Convenience helpers --------------------------------------------
    def record_error(self, module: str, error: BaseException) -> None:
        """Increment ``friday_errors_total`` for an exception.

        ``error_type`` is the exception's class name. Safe to call even
        when prometheus_client is unavailable (no-ops in that case).
        """
        if not _PROMETHEUS_AVAILABLE:
            return
        self.errors_total.labels(
            module=module,
            error_type=type(error).__name__,
        ).inc()


# Singleton — every importer gets the same metric objects.
metrics = PrometheusMetrics()


# ---------------------------------------------------------------------------
# Structured JSON logger
# ---------------------------------------------------------------------------
class StructuredLogger:
    """Logger wrapper that emits one JSON object per log line.

    Output schema::

        {
          "timestamp":     "2026-01-01T12:00:00.123456+00:00",
          "level":         "INFO",
          "logger":        "friday.api.chat",
          "message":       "chat request completed",
          "correlation_id":"req-abc-123" | null,
          "extra":         { ... arbitrary caller-supplied fields ... }
        }

    The wrapper delegates level filtering to the underlying
    ``logging.Logger`` so existing ``logging.basicConfig`` configuration
    (levels, handlers) still applies. Each call serialises to a single
    JSON line and emits it via ``logging.Logger.log``.

    Why JSON?  Container log aggregators (Loki, Datadog, CloudWatch Logs
    Insights, ELK) can parse structured JSON without regex, enabling
    queries like ``level=ERROR and correlation_id=req-abc``.
    """

    #: Fields guaranteed to appear in every log record, in this order.
    REQUIRED_FIELDS = ("timestamp", "level", "logger", "message",
                       "correlation_id", "extra")

    def __init__(
        self,
        name: str,
        *,
        stream=None,
        level: int = logging.INFO,
    ) -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        self._stream = stream or sys.stdout
        # Attach a stdout handler that emits the raw JSON line (the
        # formatter is "%(message)s" because the message IS the JSON).
        # Only attach if no handler is configured yet, to avoid duplicate
        # lines when the root logger already has handlers.
        if not self._logger.handlers:
            handler = logging.StreamHandler(self._stream)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
        # Do not propagate to the root logger — we manage our own output.
        self._logger.propagate = False

    def _emit(
        self,
        level: int,
        level_name: str,
        message: str,
        extra: Optional[dict],
    ) -> str:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level_name,
            "logger": self._logger.name,
            "message": message,
            "correlation_id": get_correlation_id(),
            "extra": extra or {},
        }
        line = json.dumps(record, default=str, ensure_ascii=False)
        self._logger.log(level, line)
        return line

    def debug(self, message: str, **extra: Any) -> str:
        return self._emit(logging.DEBUG, "DEBUG", message, extra)

    def info(self, message: str, **extra: Any) -> str:
        return self._emit(logging.INFO, "INFO", message, extra)

    def warning(self, message: str, **extra: Any) -> str:
        return self._emit(logging.WARNING, "WARNING", message, extra)

    def error(self, message: str, **extra: Any) -> str:
        return self._emit(logging.ERROR, "ERROR", message, extra)

    def exception(self, message: str, **extra: Any) -> str:
        """Like :meth:`error` but attaches the current traceback."""
        import traceback as _tb
        merged = {**extra, "exc_info": _tb.format_exc()}
        return self._emit(logging.ERROR, "ERROR", message, merged)


# ---------------------------------------------------------------------------
# Sentry integration (optional)
# ---------------------------------------------------------------------------
_sentry_initialised: bool = False


def init_sentry(
    *,
    dsn: Optional[str] = None,
    environment: Optional[str] = None,
) -> bool:
    """Initialise the Sentry SDK if a DSN is available.

    Returns ``True`` if Sentry was initialised, ``False`` otherwise.
    Safe to call multiple times — subsequent calls are no-ops.

    The DSN is read from the ``SENTRY_DSN`` environment variable unless
    explicitly passed. If no DSN is configured the function returns
    ``False`` and the rest of the application continues normally
    (graceful degradation — observability is best-effort, not a hard
    dependency).
    """
    global _sentry_initialised
    if _sentry_initialised:
        return True

    resolved_dsn = dsn or os.getenv("SENTRY_DSN")
    if not resolved_dsn:
        return False

    try:
        import sentry_sdk  # type: ignore
        from sentry_sdk.integrations.logging import (  # type: ignore
            LoggingIntegration,
        )
    except ImportError:
        # sentry-sdk not installed — skip silently.
        return False

    try:
        sentry_sdk.init(
            dsn=resolved_dsn,
            environment=environment
            or os.getenv("SENTRY_ENVIRONMENT", "production"),
            traces_sample_rate=float(
                os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")
            ),
            send_default_pii=False,
            integrations=[
                LoggingIntegration(
                    level=logging.INFO,      # breadcrumbs for INFO+
                    event_level=logging.ERROR,  # events for ERROR+
                ),
            ],
        )
        _sentry_initialised = True
        return True
    except Exception:
        # Any Sentry init failure must not break app startup.
        return False


# ---------------------------------------------------------------------------
# Correlation ID middleware — pure ASGI (not BaseHTTPMiddleware)
# ---------------------------------------------------------------------------
class CorrelationIdMiddleware:
    """ASGI middleware that sets the request-scoped correlation ID.

    Reads the ``X-Request-ID`` header if present; otherwise generates a
    fresh UUID4 hex string. The value is stored in
    :data:`correlation_id_var` so any code running within the request's
    async task tree (route handlers, background tasks, downstream
    services) can read it via :func:`get_correlation_id`.

    The correlation ID is also echoed back to the client via the
    ``X-Request-ID`` response header, allowing clients to correlate a
    response with a server-side log entry.

    Why pure ASGI instead of ``BaseHTTPMiddleware``?
        ``BaseHTTPMiddleware`` spawns a child task for the inner app,
        which can break ``ContextVar`` propagation in subtle ways. The
        pure-ASGI form runs the inner app in the same task, so the
        contextvar is reliably visible to route handlers.
    """

    HEADER_NAME = "X-Request-ID"

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        # Only intercept HTTP requests; pass through lifespan, websocket,
        # etc. unchanged.
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        headers = scope.get("headers") or []
        raw = None
        for name, value in headers:
            if name == b"x-request-id":
                raw = value
                break

        if raw:
            cid = raw.decode("utf-8", errors="replace")
        else:
            cid = uuid.uuid4().hex

        token = correlation_id_var.set(cid)

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers_list = list(message.get("headers", []))
                # Avoid duplicate header if downstream already set it.
                if not any(
                    name == b"x-request-id" for name, _ in headers_list
                ):
                    headers_list.append(
                        (b"x-request-id", cid.encode("utf-8"))
                    )
                message["headers"] = headers_list
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            correlation_id_var.reset(token)


__all__ = [
    "StructuredLogger",
    "PrometheusMetrics",
    "metrics",
    "correlation_id_var",
    "get_correlation_id",
    "set_correlation_id",
    "reset_correlation_id",
    "init_sentry",
    "CorrelationIdMiddleware",
    "CONTENT_TYPE_LATEST",
    "generate_latest",
]
