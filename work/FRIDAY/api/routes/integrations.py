"""Integration API routes — list services, execute actions.

All integration calls produce receipts with real response data from
the underlying integration, never echoes of input parameters.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import datetime
import logging

logger = logging.getLogger("friday.api.integrations")

router = APIRouter()

# ---------------------------------------------------------------------------
# Singleton connector — reuse across requests instead of creating new ones
# ---------------------------------------------------------------------------

_connector = None


def _get_connector():
    global _connector
    if _connector is None:
        try:
            from core.universal_connector import UniversalConnector
            _connector = UniversalConnector()
        except Exception as e:
            logger.exception("Failed to initialise UniversalConnector")
            _connector = None
    return _connector


_registry = None


def _get_registry():
    global _registry
    if _registry is None:
        try:
            from integrations.registry import UniversalRegistry
            _registry = UniversalRegistry()
        except Exception as e:
            logger.exception("Failed to initialise UniversalRegistry")
            _registry = None
    return _registry


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class IntegrationActionRequest(BaseModel):
    service: str
    action: str
    params: dict = {}


# ---------------------------------------------------------------------------
# Receipt builder — ensures receipts always contain real response data
# ---------------------------------------------------------------------------

def _build_receipt(
    service: str,
    action: str,
    integration_result: dict,
) -> dict:
    """Build a receipt from the *real* integration result.

    The receipt captures:
      - What was requested (service + action)
      - What was actually returned by the integration (status, message, data)
      - A timestamp

    We intentionally do NOT echo the input params as the receipt data —
    instead we extract the ``receipt`` field from the integration result,
    which contains the real output from the external API or service.
    """
    # The integration already produces a receipt via BaseIntegration._make_response
    real_data = integration_result.get("receipt", {})

    # If the integration didn't provide receipt data, build one from
    # the actual result fields (not the input params).
    if not real_data or not real_data.get("data"):
        real_data = {
            "type": "api_response",
            "data": {
                "status": integration_result.get("status", "unknown"),
                "message": integration_result.get("message", ""),
            },
            "timestamp": datetime.datetime.now().isoformat(),
        }

    return {
        "service": service,
        "action": action,
        "integration_status": integration_result.get("status", "unknown"),
        "receipt": real_data,
        "timestamp": datetime.datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /api/integrations/  — list all registered services
# ---------------------------------------------------------------------------

@router.get("/")
async def list_integrations():
    registry = _get_registry()
    if not registry:
        raise HTTPException(status_code=503, detail="Integration registry unavailable.")
    return {"integrations": registry.get_all_services()}


# ---------------------------------------------------------------------------
# GET /api/integrations/available  — list services with available() == True
# ---------------------------------------------------------------------------

@router.get("/available")
async def list_available_services():
    registry = _get_registry()
    if not registry:
        raise HTTPException(status_code=503, detail="Integration registry unavailable.")
    try:
        available = registry.get_available_services()
        return {"available": available}
    except Exception as e:
        logger.exception("Failed to list available services")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /api/integrations/categories  — list services grouped by category
# ---------------------------------------------------------------------------

@router.get("/categories")
async def list_categories():
    registry = _get_registry()
    if not registry:
        raise HTTPException(status_code=503, detail="Integration registry unavailable.")
    return {"categories": registry.get_categories()}


# ---------------------------------------------------------------------------
# POST /api/integrations/execute  — execute an integration action
# ---------------------------------------------------------------------------

@router.post("/execute")
async def execute_integration(request: IntegrationActionRequest):
    connector = _get_connector()
    if not connector:
        raise HTTPException(status_code=503, detail="Universal connector unavailable.")

    try:
        # Use a short approval timeout so the dashboard gets a clear
        # "pending approval" response quickly. If the user wants to
        # approve, they do it via /api/actions/{id}/approve and then
        # re-issue this request (the action will already be approved
        # and execute_action returns immediately).
        result = await connector.execute_action(
            request.service, request.action, request.params,
            approval_timeout=5,
        )

        # Build a receipt from the real integration result
        receipt = _build_receipt(request.service, request.action, result)

        return {
            "status": result.get("status", "unknown"),
            "message": result.get("message", ""),
            "result": result,
            "receipt": receipt,
        }
    except Exception as e:
        logger.exception("Integration execution failed")
        raise HTTPException(status_code=500, detail=str(e))
