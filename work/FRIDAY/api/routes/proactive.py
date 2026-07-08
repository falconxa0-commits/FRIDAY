"""Proactive API routes — mode-aware daily briefings.

Endpoints:
    POST /api/proactive/briefing  — trigger a proactive daily briefing
    GET  /api/proactive/status    — current mode + last briefing date
"""
import logging
from fastapi import APIRouter

logger = logging.getLogger("friday.api.proactive")

router = APIRouter()


@router.post("/briefing")
async def trigger_briefing():
    """Trigger a proactive daily briefing."""
    try:
        from core.proactive import ProactiveEngine
        from api.main import _get_brain
        from core.universal_connector import UniversalConnector

        brain = await _get_brain()
        connector = UniversalConnector()
        engine = ProactiveEngine(brain=brain, connector=connector)
        briefing = await engine.daily_briefing()
        return {"status": "success", "briefing": briefing}
    except Exception as exc:
        logger.exception("Proactive briefing failed")
        return {"status": "error", "message": str(exc)}


@router.get("/status")
async def proactive_status():
    """Return current mode and proactive engine status."""
    from core.proactive import get_current_context_mode
    mode = get_current_context_mode()
    return {"current_mode": mode, "proactive_available": True}
