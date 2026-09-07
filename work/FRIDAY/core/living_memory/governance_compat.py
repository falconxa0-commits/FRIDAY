"""Governance compatibility helpers.

Small adapters so the LivingMemoryManager can compose with Age V's
ApprovalGate without taking a hard import dependency on its internal
types. This keeps the living_memory package importable even when the
Age V governance package isn't available (e.g. during isolated unit tests).
"""
from __future__ import annotations

from typing import Any


def is_approval_approved(req: Any) -> bool:
    """Return True iff the given ApprovalRequest is in APPROVED status.

    Works with core.governance.approval.ApprovalRequest (which has a
    `status` enum attribute whose value is the string "approved").
    """
    if req is None:
        return False
    status = getattr(req, "status", None)
    if status is None:
        return False
    # ApprovalStatus is a str-Enum; comparing by .value or str() both work
    val = status.value if hasattr(status, "value") else str(status)
    return val == "approved"


__all__ = ["is_approval_approved"]
