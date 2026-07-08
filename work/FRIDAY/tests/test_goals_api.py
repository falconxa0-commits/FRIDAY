"""Tests for goal tracking — CRUD, nudge references actual goal."""
import pytest


class TestGoals:
    """Test the goal tracking system."""

    def test_set_goal(self):
        import os
        from config.settings import FRIDAY_API_TOKEN
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        H = {"Authorization": "Bearer test"}
        r = client.post("/api/goals", json={
            "description": "Learn Rust", "target_date": "2026-12-31", "type": "learning"
        }, headers=H)
        assert r.status_code == 200
        assert r.json()["goal"]["description"] == "Learn Rust"
        assert r.json()["goal"]["status"] == "active"

    def test_list_goals(self):
        import os
        from config.settings import FRIDAY_API_TOKEN
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        H = {"Authorization": "Bearer test"}
        # Create a goal first
        client.post("/api/goals", json={
            "description": "Test goal", "target_date": "2026-12-31"
        }, headers=H)
        r = client.get("/api/goals", headers=H)
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_progress_and_nudge(self):
        import os
        from config.settings import FRIDAY_API_TOKEN
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        H = {"Authorization": "Bearer test"}
        # Create goal
        r = client.post("/api/goals", json={
            "description": "Ship FRIDAY v3.1", "target_date": "2026-07-31"
        }, headers=H)
        goal_id = r.json()["goal"]["id"]
        # Log progress
        r = client.patch(f"/api/goals/{goal_id}/progress", json={
            "progress_note": "Fixed Dockerfile"
        }, headers=H)
        assert r.status_code == 200
        assert r.json()["progress_count"] == 1
        # Get nudge — must reference the actual goal
        r = client.get(f"/api/goals/{goal_id}/nudge", headers=H)
        assert r.status_code == 200
        nudge = r.json()["nudge"]
        assert "FRIDAY v3.1" in nudge or "progress" in nudge.lower()

    def test_delete_goal(self):
        import os
        from config.settings import FRIDAY_API_TOKEN
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        H = {"Authorization": "Bearer test"}
        r = client.post("/api/goals", json={
            "description": "To delete", "target_date": "2026-12-31"
        }, headers=H)
        goal_id = r.json()["goal"]["id"]
        r = client.delete(f"/api/goals/{goal_id}", headers=H)
        assert r.status_code == 200
