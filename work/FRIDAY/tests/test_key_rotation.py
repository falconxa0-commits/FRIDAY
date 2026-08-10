"""Tests for HMAC key rotation."""
import pytest

from core.key_rotation import KeyRotationManager, KeyRecord


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    import core.key_rotation
    core.key_rotation._manager = None
    core.key_rotation._CURRENT_KEY_PATH = tmp_path / "keys" / "current.key"
    core.key_rotation._KEY_HISTORY_PATH = tmp_path / "keys" / "history.json"
    core.key_rotation._KEY_DIR = tmp_path / "keys"
    core.key_rotation._KEY_DIR.mkdir(parents=True, exist_ok=True)
    mgr = KeyRotationManager()
    yield mgr
    core.key_rotation._manager = None


class TestKeyGeneration:
    def test_generate_key_returns_string(self, manager):
        key = manager.generate_key(reason="test")
        assert isinstance(key, str)
        assert len(key) >= 32

    def test_generate_key_persists_to_disk(self, manager):
        key = manager.generate_key(reason="persist test")
        from core.key_rotation import _CURRENT_KEY_PATH
        assert _CURRENT_KEY_PATH.exists()
        assert _CURRENT_KEY_PATH.read_text().strip() == key

    def test_get_current_key(self, manager):
        key = manager.get_current_key()
        assert isinstance(key, bytes)
        assert len(key) >= 32

    def test_key_fingerprint(self, manager):
        fp = manager.get_key_fingerprint()
        assert len(fp) == 16


class TestKeyRotation:
    def test_rotate_key_changes_key(self, manager):
        old_key = manager.get_current_key()
        result = manager.rotate_key(reason="test rotation")
        new_key = manager.get_current_key()
        assert old_key != new_key
        assert result["rotated"] is True

    def test_rotate_key_records_history(self, manager):
        manager.rotate_key(reason="history test")
        history = manager.get_history()
        assert len(history) >= 1
        assert history[-1]["reason"] == "history test"

    def test_rotate_key_returns_details(self, manager):
        result = manager.rotate_key(reason="details test")
        assert "old_key_id" in result
        assert "new_key_id" in result
        assert "old_fingerprint" in result
        assert "new_fingerprint" in result
        assert "timestamp" in result


class TestChainVerification:
    def test_verify_empty_chain(self, manager):
        result = manager.verify_chain_with_current_key([])
        assert result is True

    def test_verify_real_chain(self, manager):
        import json, hashlib, hmac
        from core.ledger import ActionLedger

        # Create a chain entry signed with the current key
        secret = manager.get_current_key()
        entry = {
            "action_id": "test-id",
            "component": "Test",
            "action": "test_action",
            "params": {},
            "timestamp": "2026-01-01T00:00:00+00:00",
            "status": "approved",
            "approved_by": "human",
            "prev_hash": ActionLedger.GENESIS_HASH,
        }
        content = json.dumps({
            "prev_hash": ActionLedger.GENESIS_HASH,
            "action_id": entry["action_id"],
            "component": entry["component"],
            "action": entry["action"],
            "params": entry["params"],
            "timestamp": entry["timestamp"],
            "status": entry["status"],
            "approved_by": entry["approved_by"],
        }, sort_keys=True, default=str)
        entry["hash"] = hmac.new(secret, content.encode("utf-8"), hashlib.sha256).hexdigest()

        result = manager.verify_chain_with_current_key([entry])
        assert result is True

    def test_verify_fails_after_rotation(self, manager):
        import json, hashlib, hmac
        from core.ledger import ActionLedger

        # Sign with current key
        old_secret = manager.get_current_key()
        entry = {
            "action_id": "test-id",
            "component": "Test",
            "action": "test",
            "params": {},
            "timestamp": "2026-01-01T00:00:00+00:00",
            "status": "approved",
            "approved_by": "human",
            "prev_hash": ActionLedger.GENESIS_HASH,
        }
        content = json.dumps({
            "prev_hash": ActionLedger.GENESIS_HASH,
            "action_id": entry["action_id"],
            "component": entry["component"],
            "action": entry["action"],
            "params": entry["params"],
            "timestamp": entry["timestamp"],
            "status": entry["status"],
            "approved_by": entry["approved_by"],
        }, sort_keys=True, default=str)
        entry["hash"] = hmac.new(old_secret, content.encode("utf-8"), hashlib.sha256).hexdigest()

        # Rotate key
        manager.rotate_key(reason="test invalidation")

        # Old chain should fail verification
        result = manager.verify_chain_with_current_key([entry])
        assert result is False


class TestHistory:
    def test_get_empty_history(self, manager):
        # Fresh manager with no rotations
        from core.key_rotation import _KEY_HISTORY_PATH
        if _KEY_HISTORY_PATH.exists():
            _KEY_HISTORY_PATH.unlink()
        history = manager.get_history()
        assert history == []

    def test_multiple_rotations_tracked(self, manager):
        manager.rotate_key(reason="first")
        manager.rotate_key(reason="second")
        manager.rotate_key(reason="third")
        history = manager.get_history()
        assert len(history) >= 3
