"""Provenance — origin tracking for persistent memories.

Every persistent memory (episodic, semantic, procedural, associative) must
carry a Provenance record answering:
    WHO created it?
    WHEN?
    FROM WHAT SOURCE?
    WITH WHAT CONFIDENCE?
    WHICH PROCESS modified it?
    WHICH VERSION is current?

Provenance is immutable per modification: each mutation appends a new
ProvenanceEntry to the chain. The chain is hash-chained (each entry includes
the hash of the previous entry) so tampering is detectable.

Provenance survives serialization and recovery.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import now_utc

logger = logging.getLogger("friday.living_memory.provenance")


@dataclass
class ProvenanceEntry:
    """A single entry in the provenance chain."""
    actor_id: str                    # Citizen ID performing the action
    action: str                     # "create" | "update" | "reinforce" | "archive" | "forget" | "restore"
    timestamp: str = field(default_factory=now_utc)
    source: str = ""                # Where the info came from (URI, citizen, sensor)
    process: str = ""                # Process name (e.g. "consolidation_engine")
    parent_hash: str = ""            # Hash of the previous entry (chain)
    entry_hash: str = ""            # Hash of this entry
    notes: str = ""
    correlation_id: str = ""

    def compute_hash(self) -> str:
        """SHA-256 of the entry's content (excluding entry_hash itself)."""
        h = hashlib.sha256()
        h.update(self.actor_id.encode("utf-8"))
        h.update(b"|")
        h.update(self.action.encode("utf-8"))
        h.update(b"|")
        h.update(self.timestamp.encode("utf-8"))
        h.update(b"|")
        h.update(self.source.encode("utf-8"))
        h.update(b"|")
        h.update(self.process.encode("utf-8"))
        h.update(b"|")
        h.update(self.parent_hash.encode("utf-8"))
        h.update(b"|")
        h.update(self.notes.encode("utf-8"))
        h.update(b"|")
        h.update(self.correlation_id.encode("utf-8"))
        return h.hexdigest()

    def seal(self) -> str:
        """Compute and store the entry hash."""
        self.entry_hash = self.compute_hash()
        return self.entry_hash

    def verify(self) -> bool:
        """Verify this entry's hash matches its content."""
        return self.entry_hash == self.compute_hash()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "action": self.action,
            "timestamp": self.timestamp,
            "source": self.source,
            "process": self.process,
            "parent_hash": self.parent_hash,
            "entry_hash": self.entry_hash,
            "notes": self.notes,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProvenanceEntry":
        return cls(
            actor_id=d.get("actor_id", ""),
            action=d.get("action", ""),
            timestamp=d.get("timestamp", ""),
            source=d.get("source", ""),
            process=d.get("process", ""),
            parent_hash=d.get("parent_hash", ""),
            entry_hash=d.get("entry_hash", ""),
            notes=d.get("notes", ""),
            correlation_id=d.get("correlation_id", ""),
        )


@dataclass
class Provenance:
    """Provenance chain for a single memory."""
    chain: List[ProvenanceEntry] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.chain

    @property
    def length(self) -> int:
        return len(self.chain)

    @property
    def current_version(self) -> int:
        """Current version number = chain length."""
        return len(self.chain)

    @property
    def head_hash(self) -> str:
        return self.chain[-1].entry_hash if self.chain else ""

    @property
    def creator(self) -> str:
        """Actor ID of the original creator."""
        return self.chain[0].actor_id if self.chain else ""

    @property
    def created_at(self) -> str:
        return self.chain[0].timestamp if self.chain else ""

    def append(
        self,
        actor_id: str,
        action: str,
        source: str = "",
        process: str = "",
        notes: str = "",
        correlation_id: str = "",
    ) -> ProvenanceEntry:
        """Append a new provenance entry, chained to the current head."""
        if action not in (
            "create", "update", "reinforce", "archive",
            "forget", "restore", "consolidate", "decay", "quarantine",
            "associate", "disassociate", "contradiction_resolved",
        ):
            raise ValueError(f"Unknown provenance action: {action}")
        if not actor_id:
            raise ValueError("actor_id is required for provenance")
        entry = ProvenanceEntry(
            actor_id=actor_id,
            action=action,
            source=source,
            process=process,
            parent_hash=self.head_hash,
            notes=notes,
            correlation_id=correlation_id,
        )
        entry.seal()
        self.chain.append(entry)
        return entry

    def verify_chain(self) -> bool:
        """Verify the entire hash chain is intact.

        Returns False if any entry's hash doesn't match its content,
        or if any parent_hash doesn't match the previous entry's hash.

        V10: O(n) implementation — uses enumerate() instead of list.index()
        (which was O(n) per call, making the loop O(n²)).
        """
        prev_hash = ""
        for i, entry in enumerate(self.chain):
            if not entry.verify():
                logger.warning(
                    "Provenance chain broken: entry hash mismatch at idx %d", i,
                )
                return False
            if entry.parent_hash != prev_hash:
                logger.warning(
                    "Provenance chain broken: parent hash mismatch at idx %d", i,
                )
                return False
            prev_hash = entry.entry_hash
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chain": [e.to_dict() for e in self.chain],
            "version": self.current_version,
            "head_hash": self.head_hash,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Provenance":
        p = cls()
        for entry_d in d.get("chain", []):
            p.chain.append(ProvenanceEntry.from_dict(entry_d))
        return p


__all__ = ["Provenance", "ProvenanceEntry"]
