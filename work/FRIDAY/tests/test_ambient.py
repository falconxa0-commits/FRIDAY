"""Tests for the AmbientEngine — screen awareness patterns."""
import asyncio
import pytest

from core.ambient import AmbientEngine, NOTICE_PATTERNS


@pytest.fixture()
def engine():
    return AmbientEngine(
        screen_analyzer=lambda: {},
        surface_callback=lambda s: None,
    )


class TestAmbientPatterns:
    """Test pattern evaluation."""

    def test_error_on_screen_fires(self, engine):
        ctx = {"error_visible_on_screen": True}
        suggestions = engine.evaluate_patterns(ctx)
        assert any(s["pattern_id"] == "error_on_screen" for s in suggestions)

    def test_stuck_on_screen_fires(self, engine):
        ctx = {
            "screen_summary": "VS Code",
            "same_screen_duration_minutes": 35,
            "user_active": False,
        }
        suggestions = engine.evaluate_patterns(ctx)
        assert any(s["pattern_id"] == "stuck_on_screen" for s in suggestions)

    def test_meeting_soon_fires(self, engine):
        ctx = {
            "minutes_until_next_meeting": 8,
            "current_app": "vscode",
        }
        suggestions = engine.evaluate_patterns(ctx)
        assert any(s["pattern_id"] == "meeting_soon" for s in suggestions)

    def test_clean_screen_no_suggestions(self, engine):
        ctx = {
            "screen_summary": "Calendar",
            "same_screen_duration_minutes": 5,
            "user_active": True,
            "error_visible_on_screen": False,
            "minutes_until_next_meeting": 999,
            "current_app": "calendar",
        }
        suggestions = engine.evaluate_patterns(ctx)
        assert suggestions == []

    def test_cooldown_prevents_duplicate_firing(self, engine):
        ctx = {"error_visible_on_screen": True}
        # First fire
        s1 = engine.evaluate_patterns(ctx)
        assert len(s1) >= 1
        # Second fire within cooldown → no suggestions
        s2 = engine.evaluate_patterns(ctx)
        assert s2 == []

    def test_output_depends_on_input(self, engine):
        ctx_with_error = {"error_visible_on_screen": True}
        ctx_clean = {
            "screen_summary": "Desktop",
            "same_screen_duration_minutes": 5,
            "user_active": True,
            "error_visible_on_screen": False,
            "minutes_until_next_meeting": 999,
            "current_app": "browser",
        }
        # Use fresh engines to avoid cooldown
        engine1 = AmbientEngine(screen_analyzer=lambda: ctx_with_error, surface_callback=lambda s: None)
        engine2 = AmbientEngine(screen_analyzer=lambda: ctx_clean, surface_callback=lambda s: None)
        s1 = engine1.evaluate_patterns(ctx_with_error)
        s2 = engine2.evaluate_patterns(ctx_clean)
        assert len(s1) >= 1
        assert s2 == []


class TestAmbientLoop:
    """Test the run_loop (with max_iterations to avoid hanging)."""

    @pytest.mark.asyncio
    async def test_run_loop_cli_stops_after_iterations(self, engine):
        engine.screen_analyzer = lambda: {"error_visible_on_screen": False}
        # Should complete without hanging
        await engine.run_loop_cli(max_iterations=1)
        assert engine.active is False
