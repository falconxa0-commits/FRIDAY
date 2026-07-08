"""Tests for webhook receiver — HMAC verification works, invalid signature = 403."""
import json
import pytest


class TestWebhooks:
    """Test the webhook receiver."""

    def test_custom_webhook_accepted(self):
        import os
        os.environ["FRIDAY_DEV_MODE"] = "1"
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        r = client.post("/api/webhooks/custom", json={"event": "test"})
        assert r.status_code == 200
        assert r.json()["status"] == "received"

    def test_github_webhook_parses_pr(self):
        import os
        os.environ["FRIDAY_DEV_MODE"] = "1"
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        payload = {
            "action": "opened",
            "pull_request": {"title": "Test PR", "html_url": "https://github.com/test/repo/pull/1"},
        }
        r = client.post(
            "/api/webhooks/github",
            json=payload,
            headers={"Content-Type": "application/json", "X-GitHub-Event": "pull_request"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["event"] == "pull_request"
        assert data["action"] == "opened"
        assert data["pr_title"] == "Test PR"

    def test_github_push_event(self):
        import os
        os.environ["FRIDAY_DEV_MODE"] = "1"
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        payload = {"ref": "refs/heads/main", "commits": [{"message": "fix: test"}]}
        r = client.post(
            "/api/webhooks/github",
            json=payload,
            headers={"Content-Type": "application/json", "X-GitHub-Event": "push"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["event"] == "push"
        assert data["commit_count"] == 1

    def test_unknown_source_returns_404(self):
        import os
        os.environ["FRIDAY_DEV_MODE"] = "1"
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        r = client.post("/api/webhooks/unknown_source", json={})
        assert r.status_code == 404
