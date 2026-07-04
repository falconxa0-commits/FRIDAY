"""Persona API routes — export/import the full Friday persona.

Endpoints:
    GET  /api/persona/export  — export persona as JSON
    POST /api/persona/import  — import a previously exported persona
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("friday.api.persona")

router = APIRouter()


@router.get("/export")
async def export_persona():
    """Export the current persona (memories, patterns, skills, config)."""
    from core.persona import export_persona
    from core.memory import FridayMemory
    from core.pattern_engine import PatternEngine

    try:
        mem = FridayMemory()
        pe = PatternEngine()
        persona = export_persona(memory=mem, pattern_engine=pe, include_secrets=False)
        return persona
    except Exception as exc:
        logger.exception("Persona export failed")
        raise HTTPException(status_code=500, detail=str(exc))


class PersonaImportRequest(BaseModel):
    persona: dict


@router.post("/import")
async def import_persona(req: PersonaImportRequest):
    """Import a previously exported persona."""
    from core.persona import import_persona
    from core.memory import FridayMemory
    from core.pattern_engine import PatternEngine

    try:
        mem = FridayMemory()
        pe = PatternEngine()
        summary = import_persona(req.persona, memory=mem, pattern_engine=pe)
        return {"status": "success", "summary": summary}
    except Exception as exc:
        logger.exception("Persona import failed")
        raise HTTPException(status_code=500, detail=str(exc))
