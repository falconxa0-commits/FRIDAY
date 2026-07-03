"""Tests for core/ledger.py — tamper-evident audit log + approval gate."""
import pytest
import os
import tempfile

from core.ledger import ActionLedger, NEVER_AUTO_APPROVE_COMPONENTS


@pytest.fixture()
def ledger():
    """Fresh ActionLedger with temp persistence files."""
    # Use temp files so tests don't pollute the repo
    with tempfile.TemporaryDirectory() as tmpdir:
        old_persist = ActionLedger.PERSIST_PATH
        old_chain = ActionLedger.CHAIN_PERSIST_PATH
        ActionLedger.PERSIST_PATH = os.path.join(tmpdir, "pending.json")
        ActionLedger.CHAIN_PERSIST_PATH = os.path.join(tmpdir, "chain.json")
        l = ActionLedger()
        l.audit_log = os.path.join(tmpdir, "audit.log")
        yield l
        ActionLedger.PERSIST_PATH = old_persist
        ActionLedger.CHAIN_PERSIST_PATH = old_chain


class TestNeverAutoApprove:
    """Test that dangerous components never auto-approve."""

    def test_commerce_never_auto_approves_at_power(self, ledger):
        ledger.profile = "POWER"
        action_id = ledger.queue_action(
            "Commerce", "checkout",
            {"item": "test", "price": 9.99},
            risk_level="low",  # Even at LOW risk + POWER profile
        )
        assert ledger.pending_actions[action_id]["status"] == "pending"

    def test_printer_never_auto_approves_at_power(self, ledger):
        ledger.profile = "POWER"
        action_id = ledger.queue_action(
            "Printer", "print_document",
            {"file": "test.pdf"},
            risk_level="low",
        )
        assert ledger.pending_actions[action_id]["status"] == "pending"

    def test_code_execution_never_auto_approves(self, ledger):
        ledger.profile = "POWER"
        action_id = ledger.queue_action(
            "CodeExecution", "execute_code",
            {"code": "print('hello')"},
            risk_level="low",
        )
        assert ledger.pending_actions[action_id]["status"] == "pending"

    def test_weather_auto_approves_at_power(self, ledger):
        """Non-dangerous low-risk actions SHOULD auto-approve at POWER."""
        ledger.profile = "POWER"
        action_id = ledger.queue_action(
            "Weather", "get_weather",
            {"location": "Lagos"},
            risk_level="low",
        )
        assert ledger.pending_actions[action_id]["status"] == "approved"

    def test_guest_profile_never_auto_approves(self, ledger):
        ledger.profile = "GUEST"
        action_id = ledger.queue_action(
            "Weather", "get_weather",
            {"location": "Lagos"},
            risk_level="low",
        )
        assert ledger.pending_actions[action_id]["status"] == "pending"


class TestHashChain:
    """Test the tamper-evident hash chain."""

    def test_chain_valid_after_logging(self, ledger):
        # Log 3 actions
        for comp, act in [("Weather", "get_weather"), ("Printer", "print"), ("Commerce", "checkout")]:
            aid = ledger.queue_action(comp, act, {}, risk_level="high")
            ledger.approve_action(aid)

        assert len(ledger.get_audit_log()) == 3
        assert ledger.verify_chain() is True

    def test_chain_broken_after_tampering(self, ledger):
        aid = ledger.queue_action("Weather", "get_weather", {"location": "Lagos"}, risk_level="high")
        ledger.approve_action(aid)

        assert ledger.verify_chain() is True

        # Tamper with the first entry's params
        ledger._tamper_for_test(0, {"location": "TAMPERED"})

        assert ledger.verify_chain() is False

    def test_chain_persists_to_disk(self, ledger):
        """Verify the chain survives a restart (new ActionLedger instance)."""
        aid = ledger.queue_action("Weather", "get_weather", {"location": "Lagos"}, risk_level="high")
        ledger.approve_action(aid)

        # The chain should be persisted to disk
        assert os.path.exists(ledger.CHAIN_PERSIST_PATH)

        # Create a new ledger pointing at the same persist path
        new_ledger = ActionLedger()
        new_ledger.CHAIN_PERSIST_PATH = ledger.CHAIN_PERSIST_PATH
        new_ledger._load_chain()

        assert len(new_ledger.get_audit_log()) == 1
        assert new_ledger.verify_chain() is True

    def test_different_actions_produce_different_hashes(self, ledger):
        aid1 = ledger.queue_action("Weather", "get_weather", {"location": "Lagos"}, risk_level="high")
        ledger.approve_action(aid1)

        # New ledger for a different action
        with tempfile.TemporaryDirectory() as tmpdir:
            ActionLedger.CHAIN_PERSIST_PATH = os.path.join(tmpdir, "chain2.json")
            ledger2 = ActionLedger()
            aid2 = ledger2.queue_action("Weather", "get_weather", {"location": "Abuja"}, risk_level="high")
            ledger2.approve_action(aid2)

        h1 = ledger.get_audit_log()[0]["hash"]
        h2 = ledger2.get_audit_log()[0]["hash"]
        assert h1 != h2  # Different params → different hashes


class TestParamRedaction:
    """Test that sensitive params are redacted in the file log."""

    def test_redact_password(self, ledger):
        redacted = ledger._redact_params({"password": "secret123", "user": "alice"})
        assert redacted["password"] == "***REDACTED***"
        assert redacted["user"] == "alice"

    def test_redact_api_key(self, ledger):
        redacted = ledger._redact_params({"api_key": "sk-abc123", "data": "ok"})
        assert redacted["api_key"] == "***REDACTED***"

    def test_redact_token(self, ledger):
        redacted = ledger._redact_params({"token": "bearer xyz", "name": "test"})
        assert redacted["token"] == "***REDACTED***"

    def test_redact_payment_method(self, ledger):
        redacted = ledger._redact_params({"payment_method": "pm_card_visa", "amount": 100})
        assert redacted["payment_method"] == "***REDACTED***"

    def test_non_sensitive_params_preserved(self, ledger):
        params = {"location": "Lagos", "file": "report.pdf", "count": 5}
        redacted = ledger._redact_params(params)
        assert redacted == params
