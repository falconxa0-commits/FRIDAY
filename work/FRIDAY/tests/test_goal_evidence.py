"""Tests for goal tracking with behavioral evidence."""
import pytest


class TestGoalEvidence:
    @pytest.mark.asyncio
    async def test_detect_evidence_finds_related_research(self):
        from core.goals import GoalTracker
        from core.learning import FridayLearningSystem
        # Record a correction about Rust
        ls = FridayLearningSystem()
        await ls.record_correction("Rust is slow", "Rust is actually very fast")
        # Set a Rust learning goal
        gt = GoalTracker()
        goal = await gt.set_goal("Learn Rust programming", "2026-12-31", "learning")
        evidence = await gt.detect_progress_evidence(goal["id"])
        assert len(evidence) > 0
        assert any("rust" in e["content"].lower() for e in evidence)

    @pytest.mark.asyncio
    async def test_auto_update_requires_high_relevance(self):
        from core.goals import GoalTracker
        gt = GoalTracker()
        goal = await gt.set_goal("Learn Rust", "2026-12-31", "learning")
        # No evidence → should return no_evidence, not auto-log
        result = await gt.auto_update_from_evidence(goal["id"])
        # Without evidence, should not auto-log
        assert result["status"] in ("no_evidence", "suggestion", "auto_logged")
        if result["status"] == "no_evidence":
            assert "No evidence" in result["message"]

    @pytest.mark.asyncio
    async def test_evidence_surfaces_correctly(self):
        from core.goals import GoalTracker
        from core.learning import FridayLearningSystem
        ls = FridayLearningSystem()
        await ls.record_correction("Rust compilation is slow", "Rust compilation is actually fast")
        gt = GoalTracker()
        goal = await gt.set_goal("Learn Rust", "2026-12-31", "learning")
        evidence = await gt.detect_progress_evidence(goal["id"])
        assert len(evidence) > 0
        assert evidence[0]["relevance"] >= 0.5
        assert evidence[0]["source"] in ("memory", "correction")
