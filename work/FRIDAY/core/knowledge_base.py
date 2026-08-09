"""Engineering Knowledge Base — structured institutional memory.

Stores architecture decisions, coding standards, lessons learned,
benchmark history, and troubleshooting guides so agents can reference
previous work instead of repeating it.

Design principles:
    - **Searchable**: Full-text search across all entries.
    - **Categorized**: Each entry has a type (ADR, standard, lesson, etc.).
    - **Versioned**: Entries track when they were created and updated.
    - **Linked**: Entries can reference related entries and tasks.
    - **Durable**: All entries persisted to ``.friday/knowledge/``.

Entry types:
    - **ADR**: Architecture Decision Record
    - **STANDARD**: Coding standard or convention
    - **LESSON**: Lesson learned from a failure or success
    - **DESIGN**: Design document for a feature or system
    - **BENCHMARK**: Benchmark result with historical trend
    - **RUNBOOK**: Troubleshooting guide for operational issues
    - **RESEARCH**: Research finding (separate from implemented features)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("friday.knowledge_base")

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
_KB_ROOT = Path(os.environ.get(
    "FRIDAY_ENGINEERING_DIR",
    str(Path.cwd() / ".friday"),
))
_KB_DIR = _KB_ROOT / "knowledge"
_KB_INDEX_PATH = _KB_DIR / "index.json"
_KB_ENTRIES_DIR = _KB_DIR / "entries"

for _p in (_KB_DIR, _KB_ENTRIES_DIR):
    _p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class EntryType(str, Enum):
    ADR = "adr"                      # Architecture Decision Record
    STANDARD = "standard"            # Coding standard / convention
    LESSON = "lesson"                # Lesson learned
    DESIGN = "design"                # Design document
    BENCHMARK = "benchmark"          # Benchmark result
    RUNBOOK = "runbook"              # Troubleshooting guide
    RESEARCH = "research"            # Research finding
    RELEASE = "release"              # Release note


class EntryStatus(str, Enum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class KnowledgeEntry:
    """A single entry in the engineering knowledge base.

    Attributes:
        id: Unique entry ID (UUID).
        type: Entry type (ADR, STANDARD, LESSON, etc.).
        title: Human-readable title.
        summary: One-paragraph summary.
        content: Full content (Markdown).
        status: Lifecycle status.
        tags: Free-form tags for discovery.
        related_tasks: Task IDs this entry references.
        related_entries: Entry IDs this entry references.
        created_at: ISO timestamp.
        updated_at: ISO timestamp.
        author: Agent or human who created the entry.
        metadata: Arbitrary key-value pairs.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: EntryType = EntryType.LESSON
    title: str = ""
    summary: str = ""
    content: str = ""
    status: EntryStatus = EntryStatus.ACCEPTED
    tags: List[str] = field(default_factory=list)
    related_tasks: List[str] = field(default_factory=list)
    related_entries: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    author: str = "system"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeEntry":
        if "type" in data and isinstance(data["type"], str):
            data["type"] = EntryType(data["type"])
        if "status" in data and isinstance(data["status"], str):
            data["status"] = EntryStatus(data["status"])
        return cls(**data)

    def to_markdown(self) -> str:
        """Render as a Markdown document."""
        lines = [
            f"# [{self.type.value.upper()}] {self.title}",
            "",
            f"**Status:** {self.status.value}  |  "
            f"**Author:** {self.author}  |  "
            f"**Created:** {self.created_at[:10]}",
            "",
            "## Summary",
            "",
            self.summary,
            "",
            "## Content",
            "",
            self.content,
        ]
        if self.tags:
            lines.extend(["", "**Tags:** " + ", ".join(self.tags)])
        if self.related_entries:
            lines.extend(["", "**Related entries:** " + ", ".join(self.related_entries)])
        if self.related_tasks:
            lines.extend(["", "**Related tasks:** " + ", ".join(self.related_tasks)])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Knowledge Base
# ---------------------------------------------------------------------------
class KnowledgeBase:
    """Persistent, searchable engineering knowledge base.

    All entries are persisted as individual JSON files in
    ``.friday/knowledge/entries/``. An index file tracks all entry
    IDs and their metadata for fast listing.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir is None:
            base_dir = _KB_DIR
        self.base_dir = Path(base_dir)
        self.entries_dir = self.base_dir / "entries"
        self.index_path = self.base_dir / "index.json"
        self.entries_dir.mkdir(parents=True, exist_ok=True)

        self._lock = asyncio.Lock()
        self._entries: Dict[str, KnowledgeEntry] = {}
        self._index: List[Dict[str, Any]] = []  # lightweight metadata for listing
        self._load()

    def _load(self) -> None:
        """Load entries from disk."""
        if self.index_path.exists():
            try:
                with open(self.index_path) as f:
                    self._index = json.load(f)
                logger.info(f"Loaded knowledge base index: {len(self._index)} entries")
            except Exception as exc:
                logger.error(f"Failed to load KB index: {exc}")
                self._index = []

        # Lazy-load full entries on demand
        for item in self._index:
            entry_id = item.get("id")
            if entry_id:
                entry_path = self.entries_dir / f"{entry_id}.json"
                if entry_path.exists():
                    try:
                        with open(entry_path) as f:
                            data = json.load(f)
                        self._entries[entry_id] = KnowledgeEntry.from_dict(data)
                    except Exception as exc:
                        logger.error(f"Failed to load entry {entry_id}: {exc}")

    def _persist_index(self) -> None:
        """Persist the index (lightweight metadata for all entries)."""
        self._index = [
            {
                "id": e.id,
                "type": e.type.value,
                "title": e.title,
                "status": e.status.value,
                "tags": e.tags,
                "created_at": e.created_at,
                "updated_at": e.updated_at,
            }
            for e in self._entries.values()
        ]
        with open(self.index_path, "w") as f:
            json.dump(self._index, f, indent=2, default=str)

    def _persist_entry(self, entry: KnowledgeEntry) -> None:
        """Persist a single entry to disk."""
        path = self.entries_dir / f"{entry.id}.json"
        with open(path, "w") as f:
            json.dump(entry.to_dict(), f, indent=2, default=str)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_entry(
        self,
        type: EntryType,
        title: str,
        summary: str = "",
        content: str = "",
        tags: Optional[List[str]] = None,
        author: str = "system",
        status: EntryStatus = EntryStatus.ACCEPTED,
        related_tasks: Optional[List[str]] = None,
        related_entries: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> KnowledgeEntry:
        """Create a new knowledge base entry."""
        async with self._lock:
            entry = KnowledgeEntry(
                type=type,
                title=title,
                summary=summary,
                content=content,
                tags=tags or [],
                author=author,
                status=status,
                related_tasks=related_tasks or [],
                related_entries=related_entries or [],
                metadata=metadata or {},
            )
            self._entries[entry.id] = entry
            self._persist_entry(entry)
            self._persist_index()
            logger.info(f"Created KB entry {entry.id[:8]}: [{type.value}] {title}")
            return entry

    async def get_entry(self, entry_id: str) -> Optional[KnowledgeEntry]:
        """Get an entry by ID."""
        async with self._lock:
            return self._entries.get(entry_id)

    async def update_entry(
        self, entry_id: str, **updates
    ) -> Optional[KnowledgeEntry]:
        """Update an entry's fields."""
        async with self._lock:
            entry = self._entries.get(entry_id)
            if not entry:
                return None
            for key, value in updates.items():
                if hasattr(entry, key):
                    if key == "type" and isinstance(value, str):
                        value = EntryType(value)
                    elif key == "status" and isinstance(value, str):
                        value = EntryStatus(value)
                    setattr(entry, key, value)
            entry.updated_at = datetime.now(timezone.utc).isoformat()
            self._persist_entry(entry)
            self._persist_index()
            return entry

    async def list_entries(
        self,
        type: Optional[EntryType] = None,
        status: Optional[EntryStatus] = None,
        tag: Optional[str] = None,
    ) -> List[KnowledgeEntry]:
        """List entries, optionally filtered."""
        async with self._lock:
            entries = list(self._entries.values())
            if type:
                entries = [e for e in entries if e.type == type]
            if status:
                entries = [e for e in entries if e.status == status]
            if tag:
                entries = [e for e in entries if tag in e.tags]
            entries.sort(key=lambda e: e.updated_at, reverse=True)
            return entries

    async def search(self, query: str, limit: int = 20) -> List[KnowledgeEntry]:
        """Full-text search across all entries.

        Tokenizes the query into individual words and matches entries
        that contain ALL tokens (AND semantics). Each token match
        contributes to the entry's score. This handles word-order
        independence and partial matches better than substring search.

        For production use, consider adding whoosh or sqlite FTS5.
        """
        async with self._lock:
            # Tokenize: lowercase, split on non-alphanumeric, filter empties
            tokens = re.findall(r"[a-z0-9]+", query.lower())
            if not tokens:
                return []

            scored: List[Tuple[int, KnowledgeEntry]] = []
            for entry in self._entries.values():
                title_lower = entry.title.lower()
                summary_lower = entry.summary.lower()
                content_lower = entry.content.lower()
                tags_lower = " ".join(entry.tags).lower()

                score = 0
                all_tokens_found = True
                for token in tokens:
                    token_found = False
                    if token in title_lower:
                        score += 10
                        token_found = True
                    if token in summary_lower:
                        score += 5
                        token_found = True
                    if token in content_lower:
                        score += 3
                        token_found = True
                    if token in tags_lower:
                        score += 7
                        token_found = True
                    if not token_found:
                        all_tokens_found = False
                        break

                if all_tokens_found and score > 0:
                    scored.append((score, entry))

            scored.sort(key=lambda x: x[0], reverse=True)
            return [e for _, e in scored[:limit]]

    async def get_stats(self) -> Dict[str, Any]:
        """Get knowledge base statistics."""
        async with self._lock:
            entries = list(self._entries.values())
            return {
                "total": len(entries),
                "by_type": {
                    t.value: sum(1 for e in entries if e.type == t)
                    for t in EntryType
                },
                "by_status": {
                    s.value: sum(1 for e in entries if e.status == s)
                    for s in EntryStatus
                },
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_kb: Optional[KnowledgeBase] = None


def get_knowledge_base() -> KnowledgeBase:
    """Get the singleton KnowledgeBase instance."""
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb
