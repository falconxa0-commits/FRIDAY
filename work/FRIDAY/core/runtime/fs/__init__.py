"""Runtime Filesystem — virtual workspace and knowledge filesystem.

Provides a virtual filesystem layer for:
    - Knowledge storage (ADRs, lessons, docs)
    - Workspace management (task files, outputs)
    - Virtual paths (abstracted from physical paths)

This is NOT a real filesystem — it's a management layer that
organizes runtime data into a virtual directory structure.
"""
from core.runtime.fs.knowledge_fs import KnowledgeFilesystem
from core.runtime.fs.workspace import WorkspaceManager, Workspace

__all__ = ["KnowledgeFilesystem", "WorkspaceManager", "Workspace"]
