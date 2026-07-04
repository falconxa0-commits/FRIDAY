"""Team Mode — multi-user mode for small teams.

Allows a small team to share a Friday instance with individual privacy:

Shared:
    - Project memories (explicitly tagged as shared)
    - Calendar (team-visible events only)
    - Action approvals (any team member can approve)
    - Integrations (one set of credentials shared)

Private (per user, never visible to others):
    - Personal memories
    - Personal conversation history
    - Personal behavioral patterns

Auth: each team member has their own FRIDAY_USER_TOKEN.
The server knows who's talking based on the token.
Memories are tagged with user_id and only returned to that user.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TeamMode:
    """Multi-user mode for small teams."""

    def __init__(self, memory=None):
        """
        Args:
            memory: Optional FridayMemory instance for shared memories.
                Per-user memories are stored separately.
        """
        self.memory = memory
        self._users: Dict[str, dict] = {}  # user_id → user record
        self._user_tokens: Dict[str, str] = {}  # token → user_id
        self._user_memories: Dict[str, List[dict]] = {}  # user_id → memories
        self._shared_memories: List[dict] = []
        self._invites: List[dict] = []
        self._lock = asyncio.Lock()  # protects all shared state mutations

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def register_user(self, name: str, email: str) -> dict:
        """Register a new team member. Returns the user record (including token)."""
        user_id = f"user_{len(self._users) + 1}_{secrets.token_hex(4)}"
        token = secrets.token_urlsafe(32)
        user = {
            "user_id": user_id,
            "name": name,
            "email": email,
            "token": token,
            "created_at": _now_iso(),
        }
        self._users[user_id] = user
        self._user_tokens[token] = user_id
        self._user_memories[user_id] = []
        logger.info("TeamMode: registered user %s (%s)", name, user_id)
        return user

    def get_user_by_token(self, token: str) -> Optional[dict]:
        """Look up a user by their FRIDAY_USER_TOKEN."""
        user_id = self._user_tokens.get(token)
        if user_id is None:
            return None
        return self._users.get(user_id)

    def list_members(self) -> List[dict]:
        """List all team members (without tokens)."""
        return [
            {k: v for k, v in u.items() if k != "token"}
            for u in self._users.values()
        ]

    def invite(self, email: str, inviter_user_id: str) -> dict:
        """Create an invitation for a new team member."""
        invite = {
            "invite_id": f"inv_{secrets.token_hex(8)}",
            "email": email,
            "inviter_user_id": inviter_user_id,
            "created_at": _now_iso(),
            "status": "pending",
        }
        self._invites.append(invite)
        return invite

    # ------------------------------------------------------------------
    # Memory operations
    # ------------------------------------------------------------------

    async def store_private_memory(self, user_id: str, content: str,
                                    metadata: Optional[dict] = None) -> dict:
        """Store a private memory for a specific user. Only that user can retrieve it."""
        if user_id not in self._user_memories:
            raise ValueError(f"Unknown user_id: {user_id}")
        async with self._lock:
            mem = {
                "id": f"pm_{secrets.token_hex(6)}",
                "user_id": user_id,
                "content": content,
                "metadata": metadata or {},
                "timestamp": _now_iso(),
                "scope": "private",
            }
            self._user_memories[user_id].append(mem)
            return mem

    async def store_shared_memory(self, content: str,
                                   metadata: Optional[dict] = None,
                                   author_user_id: Optional[str] = None) -> dict:
        """Store a shared memory visible to all team members."""
        async with self._lock:
            mem = {
                "id": f"sm_{secrets.token_hex(6)}",
                "content": content,
                "metadata": metadata or {},
                "author_user_id": author_user_id,
                "timestamp": _now_iso(),
                "scope": "shared",
            }
            self._shared_memories.append(mem)
            return mem

    async def get_user_memories(self, user_id: str) -> List[dict]:
        """Get a user's private memories. Only returns memories for that user."""
        return list(self._user_memories.get(user_id, []))

    async def get_shared_memories(self) -> List[dict]:
        """Get all shared memories (visible to all team members)."""
        return list(self._shared_memories)

    async def get_context_for_user(self, user_id: str) -> dict:
        """Returns the right mix of shared and private context for this user."""
        shared = await self.get_shared_memories()
        private = await self.get_user_memories(user_id)
        return {
            "user_id": user_id,
            "shared": shared,
            "private": private,
            "shared_count": len(shared),
            "private_count": len(private),
        }

    async def search_user_memories(self, user_id: str, query: str) -> dict:
        """Search both shared and private memories for a user.

        Returns:
            {shared: [...], private: [...]}
        """
        query_words = set(query.lower().split())
        def _matches(mem):
            content_words = set(mem.get("content", "").lower().split())
            return len(query_words & content_words) > 0

        shared_results = [m for m in self._shared_memories if _matches(m)]
        private_results = [m for m in self._user_memories.get(user_id, []) if _matches(m)]
        return {"shared": shared_results, "private": private_results}

    # ------------------------------------------------------------------
    # Privacy enforcement
    # ------------------------------------------------------------------

    def enforce_privacy(self, requesting_user_id: str, memory: dict) -> bool:
        """Check whether a user is allowed to see a memory.

        Returns True if allowed, False otherwise.
        """
        scope = memory.get("scope", "private")
        if scope == "shared":
            return True
        if scope == "private":
            return memory.get("user_id") == requesting_user_id
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().isoformat()
