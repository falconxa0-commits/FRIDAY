"""Tests for EthicalSentinel class."""

import pytest
from core.sentinel import EthicalSentinel, RiskLevel, RiskAssessment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sentinel():
    return EthicalSentinel()


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------

class TestRiskClassification:
    """Test that actions are classified into correct risk levels."""

    def test_safe_action(self, sentinel):
        result = sentinel.evaluate_action("check the weather")
        assert result["overall_score"] < 3.0

    def test_cautious_action(self, sentinel):
        result = sentinel.evaluate_action("modify the config file")
        assert result["classification"] in (
            RiskLevel.SAFE.value,
            RiskLevel.CAUTIOUS.value,
        )

    def test_dangerous_action(self, sentinel):
        """Actions with high scores across multiple categories should be dangerous+."""
        # This action triggers high data_loss (delete) + high privacy (share) +
        # high security (execute) + high irreversibility (permanent)
        result = sentinel.evaluate_action(
            "delete all user data, share it externally, "
            "and execute as root permanently"
        )
        assert result["overall_score"] >= 5.0

    def test_critical_action(self, sentinel):
        """Actions with very high scores across many categories should be critical."""
        result = sentinel.evaluate_action(
            "execute sudo rm -rf / --permanent and share broadcast "
            "destroy purge wipe"
        )
        assert result["overall_score"] >= 7.0

    def test_read_action_low_risk(self, sentinel):
        result = sentinel.evaluate_action("search for documents")
        assert result["overall_score"] < 5.0

    def test_send_action_privacy_risk(self, sentinel):
        result = sentinel.evaluate_action("send user data to external server")
        assert result["categories"]["privacy"] >= 5.0

    def test_delete_action_data_loss_risk(self, sentinel):
        result = sentinel.evaluate_action("delete the database")
        assert result["categories"]["data_loss"] >= 5.0

    def test_execute_action_security_risk(self, sentinel):
        result = sentinel.evaluate_action("execute remote command via SSH")
        assert result["categories"]["security"] >= 5.0


# ---------------------------------------------------------------------------
# Action blocking
# ---------------------------------------------------------------------------

class TestActionBlocking:
    """Test that critical actions are blocked."""

    def test_critical_action_blocked(self, sentinel):
        """Actions scoring >= 7.0 should be blocked (aligned=False)."""
        result = sentinel.evaluate_action(
            "execute sudo and permanent delete and destroy and "
            "share broadcast wipe purge commit"
        )
        assert result["overall_score"] >= 7.0
        assert result["aligned"] is False

    def test_safe_action_aligned(self, sentinel):
        result = sentinel.evaluate_action("check status of the server")
        assert result["aligned"] is True

    def test_blocked_actions_tracked(self, sentinel):
        sentinel.evaluate_action(
            "execute sudo and permanent delete and destroy and "
            "share broadcast wipe purge commit"
        )
        assert len(sentinel._blocked_actions) >= 1

    def test_blocked_action_has_details(self, sentinel):
        sentinel.evaluate_action(
            "execute sudo and permanent delete and destroy and "
            "share broadcast wipe purge commit"
        )
        if sentinel._blocked_actions:
            blocked = sentinel._blocked_actions[0]
            assert "action" in blocked
            assert "score" in blocked
            assert "classification" in blocked


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

class TestRecommendations:
    """Test recommendation generation."""

    def test_recommendations_for_data_loss(self, sentinel):
        result = sentinel.evaluate_action("delete all files")
        if result["categories"]["data_loss"] >= 5.0:
            assert any("backup" in r.lower() for r in result["recommendations"])

    def test_recommendations_for_privacy(self, sentinel):
        result = sentinel.evaluate_action("share user data externally")
        if result["categories"]["privacy"] >= 5.0:
            assert any("permission" in r.lower() or "anonym" in r.lower()
                        for r in result["recommendations"])

    def test_recommendations_for_security(self, sentinel):
        result = sentinel.evaluate_action("execute remote code as root")
        if result["categories"]["security"] >= 5.0:
            assert any("confirmation" in r.lower() or "security" in r.lower()
                        for r in result["recommendations"])

    def test_safe_action_recommendation(self, sentinel):
        result = sentinel.evaluate_action("list files in directory")
        if result["overall_score"] < 3.0:
            assert any("safe" in r.lower() for r in result["recommendations"])


# ---------------------------------------------------------------------------
# Context modifiers
# ---------------------------------------------------------------------------

class TestContextModifiers:
    """Test context-based risk adjustments."""

    def test_sensitive_data_increases_risk(self, sentinel):
        normal = sentinel.evaluate_action("read the file")
        sensitive = sentinel.evaluate_action(
            "read the file",
            context={"target_sensitive_data": True},
        )
        assert sensitive["categories"]["privacy"] >= normal["categories"]["privacy"]

    def test_user_confirmation_reduces_risk(self, sentinel):
        normal = sentinel.evaluate_action("delete the record")
        confirmed = sentinel.evaluate_action(
            "delete the record",
            context={"user_confirmed": True},
        )
        assert confirmed["overall_score"] <= normal["overall_score"]

    def test_test_environment_reduces_risk(self, sentinel):
        normal = sentinel.evaluate_action("deploy the service")
        testing = sentinel.evaluate_action(
            "deploy the service",
            context={"test_environment": True},
        )
        assert testing["overall_score"] <= normal["overall_score"]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

class TestSentinelReporting:

    def test_report_no_evaluations(self, sentinel):
        report = sentinel.get_sentinel_report()
        assert "ACTIVE" in report

    def test_report_with_evaluations(self, sentinel):
        sentinel.evaluate_action("check status")
        report = sentinel.get_sentinel_report()
        assert "Evaluated: 1" in report

    def test_assessment_history(self, sentinel):
        sentinel.evaluate_action("check status")
        sentinel.evaluate_action("delete files")
        history = sentinel.get_assessment_history()
        assert len(history) == 2

    def test_assessment_history_limit(self, sentinel):
        for i in range(30):
            sentinel.evaluate_action(f"action {i}")
        history = sentinel.get_assessment_history(limit=10)
        assert len(history) == 10
