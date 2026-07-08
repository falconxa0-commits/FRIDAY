"""Tests for privacy audit — report returns expected keys, purge goes through ledger."""
import pytest


class TestPrivacyAudit:
    """Test the privacy audit engine."""

    def test_report_returns_expected_keys(self):
        import os
        from config.settings import FRIDAY_API_TOKEN
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        H = {"Authorization": "Bearer test"}
        r = client.get("/api/privacy/report", headers=H)
        assert r.status_code == 200
        data = r.json()
        assert "timestamp" in data
        assert "facts_known" in data
        assert "api_calls" in data
        assert "storage" in data

    def test_purge_goes_through_ledger(self):
        """Purge must NOT execute immediately — must go through ledger."""
        import os
        from config.settings import FRIDAY_API_TOKEN
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        H = {"Authorization": "Bearer test"}
        r = client.delete("/api/privacy/purge/glm", headers=H)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "pending_approval"
        assert "action_id" in data
        assert "glm" in data["message"]

    def test_provider_data_endpoint(self):
        import os
        from config.settings import FRIDAY_API_TOKEN
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        H = {"Authorization": "Bearer test"}
        r = client.get("/api/privacy/data/glm", headers=H)
        assert r.status_code == 200
        data = r.json()
        assert data["provider"] == "glm"
        assert "requests" in data
        assert "count" in data
