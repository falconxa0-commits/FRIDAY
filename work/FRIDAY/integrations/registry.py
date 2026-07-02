"""Universal registry of all FRIDAY integrations.

Only lists services that have **real implementations** in the
``integrations`` package.  Fantasy / placeholder services have been
removed — add them back only when an integration module exists.
"""

import importlib
import logging
from typing import Dict, List, Optional, Type

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Mapping: canonical name → dotted import path + class name.
# Every entry here **must** point to a concrete BaseIntegration subclass.
# ------------------------------------------------------------------
_INTEGRATION_SPECS: Dict[str, dict] = {
    "Weather": {
        "module": "integrations.weather",
        "class_name": "WeatherIntegration",
        "category": "Core",
    },
    "Calendar": {
        "module": "integrations.calendar_integration",
        "class_name": "CalendarIntegration",
        "category": "Core",
    },
    "Gmail": {
        "module": "integrations.gmail_integration",
        "class_name": "GmailIntegration",
        "category": "Core",
    },
    "Spotify": {
        "module": "integrations.spotify_integration",
        "class_name": "SpotifyIntegration",
        "category": "Core",
    },
    "HomeAssistant": {
        "module": "integrations.smart_home",
        "class_name": "SmartHomeIntegration",
        "category": "Core",
    },
    "CryptoTracker": {
        "module": "integrations.crypto_tracker",
        "class_name": "CryptoTrackerIntegration",
        "category": "Finance",
    },
    "Finance": {
        "module": "integrations.finance",
        "class_name": "FinanceIntegration",
        "category": "Finance",
    },
    "GlobalPulse": {
        "module": "integrations.global_pulse",
        "class_name": "GlobalPulseIntegration",
        "category": "Information",
    },
}


class UniversalRegistry:
    """Discover, lazy-load, and query FRIDAY integrations."""

    def __init__(self) -> None:
        self._instances: Dict[str, BaseIntegration] = {}

    # --- lazy loading ------------------------------------------------

    def _load(self, name: str) -> Optional[BaseIntegration]:
        """Import and instantiate an integration by canonical name.

        Returns ``None`` (and logs a warning) if the module/class cannot
        be found or if instantiation fails.
        """
        if name in self._instances:
            return self._instances[name]

        spec = _INTEGRATION_SPECS.get(name)
        if spec is None:
            logger.warning("Registry: unknown integration '%s'", name)
            return None

        try:
            mod = importlib.import_module(spec["module"])
            cls: Type[BaseIntegration] = getattr(mod, spec["class_name"])
            instance = cls()
            self._instances[name] = instance
            return instance
        except Exception:
            logger.exception("Registry: failed to load integration '%s'", name)
            return None

    # --- public API --------------------------------------------------

    def get_all_services(self) -> List[str]:
        """Return all registered service names."""
        return list(_INTEGRATION_SPECS.keys())

    def get_service_count(self) -> int:
        return len(_INTEGRATION_SPECS)

    def get_categories(self) -> Dict[str, List[str]]:
        """Return ``{category: [service_name, …]}`` mapping."""
        cats: Dict[str, List[str]] = {}
        for name, spec in _INTEGRATION_SPECS.items():
            cats.setdefault(spec["category"], []).append(name)
        return cats

    def get_integration(self, name: str) -> Optional[BaseIntegration]:
        """Return a loaded integration instance (lazy-loaded on first call)."""
        return self._load(name)

    def get_available_services(self) -> List[str]:
        """Return names of integrations whose ``available()`` returns True."""
        available: List[str] = []
        for name in _INTEGRATION_SPECS:
            inst = self._load(name)
            if inst is not None and inst.available():
                available.append(name)
        return available
