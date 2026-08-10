"""WAVE3-TEST — Coverage tests for the 20 API route modules.

Each route gets ≥3 tests: happy path, auth (where applicable), and error
handling. External dependencies (FridayBrain, ledger, memory, scheduler,
registry, etc.) are mocked so no LLM calls are made and no real
integrations are touched.

Test layout (one class per route file):
    TestChatRoutes           — /api/chat, /api/chat/stream, /api/chat/history, /clear, /stats
    TestActionsRoutes        — /api/actions/, /approve, /reject, /verify, /audit
    TestHealthRoutes         — /health, /api/health/deep (auth-gated)
    TestGoalsRoutes          — /api/goals CRUD
    TestIdentityRoutes       — /api/identity GET/POST
    TestIntegrationsRoutes   — /api/integrations/, /available, /categories, /execute
    TestMemoryRoutes         — /api/memory/all, /, /export, /import, /wisdom, /compress
    TestNotifyRoutes         — /api/notify POST
    TestPersonaRoutes        — /api/persona/export, /import
    TestSchedulerRoutes      — /api/scheduler/ GET/POST/DELETE
    TestStatsRoutes          — /api/stats, /api/stats/predictor/cache
    TestTrustRoutes          — /api/trust/report (GET/POST), /api/trust/status
    TestWebhooksRoutes       — /api/webhooks/{source}
    TestBranchingRoutes      — /api/chat/branch, /api/chat/branches, /switch, /merge-insight, DELETE
    TestLearningRoutes       — /api/learning/corrections GET/POST/DELETE
    TestPrivacyRoutes        — /api/privacy/report, /data/{provider}, /purge/{provider}
    TestProactiveRoutes      — /api/proactive/briefing, /status
    TestSelfImprovementRoutes— /api/self-improvement/proposals, /analyze, /approve/{id}
    TestSubconsciousRoutes   — /api/subconscious/patterns, /intuition
    TestTeamRoutes           — /api/team/members, /invite, /context, /memory/*, /memories
"""
from __future__ import annotations

import os
import json
import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """TestClient with the brain and all subsystems mocked.

    Patches ``api.main._get_brain`` to return a mock brain whose
    ``chat_stream`` is a real async generator, and patches
    ``api.main.FRIDAY_API_TOKEN`` so the global token is "test-token".
    """
    mock_brain = MagicMock()
    mock_brain.provider = "test"
    mock_brain.model = "test-model"

    async def fake_stream(msg, user_name="User", force_provider=None):
        yield f"response to: {msg}"

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

    # Branching mocks
    mock_brain.branch_conversation = AsyncMock(return_value={"branch_id": "b1"})
    mock_brain.get_branches = AsyncMock(return_value=[{"id": "b1"}])
    mock_brain.switch_branch = AsyncMock(return_value=True)
    mock_brain.merge_branch_insight = AsyncMock(return_value="merged insight")
    mock_brain.delete_branch = AsyncMock(return_value=True)

    # Clear the in-memory request log + goals store for isolation
    import api.routes.stats as stats_module
    stats_module._request_log.clear()
    import api.routes.goals as goals_module
    goals_module._goals.clear()

    with patch("api.main._get_brain", new_callable=AsyncMock, return_value=mock_brain), \
         patch("api.main.FRIDAY_API_TOKEN", "test-token"):
        from api.main import app
        with TestClient(app) as c:
            yield c

    stats_module._request_log.clear()
    goals_module._goals.clear()


@pytest.fixture()
def auth_headers():
    return {"Authorization": "Bearer test-token"}


# ===========================================================================
# 1. /api/chat — chat.py
# ===========================================================================

class TestChatRoutes:
    """Chat endpoints: POST /api/chat, GET /stream, /history, /clear, /stats."""

    def test_chat_post_returns_response(self, client, auth_headers):
        resp = client.post(
            "/api/chat", json={"message": "hi", "user_name": "Tester"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data
        assert "response to: hi" in data["response"]

    def test_chat_post_requires_auth(self, client):
        resp = client.post("/api/chat", json={"message": "hi"})
        assert resp.status_code == 403

    def test_chat_stream_returns_sse(self, client, auth_headers):
        resp = client.get(
            "/api/chat/stream?message=hello", headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        # SSE responses should contain data: lines
        body = resp.text
        assert "data:" in body
        assert "[DONE]" in body

    def test_chat_history_returns_list(self, client, auth_headers):
        resp = client.get("/api/chat/history", headers=auth_headers)
        assert resp.status_code == 200
        assert "history" in resp.json()

    def test_chat_clear_resets_context(self, client, auth_headers):
        resp = client.post("/api/chat/clear", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    def test_chat_stats_returns_brain_stats(self, client, auth_headers):
        resp = client.get("/api/chat/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "provider" in data
        assert data["provider"] == "test"

    def test_chat_invalid_token_rejected(self, client):
        resp = client.post(
            "/api/chat", json={"message": "hi"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 403


# ===========================================================================
# 2. /api/actions — actions.py
# ===========================================================================

class TestActionsRoutes:

    def test_list_pending_actions(self, client, auth_headers):
        resp = client.get("/api/actions/", headers=auth_headers)
        assert resp.status_code == 200
        assert "pending" in resp.json()

    def test_list_actions_requires_auth(self, client):
        resp = client.get("/api/actions/")
        assert resp.status_code == 403

    def test_approve_action_success(self, client, auth_headers):
        with patch("api.routes.actions.ledger") as mock_ledger:
            mock_ledger.approve_action.return_value = True
            resp = client.post("/api/actions/abc/approve", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["status"] == "success"

    def test_approve_action_not_found(self, client, auth_headers):
        with patch("api.routes.actions.ledger") as mock_ledger:
            mock_ledger.approve_action.return_value = False
            resp = client.post("/api/actions/missing/approve", headers=auth_headers)
            assert resp.status_code == 404

    def test_reject_action_success(self, client, auth_headers):
        with patch("api.routes.actions.ledger") as mock_ledger:
            mock_ledger.reject_action.return_value = True
            resp = client.post("/api/actions/abc/reject", headers=auth_headers)
            assert resp.status_code == 200

    def test_reject_action_not_found(self, client, auth_headers):
        with patch("api.routes.actions.ledger") as mock_ledger:
            mock_ledger.reject_action.return_value = False
            resp = client.post("/api/actions/missing/reject", headers=auth_headers)
            assert resp.status_code == 404

    def test_verify_chain_endpoint(self, client, auth_headers):
        with patch("api.routes.actions.ledger") as mock_ledger:
            mock_ledger.verify_chain.return_value = True
            mock_ledger.get_audit_log.return_value = [{"id": 1}]
            resp = client.get("/api/actions/verify", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["valid"] is True
            assert data["entry_count"] == 1

    def test_audit_log_endpoint(self, client, auth_headers):
        with patch("api.routes.actions.ledger") as mock_ledger:
            mock_ledger.get_audit_log.return_value = [{"id": "e1"}]
            resp = client.get("/api/actions/audit", headers=auth_headers)
            assert resp.status_code == 200
            assert "entries" in resp.json()


# ===========================================================================
# 3. /health, /api/health/deep — health.py + main.py
# ===========================================================================

class TestHealthRoutes:

    def test_health_returns_200_no_auth(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_health_has_version(self, client):
        resp = client.get("/health")
        assert "version" in resp.json()

    def test_health_deep_requires_auth_in_production(self):
        """When FRIDAY_DEV_MODE is unset and token is set, deep health
        requires authentication. We test the 401 path directly via
        core.auth.require_auth (not the dev-mode bypass)."""
        from core import auth as auth_module
        with patch.object(auth_module, "FRIDAY_DEV_MODE", False), \
             patch.object(auth_module, "FRIDAY_API_TOKEN", "secret-token"):
            from fastapi import HTTPException
            from fastapi.security import HTTPAuthorizationCredentials

            cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
            req = MagicMock()
            req.query_params = {}

            import asyncio
            loop = asyncio.new_event_loop()
            try:
                with pytest.raises(HTTPException) as exc:
                    loop.run_until_complete(auth_module.require_auth(req, cred))
                assert exc.value.status_code == 401
            finally:
                loop.close()

    def test_health_deep_returns_full_report_in_dev_mode(self, client):
        """In dev mode (set by conftest), /api/health/deep returns the full
        system report rather than a 401."""
        # Patch the deep-check helpers to avoid real network / module imports
        with patch("api.routes.health._check_glm", new=AsyncMock(return_value={
            "name": "GLM Brain", "status": "warn", "latency_ms": 1.0,
            "detail": "no key", "fix": "set GLM_API_KEY",
        })), \
        patch("api.routes.health._check_claude", new=AsyncMock(return_value={
            "name": "Claude", "status": "warn", "latency_ms": 0.5,
            "detail": "no key", "fix": None,
        })), \
        patch("api.routes.health._check_gemini", new=AsyncMock(return_value={
            "name": "Gemini", "status": "warn", "latency_ms": 0.5,
            "detail": "no key", "fix": None,
        })), \
        patch("api.routes.health._check_ollama", new=AsyncMock(return_value={
            "name": "Ollama", "status": "warn", "latency_ms": 0.5,
            "detail": "not running", "fix": None,
        })), \
        patch("api.routes.health._check_integrations", new=AsyncMock(return_value={
            "name": "Integrations", "status": "warn", "latency_ms": 1.0,
            "detail": "0/0", "fix": None,
        })), \
        patch("api.routes.health._check_memory", new=AsyncMock(return_value={
            "name": "Memory", "status": "pass", "latency_ms": 1.0,
            "detail": "10 memories", "fix": None,
        })), \
        patch("api.routes.health._check_ledger", new=AsyncMock(return_value={
            "name": "Ledger", "status": "pass", "latency_ms": 1.0,
            "detail": "valid", "fix": None,
        })), \
        patch("api.routes.health._check_sentinel", new=AsyncMock(return_value={
            "name": "Sentinel", "status": "pass", "latency_ms": 1.0,
            "detail": "ready", "fix": None,
        })), \
        patch("api.routes.health._check_api_endpoints", new=AsyncMock(return_value={
            "name": "API", "status": "pass", "latency_ms": 1.0,
            "detail": "200 OK", "fix": None,
        })), \
        patch("api.routes.health._check_voice", new=AsyncMock(return_value={
            "name": "Voice", "status": "warn", "latency_ms": 1.0,
            "detail": "no TTS", "fix": None,
        })):
            resp = client.get("/api/health/deep")
            assert resp.status_code == 200
            data = resp.json()
            assert "overall" in data
            assert "summary" in data
            assert "checks" in data
            assert data["summary"]["total"] == 10


# ===========================================================================
# 4. /api/goals — goals.py
# ===========================================================================

class TestGoalsRoutes:

    def test_create_and_list_goal(self, client, auth_headers):
        resp = client.post(
            "/api/goals",
            json={"description": "learn rust", "target_date": "2099-01-01"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        goal = resp.json()["goal"]
        assert goal["description"] == "learn rust"
        goal_id = goal["id"]

        list_resp = client.get("/api/goals", headers=auth_headers)
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data["count"] == 1
        assert data["goals"][0]["id"] == goal_id

    def test_create_goal_requires_auth(self, client):
        resp = client.post(
            "/api/goals",
            json={"description": "x", "target_date": "2099-01-01"},
        )
        assert resp.status_code == 403

    def test_update_progress_on_goal(self, client, auth_headers):
        create = client.post(
            "/api/goals",
            json={"description": "exercise daily", "target_date": "2099-01-01"},
            headers=auth_headers,
        )
        goal_id = create.json()["goal"]["id"]
        resp = client.patch(
            f"/api/goals/{goal_id}/progress",
            json={"progress_note": "did 20 pushups"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["progress_count"] == 1

    def test_update_progress_on_missing_goal_404(self, client, auth_headers):
        resp = client.patch(
            "/api/goals/nonexistent/progress",
            json={"progress_note": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_get_nudge_for_goal(self, client, auth_headers):
        create = client.post(
            "/api/goals",
            json={"description": "write tests", "target_date": "2099-01-01"},
            headers=auth_headers,
        )
        goal_id = create.json()["goal"]["id"]
        resp = client.get(f"/api/goals/{goal_id}/nudge", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "nudge" in data
        assert "streak" in data
        assert "days_left" in data

    def test_delete_goal(self, client, auth_headers):
        create = client.post(
            "/api/goals",
            json={"description": "temp goal", "target_date": "2099-01-01"},
            headers=auth_headers,
        )
        goal_id = create.json()["goal"]["id"]
        resp = client.delete(f"/api/goals/{goal_id}", headers=auth_headers)
        assert resp.status_code == 200
        # Subsequent GET should not find it
        list_resp = client.get("/api/goals", headers=auth_headers)
        assert list_resp.json()["count"] == 0

    def test_delete_missing_goal_404(self, client, auth_headers):
        resp = client.delete("/api/goals/nope", headers=auth_headers)
        assert resp.status_code == 404


# ===========================================================================
# 5. /api/identity — identity.py
# ===========================================================================

class TestIdentityRoutes:

    def test_get_identity_returns_modes(self, client, auth_headers):
        resp = client.get("/api/identity", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "current_mode" in data
        assert "available_modes" in data
        assert "General" in data["available_modes"]

    def test_set_identity_to_valid_mode(self, client, auth_headers):
        resp = client.post("/api/identity/Strategist", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["new_mode"] == "Strategist"
        assert data["old_mode"] == "General"

    def test_set_identity_to_invalid_mode_returns_400(self, client, auth_headers):
        resp = client.post("/api/identity/Nonexistent", headers=auth_headers)
        assert resp.status_code == 400

    def test_get_identity_requires_auth(self, client):
        resp = client.get("/api/identity")
        assert resp.status_code == 403


# ===========================================================================
# 6. /api/integrations — integrations.py
# ===========================================================================

class TestIntegrationsRoutes:

    def test_list_integrations(self, client, auth_headers):
        with patch("api.routes.integrations._get_registry") as mock_reg:
            mock_reg.return_value.get_all_services.return_value = [
                {"name": "weather", "category": "info"},
            ]
            resp = client.get("/api/integrations/", headers=auth_headers)
            assert resp.status_code == 200
            assert "integrations" in resp.json()

    def test_list_integrations_requires_auth(self, client):
        resp = client.get("/api/integrations/")
        assert resp.status_code == 403

    def test_list_available_services(self, client, auth_headers):
        with patch("api.routes.integrations._get_registry") as mock_reg:
            mock_reg.return_value.get_available_services.return_value = ["weather"]
            resp = client.get("/api/integrations/available", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["available"] == ["weather"]

    def test_list_categories(self, client, auth_headers):
        with patch("api.routes.integrations._get_registry") as mock_reg:
            mock_reg.return_value.get_categories.return_value = {"info": ["weather"]}
            resp = client.get("/api/integrations/categories", headers=auth_headers)
            assert resp.status_code == 200
            assert "categories" in resp.json()

    def test_list_integrations_503_when_registry_unavailable(self, client, auth_headers):
        with patch("api.routes.integrations._get_registry", return_value=None):
            resp = client.get("/api/integrations/", headers=auth_headers)
            assert resp.status_code == 503

    def test_execute_integration(self, client, auth_headers):
        with patch("api.routes.integrations._get_connector") as mock_conn:
            mock_conn.return_value.execute_action = AsyncMock(return_value={
                "status": "success", "message": "done", "receipt": {"data": {"x": 1}},
            })
            resp = client.post(
                "/api/integrations/execute",
                json={"service": "weather", "action": "forecast", "params": {"city": "NYC"}},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert "receipt" in data


# ===========================================================================
# 7. /api/memory — memory.py
# ===========================================================================

class TestMemoryRoutes:

    def test_list_all_memories(self, client, auth_headers):
        with patch("api.routes.memory.get_memory") as mock_get:
            mock_mem = MagicMock()
            mock_mem.supabase = None
            mock_mem._memories = [{"id": "1", "content": "hello"}]
            mock_get.return_value = mock_mem
            resp = client.get("/api/memory/all", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json() == [{"id": "1", "content": "hello"}]

    def test_list_memories_requires_auth(self, client):
        resp = client.get("/api/memory/all")
        assert resp.status_code == 403

    def test_add_memory(self, client, auth_headers):
        with patch("api.routes.memory.get_memory") as mock_get:
            mock_mem = MagicMock()
            mock_mem.store_conversation = MagicMock()
            mock_get.return_value = mock_mem
            resp = client.post(
                "/api/memory/",
                json={"text": "remember this", "metadata": {"k": "v"}},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            mock_mem.store_conversation.assert_called_once()

    def test_search_memory(self, client, auth_headers):
        with patch("api.routes.memory.get_memory") as mock_get:
            mock_mem = MagicMock()
            mock_mem.retrieve_relevant_memories.return_value = [{"content": "match"}]
            mock_get.return_value = mock_mem
            resp = client.get("/api/memory/?query=test", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["results"] == [{"content": "match"}]

    def test_delete_memory(self, client, auth_headers):
        with patch("api.routes.memory.get_memory") as mock_get:
            mock_mem = MagicMock()
            mock_mem.vector_store = None
            mock_mem._memories = [{"id": "abc", "content": "x"}]
            mock_get.return_value = mock_mem
            resp = client.delete("/api/memory/abc", headers=auth_headers)
            assert resp.status_code == 200

    def test_delete_memory_not_found(self, client, auth_headers):
        with patch("api.routes.memory.get_memory") as mock_get:
            mock_mem = MagicMock()
            mock_mem.vector_store = None
            mock_mem._memories = []
            mock_get.return_value = mock_mem
            resp = client.delete("/api/memory/missing", headers=auth_headers)
            assert resp.status_code == 404

    def test_memory_unavailable_returns_503(self, client, auth_headers):
        with patch("api.routes.memory.get_memory", return_value=None):
            resp = client.get("/api/memory/all", headers=auth_headers)
            assert resp.status_code == 503

    def test_export_memories(self, client, auth_headers):
        with patch("api.routes.memory.get_memory") as mock_get:
            mock_mem = MagicMock()
            mock_mem._memories = [{"id": "1", "content": "a"}]
            mock_mem._session_facts = {"name": "Alice"}
            mock_get.return_value = mock_mem
            resp = client.get("/api/memory/export", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["memory_count"] == 1
            assert data["session_facts"] == {"name": "Alice"}


# ===========================================================================
# 8. /api/notify — notify.py
# ===========================================================================

class TestNotifyRoutes:

    def test_notify_with_no_channels_configured(self, client, auth_headers):
        """When neither Telegram nor desktop is configured, the route still
        returns 200 with results showing 'not_implemented' / 'error'."""
        resp = client.post(
            "/api/notify",
            json={"message": "hello", "title": "Test", "channels": ["telegram", "desktop"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["total_channels"] == 2

    def test_notify_requires_auth(self, client):
        resp = client.post("/api/notify", json={"message": "hello"})
        assert resp.status_code == 403

    def test_notify_with_empty_channels(self, client, auth_headers):
        resp = client.post(
            "/api/notify",
            json={"message": "hello", "channels": []},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total_channels"] == 0

    def test_notify_desktop_channel_with_mock(self, client, auth_headers):
        # DesktopNotifier is imported lazily inside the route handler, so
        # we patch it on its source module.
        with patch("integrations.notifications.DesktopNotifier") as mock_dn_cls:
            mock_dn = MagicMock()
            mock_dn.notify.return_value = {"status": "success", "message": "shown"}
            mock_dn_cls.return_value = mock_dn
            resp = client.post(
                "/api/notify",
                json={"message": "hi", "channels": ["desktop"]},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            results = resp.json()["results"]
            assert len(results) == 1
            assert results[0]["channel"] == "desktop"


# ===========================================================================
# 9. /api/persona — persona.py
# ===========================================================================

class TestPersonaRoutes:

    def test_export_persona(self, client, auth_headers):
        # persona.py imports FridayMemory / PatternEngine / export_persona
        # lazily inside the route handler, so we patch them on their source
        # modules.
        with patch("core.memory.FridayMemory") as mock_mem_cls, \
             patch("core.pattern_engine.PatternEngine") as mock_pe_cls, \
             patch("core.persona.export_persona") as mock_export:
            mock_export.return_value = {"version": "1.0", "memories": []}
            resp = client.get("/api/persona/export", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["version"] == "1.0"

    def test_export_persona_requires_auth(self, client):
        resp = client.get("/api/persona/export")
        assert resp.status_code == 403

    def test_import_persona(self, client, auth_headers):
        with patch("core.memory.FridayMemory") as mock_mem_cls, \
             patch("core.pattern_engine.PatternEngine") as mock_pe_cls, \
             patch("core.persona.import_persona") as mock_import:
            mock_import.return_value = {"memories_imported": 5, "patterns_imported": 2}
            resp = client.post(
                "/api/persona/import",
                json={"persona": {"version": "1.0", "memories": []}},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            assert resp.json()["summary"]["memories_imported"] == 5

    def test_export_persona_returns_500_on_failure(self, client, auth_headers):
        with patch("core.memory.FridayMemory", side_effect=RuntimeError("boom")):
            resp = client.get("/api/persona/export", headers=auth_headers)
            assert resp.status_code == 500


# ===========================================================================
# 10. /api/scheduler — scheduler.py
# ===========================================================================

class TestSchedulerRoutes:

    def test_list_tasks(self, client, auth_headers):
        with patch("api.routes.scheduler._get_scheduler") as mock_get:
            mock_sched = MagicMock()
            mock_sched.get_tasks.return_value = [
                {"id": "t1", "name": "task1", "func": lambda: None, "run_at": None, "last_run": None},
            ]
            mock_get.return_value = mock_sched
            resp = client.get("/api/scheduler/", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["tasks"]) == 1
            # The 'func' key should have been stripped (not serializable)
            assert "func" not in data["tasks"][0]

    def test_list_tasks_requires_auth(self, client):
        resp = client.get("/api/scheduler/")
        assert resp.status_code == 403

    def test_create_task(self, client, auth_headers):
        with patch("api.routes.scheduler._get_scheduler") as mock_get:
            mock_sched = MagicMock()
            mock_sched.add_task = AsyncMock(return_value="task_abc")
            mock_get.return_value = mock_sched
            resp = client.post(
                "/api/scheduler/",
                json={"name": "test task", "interval_seconds": 60},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            assert resp.json()["task_id"] == "task_abc"

    def test_delete_task(self, client, auth_headers):
        with patch("api.routes.scheduler._get_scheduler") as mock_get:
            mock_sched = MagicMock()
            mock_sched.remove_task.return_value = True
            mock_get.return_value = mock_sched
            resp = client.delete("/api/scheduler/t1", headers=auth_headers)
            assert resp.status_code == 200

    def test_delete_task_not_found(self, client, auth_headers):
        with patch("api.routes.scheduler._get_scheduler") as mock_get:
            mock_sched = MagicMock()
            mock_sched.remove_task.return_value = False
            mock_get.return_value = mock_sched
            resp = client.delete("/api/scheduler/missing", headers=auth_headers)
            assert resp.status_code == 404


# ===========================================================================
# 11. /api/stats — stats.py
# ===========================================================================

class TestStatsRoutes:

    def test_get_stats_returns_full_report(self, client, auth_headers):
        resp = client.get("/api/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "current_provider" in data
        assert "total_requests" in data
        assert "total_cost_usd" in data
        assert "provider_breakdown" in data
        assert "recent_requests" in data

    def test_get_stats_requires_auth(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 403

    def test_record_request_populates_log(self):
        """Unit test for the record_request helper that powers the stats log."""
        from api.routes import stats as stats_module
        stats_module._request_log.clear()
        stats_module.record_request("glm", "glm-4-flash", 100, 50, 0.0)
        assert len(stats_module._request_log) == 1
        entry = stats_module._request_log[0]
        assert entry["provider"] == "glm"
        assert entry["tokens_in"] == 100
        stats_module._request_log.clear()

    def test_estimate_cost_for_known_providers(self):
        from api.routes.stats import _estimate_cost
        # GLM is free
        assert _estimate_cost("glm", 1000, 1000) == 0.0
        # Claude: 0.003 input / 0.015 output per 1K tokens
        assert _estimate_cost("claude", 1000, 1000) == 0.018
        # Unknown provider defaults to free
        assert _estimate_cost("unknown", 1000, 1000) == 0.0

    def test_predictor_cache_endpoint(self, client, auth_headers):
        # Predictor is imported lazily inside the route handler.
        with patch("core.predictor.Predictor") as mock_pred_cls:
            mock_pred = MagicMock()
            mock_pred.get_preload_schedule.return_value = []
            mock_pred_cls.return_value = mock_pred
            resp = client.get("/api/stats/predictor/cache", headers=auth_headers)
            assert resp.status_code == 200
            assert "cache" in resp.json()


# ===========================================================================
# 12. /api/trust — trust.py
# ===========================================================================

class TestTrustRoutes:

    def test_trust_status_returns_placeholder_when_no_audit(self, client, auth_headers):
        # Reset cached audit
        import api.routes.trust as trust_module
        trust_module._last_audit = None
        resp = client.get("/api/trust/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "no_audit_run"

    def test_trust_report_runs_audit(self, client, auth_headers):
        with patch("api.routes.trust._run_hellfire_audit") as mock_run:
            mock_run.return_value = {
                "timestamp": "2026-01-01T00:00:00",
                "checks": [{"name": "x", "passed": True}],
                "total_checks": 1, "passed": 1, "failed": 0, "failures": [],
            }
            resp = client.post("/api/trust/report", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_checks"] == 1
            assert data["failed"] == 0

    def test_trust_report_get_alias(self, client, auth_headers):
        with patch("api.routes.trust._run_hellfire_audit") as mock_run:
            mock_run.return_value = {
                "timestamp": "t", "checks": [], "total_checks": 0,
                "passed": 0, "failed": 0, "failures": [],
            }
            resp = client.get("/api/trust/report", headers=auth_headers)
            assert resp.status_code == 200

    def test_trust_report_requires_auth(self, client):
        resp = client.post("/api/trust/report")
        assert resp.status_code == 403


# ===========================================================================
# 13. /api/webhooks — webhooks.py
# ===========================================================================

def _github_sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestWebhooksRoutesExtended:

    def test_custom_webhook_with_valid_json(self, client):
        resp = client.post("/api/webhooks/custom", json={"event": "deploy"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "received"
        assert "event" in data["payload_keys"]

    def test_custom_webhook_with_invalid_json_still_accepted(self, client):
        resp = client.post(
            "/api/webhooks/custom",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "received"

    def test_github_webhook_rejects_when_no_secret(self, client):
        # Unset the secret
        prev = os.environ.pop("GITHUB_WEBHOOK_SECRET", None)
        try:
            resp = client.post(
                "/api/webhooks/github",
                content=b'{"action":"opened"}',
                headers={"X-GitHub-Event": "pull_request"},
            )
            assert resp.status_code == 503
        finally:
            if prev:
                os.environ["GITHUB_WEBHOOK_SECRET"] = prev

    def test_github_webhook_rejects_invalid_signature(self, client):
        os.environ["GITHUB_WEBHOOK_SECRET"] = "secret"
        try:
            resp = client.post(
                "/api/webhooks/github",
                content=b'{"action":"opened"}',
                headers={
                    "X-GitHub-Event": "pull_request",
                    "X-Hub-Signature-256": "sha256=invalid",
                },
            )
            assert resp.status_code == 401
        finally:
            os.environ.pop("GITHUB_WEBHOOK_SECRET", None)

    def test_github_issues_event_parsed(self, client):
        secret = "test-secret"
        os.environ["GITHUB_WEBHOOK_SECRET"] = secret
        try:
            payload = {"action": "opened", "issue": {"title": "Bug", "html_url": "u"}}
            body = json.dumps(payload).encode()
            resp = client.post(
                "/api/webhooks/github",
                content=body,
                headers={
                    "X-GitHub-Event": "issues",
                    "X-Hub-Signature-256": _github_sig(secret, body),
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["event"] == "issues"
            assert data["issue_title"] == "Bug"
        finally:
            os.environ.pop("GITHUB_WEBHOOK_SECRET", None)

    def test_unknown_webhook_source_404(self, client):
        resp = client.post("/api/webhooks/salesforce", json={})
        assert resp.status_code == 404


# ===========================================================================
# 14. /api/chat/branch — branching.py
# ===========================================================================

class TestBranchingRoutes:

    def test_create_branch(self, client, auth_headers):
        resp = client.post(
            "/api/chat/branch",
            json={"message_index": 3, "new_message": "what if?"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        assert "branch" in resp.json()

    def test_create_branch_requires_auth(self, client):
        resp = client.post("/api/chat/branch", json={"message_index": 0})
        assert resp.status_code == 403

    def test_list_branches(self, client, auth_headers):
        resp = client.get("/api/chat/branches", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "branches" in data
        assert data["count"] == 1

    def test_switch_branch(self, client, auth_headers):
        resp = client.post("/api/chat/branch/b1/switch", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["active_branch"] == "b1"

    def test_switch_missing_branch_404(self, client, auth_headers):
        # Patch the brain to return False for this specific call
        with patch("api.main._get_brain", new_callable=AsyncMock) as mock_get:
            brain = MagicMock()
            brain.switch_branch = AsyncMock(return_value=False)
            mock_get.return_value = brain
            resp = client.post("/api/chat/branch/missing/switch", headers=auth_headers)
            assert resp.status_code == 404

    def test_merge_insight(self, client, auth_headers):
        resp = client.post("/api/chat/branch/b1/merge-insight", headers=auth_headers)
        assert resp.status_code == 200
        assert "insight" in resp.json()

    def test_delete_branch(self, client, auth_headers):
        resp = client.delete("/api/chat/branch/b1", headers=auth_headers)
        assert resp.status_code == 200


# ===========================================================================
# 15. /api/learning — learning.py
# ===========================================================================

class TestLearningRoutes:

    def test_list_corrections(self, client, auth_headers):
        with patch("api.routes.learning._get_learning_system") as mock_get:
            mock_ls = MagicMock()
            mock_ls.get_all_corrections.return_value = [{"id": "c1"}]
            mock_get.return_value = mock_ls
            resp = client.get("/api/learning/corrections", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1

    def test_list_corrections_requires_auth(self, client):
        resp = client.get("/api/learning/corrections")
        assert resp.status_code == 403

    def test_record_correction(self, client, auth_headers):
        with patch("api.routes.learning._get_learning_system") as mock_get:
            mock_ls = MagicMock()
            mock_ls.record_correction = AsyncMock(return_value={"id": "c1", "original": "x"})
            mock_get.return_value = mock_ls
            resp = client.post(
                "/api/learning/corrections",
                json={"original": "x", "correction": "y"},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            assert resp.json()["correction"]["id"] == "c1"

    def test_delete_correction(self, client, auth_headers):
        with patch("api.routes.learning._get_learning_system") as mock_get:
            mock_ls = MagicMock()
            mock_ls._corrections = [{"id": "c1"}, {"id": "c2"}]
            mock_get.return_value = mock_ls
            resp = client.delete("/api/learning/corrections/c1", headers=auth_headers)
            assert resp.status_code == 200

    def test_delete_correction_not_found(self, client, auth_headers):
        with patch("api.routes.learning._get_learning_system") as mock_get:
            mock_ls = MagicMock()
            mock_ls._corrections = [{"id": "c1"}]
            mock_get.return_value = mock_ls
            resp = client.delete("/api/learning/corrections/missing", headers=auth_headers)
            assert resp.status_code == 404


# ===========================================================================
# 16. /api/privacy — privacy.py
# ===========================================================================

class TestPrivacyRoutes:

    def test_get_privacy_report(self, client, auth_headers):
        with patch("api.routes.privacy._get_engine") as mock_get:
            mock_engine = MagicMock()
            mock_engine.generate_privacy_report = AsyncMock(return_value={
                "providers": ["glm"], "request_count": 5,
            })
            mock_get.return_value = mock_engine
            resp = client.get("/api/privacy/report", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["request_count"] == 5

    def test_get_privacy_report_requires_auth(self, client):
        resp = client.get("/api/privacy/report")
        assert resp.status_code == 403

    def test_get_provider_data(self, client, auth_headers):
        with patch("api.routes.privacy._get_engine") as mock_get:
            mock_engine = MagicMock()
            mock_engine.get_data_sent_to_provider = AsyncMock(return_value=[
                {"prompt": "hi", "timestamp": "t"},
            ])
            mock_get.return_value = mock_engine
            resp = client.get("/api/privacy/data/glm", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["provider"] == "glm"

    def test_purge_provider_queues_ledger_action(self, client, auth_headers):
        # get_ledger is imported lazily inside the route handler.
        with patch("core.ledger.get_ledger") as mock_get_ledger:
            mock_ledger = MagicMock()
            mock_ledger.queue_action.return_value = "action_xyz"
            mock_get_ledger.return_value = mock_ledger
            resp = client.delete("/api/privacy/purge/glm", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "pending_approval"
            assert data["action_id"] == "action_xyz"
            # Verify it was queued with high risk
            mock_ledger.queue_action.assert_called_once()
            assert mock_ledger.queue_action.call_args.kwargs["risk_level"] == "high"


# ===========================================================================
# 17. /api/proactive — proactive.py
# ===========================================================================

class TestProactiveRoutes:

    def test_proactive_status(self, client, auth_headers):
        resp = client.get("/api/proactive/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "current_mode" in data
        assert data["proactive_available"] is True

    def test_proactive_status_requires_auth(self, client):
        resp = client.get("/api/proactive/status")
        assert resp.status_code == 403

    def test_trigger_briefing_success(self, client, auth_headers):
        # ProactiveEngine and UniversalConnector are imported lazily
        # inside the route handler.
        with patch("core.proactive.ProactiveEngine") as mock_pe_cls, \
             patch("core.universal_connector.UniversalConnector") as mock_uc_cls:
            mock_engine = MagicMock()
            mock_engine.daily_briefing = AsyncMock(return_value={"summary": "today"})
            mock_pe_cls.return_value = mock_engine
            mock_uc_cls.return_value = MagicMock()
            resp = client.post("/api/proactive/briefing", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["status"] == "success"

    def test_trigger_briefing_handles_error(self, client, auth_headers):
        with patch("core.proactive.ProactiveEngine", side_effect=RuntimeError("no brain")):
            resp = client.post("/api/proactive/briefing", headers=auth_headers)
            assert resp.status_code == 200  # Route catches the exception
            assert resp.json()["status"] == "error"


# ===========================================================================
# 18. /api/self-improvement — self_improvement.py
# ===========================================================================

class TestSelfImprovementRoutes:

    def test_get_proposals(self, client, auth_headers):
        with patch("api.routes.self_improvement._get_engine") as mock_get:
            mock_engine = MagicMock()
            mock_engine.get_proposals.return_value = [
                {"id": "p1", "title": "Cache responses", "type": "perf"},
            ]
            mock_get.return_value = mock_engine
            resp = client.get("/api/self-improvement/proposals", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1

    def test_get_proposals_requires_auth(self, client):
        resp = client.get("/api/self-improvement/proposals")
        assert resp.status_code == 403

    def test_analyze_performance(self, client, auth_headers):
        with patch("api.routes.self_improvement._get_engine") as mock_get:
            mock_engine = MagicMock()
            mock_engine.analyze_performance = AsyncMock(return_value={"score": 0.8})
            mock_engine.propose_improvements = AsyncMock(return_value=[{"id": "p1"}])
            mock_engine.get_proposals.return_value = [{"id": "p1"}]
            mock_get.return_value = mock_engine
            resp = client.post("/api/self-improvement/analyze", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["analysis"]["score"] == 0.8
            assert data["new_proposals"] == 1

    def test_approve_proposal_queues_ledger_action(self, client, auth_headers):
        with patch("api.routes.self_improvement._get_engine") as mock_get, \
             patch("core.ledger.get_ledger") as mock_get_ledger:
            mock_engine = MagicMock()
            mock_engine.get_proposals.return_value = [
                {"id": "p1", "title": "T", "type": "perf"},
            ]
            mock_get.return_value = mock_engine
            mock_ledger = MagicMock()
            mock_ledger.queue_action.return_value = "act_1"
            mock_get_ledger.return_value = mock_ledger
            resp = client.post("/api/self-improvement/approve/p1", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "pending_approval"
            assert data["action_id"] == "act_1"

    def test_approve_missing_proposal_404(self, client, auth_headers):
        with patch("api.routes.self_improvement._get_engine") as mock_get:
            mock_engine = MagicMock()
            mock_engine.get_proposals.return_value = []
            mock_get.return_value = mock_engine
            resp = client.post("/api/self-improvement/approve/missing", headers=auth_headers)
            assert resp.status_code == 404


# ===========================================================================
# 19. /api/subconscious — subconscious.py
# ===========================================================================

class TestSubconsciousRoutes:

    def test_get_patterns(self, client, auth_headers):
        with patch("api.routes.subconscious._get_subconscious") as mock_get:
            mock_sub = MagicMock()
            mock_sub.surface_patterns.return_value = [
                {"pattern": "user works at night", "confidence": 0.7},
            ]
            mock_get.return_value = mock_sub
            resp = client.get("/api/subconscious/patterns", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1

    def test_get_patterns_requires_auth(self, client):
        resp = client.get("/api/subconscious/patterns")
        assert resp.status_code == 403

    def test_get_patterns_when_unavailable(self, client, auth_headers):
        with patch("api.routes.subconscious._get_subconscious", return_value=None):
            resp = client.get("/api/subconscious/patterns", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["patterns"] == []
            assert "unavailable" in data["message"]

    def test_get_intuition(self, client, auth_headers):
        with patch("api.routes.subconscious._get_subconscious") as mock_get:
            mock_sub = MagicMock()
            mock_sub.get_intuition.return_value = "trust the user's judgment"
            mock_get.return_value = mock_sub
            resp = client.get("/api/subconscious/intuition", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["intuition"] == "trust the user's judgment"

    def test_get_intuition_when_unavailable(self, client, auth_headers):
        with patch("api.routes.subconscious._get_subconscious", return_value=None):
            resp = client.get("/api/subconscious/intuition", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["intuition"] == ""


# ===========================================================================
# 20. /api/team — team.py (uses FRIDAY_USER_TOKEN, not API_TOKEN)
# ===========================================================================

class TestTeamRoutes:

    def test_list_members_requires_user_token(self, client):
        # No Authorization header → 403 (not 401, because team uses its own auth)
        resp = client.get("/api/team/members")
        assert resp.status_code == 403

    def test_list_members_with_valid_user_token(self, client):
        with patch("api.routes.team._get_team_mode") as mock_get:
            mock_tm = MagicMock()
            mock_tm.get_user_by_token.return_value = {
                "user_id": "u1", "name": "Alice", "token": "tok",
            }
            mock_tm.list_members.return_value = [{"user_id": "u1", "name": "Alice"}]
            mock_get.return_value = mock_tm
            resp = client.get(
                "/api/team/members",
                headers={"Authorization": "Bearer tok"},
            )
            assert resp.status_code == 200
            assert resp.json()["members"][0]["name"] == "Alice"

    def test_invite_member(self, client):
        with patch("api.routes.team._get_team_mode") as mock_get:
            mock_tm = MagicMock()
            mock_tm.get_user_by_token.return_value = {
                "user_id": "u1", "name": "Alice", "token": "tok",
            }
            mock_tm.invite.return_value = {"email": "bob@x.com", "token": "inv123"}
            mock_get.return_value = mock_tm
            resp = client.post(
                "/api/team/invite",
                json={"email": "bob@x.com"},
                headers={"Authorization": "Bearer tok"},
            )
            assert resp.status_code == 200
            assert resp.json()["invite"]["email"] == "bob@x.com"

    def test_invite_requires_valid_user_token(self, client):
        with patch("api.routes.team._get_team_mode") as mock_get:
            mock_tm = MagicMock()
            mock_tm.get_user_by_token.return_value = None
            mock_get.return_value = mock_tm
            resp = client.post(
                "/api/team/invite",
                json={"email": "x@y.com"},
                headers={"Authorization": "Bearer invalid"},
            )
            assert resp.status_code == 403

    def test_get_context(self, client):
        with patch("api.routes.team._get_team_mode") as mock_get:
            mock_tm = MagicMock()
            mock_tm.get_user_by_token.return_value = {
                "user_id": "u1", "name": "A", "token": "t",
            }
            mock_tm.get_context_for_user = AsyncMock(return_value={
                "shared": [], "private": [],
            })
            mock_get.return_value = mock_tm
            resp = client.get(
                "/api/team/context",
                headers={"Authorization": "Bearer t"},
            )
            assert resp.status_code == 200

    def test_store_private_memory(self, client):
        with patch("api.routes.team._get_team_mode") as mock_get:
            mock_tm = MagicMock()
            mock_tm.get_user_by_token.return_value = {
                "user_id": "u1", "name": "A", "token": "t",
            }
            mock_tm.store_private_memory = AsyncMock(return_value={"id": "m1", "content": "secret"})
            mock_get.return_value = mock_tm
            resp = client.post(
                "/api/team/memory/private",
                json={"content": "my secret", "metadata": {}},
                headers={"Authorization": "Bearer t"},
            )
            assert resp.status_code == 200
            # Mock returns the canned "secret" value (not the echoed input).
            assert resp.json()["memory"]["content"] == "secret"

    def test_store_shared_memory(self, client):
        with patch("api.routes.team._get_team_mode") as mock_get:
            mock_tm = MagicMock()
            mock_tm.get_user_by_token.return_value = {
                "user_id": "u1", "name": "A", "token": "t",
            }
            mock_tm.store_shared_memory = AsyncMock(return_value={"id": "m2", "content": "shared"})
            mock_get.return_value = mock_tm
            resp = client.post(
                "/api/team/memory/shared",
                json={"content": "team note"},
                headers={"Authorization": "Bearer t"},
            )
            assert resp.status_code == 200
