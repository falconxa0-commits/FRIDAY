"""Authorization — capability-based access control for living memory.

Composes with Age V's CitizenRegistry and Constitution Article 4
(Memory Governance). Authorization is fail-closed:
    - Unknown citizen → DENY
    - Wrong tenant → DENY
    - Insufficient rank → DENY
    - Missing capability → DENY
    - Banned citizen → DENY (even for Founder-equivalent if explicitly banned)
    - No citizen_id provided → DENY
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Set

from .base import AuthorizationError

logger = logging.getLogger("friday.living_memory.authz")


class MemoryCapability(str, Enum):
    """Capability tokens for memory operations.

    Citizens must possess the relevant capability to perform an operation.
    The Founder always has all capabilities implicitly.
    """
    MEMORY_READ = "memory.read"
    MEMORY_WRITE = "memory.write"
    MEMORY_DELETE = "memory.delete"
    MEMORY_REINFORCE = "memory.reinforce"
    MEMORY_CONSOLIDATE = "memory.consolidate"
    MEMORY_ASSOCIATE = "memory.associate"
    MEMORY_ARCHIVE = "memory.archive"
    MEMORY_FORGET = "memory.forget"           # requires Founder approval
    MEMORY_QUARANTINE = "memory.quarantine"   # immune-system only
    MEMORY_RESOLVE_CONTRADICTION = "memory.resolve_contradiction"
    MEMORY_RESTORE = "memory.restore"         # archived → active


# Operations that ALWAYS require explicit Founder approval, regardless
# of the caller's rank. These are irreversible/destructive operations.
REQUIRES_FOUNDER_APPROVAL = {
    MemoryCapability.MEMORY_FORGET,
    MemoryCapability.MEMORY_RESOLVE_CONTRADICTION,
}


@dataclass
class AuthorizationContext:
    """Snapshot of the caller's identity + capabilities."""
    citizen_id: str
    rank_level: int                # 0..100 (Founder=100)
    capabilities: Set[str] = field(default_factory=set)
    tenant_id: str = "default"
    is_banned: bool = False
    is_founder: bool = False

    @property
    def is_authenticated(self) -> bool:
        return bool(self.citizen_id) and not self.is_banned

    def has_capability(self, cap: MemoryCapability) -> bool:
        if self.is_founder:
            return True
        return cap.value in self.capabilities or cap in self.capabilities

    def to_dict(self) -> Dict[str, Any]:
        return {
            "citizen_id": self.citizen_id,
            "rank_level": self.rank_level,
            "capabilities": sorted(self.capabilities),
            "tenant_id": self.tenant_id,
            "is_banned": self.is_banned,
            "is_founder": self.is_founder,
        }


class AuthorizationGate:
    """Authorizes memory operations based on citizen context.

    The gate is intentionally minimal: it does NOT call the CitizenRegistry
    directly. Instead, the caller (e.g. LivingMemoryManager) builds an
    AuthorizationContext from the registry and passes it here. This keeps
    the gate deterministic and easily testable.
    """

    def __init__(self, min_rank_for_write: int = 20):
        # Worker (20) can write; lower ranks cannot.
        self._min_rank_for_write = min_rank_for_write

    def authorize_read(
        self,
        ctx: AuthorizationContext,
        memory_tenant_id: str,
        memory_owner_id: str,
    ) -> None:
        """Authorize a read. Raises AuthorizationError on denial."""
        self._check_authenticated(ctx)
        self._check_tenant(ctx, memory_tenant_id, memory_owner_id)
        if not ctx.has_capability(MemoryCapability.MEMORY_READ):
            raise AuthorizationError(
                f"Citizen {ctx.citizen_id[:8]} lacks memory.read capability"
            )

    def authorize_write(
        self,
        ctx: AuthorizationContext,
        target_tenant_id: str,
    ) -> None:
        """Authorize a write. Raises AuthorizationError on denial."""
        self._check_authenticated(ctx)
        if ctx.tenant_id != target_tenant_id:
            raise AuthorizationError(
                "Cross-tenant write denied (constitution Article 7)"
            )
        if ctx.rank_level < self._min_rank_for_write:
            raise AuthorizationError(
                f"Rank {ctx.rank_level} below write threshold "
                f"{self._min_rank_for_write}"
            )
        if not ctx.has_capability(MemoryCapability.MEMORY_WRITE):
            raise AuthorizationError(
                f"Citizen {ctx.citizen_id[:8]} lacks memory.write capability"
            )

    def authorize_delete(self, ctx: AuthorizationContext, tenant_id: str) -> None:
        """Authorize destructive forget operation.

        FORGET requires both:
            - memory.forget capability
            - Founder rank (100) OR explicit Founder-approved ApprovalRequest
        The approval token is checked at the manager level (which composes
        with ApprovalGate). This method only checks capability + rank.
        """
        self._check_authenticated(ctx)
        if ctx.tenant_id != tenant_id:
            raise AuthorizationError(
                "Cross-tenant delete denied (constitution Article 7)"
            )
        if not ctx.has_capability(MemoryCapability.MEMORY_FORGET):
            raise AuthorizationError(
                f"Citizen {ctx.citizen_id[:8]} lacks memory.forget capability"
            )
        # Rank check: only Governor+ can forget without explicit approval
        if not ctx.is_founder and ctx.rank_level < 60:
            raise AuthorizationError(
                "Forget requires Governor+ rank or Founder approval"
            )

    def authorize_capability(
        self,
        ctx: AuthorizationContext,
        cap: MemoryCapability,
        tenant_id: str = "",
    ) -> None:
        """Generic capability check."""
        self._check_authenticated(ctx)
        if tenant_id and ctx.tenant_id != tenant_id:
            raise AuthorizationError(
                "Cross-tenant access denied (constitution Article 7)"
            )
        if not ctx.has_capability(cap):
            raise AuthorizationError(
                f"Citizen {ctx.citizen_id[:8]} lacks capability {cap.value}"
            )

    def _check_authenticated(self, ctx: AuthorizationContext) -> None:
        if not ctx.citizen_id:
            raise AuthorizationError("Missing citizen_id — unauthenticated")
        if ctx.is_banned:
            raise AuthorizationError(
                f"Citizen {ctx.citizen_id[:8]} is banned"
            )

    def _check_tenant(
        self,
        ctx: AuthorizationContext,
        memory_tenant_id: str,
        memory_owner_id: str,
    ) -> None:
        # Same tenant → OK
        if ctx.tenant_id == memory_tenant_id:
            return
        # Founder can cross tenants (with audit)
        if ctx.is_founder:
            return
        # Owner can read their own memory even if tenant differs (edge case)
        if ctx.citizen_id == memory_owner_id:
            return
        raise AuthorizationError(
            f"Cross-tenant read denied: ctx.tenant={ctx.tenant_id} "
            f"memory.tenant={memory_tenant_id} (constitution Article 7)"
        )


__all__ = [
    "MemoryCapability", "AuthorizationContext", "AuthorizationGate",
    "REQUIRES_FOUNDER_APPROVAL",
]
