"""Team API routes — multi-user management endpoints.

Endpoints:
    GET  /api/team/members     — list all team members (without tokens)
    POST /api/team/invite      — create an invitation
    GET  /api/team/context     — get shared + private context for the requesting user
    POST /api/team/memory/private — store a private memory for the requesting user
    POST /api/team/memory/shared  — store a shared memory
    GET  /api/team/memories    — get all memories visible to the requesting user
"""
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

logger = logging.getLogger("friday.api.team")

router = APIRouter()


# ---------------------------------------------------------------------------
# Singleton TeamMode instance
# ---------------------------------------------------------------------------

_team_mode = None


def _get_team_mode():
    global _team_mode
    if _team_mode is None:
        from core.team_mode import TeamMode
        _team_mode = TeamMode()
    return _team_mode


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class InviteRequest(BaseModel):
    email: str


class PrivateMemoryRequest(BaseModel):
    content: str
    metadata: dict = {}


class SharedMemoryRequest(BaseModel):
    content: str
    metadata: dict = {}


# ---------------------------------------------------------------------------
# Auth helper — extract user from FRIDAY_USER_TOKEN header
# ---------------------------------------------------------------------------

def _get_user_from_header(authorization: Optional[str]):
    """Extract user from the Authorization header (FRIDAY_USER_TOKEN)."""
    if not authorization:
        return None
    # Accept "Bearer <token>" or just "<token>"
    token = authorization
    if authorization.lower().startswith("bearer "):
        token = authorization[7:]
    tm = _get_team_mode()
    return tm.get_user_by_token(token)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/members")
async def list_members(authorization: Optional[str] = Header(None)):
    """List all team members (without their tokens).

    Requires a valid FRIDAY_USER_TOKEN — only authenticated team members
    can see the roster.
    """
    user = _get_user_from_header(authorization)
    if not user:
        raise HTTPException(
            status_code=403,
            detail="Valid FRIDAY_USER_TOKEN required to list team members.",
        )
    tm = _get_team_mode()
    return {"members": tm.list_members()}


@router.post("/invite")
async def invite_member(req: InviteRequest, authorization: Optional[str] = Header(None)):
    """Create an invitation for a new team member."""
    user = _get_user_from_header(authorization)
    if not user:
        raise HTTPException(status_code=403, detail="Valid FRIDAY_USER_TOKEN required to invite.")
    tm = _get_team_mode()
    invite = tm.invite(req.email, user["user_id"])
    return {"invite": invite}


@router.get("/context")
async def get_context(authorization: Optional[str] = Header(None)):
    """Get shared + private context for the requesting user."""
    user = _get_user_from_header(authorization)
    if not user:
        raise HTTPException(status_code=403, detail="Valid FRIDAY_USER_TOKEN required.")
    tm = _get_team_mode()
    return await tm.get_context_for_user(user["user_id"])


@router.post("/memory/private")
async def store_private_memory(req: PrivateMemoryRequest,
                                authorization: Optional[str] = Header(None)):
    """Store a private memory for the requesting user."""
    user = _get_user_from_header(authorization)
    if not user:
        raise HTTPException(status_code=403, detail="Valid FRIDAY_USER_TOKEN required.")
    tm = _get_team_mode()
    mem = await tm.store_private_memory(user["user_id"], req.content, req.metadata)
    return {"memory": mem}


@router.post("/memory/shared")
async def store_shared_memory(req: SharedMemoryRequest,
                               authorization: Optional[str] = Header(None)):
    """Store a shared memory visible to all team members."""
    user = _get_user_from_header(authorization)
    if not user:
        raise HTTPException(status_code=403, detail="Valid FRIDAY_USER_TOKEN required.")
    tm = _get_team_mode()
    mem = await tm.store_shared_memory(req.content, req.metadata, user["user_id"])
    return {"memory": mem}


@router.get("/memories")
async def get_memories(authorization: Optional[str] = Header(None)):
    """Get all memories visible to the requesting user (shared + private)."""
    user = _get_user_from_header(authorization)
    if not user:
        raise HTTPException(status_code=403, detail="Valid FRIDAY_USER_TOKEN required.")
    tm = _get_team_mode()
    context = await tm.get_context_for_user(user["user_id"])
    return {
        "user": {"user_id": user["user_id"], "name": user["name"]},
        "shared": context["shared"],
        "private": context["private"],
    }
