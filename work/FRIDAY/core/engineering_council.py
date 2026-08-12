"""Engineering Council — independent executive AI reviewers.

Every major engineering decision receives independent reviews from
multiple executive perspectives. The council produces:
    - Agreement / disagreement
    - Risk analysis
    - Confidence scores
    - Tradeoff analysis
    - Alternative solutions
    - Final consensus

Design principles:
    - **Independent**: Each reviewer has no knowledge of other reviews.
    - **Structured**: Every review follows the same format.
    - **Quantified**: Confidence scores are 0-100.
    - **Actionable**: Consensus includes a recommendation.

Usage::

    council = get_engineering_council()
    decision = await council.review(
        title="Adopt Redis for distributed locking",
        description="Replace asyncio.Lock with Redis-based distributed lock",
        context="Need multi-process coordination for horizontal scaling",
    )
    # decision.reviews → list of CouncilReview
    # decision.consensus → final recommendation
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.council")


class ReviewerRole(str, Enum):
    CHIEF_ARCHITECT = "chief_architect"
    CHIEF_SECURITY_OFFICER = "chief_security_officer"
    CHIEF_DEVOPS_OFFICER = "chief_devops_officer"
    CHIEF_PERFORMANCE_OFFICER = "chief_performance_officer"
    CHIEF_RESEARCH_OFFICER = "chief_research_officer"
    CHIEF_QA_OFFICER = "chief_qa_officer"
    CHIEF_PLATFORM_OFFICER = "chief_platform_officer"
    CHIEF_INFRASTRUCTURE_OFFICER = "chief_infrastructure_officer"
    CHIEF_RELIABILITY_OFFICER = "chief_reliability_officer"
    CHIEF_DOCUMENTATION_OFFICER = "chief_documentation_officer"
    CHIEF_PRODUCT_OFFICER = "chief_product_officer"
    CHIEF_COMPLIANCE_OFFICER = "chief_compliance_officer"


class Verdict(str, Enum):
    APPROVE = "approve"
    APPROVE_WITH_CONCERNS = "approve_with_concerns"
    REJECT = "reject"
    NEEDS_MORE_INFO = "needs_more_info"


@dataclass
class CouncilReview:
    """A single reviewer's assessment."""
    reviewer: str
    role: ReviewerRole
    verdict: Verdict
    confidence: int  # 0-100
    risks: List[str] = field(default_factory=list)
    concerns: List[str] = field(default_factory=list)
    alternatives: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        d["verdict"] = self.verdict.value
        return d


@dataclass
class CouncilDecision:
    """The final consensus of the engineering council."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    context: str = ""
    reviews: List[CouncilReview] = field(default_factory=list)
    consensus: Verdict = Verdict.NEEDS_MORE_INFO
    consensus_confidence: int = 0
    overall_risks: List[str] = field(default_factory=list)
    recommendation: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def summary(self) -> str:
        approvals = sum(1 for r in self.reviews if r.verdict in (Verdict.APPROVE, Verdict.APPROVE_WITH_CONCERNS))
        rejections = sum(1 for r in self.reviews if r.verdict == Verdict.REJECT)
        avg_confidence = sum(r.confidence for r in self.reviews) / len(self.reviews) if self.reviews else 0
        return (
            f"{len(self.reviews)} reviews: {approvals} approve, {rejections} reject, "
            f"avg confidence {avg_confidence:.0f}%"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "context": self.context,
            "reviews": [r.to_dict() for r in self.reviews],
            "consensus": self.consensus.value,
            "consensus_confidence": self.consensus_confidence,
            "overall_risks": self.overall_risks,
            "recommendation": self.recommendation,
            "created_at": self.created_at,
            "summary": self.summary,
        }


class EngineeringCouncil:
    """The engineering council that reviews major decisions.

    Each reviewer assesses the decision from their domain perspective.
    The council then synthesizes a consensus.
    """

    def __init__(self):
        self._reviewers: Dict[ReviewerRole, str] = {
            ReviewerRole.CHIEF_ARCHITECT: "Chief Architect",
            ReviewerRole.CHIEF_SECURITY_OFFICER: "Chief Security Officer",
            ReviewerRole.CHIEF_DEVOPS_OFFICER: "Chief DevOps Officer",
            ReviewerRole.CHIEF_PERFORMANCE_OFFICER: "Chief Performance Officer",
            ReviewerRole.CHIEF_RESEARCH_OFFICER: "Chief Research Officer",
            ReviewerRole.CHIEF_QA_OFFICER: "Chief QA Officer",
            ReviewerRole.CHIEF_PLATFORM_OFFICER: "Chief Platform Officer",
            ReviewerRole.CHIEF_INFRASTRUCTURE_OFFICER: "Chief Infrastructure Officer",
            ReviewerRole.CHIEF_RELIABILITY_OFFICER: "Chief Reliability Officer",
            ReviewerRole.CHIEF_DOCUMENTATION_OFFICER: "Chief Documentation Officer",
            ReviewerRole.CHIEF_PRODUCT_OFFICER: "Chief Product Officer",
            ReviewerRole.CHIEF_COMPLIANCE_OFFICER: "Chief Compliance Officer",
        }

    async def review(
        self,
        title: str,
        description: str,
        context: str = "",
        reviewers: Optional[List[ReviewerRole]] = None,
    ) -> CouncilDecision:
        """Submit a decision for council review.

        Args:
            title: Short title of the decision.
            description: What is being proposed.
            context: Background information.
            reviewers: Which reviewers to include (default: all).
        """
        if reviewers is None:
            reviewers = list(self._reviewers.keys())

        decision = CouncilDecision(
            title=title,
            description=description,
            context=context,
        )

        # Each reviewer assesses independently (in parallel)
        review_tasks = [
            self._review_from_perspective(role, decision)
            for role in reviewers
        ]
        reviews = await asyncio.gather(*review_tasks)
        decision.reviews = list(reviews)

        # Synthesize consensus
        decision = self._synthesize_consensus(decision)

        return decision

    async def _review_from_perspective(
        self, role: ReviewerRole, decision: CouncilDecision
    ) -> CouncilReview:
        """Generate a review from a specific executive perspective.

        Domain-specific risk/concern/recommendation detection is delegated
        to :meth:`_review_security`, :meth:`_review_architecture`,
        :meth:`_review_performance`, :meth:`_review_reliability`,
        :meth:`_review_qa`, :meth:`_review_devops`, and
        :meth:`_review_compliance` so this method is a simple dispatcher.
        The final verdict is computed by :meth:`_determine_verdict`.
        """
        name = self._reviewers[role]
        desc_lower = (decision.description + " " + decision.context).lower()

        # Domain-specific risk patterns
        risks: List[str] = []
        concerns: List[str] = []
        alternatives: List[str] = []
        recommendations: List[str] = []
        confidence = 70  # default

        # Dispatch to the appropriate domain reviewer. Each helper
        # mutates the lists above and returns an updated confidence.
        if role == ReviewerRole.CHIEF_SECURITY_OFFICER:
            confidence = self._review_security(
                desc_lower, risks, concerns, alternatives, recommendations, confidence,
            )
        elif role == ReviewerRole.CHIEF_ARCHITECT:
            confidence = self._review_architecture(
                desc_lower, risks, concerns, alternatives, recommendations, confidence,
            )
        elif role == ReviewerRole.CHIEF_PERFORMANCE_OFFICER:
            confidence = self._review_performance(
                desc_lower, risks, concerns, alternatives, recommendations, confidence,
            )
        elif role == ReviewerRole.CHIEF_RELIABILITY_OFFICER:
            confidence = self._review_reliability(
                desc_lower, risks, concerns, alternatives, recommendations, confidence,
            )
        elif role == ReviewerRole.CHIEF_QA_OFFICER:
            recommendations.append("Add regression tests before merging")
            recommendations.append("Verify no existing tests break")
            confidence = 75
        elif role == ReviewerRole.CHIEF_DEVOPS_OFFICER:
            confidence = self._review_devops(
                desc_lower, risks, concerns, alternatives, recommendations, confidence,
            )
        elif role == ReviewerRole.CHIEF_COMPLIANCE_OFFICER:
            confidence = self._review_compliance(
                desc_lower, risks, concerns, alternatives, recommendations, confidence,
            )

        verdict = self._determine_verdict(risks, concerns)

        return CouncilReview(
            reviewer=name,
            role=role,
            verdict=verdict,
            confidence=confidence,
            risks=risks,
            concerns=concerns,
            alternatives=alternatives,
            recommendations=recommendations,
            reasoning=f"Reviewed from {name} perspective.",
        )

    # ------------------------------------------------------------------
    # Domain-specific reviewers — each has a single responsibility
    # (matching one ReviewerRole) and mutates the supplied lists in-place.
    # They return the updated confidence so the dispatcher can stay tiny.
    # ------------------------------------------------------------------
    @staticmethod
    def _review_security(
        desc_lower: str,
        risks: List[str],
        concerns: List[str],
        alternatives: List[str],
        recommendations: List[str],
        confidence: int,
    ) -> int:
        if any(w in desc_lower for w in ["auth", "token", "secret", "key"]):
            risks.append("Key management complexity")
            concerns.append("Secret rotation may cause downtime")
            recommendations.append("Implement zero-downtime key rotation")
            confidence = 80
        if any(w in desc_lower for w in ["network", "port", "external"]):
            risks.append("Increased attack surface")
            confidence = 75
        if any(w in desc_lower for w in ["sandbox", "isolation"]):
            recommendations.append("Use seccomp + namespace isolation")
            confidence = 85
        return confidence

    @staticmethod
    def _review_architecture(
        desc_lower: str,
        risks: List[str],
        concerns: List[str],
        alternatives: List[str],
        recommendations: List[str],
        confidence: int,
    ) -> int:
        if any(w in desc_lower for w in ["singleton", "global", "module-level"]):
            risks.append("Breaking change to singleton consumers")
            concerns.append("Requires dependency injection refactor across all callers")
            alternatives.append("Use factory pattern with lazy initialization")
            confidence = 65
        if any(w in desc_lower for w in ["decompose", "refactor", "split"]):
            recommendations.append("Maintain backward-compatible facade during transition")
            confidence = 80
        return confidence

    @staticmethod
    def _review_performance(
        desc_lower: str,
        risks: List[str],
        concerns: List[str],
        alternatives: List[str],
        recommendations: List[str],
        confidence: int,
    ) -> int:
        if any(w in desc_lower for w in ["cache", "index", "optimize"]):
            recommendations.append("Benchmark before and after")
            confidence = 85
        if any(w in desc_lower for w in ["async", "thread", "concurrent"]):
            risks.append("Potential deadlock under high concurrency")
            recommendations.append("Stress test with 100+ concurrent requests")
            confidence = 75
        return confidence

    @staticmethod
    def _review_reliability(
        desc_lower: str,
        risks: List[str],
        concerns: List[str],
        alternatives: List[str],
        recommendations: List[str],
        confidence: int,
    ) -> int:
        if any(w in desc_lower for w in ["persistence", "disk", "file"]):
            risks.append("Data loss on crash if not atomic")
            recommendations.append("Use write-then-rename pattern")
            confidence = 80
        if any(w in desc_lower for w in ["distributed", "cluster", "scale"]):
            risks.append("Split-brain scenarios")
            concerns.append("Network partition handling")
            confidence = 60
        return confidence

    @staticmethod
    def _review_devops(
        desc_lower: str,
        risks: List[str],
        concerns: List[str],
        alternatives: List[str],
        recommendations: List[str],
        confidence: int,
    ) -> int:
        if any(w in desc_lower for w in ["docker", "container", "deploy"]):
            recommendations.append("Update Dockerfile and docker-compose")
            recommendations.append("Add health check endpoint")
            confidence = 80
        concerns.append("Deployment rollback plan needed")
        return confidence

    @staticmethod
    def _review_compliance(
        desc_lower: str,
        risks: List[str],
        concerns: List[str],
        alternatives: List[str],
        recommendations: List[str],
        confidence: int,
    ) -> int:
        if any(w in desc_lower for w in ["audit", "log", "receipt"]):
            recommendations.append("Ensure NDPR/GDPR compliance")
            confidence = 75
        return confidence

    @staticmethod
    def _determine_verdict(risks: List[str], concerns: List[str]) -> Verdict:
        """Translate the collected risks/concerns into a Verdict bucket.

        * no risks and no concerns → APPROVE;
        * at most 2 *potential* risks → APPROVE_WITH_CONCERNS;
        * more than 3 risks → REJECT;
        * otherwise → APPROVE_WITH_CONCERNS.
        """
        if len(risks) == 0 and len(concerns) == 0:
            return Verdict.APPROVE
        if len(risks) <= 2 and all(r.lower().startswith("potential") for r in risks):
            return Verdict.APPROVE_WITH_CONCERNS
        if len(risks) > 3:
            return Verdict.REJECT
        return Verdict.APPROVE_WITH_CONCERNS

    def _synthesize_consensus(self, decision: CouncilDecision) -> CouncilDecision:
        """Synthesize individual reviews into a consensus."""
        if not decision.reviews:
            decision.consensus = Verdict.NEEDS_MORE_INFO
            decision.consensus_confidence = 0
            return decision

        approvals = sum(1 for r in decision.reviews if r.verdict == Verdict.APPROVE)
        concerns = sum(1 for r in decision.reviews if r.verdict == Verdict.APPROVE_WITH_CONCERNS)
        rejections = sum(1 for r in decision.reviews if r.verdict == Verdict.REJECT)
        total = len(decision.reviews)

        # Collect all risks
        all_risks = []
        for r in decision.reviews:
            all_risks.extend(r.risks)
        decision.overall_risks = list(set(all_risks))  # deduplicate

        # Consensus logic
        if rejections > total / 3:
            decision.consensus = Verdict.REJECT
            decision.recommendation = "Rejected: too many reviewers disagree."
        elif approvals >= total * 0.7:
            decision.consensus = Verdict.APPROVE
            decision.recommendation = "Approved with high confidence."
        elif (approvals + concerns) >= total * 0.8:
            decision.consensus = Verdict.APPROVE_WITH_CONCERNS
            decision.recommendation = "Approved with concerns. Address risks before implementation."
        else:
            decision.consensus = Verdict.NEEDS_MORE_INFO
            decision.recommendation = "Insufficient consensus. Provide more context."

        # Average confidence
        decision.consensus_confidence = int(
            sum(r.confidence for r in decision.reviews) / total
        )

        return decision


# Singleton
_council: Optional[EngineeringCouncil] = None


def get_engineering_council() -> EngineeringCouncil:
    global _council
    if _council is None:
        _council = EngineeringCouncil()
    return _council
