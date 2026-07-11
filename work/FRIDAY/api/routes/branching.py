"""Conversation branching API routes.

Endpoints:
    POST   /api/chat/branch                  — create a fork from a message index
    GET    /api/chat/branches                 — list all branches
    POST   /api/chat/branch/{id}/switch       — switch active branch
    POST   /api/chat/branch/{id}/merge-insight — bring branch learning back
    DELETE /api/chat/branch/{id}              — close a branch
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger("friday.api.branching")

router = APIRouter()


class BranchRequest(BaseModel):
    message_index: int
    new_message: Optional[str] = ""


@router.post("/chat/branch")
async def create_branch(req: BranchRequest):
    """Create a conversation branch at the specified message index."""
    from api.main import _get_brain
    brain = await _get_brain()
    result = await brain.branch_conversation(str(req.message_index), req.new_message or "")
    return {"status": "success", "branch": result}


@router.get("/chat/branches")
async def list_branches():
    """List all active conversation branches."""
    from api.main import _get_brain
    brain = await _get_brain()
    branches = await brain.get_branches()
    return {"branches": branches, "count": len(branches)}


@router.post("/chat/branch/{branch_id}/switch")
async def switch_branch(branch_id: str):
    """Switch the active conversation to a different branch."""
    from api.main import _get_brain
    brain = await _get_brain()
    success = await brain.switch_branch(branch_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found.")
    return {"status": "success", "active_branch": branch_id}


@router.post("/chat/branch/{branch_id}/merge-insight")
async def merge_insight(branch_id: str):
    """Bring a branch's key insights back to the main conversation."""
    from api.main import _get_brain
    brain = await _get_brain()
    insight = await brain.merge_branch_insight(branch_id)
    return {"status": "success", "insight": insight}


@router.delete("/chat/branch/{branch_id}")
async def delete_branch(branch_id: str):
    """Close a conversation branch."""
    from api.main import _get_brain
    brain = await _get_brain()
    success = await brain.delete_branch(branch_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found or cannot delete.")
    return {"status": "success", "message": f"Branch {branch_id} deleted."}
