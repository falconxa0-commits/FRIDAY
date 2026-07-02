"""Universal Connector — dispatches actions to integrations via the ledger.

Key fixes:
- Risk level is properly classified (not always "low" for unknowns).
- ``close_all()`` gracefully shuts down integrations.
- Uses the shared registry instance instead of creating a separate one.
"""

import asyncio
import logging
import inspect
from typing import Dict, Optional

from integrations.registry import UniversalRegistry
from integrations.base import BaseIntegration
import pkgutil
import importlib
import os
from core.ledger import get_ledger

logger = logging.getLogger("UniversalConnector")


class UniversalConnector:
    """Dispatches actions to integrations with risk gating via the ledger."""

    def __init__(self, registry: Optional[UniversalRegistry] = None):
        self.registry = registry or UniversalRegistry()
        self.integrations: Dict[str, BaseIntegration] = {}
        self.ledger = get_ledger()
        self._discover_plugins()

    # ------------------------------------------------------------------
    # Plugin discovery
    # ------------------------------------------------------------------

    def _discover_plugins(self):
        """Auto-discover and load all BaseIntegration plugins."""
        import integrations
        path = os.path.dirname(integrations.__file__)
        for loader, module_name, is_pkg in pkgutil.iter_modules([path]):
            if module_name in ("base", "registry", "__init__"):
                continue
            try:
                module = importlib.import_module(f"integrations.{module_name}")
                for name, obj in inspect.getmembers(module):
                    if (
                        inspect.isclass(obj)
                        and issubclass(obj, BaseIntegration)
                        and obj is not BaseIntegration
                    ):
                        instance = obj()
                        self.integrations[instance.name] = instance
            except Exception as exc:
                logger.warning(f"Failed to load integration {module_name}: {exc}")

        logger.info(f"Discovered {len(self.integrations)} integration plugins.")

    # ------------------------------------------------------------------
    # Risk classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_risk(action: str) -> str:
        """Classify an action's risk level based on its name.

        Returns one of: "low", "medium", "high", "critical".
        Unknown actions default to "medium" (not "low") for safety.
        """
        action_lower = action.lower()

        critical_keywords = {"delete", "remove", "destroy", "wipe", "purge", "execute", "sudo"}
        high_keywords = {"send", "post", "publish", "share", "broadcast", "ssh", "remote", "eval"}
        medium_keywords = {"update", "write", "modify", "create", "install", "config"}

        for kw in critical_keywords:
            if kw in action_lower:
                return "critical"
        for kw in high_keywords:
            if kw in action_lower:
                return "high"
        for kw in medium_keywords:
            if kw in action_lower:
                return "medium"

        # Default to "medium" for unknown actions (safe-by-default)
        return "medium"

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    async def execute_action(
        self,
        service_name: str,
        action: str,
        params: Optional[dict] = None,
        approval_timeout: int = 300,
    ) -> dict:
        """Dispatch an action to the appropriate integration.

        The action goes through the ledger's approval gate before
        being sent to the integration.

        Args:
            service_name: Integration name (e.g. "Weather", "Printer")
            action: Action name to execute
            params: Optional action parameters
            approval_timeout: Seconds to wait for manual approval.
                Defaults to 300 (5 min). MCP callers may pass a shorter
                timeout (e.g. 30) so the client gets a "pending approval"
                response within a reasonable window.
        """
        logger.info(f"Dispatching '{action}' to {service_name}...")

        # Classify risk properly
        risk_level = self._classify_risk(action)

        action_id = self.ledger.queue_action(
            service_name, action, params, risk_level=risk_level
        )
        if not await self.ledger.wait_for_approval(action_id, timeout=approval_timeout):
            return {
                "status": "error",
                "message": (
                    f"Action '{action}' on {service_name} requires explicit approval. "
                    f"Action queued (id={action_id}) but not approved within "
                    f"{approval_timeout}s. Approve via POST /api/actions/{action_id}/approve "
                    f"and re-issue the request."
                ),
            }

        if service_name in self.integrations:
            integration = self.integrations[service_name]
            try:
                return await integration.execute(action, params)
            except Exception as exc:
                logger.error(f"Integration execution failed: {exc}")
                return {
                    "status": "error",
                    "message": f"Integration error: {exc}",
                }

        return {
            "status": "not_implemented",
            "message": (
                f"Integration for {service_name} not found or "
                "not implemented as a plugin."
            ),
        }

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    async def close_all(self):
        """Gracefully close all integration connections.

        Calls ``close()`` on integrations that expose it, then
        clears the integration cache.
        """
        for name, integration in self.integrations.items():
            try:
                if hasattr(integration, "close") and callable(integration.close):
                    result = integration.close()
                    # Handle both sync and async close methods
                    if asyncio.iscoroutine(result):
                        await result
                    logger.info(f"Closed integration: {name}")
            except Exception as exc:
                logger.warning(f"Error closing integration {name}: {exc}")

        self.integrations.clear()
        logger.info("All integrations closed.")
