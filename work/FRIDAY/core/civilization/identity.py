"""Identity Engine — manages citizen identity and authentication.

Provides:
    - Unique ID generation
    - Identity verification
    - Capability token issuance
    - Identity lifecycle management

Uses Age IV PolicyEngine (if available) for capability validation.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.civilization.citizen import Citizen, CitizenID, CitizenRank

logger = logging.getLogger("friday.civilization.identity")


@dataclass
class CapabilityToken:
    """A token granting specific capabilities to a citizen."""
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    citizen_id: str = ""
    capabilities: list = field(default_factory=list)
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str = ""
    revoked: bool = False

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc).isoformat() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.revoked and not self.is_expired

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token": self.token[:16] + "...",
            "citizen_id": self.citizen_id,
            "capabilities": self.capabilities,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
            "is_valid": self.is_valid,
        }


class IdentityEngine:
    """Manages citizen identity and capability tokens.

    Usage::

        engine = IdentityEngine()
        token = engine.issue_token(citizen, ["memory.read", "memory.write"])
        if engine.verify_token(token.token, "memory.read"):
            # authorized
    """

    def __init__(self, policy_engine=None):
        self._tokens: Dict[str, CapabilityToken] = {}
        self._policy_engine = policy_engine

    def issue_token(
        self,
        citizen: Citizen,
        capabilities: list,
        expires_in_seconds: int = 3600,
    ) -> CapabilityToken:
        """Issue a capability token to a citizen.

        Args:
            citizen: The citizen receiving the token.
            capabilities: List of capability strings.
            expires_in_seconds: Token lifetime (default 1 hour).

        Returns:
            A CapabilityToken.
        """
        expires_at = ""
        if expires_in_seconds > 0:
            from datetime import timedelta
            expires_at = (datetime.now(timezone.utc) +
                          timedelta(seconds=expires_in_seconds)).isoformat()

        token = CapabilityToken(
            citizen_id=citizen.id.id,
            capabilities=capabilities,
            expires_at=expires_at,
        )
        self._tokens[token.token] = token

        logger.info(f"Issued token to {citizen.name} ({citizen.id.id[:8]}) — "
                     f"{len(capabilities)} capabilities")

        return token

    def verify_token(self, token_str: str, capability: str) -> bool:
        """Verify a token has a specific capability.

        Args:
            token_str: The token string.
            capability: The capability to check.

        Returns:
            True if the token is valid and has the capability.
        """
        token = self._tokens.get(token_str)
        if not token or not token.is_valid:
            return False
        if capability not in token.capabilities:
            return False

        # If policy engine is available, also check policy
        if self._policy_engine:
            decision = self._policy_engine.evaluate(capability)
            if not decision.allowed:
                return False

        return True

    def revoke_token(self, token_str: str) -> bool:
        """Revoke a capability token."""
        token = self._tokens.get(token_str)
        if not token:
            return False
        token.revoked = True
        logger.info(f"Revoked token for citizen {token.citizen_id[:8]}")
        return True

    def revoke_all_for_citizen(self, citizen_id: str) -> int:
        """Revoke all tokens for a citizen. Returns count revoked."""
        count = 0
        for token in self._tokens.values():
            if token.citizen_id == citizen_id and not token.revoked:
                token.revoked = True
                count += 1
        if count:
            logger.info(f"Revoked {count} tokens for citizen {citizen_id[:8]}")
        return count

    def get_token(self, token_str: str) -> Optional[CapabilityToken]:
        """Get a token by string."""
        return self._tokens.get(token_str)

    def list_tokens(self, citizen_id: Optional[str] = None) -> list:
        """List tokens, optionally filtered by citizen."""
        tokens = list(self._tokens.values())
        if citizen_id:
            tokens = [t for t in tokens if t.citizen_id == citizen_id]
        return tokens

    def cleanup_expired(self) -> int:
        """Remove expired tokens. Returns count removed."""
        expired = [k for k, t in self._tokens.items() if t.is_expired]
        for key in expired:
            del self._tokens[key]
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired tokens")
        return len(expired)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_tokens": len(self._tokens),
            "active_tokens": sum(1 for t in self._tokens.values() if t.is_valid),
            "revoked_tokens": sum(1 for t in self._tokens.values() if t.revoked),
            "expired_tokens": sum(1 for t in self._tokens.values() if t.is_expired),
        }

    async def is_healthy(self) -> bool:
        return True

    async def stop(self) -> None:
        logger.info("Identity engine stopped")
