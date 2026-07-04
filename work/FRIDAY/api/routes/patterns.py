"""Pattern Engine API routes — behavioral pattern learning endpoints.

Endpoints:
    GET  /api/patterns           — list discovered patterns
    POST /api/patterns/observe   — record a new interaction
    GET  /api/patterns/suggest   — get proactive suggestions for current context
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("friday.api.patterns")

router = APIRouter()


# ---------------------------------------------------------------------------
# Singleton PatternEngine instance
# ---------------------------------------------------------------------------

_pattern_engine = None


def _get_pattern_engine():
    global _pattern_engine
    if _pattern_engine is None:
        from core.pattern_engine import PatternEngine
        _pattern_engine = PatternEngine()
    return _pattern_engine


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ObserveRequest(BaseModel):
    action_type: str = "other"
    content: str = ""
    context: dict = {}
    outcome: str = "unknown"


class SuggestRequest(BaseModel):
    current_action: str = ""
    current_mode: str = ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
async def list_patterns():
    """List discovered behavioral patterns (runs discover if not yet done)."""
    import asyncio
    pe = _get_pattern_engine()
    if not pe.patterns:
        await pe.discover_patterns()
    return {
        "patterns": pe.patterns,
        "interaction_count": len(pe.interactions),
    }


@router.post("/observe")
async def observe_interaction(req: ObserveRequest):
    """Record a new interaction for pattern analysis."""
    pe = _get_pattern_engine()
    await pe.observe({
        "action_type": req.action_type,
        "content": req.content,
        "context": req.context,
        "outcome": req.outcome,
    })
    return {"status": "success", "total_interactions": len(pe.interactions)}


@router.post("/suggest")
async def get_suggestions(req: SuggestRequest):
    """Get proactive suggestions based on current context + learned patterns."""
    pe = _get_pattern_engine()
    suggestions = await pe.get_suggestions({
        "current_action": req.current_action,
        "current_mode": req.current_mode,
    })
    return {"suggestions": suggestions}
