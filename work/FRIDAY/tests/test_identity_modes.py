"""Tests for identity modes — all 5 modes exist, switching changes config."""
import pytest


class TestIdentityModes:
    """Test the 5 configurable identity modes."""

    def test_all_5_modes_exist(self):
        from config.identities import FRIDAY_IDENTITIES
        assert "General" in FRIDAY_IDENTITIES
        assert "Strategist" in FRIDAY_IDENTITIES
        assert "Creative" in FRIDAY_IDENTITIES
        assert "Debugger" in FRIDAY_IDENTITIES
        assert "Guardian" in FRIDAY_IDENTITIES
        assert len(FRIDAY_IDENTITIES) == 5

    def test_each_mode_has_required_fields(self):
        from config.identities import FRIDAY_IDENTITIES
        for name, cfg in FRIDAY_IDENTITIES.items():
            assert "voice" in cfg, f"{name} missing 'voice'"
            assert "style" in cfg, f"{name} missing 'style'"
            assert "focus" in cfg, f"{name} missing 'focus'"
            assert "warmth" in cfg, f"{name} missing 'warmth'"
            assert "wit" in cfg, f"{name} missing 'wit'"
            assert "formality" in cfg, f"{name} missing 'formality'"
            assert "assertiveness" in cfg, f"{name} missing 'assertiveness'"

    def test_modes_have_different_configs(self):
        from config.identities import FRIDAY_IDENTITIES
        general = FRIDAY_IDENTITIES["General"]
        debugger = FRIDAY_IDENTITIES["Debugger"]
        assert general["warmth"] != debugger["warmth"], \
            "General and Debugger should have different warmth"
        assert general["formality"] != debugger["formality"], \
            "General and Debugger should have different formality"

    def test_api_endpoint_returns_modes(self):
        import os
        from config.settings import FRIDAY_API_TOKEN
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        H = {"Authorization": "Bearer test"}
        r = client.get("/api/identity", headers=H)
        assert r.status_code == 200
        data = r.json()
        assert "available_modes" in data
        assert "General" in data["available_modes"]

    def test_api_endpoint_switches_mode(self):
        import os
        from config.settings import FRIDAY_API_TOKEN
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        H = {"Authorization": "Bearer test"}
        r = client.post("/api/identity/Debugger", headers=H)
        assert r.status_code == 200
        data = r.json()
        assert data["new_mode"] == "Debugger"
        assert data["old_mode"] != data["new_mode"]
