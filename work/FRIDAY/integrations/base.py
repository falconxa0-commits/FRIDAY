from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import datetime


class BaseIntegration(ABC):
    """Abstract base class for all FRIDAY integrations.

    Every integration must:
    - Expose a unique ``name`` property.
    - Report whether it is ``available`` (configured + reachable).
    - Implement ``execute`` for dispatched actions.
    - Optionally override ``health_check`` and ``list_actions``.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable integration name (e.g. 'Weather', 'Gmail')."""
        pass

    @abstractmethod
    def available(self) -> bool:
        """Returns True if the integration is correctly configured and reachable."""
        pass

    @abstractmethod
    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        """Execute the specified action with params.

        Returns a structured response::

            {
                "status": "success" | "error" | "not_implemented" | "demo_mode",
                "message": "...",
                "receipt": {"type": "...", "data": "...", "timestamp": "..."}
            }
        """
        pass

    async def health_check(self) -> dict:
        """Lightweight check that the integration backend is reachable.

        Returns a dict with at least ``status`` and ``message`` keys.
        Default implementation calls ``available()``.
        """
        if self.available():
            return {
                "status": "healthy",
                "message": f"{self.name} integration is available.",
                "timestamp": datetime.datetime.now().isoformat(),
            }
        return {
            "status": "unhealthy",
            "message": f"{self.name} integration is not available (missing credentials or unreachable).",
            "timestamp": datetime.datetime.now().isoformat(),
        }

    def list_actions(self) -> List[str]:
        """Return the list of action names this integration supports.

        Override in subclasses to provide a concrete list.
        """
        return []

    def _make_response(
        self,
        status: str,
        message: str,
        receipt_data: Optional[dict] = None,
    ) -> dict:
        """Helper to build a standardised response dict."""
        response: Dict[str, object] = {
            "status": status,
            "message": message,
        }
        if receipt_data is not None:
            response["receipt"] = {
                "type": "api_response",
                "data": receipt_data,
                "timestamp": datetime.datetime.now().isoformat(),
            }
        return response
