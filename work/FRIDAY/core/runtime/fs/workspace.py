"""Workspace Manager — virtual workspace for task execution.

Each task or workflow can have its own virtual workspace with
input files, output files, and temporary storage.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.fs.workspace")


@dataclass
class Workspace:
    """A virtual workspace."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    files: Dict[str, str] = field(default_factory=dict)  # path → content
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "file_count": len(self.files),
            "created_at": self.created_at,
            "active": self.active,
        }


class WorkspaceManager:
    """Manages virtual workspaces.

    Workspaces provide isolated file storage for task execution.
    Each workspace is identified by a unique ID and can contain
    files that are accessible only within that workspace.
    """

    def __init__(self):
        self._workspaces: Dict[str, Workspace] = {}

    async def create_workspace(self, name: str = "") -> Workspace:
        """Create a new virtual workspace."""
        ws = Workspace(name=name)
        self._workspaces[ws.id] = ws
        logger.info(f"Created workspace {ws.id[:8]} ({name})")
        return ws

    async def get_workspace(self, ws_id: str) -> Optional[Workspace]:
        return self._workspaces.get(ws_id)

    async def write_file(self, ws_id: str, path: str, content: str) -> bool:
        """Write a file to a workspace."""
        ws = self._workspaces.get(ws_id)
        if not ws:
            return False
        ws.files[path] = content
        return True

    async def read_file(self, ws_id: str, path: str) -> Optional[str]:
        """Read a file from a workspace."""
        ws = self._workspaces.get(ws_id)
        if not ws:
            return None
        return ws.files.get(path)

    async def list_files(self, ws_id: str) -> List[str]:
        """List files in a workspace."""
        ws = self._workspaces.get(ws_id)
        if not ws:
            return []
        return list(ws.files.keys())

    async def delete_file(self, ws_id: str, path: str) -> bool:
        """Delete a file from a workspace."""
        ws = self._workspaces.get(ws_id)
        if not ws:
            return False
        return ws.files.pop(path, None) is not None

    async def close_workspace(self, ws_id: str) -> bool:
        """Close a workspace (mark inactive, keep files)."""
        ws = self._workspaces.get(ws_id)
        if not ws:
            return False
        ws.active = False
        return True

    async def delete_workspace(self, ws_id: str) -> bool:
        """Delete a workspace entirely."""
        return self._workspaces.pop(ws_id, None) is not None

    def list_workspaces(self, active_only: bool = False) -> List[Workspace]:
        """List all workspaces."""
        wss = list(self._workspaces.values())
        if active_only:
            wss = [ws for ws in wss if ws.active]
        return wss

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_workspaces": len(self._workspaces),
            "active": sum(1 for ws in self._workspaces.values() if ws.active),
            "total_files": sum(len(ws.files) for ws in self._workspaces.values()),
        }

    async def is_healthy(self) -> bool:
        return True

    async def stop(self) -> None:
        logger.info("Workspace manager stopped")
