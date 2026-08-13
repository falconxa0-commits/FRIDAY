"""FRIDAY Age V — Governance.

The governance layer provides:
    - Constitution (immutable rules)
    - Policies (departmental rules)
    - Approval Gates (human-in-the-loop)
    - Delegation (authority transfer)
    - Founder Override (absolute authority)
    - Voting (council decisions)
    - Audit (immutable decision log)

This module is 100% additive. It uses Age IV PolicyEngine via
composition, not inheritance.
"""
from core.governance.constitution import Constitution, ConstitutionArticle
from core.governance.policy import Policy, PolicyDecision, GovernanceEngine
from core.governance.approval import ApprovalGate, ApprovalRequest

__all__ = [
    "Constitution", "ConstitutionArticle",
    "Policy", "PolicyDecision", "GovernanceEngine",
    "ApprovalGate", "ApprovalRequest",
]
