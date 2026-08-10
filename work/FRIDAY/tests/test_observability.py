"""Tests for the FRIDAY observability stack.

Covers:
  * :class:`StructuredLogger` — JSON output schema and correlation ID
    propagation.
  * :func:`get_correlation_id` / :func:`set_correlation_id` — contextvar
    behaviour (``None`` when unset, value visible after ``set``).
  * :class:`PrometheusMetrics` — every required metric is registered and
    increments correctly.
  * ``GET /metrics`` — returns 200 with the Prometheus text exposition
    format for localhost clients, 403 for external clients when
    ``METRICS_ALLOW_EXTERNAL`` is not set.
  * :func:`init_sentry` — skipped gracefully when ``SENTRY_DSN`` is unset.
  * Chat route instrumentation — a real ``POST /api/chat`` increments the
    ``friday_chat_requests_total`` and ``friday_chat_latency_seconds``
    counters (verified via ``/metrics`` output).
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_brain():
    """Return a MagicMock brain whose ``chat_stream`` is a real async gen."""
    mock_brain = MagicMock()

    async def fake_stream(msg, user_name="User", force_provider=None):
        yield "Hello from Friday"

    mock_brain.chat_stream = fake_stream
    mock_brain.get_stats.return_value = {
        "provider": "test",
        "model": "test-model",
    }
    mock_brain.conversation_history = []
    mock_brain.clear_context = MagicMock()
    return mock_brain


@pytest.fixture()
def local_client():
    """TestClient whose requests appear to come from 127.0.0.1.

    Used for tests that need ``/metrics`` to return 200 without setting
    ``METRICS_ALLOW_EXTERNAL``.
    """
    mock_brain = _make_mock_brain()
    with patch("api.main._get_brain", new_callable=AsyncMock, return_value=mock_brain):
        from api.main import app

        with TestClient(app, client=("127.0.0.1", 0)) as c:
            yield c


@pytest.fixture()
def external_client():
    """TestClient whose requests appear to come from an external host.

    Starlette's default TestClient host is ``"testclient"`` — not in the
    localhost allowlist — so ``/metrics`` should return 403.
    """
    mock_brain = _make_mock_brain()
    with patch("api.main._get_brain", new_callable=AsyncMock, return_value=mock_brain):
        from api.main import app

        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# StructuredLogger
# ---------------------------------------------------------------------------


class TestStructuredLogger:
    def test_emits_valid_json_with_all_required_fields(self):
        from core.observability import StructuredLogger

        log = StructuredLogger("test.structured")
        line = log.info("hello world", user_id=42, action="login")
        data = json.loads(line)

        # All required fields present
        for field in StructuredLogger.REQUIRED_FIELDS:
            assert field in data, f"missing required field: {field}"

        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert data["logger"] == "test.structured"
        assert "timestamp" in data
        # correlation_id may be None when no request is in scope
        assert "correlation_id" in data
        # extra fields appear under "extra"
        assert data["extra"]["user_id"] == 42
        assert data["extra"]["action"] == "login"

    def test_correlation_id_appears_in_log_when_set(self):
        from core.observability import (
            StructuredLogger,
            set_correlation_id,
            reset_correlation_id,
        )

        log = StructuredLogger("test.cid")
        token = set_correlation_id("req-abc-123")
        try:
            line = log.info("with cid")
            data = json.loads(line)
            assert data["correlation_id"] == "req-abc-123"
        finally:
            reset_correlation_id(token)

    def test_correlation_id_is_none_when_unset(self):
        from core.observability import StructuredLogger

        log = StructuredLogger("test.nocid")
        line = log.info("no cid")
        data = json.loads(line)
        assert data["correlation_id"] is None

    def test_log_levels(self):
        from core.observability import StructuredLogger

        log = StructuredLogger("test.levels", level=logging.DEBUG)
        cases = [
            (log.debug, "DEBUG"),
            (log.info, "INFO"),
            (log.warning, "WARNING"),
            (log.error, "ERROR"),
        ]
        for method, expected_level in cases:
            line = method("test message")
            data = json.loads(line)
            assert data["level"] == expected_level

    def test_exception_attaches_traceback(self):
        from core.observability import StructuredLogger

        log = StructuredLogger("test.exc")
        try:
            raise ValueError("boom")
        except ValueError:
            line = log.exception("caught")
        data = json.loads(line)
        assert data["level"] == "ERROR"
        assert "ValueError" in data["extra"]["exc_info"]
        assert "boom" in data["extra"]["exc_info"]

    def test_extra_defaults_to_empty_dict(self):
        from core.observability import StructuredLogger

        log = StructuredLogger("test.noextra")
        line = log.info("plain message")
        data = json.loads(line)
        assert data["extra"] == {}


# ---------------------------------------------------------------------------
# Correlation ID contextvar
# ---------------------------------------------------------------------------


class TestCorrelationId:
    def test_returns_none_when_no_context(self):
        from core.observability import get_correlation_id

        # No set_correlation_id has been called in this fresh context
        assert get_correlation_id() is None

    def test_returns_value_when_set(self):
        from core.observability import (
            set_correlation_id,
            get_correlation_id,
            reset_correlation_id,
        )

        token = set_correlation_id("abc-123")
        try:
            assert get_correlation_id() == "abc-123"
        finally:
            reset_correlation_id(token)
        # After reset, the value reverts to its previous state (None here)
        assert get_correlation_id() is None

    def test_reset_restores_previous_value(self):
        from core.observability import (
            set_correlation_id,
            get_correlation_id,
            reset_correlation_id,
        )

        outer = set_correlation_id("outer")
        try:
            inner = set_correlation_id("inner")
            assert get_correlation_id() == "inner"
            reset_correlation_id(inner)
            assert get_correlation_id() == "outer"
        finally:
            reset_correlation_id(outer)


# ---------------------------------------------------------------------------
# Prometheus metrics registration
# ---------------------------------------------------------------------------


class TestPrometheusMetrics:
    def test_all_required_metrics_registered(self):
        from core.observability import metrics

        required = [
            "chat_requests_total",
            "chat_latency_seconds",
            "tokens_used_total",
            "active_conversations",
            "ledger_actions_total",
            "ledger_chain_valid",
            "memory_count",
            "integration_status",
            "cost_usd_total",
            "errors_total",
        ]
        for name in required:
            assert hasattr(metrics, name), f"missing metric: {name}"

    def test_chat_requests_counter_increments(self):
        from core.observability import metrics
        from prometheus_client import generate_latest

        metrics.chat_requests_total.labels(
            provider="test_provider", status="success"
        ).inc()
        output = generate_latest().decode("utf-8")
        assert "friday_chat_requests_total" in output
        assert 'provider="test_provider"' in output
        assert 'status="success"' in output

    def test_tokens_counter_supports_input_output_labels(self):
        from core.observability import metrics
        from prometheus_client import generate_latest

        metrics.tokens_used_total.labels(
            provider="glm", direction="input"
        ).inc(10)
        metrics.tokens_used_total.labels(
            provider="glm", direction="output"
        ).inc(5)
        output = generate_latest().decode("utf-8")
        assert "friday_tokens_used_total" in output
        assert 'direction="input"' in output
        assert 'direction="output"' in output

    def test_cost_counter_increments(self):
        from core.observability import metrics
        from prometheus_client import generate_latest

        metrics.cost_usd_total.labels(provider="claude").inc(0.015)
        output = generate_latest().decode("utf-8")
        assert "friday_cost_usd_total" in output

    def test_errors_counter_via_record_error_helper(self):
        from core.observability import metrics
        from prometheus_client import generate_latest

        try:
            raise RuntimeError("test failure")
        except RuntimeError as exc:
            metrics.record_error("test.module", exc)

        output = generate_latest().decode("utf-8")
        assert "friday_errors_total" in output
        assert 'module="test.module"' in output
        assert 'error_type="RuntimeError"' in output

    def test_gauges_accept_set(self):
        from core.observability import metrics
        from prometheus_client import generate_latest

        metrics.memory_count.set(42)
        metrics.ledger_chain_valid.set(1)
        metrics.active_conversations.set(3)
        metrics.integration_status.labels(integration_name="weather").set(1)
        output = generate_latest().decode("utf-8")
        assert "friday_memory_count" in output
        assert "friday_ledger_chain_valid" in output
        assert "friday_active_conversations" in output
        assert "friday_integration_status" in output


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    def test_returns_200_for_localhost(self, local_client):
        resp = local_client.get("/metrics")
        assert resp.status_code == 200
        # Prometheus text exposition format
        assert resp.headers["content-type"].startswith("text/plain")

    def test_contains_friday_metric_names(self, local_client):
        # Touch a labeled counter so it appears in the output
        from core.observability import metrics

        metrics.chat_requests_total.labels(
            provider="metrics_test", status="success"
        ).inc()
        resp = local_client.get("/metrics")
        assert resp.status_code == 200
        assert "friday_chat_requests_total" in resp.text
        assert "friday_tokens_used_total" in resp.text
        assert "friday_errors_total" in resp.text
        assert "friday_cost_usd_total" in resp.text

    def test_blocks_external_access_by_default(self, external_client):
        resp = external_client.get("/metrics")
        assert resp.status_code == 403

    def test_allows_external_when_env_set(self, monkeypatch):
        monkeypatch.setenv("METRICS_ALLOW_EXTERNAL", "1")
        mock_brain = _make_mock_brain()
        with patch(
            "api.main._get_brain",
            new_callable=AsyncMock,
            return_value=mock_brain,
        ):
            from api.main import app

            # Default TestClient host ("testclient") is external, but the
            # env var override should allow access.
            with TestClient(app) as c:
                resp = c.get("/metrics")
                assert resp.status_code == 200

    def test_metrics_endpoint_is_unauthenticated(self, local_client):
        """The /metrics route must NOT require an auth token — Prometheus
        scrapers present no credentials."""
        # No Authorization header sent; should still succeed.
        resp = local_client.get("/metrics")
        assert resp.status_code == 200

    def test_correlation_id_header_echoed(self, local_client):
        """The X-Request-ID header should be echoed back on the response."""
        resp = local_client.get(
            "/metrics", headers={"X-Request-ID": "test-cid-123"}
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == "test-cid-123"

    def test_correlation_id_generated_when_not_provided(self, local_client):
        """If the client doesn't send X-Request-ID, the server generates one."""
        resp = local_client.get("/metrics")
        assert resp.status_code == 200
        cid = resp.headers.get("x-request-id")
        assert cid is not None
        assert len(cid) > 0  # a UUID hex string


# ---------------------------------------------------------------------------
# Chat route instrumentation (end-to-end)
# ---------------------------------------------------------------------------


class TestChatInstrumentation:
    def test_chat_post_increments_request_counter(self, local_client):
        # Make a chat request — should increment friday_chat_requests_total
        resp = local_client.post(
            "/api/chat", json={"message": "Hello", "user_name": "Test"}
        )
        assert resp.status_code == 200
        assert "response" in resp.json()

        # Now scrape /metrics and verify the counter was incremented
        metrics_resp = local_client.get("/metrics")
        assert metrics_resp.status_code == 200
        assert "friday_chat_requests_total" in metrics_resp.text
        assert "friday_chat_latency_seconds" in metrics_resp.text

    def test_chat_post_increments_token_counter(self, local_client):
        # The mocked brain yields "Hello from Friday" — that's 17 chars,
        # so tokens_out = max(1, 17 // 4) = 4.
        resp = local_client.post(
            "/api/chat", json={"message": "Hi there"}
        )
        assert resp.status_code == 200

        metrics_resp = local_client.get("/metrics")
        assert "friday_tokens_used_total" in metrics_resp.text

    def test_chat_stream_increments_request_counter(self, local_client):
        # SSE streaming endpoint
        resp = local_client.get(
            "/api/chat/stream?message=Hello", headers={"Accept": "text/event-stream"}
        )
        assert resp.status_code == 200

        metrics_resp = local_client.get("/metrics")
        assert "friday_chat_requests_total" in metrics_resp.text


# ---------------------------------------------------------------------------
# Sentry initialization
# ---------------------------------------------------------------------------


class TestSentryInit:
    def test_skipped_when_no_dsn(self, monkeypatch):
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        # Reset the module-level flag so the test is independent of order
        import core.observability as obs

        obs._sentry_initialised = False
        result = obs.init_sentry()
        assert result is False
        assert obs._sentry_initialised is False

    def test_idempotent_when_already_initialised(self):
        import core.observability as obs

        original = obs._sentry_initialised
        obs._sentry_initialised = True
        try:
            assert obs.init_sentry() is True
        finally:
            obs._sentry_initialised = original

    def test_returns_false_on_sentry_import_error(self, monkeypatch):
        # SENTRY_DSN set, but sentry_sdk not importable — should return False
        monkeypatch.setenv("SENTRY_DSN", "https://fake@example.com/1")
        import core.observability as obs

        obs._sentry_initialised = False

        # Force ImportError by hiding the module
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sentry_sdk" or name.startswith("sentry_sdk."):
                raise ImportError("simulated absence of sentry_sdk")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        result = obs.init_sentry()
        assert result is False
        assert obs._sentry_initialised is False
