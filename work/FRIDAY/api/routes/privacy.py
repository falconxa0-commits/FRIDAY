"""Privacy Audit API routes — what does Friday know, where did data go?

Endpoints:
    GET    /api/privacy/report           — full privacy report
    GET    /api/privacy/data/{provider}  — data sent to a specific provider
    DELETE /api/privacy/purge/{provider} — purge provider history (ledger-gated)
"""
import logging
from fastapi import APIRouter, HTTPException

logger = logging.getLogger("friday.api.privacy")

router = APIRouter()

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from core.privacy_audit import PrivacyAuditEngine
        _engine = PrivacyAuditEngine()
    return _engine


@router.get("/report")
async def get_privacy_report():
    """Generate a complete privacy report from real data."""
    engine = _get_engine()
    report = await engine.generate_privacy_report()
    return report


@router.get("/data/{provider}")
async def get_provider_data(provider: str):
    """Return all requests sent to a specific provider."""
    engine = _get_engine()
    data = await engine.get_data_sent_to_provider(provider)
    return {"provider": provider, "requests": data, "count": len(data)}


@router.delete("/purge/{provider}")
async def purge_provider(provider: str):
    """Purge all history for a specific provider — goes through ledger."""
    from core.ledger import get_ledger
    ledger = get_ledger()
    action_id = ledger.queue_action(
        "PrivacyAudit",
        "purge_provider",
        {"provider": provider},
        risk_level="high",
    )
    return {
        "status": "pending_approval",
        "action_id": action_id,
        "message": f"Purge of {provider} history queued. Approve at POST /api/actions/{action_id}/approve",
    }
