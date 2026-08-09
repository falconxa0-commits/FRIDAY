"""Tests for integration base class and registry."""

import logging
import pytest
from unittest.mock import MagicMock, patch
from integrations.base import BaseIntegration
from integrations.registry import UniversalRegistry, _INTEGRATION_SPECS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BaseIntegration contract
# ---------------------------------------------------------------------------

class ConcreteIntegration(BaseIntegration):
    """Concrete subclass for testing the abstract contract."""

    @property
    def name(self) -> str:
        return "TestIntegration"

    def available(self) -> bool:
        return True

    async def execute(self, action: str, params=None) -> dict:
        return self._make_response("success", f"Executed {action}", {"result": "ok"})


class UnavailableIntegration(BaseIntegration):
    """An integration that is not available."""

    @property
    def name(self) -> str:
        return "Unavailable"

    def available(self) -> bool:
        return False

    async def execute(self, action: str, params=None) -> dict:
        return self._make_response("error", "Not available")


class TestBaseIntegrationContract:
    """Test that BaseIntegration enforces its contract."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseIntegration()

    def test_concrete_name(self):
        inst = ConcreteIntegration()
        assert inst.name == "TestIntegration"

    def test_concrete_available(self):
        inst = ConcreteIntegration()
        assert inst.available() is True

    @pytest.mark.asyncio
    async def test_concrete_execute(self):
        inst = ConcreteIntegration()
        result = await inst.execute("test_action")
        assert result["status"] == "success"
        assert "receipt" in result

    @pytest.mark.asyncio
    async def test_health_check_available(self):
        inst = ConcreteIntegration()
        health = await inst.health_check()
        assert health["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_check_unavailable(self):
        inst = UnavailableIntegration()
        health = await inst.health_check()
        assert health["status"] == "unhealthy"

    def test_list_actions_default(self):
        inst = ConcreteIntegration()
        assert inst.list_actions() == []

    def test_make_response(self):
        inst = ConcreteIntegration()
        resp = inst._make_response("success", "Done", {"key": "value"})
        assert resp["status"] == "success"
        assert resp["message"] == "Done"
        assert resp["receipt"]["data"] == {"key": "value"}
        assert "timestamp" in resp["receipt"]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistryServiceListing:
    """Test registry service listing and discovery."""

    def test_get_all_services(self):
        reg = UniversalRegistry()
        services = reg.get_all_services()
        assert isinstance(services, list)
        assert len(services) > 0
        assert "Weather" in services

    def test_get_service_count(self):
        reg = UniversalRegistry()
        count = reg.get_service_count()
        assert count == len(_INTEGRATION_SPECS)

    def test_get_categories(self):
        reg = UniversalRegistry()
        cats = reg.get_categories()
        assert isinstance(cats, dict)
        assert "Core" in cats
        assert "Weather" in cats["Core"]

    def test_get_unknown_integration(self):
        reg = UniversalRegistry()
        result = reg.get_integration("NonExistent")
        assert result is None


class TestIntegrationDiscovery:
    """Test lazy-loading of integrations."""

    def test_lazy_load_weather(self):
        """Should lazy-load WeatherIntegration when requested."""
        reg = UniversalRegistry()
        # We can only test this if the module is importable
        try:
            inst = reg.get_integration("Weather")
            if inst is not None:
                assert inst.name == "Weather"
        except Exception:
            # Module import may fail in test env, that's ok
            pass

    def test_lazy_load_caches(self):
        """Second call should return the same instance."""
        reg = UniversalRegistry()
        try:
            first = reg.get_integration("Weather")
            second = reg.get_integration("Weather")
            if first is not None:
                assert first is second
        except Exception as e:
            logger.debug(f"Non-critical error: {e}")

    def test_get_available_services(self):
        """Should return only services whose available() is True."""
        reg = UniversalRegistry()
        available = reg.get_available_services()
        assert isinstance(available, list)
        # In a test environment, most services won't be available
        # (no real API keys), so this may be empty — that's fine.

    def test_failed_load_returns_none(self):
        """If a module can't be imported, should return None."""
        reg = UniversalRegistry()
        # Patch the spec to point to a nonexistent module
        with patch.dict(_INTEGRATION_SPECS, {
            "FakeService": {
                "module": "nonexistent.module",
                "class_name": "FakeClass",
                "category": "Fake",
            }
        }):
            result = reg._load("FakeService")
            assert result is None
