"""Tests for the learning system — correction detected, stored, surfaced in RAG."""
import pytest
import asyncio


class TestLearningSystem:
    """Test the Friday learning system."""

    @pytest.mark.asyncio
    async def test_record_correction(self):
        from core.learning import FridayLearningSystem
        FridayLearningSystem._shared_corrections.clear()
        ls = FridayLearningSystem()
        entry = await ls.record_correction("Paris is in Germany", "Paris is in France")
        assert entry["original"] == "Paris is in Germany"
        assert entry["correction"] == "Paris is in France"
        assert "id" in entry

    @pytest.mark.asyncio
    async def test_check_similar_corrections(self):
        from core.learning import FridayLearningSystem
        FridayLearningSystem._shared_corrections.clear()
        ls = FridayLearningSystem()
        await ls.record_correction("Python is compiled", "Python is interpreted")
        results = await ls.check_similar_corrections("Python programming")
        assert len(results) > 0
        assert "Python" in results[0]["original"] or "Python" in results[0]["correction"]

    @pytest.mark.asyncio
    async def test_confidence_decreases_with_corrections(self):
        from core.learning import FridayLearningSystem
        FridayLearningSystem._shared_corrections.clear()
        ls = FridayLearningSystem()
        # No corrections → high confidence
        conf_before = await ls.get_confidence("Python")
        assert conf_before == 1.0
        # Record correction
        await ls.record_correction("Python is compiled", "Python is interpreted")
        # Confidence should decrease
        conf_after = await ls.get_confidence("Python")
        assert conf_after < 1.0

    @pytest.mark.asyncio
    async def test_api_endpoint_lists_corrections(self):
        import os
        from config.settings import FRIDAY_API_TOKEN
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        H = {"Authorization": "Bearer test"}
        # Record a correction
        client.post("/api/learning/corrections", json={
            "original": "test wrong", "correction": "test right"
        }, headers=H)
        # List corrections
        r = client.get("/api/learning/corrections", headers=H)
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1
