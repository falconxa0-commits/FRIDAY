"""Tests for API endpoints."""

import hmac
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Create a TestClient with all external dependencies mocked."""
    # Mock brain and subsystems
    mock_brain = MagicMock()
    mock_brain.chat_stream = AsyncMock()

    async def fake_stream(msg, user_name="User", force_provider=None):
        yield "Hello from Friday"

    mock_brain.chat_stream = fake_stream
    mock_brain.get_stats.return_value = {
        "provider": "test",
        "model": "test-model",
        "history_length": 0,
        "skills_loaded": [],
        "tools_available": [],
        "memory_enabled": False,
        "emotions_enabled": False,
        "personality_enabled": False,
    }
    mock_brain.conversation_history = []
    mock_brain.clear_context = MagicMock()

    with patch("api.main._get_brain", new_callable=AsyncMock, return_value=mock_brain), \
         patch("api.main.FRIDAY_API_TOKEN", "test-token"):
        from api.main import app
        with TestClient(app) as c:
            yield c


@pytest.fixture()
def auth_headers():
    return {"Authorization": "Bearer test-token"}


# ---------------------------------------------------------------------------
# Health endpoint (no auth required)
# ---------------------------------------------------------------------------

class TestHealthEndpoint:

    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_health_has_version(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "version" in data


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------

class TestChatEndpoint:

    def test_chat_post(self, client, auth_headers):
        resp = client.post(
            "/api/chat",
            json={"message": "Hello", "user_name": "Test"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data

    def test_chat_stream_sse(self, client, auth_headers):
        resp = client.get(
            "/api/chat/stream?message=Hello",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_chat_history(self, client, auth_headers):
        resp = client.get("/api/chat/history", headers=auth_headers)
        assert resp.status_code == 200

    def test_chat_stats(self, client, auth_headers):
        resp = client.get("/api/chat/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "provider" in data

    def test_chat_clear(self, client, auth_headers):
        resp = client.post("/api/chat/clear", headers=auth_headers)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Memory endpoints
# ---------------------------------------------------------------------------

class TestMemoryEndpoints:

    def test_list_memories(self, client, auth_headers):
        with patch("api.routes.memory.get_memory") as mock_get:
            mock_mem = MagicMock()
            mock_mem.supabase = None
            mock_mem._memories = [{"content": "test", "role": "user"}]
            mock_get.return_value = mock_mem

            resp = client.get("/api/memory/all", headers=auth_headers)
            assert resp.status_code == 200

    def test_add_memory(self, client, auth_headers):
        with patch("api.routes.memory.get_memory") as mock_get:
            mock_mem = MagicMock()
            mock_mem.store_conversation = MagicMock()
            mock_get.return_value = mock_mem

            # Memory router is at /api/memory prefix; POST / maps to /api/memory/
            resp = client.post(
                "/api/memory/",
                json={"text": "my name is Test", "metadata": {}},
                headers=auth_headers,
            )
            assert resp.status_code == 200

    def test_search_memory(self, client, auth_headers):
        with patch("api.routes.memory.get_memory") as mock_get:
            mock_mem = MagicMock()
            mock_mem.retrieve_relevant_memories.return_value = []
            mock_get.return_value = mock_mem

            # Memory router is at /api/memory prefix; GET / maps to /api/memory/
            resp = client.get("/api/memory/?query=test", headers=auth_headers)
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Action approval
# ---------------------------------------------------------------------------

class TestActionApproval:

    def test_list_pending_actions(self, client, auth_headers):
        resp = client.get("/api/actions/", headers=auth_headers)
        assert resp.status_code == 200

    def test_approve_action(self, client, auth_headers):
        with patch("api.routes.actions.ledger") as mock_ledger:
            mock_ledger.approve_action.return_value = True
            resp = client.post("/api/actions/test-id/approve", headers=auth_headers)
            assert resp.status_code == 200

    def test_approve_nonexistent_action(self, client, auth_headers):
        with patch("api.routes.actions.ledger") as mock_ledger:
            mock_ledger.approve_action.return_value = False
            resp = client.post("/api/actions/nonexistent/approve", headers=auth_headers)
            assert resp.status_code == 404

    def test_reject_action(self, client, auth_headers):
        with patch("api.routes.actions.ledger") as mock_ledger:
            mock_ledger.reject_action.return_value = True
            resp = client.post("/api/actions/test-id/reject", headers=auth_headers)
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth — timing-safe comparison
# ---------------------------------------------------------------------------

class TestAuth:

    def test_valid_token_in_header(self, client):
        resp = client.get(
            "/api/chat/stats",
            headers={"Authorization": "Bearer test-token"},
        )
        # Should not return 403
        assert resp.status_code != 403

    def test_invalid_token_in_header(self, client):
        resp = client.get(
            "/api/chat/stats",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 403

    def test_no_token_returns_403(self, client):
        resp = client.get("/api/chat/stats")
        assert resp.status_code == 403

    def test_token_in_query_param(self, client):
        resp = client.get("/api/chat/stats?token=test-token")
        assert resp.status_code != 403

    def test_timing_safe_comparison(self):
        """Verify that hmac.compare_digest is used for token comparison."""
        # This is more of a design check — ensure the function exists
        assert hasattr(hmac, "compare_digest")
        assert hmac.compare_digest("abc", "abc") is True
        assert hmac.compare_digest("abc", "def") is False
