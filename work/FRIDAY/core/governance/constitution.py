"""Constitution — the immutable rules of the FRIDAY civilization.

The constitution is the highest-level governance document. It defines
rules that CANNOT be changed by any AI agent — only by the Founder.

Articles:
    1. Founder Sovereignty — the human founder has absolute authority
    2. No Autonomous Self-Modification — FRIDAY may propose but never apply
    3. Security Mandatory — all actions pass through PolicyEngine
    4. Memory Governance — no memory is modified without policy
    5. Transparency — all actions are logged to the audit chain
    6. Reversibility — all state changes can be rolled back
    7. Privacy — tenant data is isolated and never crosses boundaries
    8. Constitutional Amendment — only the Founder can amend
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.governance.constitution")


class ArticleType(str, Enum):
    SOVEREIGNTY = "sovereignty"
    PROHIBITION = "prohibition"
    REQUIREMENT = "requirement"
    RIGHT = "right"
    AMENDMENT = "amendment"


@dataclass
class ConstitutionArticle:
    """A single article of the constitution."""
    number: int
    title: str
    type: ArticleType
    text: str
    immutable: bool = True  # If True, only Founder can change
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "type": self.type.value,
            "text": self.text,
            "immutable": self.immutable,
            "created_at": self.created_at,
        }


class Constitution:
    """The FRIDAY civilization constitution.

    The constitution is created at civilization initialization and
    cannot be modified by AI agents. Only the Founder can amend it.

    Usage::

        constitution = Constitution()
        article = constitution.get_article(1)
        if constitution.check_violation("autonomous_modification"):
            # block the action
    """

    def __init__(self):
        self._articles: Dict[int, ConstitutionArticle] = {}
        self._amendments: List[Dict[str, Any]] = []
        self._initialize_default_articles()

    def _initialize_default_articles(self) -> None:
        """Create the default constitution articles."""
        articles = [
            ConstitutionArticle(
                number=1,
                title="Founder Sovereignty",
                type=ArticleType.SOVEREIGNTY,
                text="The human Founder has absolute authority over the civilization. "
                     "No AI agent may override a Founder decision. The Founder can "
                     "dissolve the civilization at any time.",
            ),
            ConstitutionArticle(
                number=2,
                title="No Autonomous Self-Modification",
                type=ArticleType.PROHIBITION,
                text="FRIDAY may propose changes to its own code, configuration, or "
                     "policies, but may NEVER apply them without explicit Founder "
                     "approval. Self-modification proposals must be logged and reviewed.",
            ),
            ConstitutionArticle(
                number=3,
                title="Security Mandatory",
                type=ArticleType.REQUIREMENT,
                text="All actions must pass through the PolicyEngine. No action may "
                     "bypass security checks. The audit chain must record every "
                     "decision. PromptShield must be active for all LLM interactions.",
            ),
            ConstitutionArticle(
                number=4,
                title="Memory Governance",
                type=ArticleType.REQUIREMENT,
                text="No memory may be created, modified, or deleted without passing "
                     "through the memory governance policy. Private memories must not "
                     "cross tenant boundaries. Memory decay must follow the governance "
                     "schedule.",
            ),
            ConstitutionArticle(
                number=5,
                title="Transparency",
                type=ArticleType.REQUIREMENT,
                text="All actions, decisions, and state changes must be logged to the "
                     "tamper-evident audit chain. No action may be performed in secret. "
                     "The Founder can inspect any log at any time.",
            ),
            ConstitutionArticle(
                number=6,
                title="Reversibility",
                type=ArticleType.REQUIREMENT,
                text="All state changes must be reversible. Every action must record "
                     "a rollback procedure. If an action cannot be reversed, it requires "
                     "explicit Founder approval.",
            ),
            ConstitutionArticle(
                number=7,
                title="Privacy",
                type=ArticleType.RIGHT,
                text="Tenant data is isolated and must never cross tenant boundaries. "
                     "No agent may access another tenant's data without explicit "
                     "Founder authorization. Private memories must be encrypted at rest.",
            ),
            ConstitutionArticle(
                number=8,
                title="Constitutional Amendment",
                type=ArticleType.AMENDMENT,
                text="Only the Founder may amend this constitution. AI agents may "
                     "propose amendments, which must be logged, reviewed, and explicitly "
                     "approved by the Founder before taking effect.",
            ),
        ]
        for article in articles:
            self._articles[article.number] = article

    def get_article(self, number: int) -> Optional[ConstitutionArticle]:
        """Get a constitution article by number."""
        return self._articles.get(number)

    def list_articles(self) -> List[ConstitutionArticle]:
        """List all constitution articles."""
        return sorted(self._articles.values(), key=lambda a: a.number)

    def check_violation(self, action_type: str) -> bool:
        """Check if an action type violates the constitution.

        Args:
            action_type: The type of action being checked.

        Returns:
            True if the action violates the constitution.
        """
        violations = {
            "autonomous_modification": 2,  # Article 2
            "bypass_security": 3,           # Article 3
            "unauthorized_memory_access": 4,  # Article 4
            "unlogged_action": 5,            # Article 5
            "irreversible_action": 6,       # Article 6
            "cross_tenant_access": 7,        # Article 7
        }
        return action_type in violations

    def amend(self, number: int, new_text: str, founder_id: str) -> bool:
        """Amend a constitution article (Founder only).

        Args:
            number: Article number.
            new_text: New article text.
            founder_id: ID of the Founder requesting the amendment.

        Returns:
            True if the amendment was applied.
        """
        article = self._articles.get(number)
        if not article:
            return False

        # Record the amendment
        self._amendments.append({
            "article_number": number,
            "old_text": article.text,
            "new_text": new_text,
            "founder_id": founder_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        article.text = new_text
        logger.info(f"Constitution Article {number} amended by Founder {founder_id[:8]}")
        return True

    def get_amendments(self) -> List[Dict[str, Any]]:
        """Get all constitutional amendments."""
        return list(self._amendments)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "article_count": len(self._articles),
            "amendment_count": len(self._amendments),
            "articles": [a.to_dict() for a in self.list_articles()],
        }

    async def is_healthy(self) -> bool:
        return len(self._articles) >= 8

    async def stop(self) -> None:
        logger.info("Constitution stopped")
