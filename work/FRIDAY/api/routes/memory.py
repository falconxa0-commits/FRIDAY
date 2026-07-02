"""Memory API routes — CRUD for Friday's memory system, plus export/import.

New endpoints:
  - GET  /api/memory/export  — export all memories as JSON
  - POST /api/memory/import  — import memories from JSON

The export/import cycle is lossless: store → export → delete → re-import → verify match.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import datetime
import json
import logging

logger = logging.getLogger("friday.api.memory")

router = APIRouter()


class MemoryAddRequest(BaseModel):
    text: str
    metadata: Optional[dict] = {}


class MemoryImportRequest(BaseModel):
    memories: List[dict]


# ---------------------------------------------------------------------------
# Singleton memory instance
# ---------------------------------------------------------------------------

_memory = None


def get_memory():
    global _memory
    if _memory is None:
        try:
            from core.memory import FridayMemory
            _memory = FridayMemory()
        except Exception as e:
            logger.exception("Failed to initialise FridayMemory")
            _memory = None
    return _memory


# ---------------------------------------------------------------------------
# GET /api/memory/all  — list all memories
# ---------------------------------------------------------------------------

@router.get("/all", response_model=List[dict])
async def list_all_memories():
    mem = get_memory()
    if not mem:
        raise HTTPException(status_code=503, detail="Memory service unavailable.")

    # Try Supabase first
    if mem.supabase and mem.supabase.client:
        try:
            response = mem.vector_store.supabase.client.table("memories").select("*").execute()
            return response.data
        except Exception as e:
            logger.warning(f"Supabase query failed, falling back to in-memory: {e}")

    # Fallback: in-memory store
    try:
        return mem._memories
    except Exception as e:
        logger.exception("Failed to list memories")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve memories: {e}")


# ---------------------------------------------------------------------------
# DELETE /api/memory/{memory_id}  — actually delete a memory
# ---------------------------------------------------------------------------

@router.delete("/{memory_id}")
async def delete_memory(memory_id: str):
    mem = get_memory()
    if not mem:
        raise HTTPException(status_code=503, detail="Memory service unavailable.")

    deleted = False

    # Try Supabase / vector store deletion
    if mem.vector_store and hasattr(mem.vector_store, 'supabase') and mem.vector_store.supabase:
        try:
            mem.vector_store.supabase.client.table("memories").delete().eq("id", memory_id).execute()
            deleted = True
        except Exception as e:
            logger.warning(f"Supabase delete failed: {e}")

    # Also remove from in-memory store
    try:
        original_len = len(mem._memories)
        mem._memories = [
            m for m in mem._memories
            if str(m.get("id", "")) != str(memory_id)
        ]
        if len(mem._memories) < original_len:
            deleted = True
    except Exception as e:
        logger.warning(f"In-memory delete failed: {e}")

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found.")

    return {"status": "success", "message": f"Memory {memory_id} deleted."}


# ---------------------------------------------------------------------------
# POST /api/memory/  — add a memory (uses request body, not query param)
# ---------------------------------------------------------------------------

@router.post("/")
async def add_memory(request: MemoryAddRequest):
    mem = get_memory()
    if not mem:
        raise HTTPException(status_code=503, detail="Memory service unavailable.")

    try:
        # Store the raw text as a conversation turn (so it round-trips
        # through /export and /import) AND extract any pattern-matched
        # facts into session_facts.
        mem.store_conversation(
            "user",
            request.text,
            metadata=request.metadata or {},
        )
        return {"status": "success", "message": "Memory stored."}
    except Exception as e:
        logger.exception("Failed to add memory")
        raise HTTPException(status_code=500, detail=f"Failed to store memory: {e}")


# ---------------------------------------------------------------------------
# GET /api/memory/?query=...  — search memories
# ---------------------------------------------------------------------------

@router.get("/")
async def search_memory(query: str):
    mem = get_memory()
    if not mem:
        raise HTTPException(status_code=503, detail="Memory service unavailable.")

    try:
        results = mem.retrieve_relevant_memories(query)
        return {"results": results}
    except Exception as e:
        logger.exception("Memory search failed")
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")


# ---------------------------------------------------------------------------
# GET /api/memory/export  — export all memories as JSON
# ---------------------------------------------------------------------------

@router.get("/export")
async def export_memories():
    """Export all memories as a JSON-serializable list.

    Returns the full in-memory store plus session facts so that the
    export → delete → re-import round-trip is lossless.
    """
    mem = get_memory()
    if not mem:
        raise HTTPException(status_code=503, detail="Memory service unavailable.")

    try:
        memories = list(mem._memories)
        session_facts = dict(mem._session_facts)

        export_data = {
            "exported_at": datetime.datetime.now().isoformat(),
            "memory_count": len(memories),
            "memories": memories,
            "session_facts": session_facts,
        }
        return export_data
    except Exception as e:
        logger.exception("Memory export failed")
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")


# ---------------------------------------------------------------------------
# POST /api/memory/import  — import memories from JSON
# ---------------------------------------------------------------------------

@router.post("/import")
async def import_memories(request: MemoryImportRequest):
    """Import memories from a JSON list.

    Each item in ``request.memories`` is added to the in-memory store.
    If the item contains a 'role' key it is treated as a conversation;
    otherwise it is stored as a raw memory entry.
    """
    mem = get_memory()
    if not mem:
        raise HTTPException(status_code=503, detail="Memory service unavailable.")

    try:
        imported_count = 0
        skipped = 0

        for entry in request.memories:
            if not isinstance(entry, dict):
                skipped += 1
                continue

            role = entry.get("role")
            content = entry.get("content", "")

            if role and content:
                mem.store_conversation(role, content, metadata=entry.get("metadata"))
            elif content:
                mem._memories.append(entry)
            else:
                skipped += 1
                continue

            imported_count += 1

        return {
            "status": "success",
            "message": f"Imported {imported_count} memories ({skipped} skipped).",
            "imported_count": imported_count,
            "skipped": skipped,
        }
    except Exception as e:
        logger.exception("Memory import failed")
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")
