"""Governance Engine — policy management and enforcement.

Manages policies that govern the civilization. Policies are hierarchical:
    Constitution (immutable) > High Council > Governor > Specialist

Uses Age IV PolicyEngine (if available) for capability checking.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.governance.policy")


@dataclass
class Policy:
    """A governance policy."""
    name: str
    effect: str = "deny"  # "allow" or "deny"
    capabilities: List[str] = field(default_factory=list)
    description: str = ""
    department: str = ""
    priority: int = 0  # higher = overrides lower
    created_by: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "effect": self.effect,
            "capabilities": self.capabilities,
            "description": self.description,
            "department": self.department,
            "priority": self.priority,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


@dataclass
class PolicyDecision:
    """Result of a governance evaluation."""
    allowed: bool
    reason: str = ""
    policy_name: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "policy_name": self.policy_name,
            "timestamp": self.timestamp,
        }


class GovernanceEngine:
    """Manages governance policies for the civilization.

    The GovernanceEngine wraps the Age IV PolicyEngine (if available)
    and adds hierarchical policy management.

    Usage::

        engine = GovernanceEngine()
        engine.add_policy(Policy(
            name="allow_memory_access",
            effect="allow",
            capabilities=["memory.read", "memory.write"],
            department="Memory",
        ))
        decision = engine.evaluate("memory.read")
        if decision.allowed:
            # proceed
    """

    def __init__(self, policy_engine=None):
        self._policies: List[Policy] = []
        self._policy_engine = policy_engine  # Age IV PolicyEngine
        self._decision_log: List[PolicyDecision] = []

    def add_policy(self, policy: Policy) -> None:
        """Add a governance policy."""
        self._policies.append(policy)
        self._policies.sort(key=lambda p: -p.priority)  # highest priority first
        logger.info(f"Added policy: {policy.name} ({policy.effect}) — priority={policy.priority}")

    def remove_policy(self, name: str) -> bool:
        """Remove a policy by name."""
        before = len(self._policies)
        self._policies = [p for p in self._policies if p.name != name]
        return len(self._policies) < before

    def evaluate(self, capability: str, citizen_rank: int = 0) -> PolicyDecision:
        """Evaluate whether a capability is allowed.

        Evaluation order:
            1. Age IV PolicyEngine (if available)
            2. Governance policies (highest priority first)
            3. Default: deny

        Args:
            capability: The capability to check.
            citizen_rank: The rank of the requesting citizen (for authority check).

        Returns:
            A PolicyDecision.
        """
        # Check Age IV PolicyEngine first
        if self._policy_engine:
            decision = self._policy_engine.evaluate(capability)
            if not decision.allowed:
                result = PolicyDecision(
                    allowed=False,
                    reason=f"Blocked by Age IV policy: {decision.reason}",
                    policy_name="age_iv_policy",
                )
                self._decision_log.append(result)
                return result

        # Check governance policies (highest priority first)
        for policy in self._policies:
            if policy.capabilities and capability not in policy.capabilities:
                continue
            result = PolicyDecision(
                allowed=(policy.effect == "allow"),
                reason=f"Policy: {policy.name}",
                policy_name=policy.name,
            )
            self._decision_log.append(result)
            return result

        # Default: deny
        result = PolicyDecision(
            allowed=False,
            reason=f"No matching policy (deny by default): {capability}",
            policy_name="default_deny",
        )
        self._decision_log.append(result)
        return result

    def get_decision_log(self, limit: int = 100) -> List[PolicyDecision]:
        """Get recent policy decisions."""
        return self._decision_log[-limit:]

    def list_policies(self, department: Optional[str] = None) -> List[Policy]:
        """List policies, optionally filtered by department."""
        if department:
            return [p for p in self._policies if p.department == department]
        return list(self._policies)

    def get_stats(self) -> Dict[str, Any]:
        allow_count = sum(1 for d in self._decision_log if d.allowed)
        deny_count = len(self._decision_log) - allow_count
        return {
            "total_policies": len(self._policies),
            "total_decisions": len(self._decision_log),
            "allow_count": allow_count,
            "deny_count": deny_count,
        }

    async def is_healthy(self) -> bool:
        return True

    async def stop(self) -> None:
        logger.info("Governance engine stopped")
