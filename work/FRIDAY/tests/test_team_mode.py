"""Tests for core/team_mode.py — multi-user privacy."""
import asyncio
import pytest

from core.team_mode import TeamMode


@pytest.fixture()
def tm():
    return TeamMode()


class TestUserRegistration:
    """Test user registration."""

    def test_register_user_returns_user_with_token(self, tm):
        user = tm.register_user("Alice", "alice@example.com")
        assert user["name"] == "Alice"
        assert user["email"] == "alice@example.com"
        assert user["user_id"].startswith("user_")
        assert len(user["token"]) > 20

    def test_register_two_users_distinct_ids(self, tm):
        alice = tm.register_user("Alice", "alice@example.com")
        bob = tm.register_user("Bob", "bob@example.com")
        assert alice["user_id"] != bob["user_id"]
        assert alice["token"] != bob["token"]

    def test_get_user_by_token(self, tm):
        alice = tm.register_user("Alice", "alice@example.com")
        found = tm.get_user_by_token(alice["token"])
        assert found["user_id"] == alice["user_id"]

    def test_get_user_by_invalid_token_returns_none(self, tm):
        assert tm.get_user_by_token("invalid-token") is None


class TestMemoryPrivacy:
    """Test per-user memory privacy."""

    @pytest.mark.asyncio
    async def test_private_memory_only_visible_to_owner(self, tm):
        alice = tm.register_user("Alice", "alice@example.com")
        bob = tm.register_user("Bob", "bob@example.com")

        await tm.store_private_memory(alice["user_id"], "Alice's secret")
        await tm.store_private_memory(bob["user_id"], "Bob's secret")

        alice_ctx = await tm.get_context_for_user(alice["user_id"])
        bob_ctx = await tm.get_context_for_user(bob["user_id"])

        alice_contents = [m["content"] for m in alice_ctx["private"]]
        bob_contents = [m["content"] for m in bob_ctx["private"]]

        assert "Alice's secret" in alice_contents
        assert "Bob's secret" not in alice_contents
        assert "Bob's secret" in bob_contents
        assert "Alice's secret" not in bob_contents

    @pytest.mark.asyncio
    async def test_shared_memory_visible_to_all(self, tm):
        alice = tm.register_user("Alice", "alice@example.com")
        bob = tm.register_user("Bob", "bob@example.com")

        await tm.store_shared_memory("Project deadline", author_user_id=alice["user_id"])

        alice_ctx = await tm.get_context_for_user(alice["user_id"])
        bob_ctx = await tm.get_context_for_user(bob["user_id"])

        assert len(alice_ctx["shared"]) == 1
        assert len(bob_ctx["shared"]) == 1
        assert alice_ctx["shared"][0]["content"] == "Project deadline"

    @pytest.mark.asyncio
    async def test_enforce_privacy_blocks_cross_user(self, tm):
        alice = tm.register_user("Alice", "alice@example.com")
        bob = tm.register_user("Bob", "bob@example.com")

        alice_mem = await tm.store_private_memory(alice["user_id"], "secret")
        assert tm.enforce_privacy(alice["user_id"], alice_mem) is True
        assert tm.enforce_privacy(bob["user_id"], alice_mem) is False

    @pytest.mark.asyncio
    async def test_unknown_user_raises(self, tm):
        with pytest.raises(ValueError):
            await tm.store_private_memory("unknown_user", "test")
