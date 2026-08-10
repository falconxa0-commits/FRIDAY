"""Session Runtime — manages user/conversation sessions.

A session represents a multi-turn interaction between a user and
FRIDAY. Sessions carry arbitrary per-user data and track their own
activity timestamps so that idle sessions can be expired by the
caller.

Usage::

    sr = SessionRuntime()
    session = await sr.create_session(user_id="alice")
    await sr.update_session(session.id, {"topic": "deploy"})
    active = await sr.list_active_sessions()
    await sr.end_session(session.id)
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.session_runtime")


@dataclass
class Session:
    """A single session.

    Attributes:
        id: Unique session identifier.
        user_id: Identifier of the owning user (default ``"default"``).
        data: Arbitrary per-session payload.
        created_at: ISO-8601 creation timestamp.
        last_active: ISO-8601 timestamp of the most recent activity.
        active: Whether the session is currently active.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = "default"
    data: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_active: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "data": dict(self.data),
            "created_at": self.created_at,
            "last_active": self.last_active,
            "active": self.active,
        }

    def touch(self) -> None:
        """Update ``last_active`` to the current UTC time."""
        self.last_active = datetime.now(timezone.utc).isoformat()


class SessionRuntime:
    """Manages sessions for multi-turn interactions."""

    def __init__(self, event_bus: Any = None):
        self._sessions: Dict[str, Session] = {}
        self._event_bus = event_bus
        self._running = True

    async def create_session(
        self, user_id: str = "default", data: Optional[Dict[str, Any]] = None
    ) -> Session:
        """Create a new session.

        Args:
            user_id: Owning user identifier.
            data: Initial per-session payload.

        Returns:
            The created Session.
        """
        session = Session(
            user_id=user_id,
            data=dict(data or {}),
        )
        self._sessions[session.id] = session
        logger.debug(
            f"Created session {session.id[:8]} for user '{user_id}'"
        )

        if self._event_bus:
            await self._event_bus.publish(
                "session.created",
                {"session_id": session.id, "user_id": user_id},
                source="session_runtime",
            )
        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        session = self._sessions.get(session_id)
        if session is not None and session.active:
            session.touch()
        return session

    async def update_session(
        self, session_id: str, updates: Dict[str, Any]
    ) -> Optional[Session]:
        """Update a session's data with a shallow merge.

        Args:
            session_id: ID of the session to update.
            updates: Key-value pairs to merge into ``session.data``.

        Returns:
            The updated Session, or None if not found.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None

        session.data.update(updates)
        session.touch()

        if self._event_bus:
            await self._event_bus.publish(
                "session.updated",
                {"session_id": session_id, "keys": list(updates.keys())},
                source="session_runtime",
            )
        return session

    async def end_session(self, session_id: str) -> bool:
        """End a session (mark inactive). The session is retained for
        audit purposes; only its ``active`` flag flips to False.

        Returns:
            True if the session was found and ended; False otherwise.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return False

        if session.active:
            session.active = False
            session.touch()
            if self._event_bus:
                await self._event_bus.publish(
                    "session.ended",
                    {"session_id": session_id, "user_id": session.user_id},
                    source="session_runtime",
                )
            logger.debug(f"Ended session {session_id[:8]}")
        return True

    async def list_active_sessions(self) -> List[Session]:
        """List all currently active sessions."""
        return [s for s in self._sessions.values() if s.active]

    async def list_sessions(self) -> List[Session]:
        """List all sessions (active and inactive)."""
        return list(self._sessions.values())

    async def get_session_count(self) -> int:
        """Return the total number of sessions (active + inactive)."""
        return len(self._sessions)

    async def get_active_session_count(self) -> int:
        """Return the number of currently active sessions."""
        return sum(1 for s in self._sessions.values() if s.active)

    async def is_healthy(self) -> bool:
        """Check if the session runtime is healthy."""
        return self._running

    async def stop(self) -> None:
        """Stop the session runtime and end all active sessions."""
        self._running = False
        for session in self._sessions.values():
            session.active = False
        logger.info("Session runtime stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get session runtime statistics."""
        active = sum(1 for s in self._sessions.values() if s.active)
        return {
            "total_sessions": len(self._sessions),
            "active_sessions": active,
            "inactive_sessions": len(self._sessions) - active,
            "unique_users": len({s.user_id for s in self._sessions.values()}),
        }
