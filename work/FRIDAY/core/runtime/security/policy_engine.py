"""Runtime Security Policy Engine — capability-based access control.

Enforces security policies for runtime operations:
    - Capability-based permissions (what can be accessed)
    - Execution permissions (what can be executed)
    - Resource quotas (how much can be consumed)
    - Policy evaluation (rule-based decisions)

This is NOT a sandbox — it's a policy engine that gates access to
runtime capabilities. True sandboxing (subprocess + seccomp) is
planned for Age V.

Design principles:
    - **Deny by default**: Unknown capabilities are denied.
    - **Explicit grant**: Capabilities must be explicitly granted.
    - **Auditable**: Every policy decision is logged.
    - **Composable**: Policies can be combined and overridden.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("friday.runtime.security")


@dataclass
class Policy:
    """A security policy rule."""
    name: str
    effect: str = "deny"  # "allow" or "deny"
    capabilities: List[str] = field(default_factory=list)
    resources: List[str] = field(default_factory=list)
    conditions: Dict[str, Any] = field(default_factory=dict)

    def matches(self, capability: str, resource: str = "") -> bool:
        """Check if this policy applies to the given capability/resource."""
        cap_match = not self.capabilities or capability in self.capabilities
        res_match = not self.resources or resource in self.resources
        return cap_match and res_match


@dataclass
class PolicyDecision:
    """Result of a policy evaluation."""
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


class PolicyEngine:
    """Evaluates security policies for runtime operations.

    Usage::

        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="allow_chat",
            effect="allow",
            capabilities=["brain.chat"],
        ))
        
        decision = engine.evaluate("brain.chat")
        if decision.allowed:
            # proceed
    """

    def __init__(self):
        self._policies: List[Policy] = []
        self._granted_capabilities: Set[str] = set()
        self._denied_capabilities: Set[str] = set()
        self._decision_log: List[PolicyDecision] = []

    def add_policy(self, policy: Policy) -> None:
        """Add a security policy."""
        self._policies.append(policy)
        logger.info(f"Added policy: {policy.name} ({policy.effect})")

    def grant_capability(self, capability: str) -> None:
        """Explicitly grant a capability."""
        self._granted_capabilities.add(capability)
        self._denied_capabilities.discard(capability)
        logger.info(f"Granted capability: {capability}")

    def deny_capability(self, capability: str) -> None:
        """Explicitly deny a capability."""
        self._denied_capabilities.add(capability)
        self._granted_capabilities.discard(capability)
        logger.warning(f"Denied capability: {capability}")

    def evaluate(
        self, capability: str, resource: str = ""
    ) -> PolicyDecision:
        """Evaluate whether a capability is allowed.

        Evaluation order:
            1. Explicit deny → deny
            2. Explicit grant → allow
            3. Policy match → policy effect
            4. Default → deny (deny by default)
        """
        # Check explicit deny
        if capability in self._denied_capabilities:
            decision = PolicyDecision(
                allowed=False,
                reason=f"Explicitly denied: {capability}",
                policy_name="explicit_deny",
            )
            self._decision_log.append(decision)
            return decision

        # Check explicit grant
        if capability in self._granted_capabilities:
            decision = PolicyDecision(
                allowed=True,
                reason=f"Explicitly granted: {capability}",
                policy_name="explicit_grant",
            )
            self._decision_log.append(decision)
            return decision

        # Check policies
        for policy in self._policies:
            if policy.matches(capability, resource):
                decision = PolicyDecision(
                    allowed=(policy.effect == "allow"),
                    reason=f"Policy: {policy.name}",
                    policy_name=policy.name,
                )
                self._decision_log.append(decision)
                return decision

        # Deny by default
        decision = PolicyDecision(
            allowed=False,
            reason=f"No matching policy (deny by default): {capability}",
            policy_name="default_deny",
        )
        self._decision_log.append(decision)
        return decision

    def check_execution_permission(
        self, func_name: str, capability: str = ""
    ) -> bool:
        """Check if a function execution is permitted."""
        if capability:
            decision = self.evaluate(capability)
            return decision.allowed
        # No capability required → allow
        return True

    def get_decision_log(self, limit: int = 100) -> List[PolicyDecision]:
        """Get recent policy decisions."""
        return self._decision_log[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Get policy engine statistics."""
        allow_count = sum(1 for d in self._decision_log if d.allowed)
        deny_count = len(self._decision_log) - allow_count
        return {
            "total_policies": len(self._policies),
            "granted_capabilities": len(self._granted_capabilities),
            "denied_capabilities": len(self._denied_capabilities),
            "total_decisions": len(self._decision_log),
            "allow_count": allow_count,
            "deny_count": deny_count,
        }

    async def is_healthy(self) -> bool:
        """Check if the policy engine is healthy."""
        return True

    async def stop(self) -> None:
        """Stop the policy engine."""
        logger.info("Policy engine stopped")


# Default policies for FRIDAY runtime
def create_default_policy_engine() -> PolicyEngine:
    """Create a policy engine with default FRIDAY policies."""
    engine = PolicyEngine()

    # Allow brain capabilities
    engine.add_policy(Policy(
        name="allow_brain_chat",
        effect="allow",
        capabilities=["brain.chat", "brain.reasoning", "brain.creative"],
    ))

    # Allow memory access
    engine.add_policy(Policy(
        name="allow_memory",
        effect="allow",
        capabilities=["brain.memory", "memory.read", "memory.write"],
    ))

    # Allow runtime monitoring
    engine.add_policy(Policy(
        name="allow_monitoring",
        effect="allow",
        capabilities=["runtime.observe", "runtime.metrics"],
    ))

    # Deny dangerous capabilities by default
    engine.deny_capability("filesystem.delete")
    engine.deny_capability("network.raw")
    engine.deny_capability("process.spawn")

    return engine
