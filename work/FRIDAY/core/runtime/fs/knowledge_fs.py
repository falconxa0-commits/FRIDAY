"""Knowledge Filesystem — virtual filesystem for engineering knowledge.

Organizes engineering knowledge (ADRs, lessons, benchmarks, research)
into a virtual directory structure that can be browsed and queried.

Virtual layout::

    /knowledge
    ├── /adrs           — Architecture Decision Records
    ├── /lessons        — Lessons learned
    ├── /benchmarks     — Benchmark results
    ├── /research       — Research findings
    ├── /standards      — Coding standards
    └── /runbooks       — Operational runbooks
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.fs.knowledge")


@dataclass
class VirtualFile:
    """A file in the virtual knowledge filesystem."""
    path: str
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "content_length": len(self.content),
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class KnowledgeFilesystem:
    """Virtual filesystem for engineering knowledge.

    Files are organized into directories by type. The filesystem
    supports read, write, list, and search operations.
    """

    # Standard directories
    DIRECTORIES = ["/adrs", "/lessons", "/benchmarks", "/research", "/standards", "/runbooks"]

    def __init__(self):
        self._files: Dict[str, VirtualFile] = {}
        self._lock = None  # lazy init

    async def write(self, path: str, content: str, metadata: Optional[Dict] = None) -> VirtualFile:
        """Write a file to the virtual filesystem."""
        # Normalize path
        if not path.startswith("/"):
            path = "/" + path

        file = VirtualFile(
            path=path,
            content=content,
            metadata=metadata or {},
        )

        # Preserve creation time if file exists
        if path in self._files:
            file.created_at = self._files[path].created_at

        self._files[path] = file
        logger.debug(f"Wrote {len(content)} bytes to {path}")
        return file

    async def read(self, path: str) -> Optional[VirtualFile]:
        """Read a file from the virtual filesystem."""
        if not path.startswith("/"):
            path = "/" + path
        return self._files.get(path)

    async def list_dir(self, dir_path: str = "/") -> List[str]:
        """List files in a directory."""
        if not dir_path.startswith("/"):
            dir_path = "/" + dir_path
        if not dir_path.endswith("/"):
            dir_path += "/"

        files = []
        for path in self._files:
            if path.startswith(dir_path):
                # Get relative path
                rel = path[len(dir_path):]
                # Only direct children (no deeper nesting in listing)
                if "/" not in rel.rstrip("/"):
                    files.append(path)
        return sorted(files)

    async def search(self, query: str) -> List[VirtualFile]:
        """Search file contents for a query string."""
        query_lower = query.lower()
        results = []
        for file in self._files.values():
            if query_lower in file.content.lower() or query_lower in file.path.lower():
                results.append(file)
        return results

    async def delete(self, path: str) -> bool:
        """Delete a file."""
        if not path.startswith("/"):
            path = "/" + path
        return self._files.pop(path, None) is not None

    async def exists(self, path: str) -> bool:
        """Check if a file exists."""
        if not path.startswith("/"):
            path = "/" + path
        return path in self._files

    def get_stats(self) -> Dict[str, Any]:
        """Get filesystem statistics."""
        by_dir = {}
        for path in self._files:
            dir_name = "/" + path.split("/")[1] if len(path.split("/")) > 1 else "/"
            by_dir[dir_name] = by_dir.get(dir_name, 0) + 1
        return {
            "total_files": len(self._files),
            "files_by_directory": by_dir,
            "directories": self.DIRECTORIES,
        }

    async def is_healthy(self) -> bool:
        return True

    async def stop(self) -> None:
        logger.info("Knowledge filesystem stopped")
