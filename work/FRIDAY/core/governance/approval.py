"""Approval Gate — human-in-the-loop approval for critical actions.

Provides:
    - Approval requests (async, with timeout)
    - Founder override (instant approve/reject)
    - Council voting (for non-Founder approvals)
    - Audit trail (all decisions logged)

Uses Age IV ActionLedger (if available) for audit.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.governance.approval")


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class ApprovalRequest:
    """A request for human approval of an action."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    citizen_id: str = ""
    action: str = ""
    description: str = ""
    risk_level: str = "medium"
    params: Dict[str, Any] = field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    approved_by: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: str = ""
    timeout_seconds: int = 300  # 5 min default
    votes: Dict[str, str] = field(default_factory=dict)  # citizen_id → "approve"/"reject"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "citizen_id": self.citizen_id,
            "action": self.action,
            "description": self.description,
            "risk_level": self.risk_level,
            "status": self.status.value,
            "approved_by": self.approved_by,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "timeout_seconds": self.timeout_seconds,
            "votes": self.votes,
        }


class ApprovalGate:
    """Manages approval requests for the civilization.

    Usage::

        gate = ApprovalGate()
        request = gate.request(
            citizen_id="agent-123",
            action="deploy_code",
            description="Deploy v5.0.0 to production",
            risk_level="critical",
        )
        # ... wait for approval ...
        if gate.approve(request.id, approver_id="founder"):
            # proceed
    """

    def __init__(self, event_bus=None):
        self._requests: Dict[str, ApprovalRequest] = {}
        self._events: Dict[str, asyncio.Event] = {}
        self._event_bus = event_bus

    def request(
        self,
        citizen_id: str,
        action: str,
        description: str = "",
        risk_level: str = "medium",
        params: Optional[Dict] = None,
        timeout_seconds: int = 300,
    ) -> ApprovalRequest:
        """Create an approval request.

        Args:
            citizen_id: The citizen requesting approval.
            action: The action to be approved.
            description: Human-readable description.
            risk_level: "low", "medium", "high", "critical".
            params: Action parameters.
            timeout_seconds: How long to wait (default 5 min).

        Returns:
            The ApprovalRequest (pending).
        """
        req = ApprovalRequest(
            citizen_id=citizen_id,
            action=action,
            description=description,
            risk_level=risk_level,
            params=params or {},
            timeout_seconds=timeout_seconds,
        )
        self._requests[req.id] = req
        self._events[req.id] = asyncio.Event()

        logger.info(f"Approval requested: {action} (risk={risk_level}) — id={req.id[:8]}")
        return req

    async def wait_for_approval(self, request_id: str, timeout: int = 300) -> bool:
        """Wait for an approval request to be resolved.

        Args:
            request_id: The approval request ID.
            timeout: Maximum seconds to wait.

        Returns:
            True if approved, False if rejected/timeout.
        """
        event = self._events.get(request_id)
        if not event:
            return False

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            req = self._requests.get(request_id)
            if req and req.status == ApprovalStatus.PENDING:
                req.status = ApprovalStatus.TIMEOUT
                req.resolved_at = datetime.now(timezone.utc).isoformat()
            return False

        req = self._requests.get(request_id)
        return req is not None and req.status == ApprovalStatus.APPROVED

    def approve(self, request_id: str, approver_id: str) -> bool:
        """Approve a request.

        Args:
            request_id: The request to approve.
            approver_id: The citizen approving.

        Returns:
            True if the approval was recorded.
        """
        req = self._requests.get(request_id)
        if not req or req.status != ApprovalStatus.PENDING:
            return False

        req.status = ApprovalStatus.APPROVED
        req.approved_by = approver_id
        req.resolved_at = datetime.now(timezone.utc).isoformat()

        event = self._events.get(request_id)
        if event:
            event.set()

        logger.info(f"Approval {request_id[:8]} APPROVED by {approver_id[:8]}")
        return True

    def reject(self, request_id: str, rejector_id: str) -> bool:
        """Reject a request."""
        req = self._requests.get(request_id)
        if not req or req.status != ApprovalStatus.PENDING:
            return False

        req.status = ApprovalStatus.REJECTED
        req.approved_by = rejector_id
        req.resolved_at = datetime.now(timezone.utc).isoformat()

        event = self._events.get(request_id)
        if event:
            event.set()

        logger.info(f"Approval {request_id[:8]} REJECTED by {rejector_id[:8]}")
        return True

    def founder_override(self, request_id: str, approve: bool, founder_id: str) -> bool:
        """Founder can instantly approve or reject any request."""
        if approve:
            return self.approve(request_id, founder_id)
        else:
            return self.reject(request_id, founder_id)

    def vote(self, request_id: str, voter_id: str, vote: str) -> bool:
        """Cast a vote on a request (council voting).

        Args:
            request_id: The request being voted on.
            voter_id: The citizen voting.
            vote: "approve" or "reject".

        Returns:
            True if the vote was recorded.
        """
        req = self._requests.get(request_id)
        if not req or req.status != ApprovalStatus.PENDING:
            return False
        req.votes[voter_id] = vote

        # Check if majority reached (for council voting)
        approve_votes = sum(1 for v in req.votes.values() if v == "approve")
        reject_votes = sum(1 for v in req.votes.values() if v == "reject")
        total_votes = len(req.votes)

        if total_votes >= 3:  # minimum 3 votes for council decision
            if approve_votes > reject_votes:
                self.approve(request_id, "council")
            elif reject_votes > approve_votes:
                self.reject(request_id, "council")

        return True

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        """Get an approval request by ID."""
        return self._requests.get(request_id)

    def list_requests(self, status: Optional[ApprovalStatus] = None) -> List[ApprovalRequest]:
        """List approval requests, optionally filtered by status."""
        requests = list(self._requests.values())
        if status:
            requests = [r for r in requests if r.status == status]
        return requests

    def get_stats(self) -> Dict[str, Any]:
        by_status = {}
        for r in self._requests.values():
            by_status[r.status.value] = by_status.get(r.status.value, 0) + 1
        return {
            "total_requests": len(self._requests),
            "by_status": by_status,
            "pending": by_status.get("pending", 0),
        }

    async def is_healthy(self) -> bool:
        return True

    async def stop(self) -> None:
        # Cancel all pending requests
        for req in self._requests.values():
            if req.status == ApprovalStatus.PENDING:
                req.status = ApprovalStatus.CANCELLED
                req.resolved_at = datetime.now(timezone.utc).isoformat()
                event = self._events.get(req.id)
                if event:
                    event.set()
        logger.info("Approval gate stopped")
