"""Multi-modal memory API routes — store and retrieve visual memories.

Endpoints:
    POST /api/visual-memory/store   — store a visual memory (image + description)
    GET  /api/visual-memory/search  — semantic search across visual memories
    GET  /api/visual-memory/list    — list all visual memories
    DELETE /api/visual-memory/{id}  — delete a visual memory
"""
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

logger = logging.getLogger("friday.api.visual_memory")

router = APIRouter()


# ---------------------------------------------------------------------------
# Singleton MultiModalMemory instance
# ---------------------------------------------------------------------------

_memory = None


def _get_memory():
    global _memory
    if _memory is None:
        from core.multimodal_memory import MultiModalMemory
        _memory = MultiModalMemory()
    return _memory


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/store")
async def store_visual(
    file: UploadFile = File(...),
    description: str = Form(...),
    context: str = Form("{}"),
):
    """Store a visual memory — upload an image + text description."""
    import tempfile
    import json

    mem = _get_memory()
    if not mem:
        raise HTTPException(status_code=503, detail="Multi-modal memory unavailable.")

    # Save uploaded file to temp location
    suffix = os.path.splitext(file.filename or "image.png")[1] or ".png"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        ctx = json.loads(context) if context else {}
        result = await mem.store_visual(tmp_path, description, ctx)
        return {"memory": {k: v for k, v in result.items() if k != "embedding"}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def search_visual(query: str):
    """Semantic search across visual memories."""
    mem = _get_memory()
    if not mem:
        raise HTTPException(status_code=503, detail="Multi-modal memory unavailable.")
    results = await mem.search_visual(query)
    # Strip embeddings from response
    for r in results:
        if "embedding" in r.get("memory", {}):
            del r["memory"]["embedding"]
    return {"query": query, "results": results}


@router.get("/list")
async def list_visual():
    """List all visual memories (without embeddings)."""
    mem = _get_memory()
    if not mem:
        raise HTTPException(status_code=503, detail="Multi-modal memory unavailable.")
    return {"memories": mem.list_visual_memories(), "count": mem.count()}


@router.delete("/{memory_id}")
async def delete_visual(memory_id: str):
    """Delete a visual memory by ID."""
    mem = _get_memory()
    if not mem:
        raise HTTPException(status_code=503, detail="Multi-modal memory unavailable.")
    deleted = mem.delete_visual_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Visual memory {memory_id} not found.")
    return {"status": "success", "message": f"Visual memory {memory_id} deleted."}
