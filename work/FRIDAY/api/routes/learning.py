"""Learning API routes — correction tracking and confidence adjustment.

Endpoints:
    GET    /api/learning/corrections          — list all recorded corrections
    DELETE /api/learning/corrections/{id}     — remove a correction
    POST   /api/learning/corrections          — manually record a correction
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("friday.api.learning")

router = APIRouter()


class CorrectionRequest(BaseModel):
    original: str
    correction: str
    context: dict = {}


_learning_system = None


def _get_learning_system():
    global _learning_system
    if _learning_system is None:
        from core.learning import FridayLearningSystem
        _learning_system = FridayLearningSystem()
    return _learning_system


@router.get("/corrections")
async def list_corrections():
    """List all recorded corrections."""
    ls = _get_learning_system()
    return {"corrections": ls.get_all_corrections(), "count": len(ls.get_all_corrections())}


@router.post("/corrections")
async def record_correction(req: CorrectionRequest):
    """Manually record a correction."""
    ls = _get_learning_system()
    entry = await ls.record_correction(req.original, req.correction, req.context)
    return {"status": "success", "correction": entry}


@router.delete("/corrections/{correction_id}")
async def delete_correction(correction_id: str):
    """Remove a correction by ID."""
    ls = _get_learning_system()
    corrections = ls._corrections
    original_len = len(corrections)
    ls._corrections = [c for c in corrections if c.get("id") != correction_id]
    if len(ls._corrections) == original_len:
        raise HTTPException(status_code=404, detail=f"Correction {correction_id} not found.")
    return {"status": "success", "message": f"Correction {correction_id} removed."}
