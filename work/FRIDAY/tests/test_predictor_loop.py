"""Tests for predictor loop — preload runs, cache populated."""
import pytest


class TestPredictorLoop:
    def test_start_method_exists(self):
        from core.predictor import Predictor
        p = Predictor()
        assert hasattr(p, "start"), "Predictor must have start() method"
        assert hasattr(p, "get_cached"), "Predictor must have get_cached() method"
        assert hasattr(p, "is_preloaded"), "Predictor must have is_preloaded() method"

    def test_cache_set_and_get(self):
        from core.predictor import Predictor
        p = Predictor()
        p.set_cached("test_key", {"data": "test_value"})
        cached = p.get_cached("test_key", max_age_seconds=60)
        assert cached is not None
        assert cached["data"] == "test_value"

    def test_cache_expires(self):
        from core.predictor import Predictor
        p = Predictor()
        p.set_cached("test_key", {"data": "test_value"})
        cached = p.get_cached("test_key", max_age_seconds=0)
        assert cached is None

    @pytest.mark.asyncio
    async def test_is_preloaded_returns_bool(self):
        from core.predictor import Predictor
        p = Predictor()
        p.set_cached("test_key", {"data": "test_value"})
        result = await p.is_preloaded("test_key")
        assert result is True
        result2 = await p.is_preloaded("nonexistent_key")
        assert result2 is False
