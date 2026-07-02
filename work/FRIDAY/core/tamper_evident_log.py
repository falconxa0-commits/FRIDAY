"""Tamper-Evident Log — Hash-chain audit log for FRIDAY.

Every entry's hash includes the previous entry's hash, forming a
cryptographic chain.  Any modification to an entry breaks the chain
and is detectable via ``verify_chain()``.

Uses SHA-256 for hashing.  The first entry has ``previous_hash`` set to
``"0" * 64`` (64 zero characters).
"""

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TamperEvidentLog:
    """Hash-chain audit log where each entry is linked to the previous one.

    Usage::

        tel = TamperEvidentLog()
        tel.add_entry({"action": "login", "user": "alice"})
        tel.add_entry({"action": "file_delete", "user": "alice", "path": "/tmp/x"})
        ok, broken = tel.verify_chain()
        if not ok:
            print("Chain broken at indices:", broken)
    """

    GENESIS_HASH = "0" * 64  # 64 zero chars — SHA-256 produces 64-hex-char output

    def __init__(self):
        self._entries: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_payload(data: Any, previous_hash: str, timestamp: str) -> str:
        """Compute the SHA-256 hash for a log entry.

        The hash input is a deterministic JSON string containing the
        entry data, the previous hash, and the timestamp.
        """
        payload = json.dumps(
            {"data": data, "previous_hash": previous_hash, "timestamp": timestamp},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def add_entry(self, data: Any) -> Dict[str, Any]:
        """Add a new entry to the log and return it.

        The entry dict contains:
            - index: 0-based position in the log
            - timestamp: ISO-8601 timestamp
            - data: the caller-supplied payload
            - previous_hash: hash of the previous entry (or genesis hash)
            - hash: SHA-256 hash of this entry
        """
        previous_hash = self._entries[-1]["hash"] if self._entries else self.GENESIS_HASH
        timestamp = datetime.utcnow().isoformat()
        entry_hash = self._hash_payload(data, previous_hash, timestamp)

        entry = {
            "index": len(self._entries),
            "timestamp": timestamp,
            "data": data,
            "previous_hash": previous_hash,
            "hash": entry_hash,
        }
        self._entries.append(entry)
        logger.debug("TamperEvidentLog: added entry %d", entry["index"])
        return entry

    def verify_chain(self) -> tuple:
        """Verify the integrity of the entire hash chain.

        Returns:
            A tuple ``(is_valid, broken_indices)`` where ``is_valid`` is
            ``True`` when every link in the chain is intact and
            ``broken_indices`` is a list of entry indices where the
            chain is broken (empty when valid).
        """
        broken: List[int] = []

        for i, entry in enumerate(self._entries):
            # Check previous_hash link
            if i == 0:
                expected_prev = self.GENESIS_HASH
            else:
                expected_prev = self._entries[i - 1]["hash"]

            if entry.get("previous_hash") != expected_prev:
                broken.append(i)
                logger.warning(
                    "Chain broken at index %d: previous_hash mismatch "
                    "(expected %s, got %s)",
                    i,
                    expected_prev,
                    entry.get("previous_hash"),
                )
                continue

            # Recompute the hash and compare
            expected_hash = self._hash_payload(
                entry["data"], entry["previous_hash"], entry["timestamp"]
            )
            if entry.get("hash") != expected_hash:
                broken.append(i)
                logger.warning(
                    "Chain broken at index %d: hash mismatch "
                    "(expected %s, got %s)",
                    i,
                    expected_hash,
                    entry.get("hash"),
                )

        is_valid = len(broken) == 0
        if is_valid:
            logger.info("TamperEvidentLog: chain verified OK (%d entries)", len(self._entries))
        else:
            logger.warning(
                "TamperEvidentLog: chain BROKEN at %d index(es): %s",
                len(broken),
                broken,
            )
        return is_valid, broken

    def tamper(self, entry_index: int, new_data: Any) -> Optional[Dict[str, Any]]:
        """Intentionally modify an entry's data (for demo / testing).

        This **breaks** the hash chain.  Subsequent calls to
        ``verify_chain()`` will detect the tampering.

        Args:
            entry_index: 0-based index of the entry to modify.
            new_data: The replacement data payload.

        Returns:
            The modified entry dict, or ``None`` if the index is out of
            range.
        """
        if entry_index < 0 or entry_index >= len(self._entries):
            logger.error(
                "TamperEvidentLog: tamper index %d out of range (0-%d)",
                entry_index,
                len(self._entries) - 1,
            )
            return None

        entry = self._entries[entry_index]
        old_data = entry["data"]
        entry["data"] = new_data

        logger.warning(
            "TamperEvidentLog: TAMPERED entry %d (old_data=%r → new_data=%r). "
            "Hash chain is now BROKEN.",
            entry_index,
            old_data,
            new_data,
        )
        return entry

    def get_entries(self) -> List[Dict[str, Any]]:
        """Return a shallow copy of all entries."""
        return list(self._entries)
