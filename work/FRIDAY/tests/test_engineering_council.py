"""Tests for the engineering council."""
import asyncio
import pytest

from core.engineering_council import (
    EngineeringCouncil, CouncilDecision, CouncilReview,
    ReviewerRole, Verdict, get_engineering_council,
)


@pytest.fixture()
def council():
    return EngineeringCouncil()


class TestReview:
    def test_review_returns_decision(self, council):
        decision = asyncio.run(council.review(
            title="Test decision",
            description="A test proposal",
            context="Testing the council",
        ))
        assert isinstance(decision, CouncilDecision)
        assert len(decision.reviews) > 0

    def test_review_with_subset_of_reviewers(self, council):
        decision = asyncio.run(council.review(
            title="Subset test",
            description="Test",
            reviewers=[ReviewerRole.CHIEF_ARCHITECT, ReviewerRole.CHIEF_SECURITY_OFFICER],
        ))
        assert len(decision.reviews) == 2

    def test_security_reviewer_flags_auth_risks(self, council):
        decision = asyncio.run(council.review(
            title="Auth change",
            description="Change the authentication token system",
            reviewers=[ReviewerRole.CHIEF_SECURITY_OFFICER],
        ))
        sec_review = decision.reviews[0]
        assert sec_review.role == ReviewerRole.CHIEF_SECURITY_OFFICER
        assert len(sec_review.risks) > 0 or len(sec_review.recommendations) > 0

    def test_architect_reviewer_flags_singleton_risks(self, council):
        decision = asyncio.run(council.review(
            title="Singleton refactor",
            description="Remove module-level singleton pattern",
            reviewers=[ReviewerRole.CHIEF_ARCHITECT],
        ))
        arch_review = decision.reviews[0]
        assert any("singleton" in r.lower() or "breaking" in r.lower() for r in arch_review.risks + arch_review.concerns)

    def test_consensus_synthesized(self, council):
        decision = asyncio.run(council.review(
            title="Consensus test",
            description="A safe, well-understood change",
        ))
        assert decision.consensus in Verdict
        assert 0 <= decision.consensus_confidence <= 100

    def test_decision_summary(self, council):
        decision = asyncio.run(council.review(
            title="Summary test",
            description="Test",
        ))
        assert "reviews" in decision.summary
        assert "confidence" in decision.summary

    def test_decision_to_dict(self, council):
        decision = asyncio.run(council.review(
            title="Dict test",
            description="Test",
        ))
        d = decision.to_dict()
        assert "id" in d
        assert "reviews" in d
        assert "consensus" in d
        assert "executive_summary" not in d or isinstance(d.get("recommendation"), str)


class TestConsensusLogic:
    def test_all_approvals_yields_approve(self, council):
        decision = CouncilDecision(reviews=[
            CouncilReview(reviewer="A", role=ReviewerRole.CHIEF_ARCHITECT,
                          verdict=Verdict.APPROVE, confidence=90),
            CouncilReview(reviewer="B", role=ReviewerRole.CHIEF_SECURITY_OFFICER,
                          verdict=Verdict.APPROVE, confidence=85),
        ])
        result = council._synthesize_consensus(decision)
        assert result.consensus == Verdict.APPROVE

    def test_many_rejections_yields_reject(self, council):
        decision = CouncilDecision(reviews=[
            CouncilReview(reviewer="A", role=ReviewerRole.CHIEF_ARCHITECT,
                          verdict=Verdict.REJECT, confidence=80),
            CouncilReview(reviewer="B", role=ReviewerRole.CHIEF_SECURITY_OFFICER,
                          verdict=Verdict.REJECT, confidence=75),
            CouncilReview(reviewer="C", role=ReviewerRole.CHIEF_DEVOPS_OFFICER,
                          verdict=Verdict.APPROVE, confidence=70),
        ])
        result = council._synthesize_consensus(decision)
        assert result.consensus == Verdict.REJECT

    def test_overall_risks_deduplicated(self, council):
        decision = CouncilDecision(reviews=[
            CouncilReview(reviewer="A", role=ReviewerRole.CHIEF_ARCHITECT,
                          verdict=Verdict.APPROVE_WITH_CONCERNS, confidence=80,
                          risks=["Data loss", "Performance regression"]),
            CouncilReview(reviewer="B", role=ReviewerRole.CHIEF_SECURITY_OFFICER,
                          verdict=Verdict.APPROVE_WITH_CONCERNS, confidence=75,
                          risks=["Data loss", "Security breach"]),
        ])
        result = council._synthesize_consensus(decision)
        assert len(result.overall_risks) == 3  # "Data loss" deduplicated
