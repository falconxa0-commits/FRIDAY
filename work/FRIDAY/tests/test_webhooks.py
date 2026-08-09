"""Tests for webhook receiver — HMAC verification works, invalid signature = 401."""
import hashlib
import hmac
import json
import os
import pytest


def _github_signature(secret: str, body: bytes) -> str:
    """Compute a valid X-Hub-Signature-256 header value."""
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


class TestWebhooks:
    """Test the webhook receiver.

    WAVE1-SEC changed the security model to fail-closed: GitHub webhooks
    require GITHUB_WEBHOOK_SECRET + valid X-Hub-Signature-256. These
    tests set the secret and compute a valid signature so the happy-path
    logic is exercised. See tests/test_security_regression.py for the
    fail-closed regression tests.
    """

    def test_custom_webhook_accepted(self):
        os.environ["FRIDAY_DEV_MODE"] = "1"
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        r = client.post("/api/webhooks/custom", json={"event": "test"})
        assert r.status_code == 200
        assert r.json()["status"] == "received"

    def test_github_webhook_parses_pr(self):
        secret = "test-github-webhook-secret"
        os.environ["GITHUB_WEBHOOK_SECRET"] = secret
        os.environ["FRIDAY_DEV_MODE"] = "1"
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        payload = {
            "action": "opened",
            "pull_request": {"title": "Test PR", "html_url": "https://github.com/test/repo/pull/1"},
        }
        body = json.dumps(payload).encode("utf-8")
        r = client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": _github_signature(secret, body),
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["event"] == "pull_request"
        assert data["action"] == "opened"
        assert data["pr_title"] == "Test PR"

    def test_github_push_event(self):
        secret = "test-github-webhook-secret"
        os.environ["GITHUB_WEBHOOK_SECRET"] = secret
        os.environ["FRIDAY_DEV_MODE"] = "1"
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        payload = {"ref": "refs/heads/main", "commits": [{"message": "fix: test"}]}
        body = json.dumps(payload).encode("utf-8")
        r = client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": _github_signature(secret, body),
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["event"] == "push"
        assert data["commit_count"] == 1

    def test_unknown_source_returns_404(self):
        os.environ["FRIDAY_DEV_MODE"] = "1"
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        r = client.post("/api/webhooks/unknown_source", json={})
        assert r.status_code == 404
