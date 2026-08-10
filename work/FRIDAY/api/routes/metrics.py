"""Prometheus metrics endpoint — ``GET /metrics``.

Exposes every metric registered with the default ``prometheus_client``
REGISTRY in the standard Prometheus text exposition format.

The endpoint is **unauthenticated** (Prometheus scrapers need anonymous
access) but is restricted to localhost clients unless the
``METRICS_ALLOW_EXTERNAL=1`` environment variable is set. This prevents
accidentally exposing internal counters to the public internet when
FRIDAY is deployed without a reverse proxy that filters ``/metrics``.

Dynamic gauges (memory count, integration status, ledger chain valid)
are refreshed on every scrape so the values reflect the current state
of the system rather than the state at process start.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

from fastapi import APIRouter, HTTPException, Request, Response

from core.observability import (
    CONTENT_TYPE_LATEST,
    StructuredLogger,
    generate_latest,
    metrics,
)

logger = StructuredLogger("friday.api.metrics")

router = APIRouter()


# Hosts considered "local" — Prometheus scrapers running on the same
# host as FRIDAY. We deliberately do NOT include the Starlette
# TestClient's "testclient" host so that production-style external
# requests are blocked by default; the test suite opt-ins use either
# ``TestClient(client=("127.0.0.1", 0))`` or ``METRICS_ALLOW_EXTERNAL=1``.
_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_local_request(client_host: str) -> bool:
    """Return True if the requesting host is on the local machine."""
    return client_host in _LOCALHOST_HOSTS


def _refresh_dynamic_gauges() -> None:
    """Update gauges whose values depend on live subsystem state.

    Each subsystem update is wrapped in its own try/except so a failure
    in one (e.g. memory not yet initialised) does not prevent the others
    from updating or break the ``/metrics`` scrape.
    """
    # ---- Memory count ---------------------------------------------------
    try:
        from core.memory import FridayMemory
        memory = FridayMemory()
        count = len(getattr(memory, "_memories", []))
        metrics.memory_count.set(count)
    except Exception as exc:
        logger.debug("memory_count gauge update skipped", reason=str(exc))

    # ---- Integration status --------------------------------------------
    try:
        from core.universal_connector import UniversalConnector
        connector = UniversalConnector()
        for name, integration in connector.integrations.items():
            try:
                live = bool(integration.available())
            except Exception:
                live = False
            metrics.integration_status.labels(integration_name=name).set(
                1 if live else 0
            )
    except Exception as exc:
        logger.debug(
            "integration_status gauge update skipped", reason=str(exc)
        )

    # ---- Ledger chain validity -----------------------------------------
    try:
        from core.ledger import get_ledger
        ledger = get_ledger()
        valid = bool(ledger.verify_chain())
        metrics.ledger_chain_valid.set(1 if valid else 0)
    except Exception as exc:
        logger.debug(
            "ledger_chain_valid gauge update skipped", reason=str(exc)
        )


@router.get("/metrics")
async def metrics_endpoint(request: Request):
    """Expose Prometheus metrics in the text exposition format.

    Access control:
      * Requests from localhost (``127.0.0.1``, ``::1``, ``localhost``)
        are always allowed.
      * Requests from any other host are rejected with ``403 Forbidden``
        unless ``METRICS_ALLOW_EXTERNAL=1`` is set in the environment.

    The endpoint is intentionally unauthenticated — Prometheus scrapers
    typically present no credentials. Restrict by network origin instead.
    """
    client = request.client
    client_host = client.host if client else ""

    allow_external = os.getenv("METRICS_ALLOW_EXTERNAL") == "1"
    if not _is_local_request(client_host) and not allow_external:
        # Do not leak that the endpoint exists to anonymous external
        # callers — return 403 with a minimal body.
        raise HTTPException(
            status_code=403,
            detail="Metrics endpoint restricted to localhost. "
            "Set METRICS_ALLOW_EXTERNAL=1 to allow external scrapers.",
        )

    # Refresh gauges that reflect live state, then serialise.
    _refresh_dynamic_gauges()
    body = generate_latest()
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)
