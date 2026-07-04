"""Subconscious API routes — surface patterns from the subconscious mind.

Endpoints:
    GET /api/subconscious/patterns  — return recently surfaced patterns
    GET /api/subconscious/intuition — return current intuition string
"""
import logging
from fastapi import APIRouter

logger = logging.getLogger("friday.api.subconscious")

router = APIRouter()

_subconscious = None


def _get_subconscious():
    global _subconscious
    if _subconscious is None:
        try:
            from database.subconscious import SubconsciousMind
            _subconscious = SubconsciousMind()
        except Exception as exc:
            logger.warning(f"SubconsciousMind init failed: {exc}")
    return _subconscious


@router.get("/patterns")
async def get_patterns(context: str = ""):
    """Return patterns surfaced by the subconscious mind."""
    sub = _get_subconscious()
    if not sub:
        return {"patterns": [], "message": "Subconscious mind unavailable"}
    patterns = sub.surface_patterns(context or "general")
    return {"patterns": patterns, "count": len(patterns)}


@router.get("/intuition")
async def get_intuition():
    """Return the subconscious mind's current intuition."""
    sub = _get_subconscious()
    if not sub:
        return {"intuition": "", "message": "Subconscious mind unavailable"}
    intuition = sub.get_intuition()
    return {"intuition": intuition}
