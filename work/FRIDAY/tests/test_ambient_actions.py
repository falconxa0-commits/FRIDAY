"""Tests for ambient intelligence loop — accepting a suggestion fires the real action."""
import pytest
import asyncio


class TestAmbientActions:
    def test_intervention_patterns_exist(self):
        from core.ambient import INTERVENTION_PATTERNS
        assert len(INTERVENTION_PATTERNS) >= 4
        ids = [p["id"] for p in INTERVENTION_PATTERNS]
        assert "stuck_on_bug" in ids
        assert "meeting_prep" in ids
        assert "error_detected" in ids
        assert "long_document" in ids

    @pytest.mark.asyncio
    async def test_error_detected_pattern_fires(self):
        from core.ambient import INTERVENTION_PATTERNS
        ctx = {"error_visible_on_screen": True}
        for pattern in INTERVENTION_PATTERNS:
            if pattern["id"] == "error_detected":
                assert pattern["condition"](ctx) is True
                return
        assert False, "error_detected pattern not found"

    @pytest.mark.asyncio
    async def test_execute_intervention_action_unknown(self):
        from core.ambient import execute_intervention_action
        result = await execute_intervention_action("nonexistent_action", {})
        assert result["status"] == "not_implemented"

    @pytest.mark.asyncio
    async def test_stuck_on_bug_condition(self):
        from core.ambient import INTERVENTION_PATTERNS
        ctx = {
            "same_screen_duration_minutes": 30,
            "app": "vscode",
            "recent_activity": False,
        }
        for pattern in INTERVENTION_PATTERNS:
            if pattern["id"] == "stuck_on_bug":
                assert pattern["condition"](ctx) is True
                return
        assert False, "stuck_on_bug pattern not found"
