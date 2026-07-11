"""Tests for conversation branching — two independent histories."""
import pytest
import asyncio
from unittest.mock import patch, MagicMock


@pytest.fixture()
def brain_cls():
    with patch.dict("sys.modules", {
        "anthropic": MagicMock(),
        "google.generativeai": MagicMock(),
        "google.genai": MagicMock(),
        "tavily": MagicMock(),
    }):
        with patch("config.settings.ANTHROPIC_API_KEY", None), \
             patch("config.settings.GEMINI_API_KEY", None), \
             patch("config.settings.TAVILY_API_KEY", None):
            from core.brain import FridayBrain
            return FridayBrain


class TestConversationBranching:

    @pytest.mark.asyncio
    async def test_branch_creates_independent_history(self, brain_cls):
        brain = brain_cls()
        brain.conversation_history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "Tell me about Python"},
            {"role": "assistant", "content": "Python is great!"},
        ]

        # Create branch at message index 1
        result = await brain.branch_conversation("1")
        assert result["branch_id"].startswith("branch_")
        assert result["message_count"] == 2  # messages 0 and 1

        # Add different message on branch
        brain.conversation_history.append({"role": "user", "content": "I want option A"})

        # Switch to main — should have original 4 messages
        await brain.switch_branch("main")
        assert len(brain.conversation_history) == 4
        assert brain.conversation_history[2]["content"] == "Tell me about Python"

    @pytest.mark.asyncio
    async def test_switch_branch_changes_context(self, brain_cls):
        brain = brain_cls()
        brain.conversation_history = [
            {"role": "user", "content": "Original message"},
            {"role": "assistant", "content": "Original response"},
        ]

        # Create branch at index 1 (both messages copied)
        result = await brain.branch_conversation("1")
        branch_id = result["branch_id"]
        assert result["message_count"] == 2

        # Add message on branch
        brain.conversation_history.append({"role": "user", "content": "Branch message"})
        assert len(brain.conversation_history) == 3

        # Switch to main — should have original 2 messages
        await brain.switch_branch("main")
        assert len(brain.conversation_history) == 2

        # Switch back to branch — should have 3 messages (2 original + 1 branch)
        await brain.switch_branch(branch_id)
        assert len(brain.conversation_history) == 3
        assert brain.conversation_history[2]["content"] == "Branch message"

    @pytest.mark.asyncio
    async def test_merge_insight_references_branch_content(self, brain_cls):
        brain = brain_cls()
        brain.conversation_history = [
            {"role": "user", "content": "What is Rust?"},
            {"role": "assistant", "content": "Rust is a systems programming language."},
        ]

        result = await brain.branch_conversation("1")
        branch_id = result["branch_id"]

        brain.conversation_history.append({"role": "user", "content": "How does Rust handle memory?"})
        brain.conversation_history.append({"role": "assistant", "content": "Rust uses ownership and borrowing."})

        insight = await brain.merge_branch_insight(branch_id)
        assert "Branch" in insight or "branch" in insight
        assert "Rust" in insight or "memory" in insight.lower()

    @pytest.mark.asyncio
    async def test_delete_branch_removes_it(self, brain_cls):
        brain = brain_cls()
        brain.conversation_history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]

        result = await brain.branch_conversation("0")
        branch_id = result["branch_id"]

        branches = await brain.get_branches()
        assert any(b["branch_id"] == branch_id for b in branches)

        success = await brain.delete_branch(branch_id)
        assert success is True

        branches = await brain.get_branches()
        assert not any(b["branch_id"] == branch_id for b in branches)

    @pytest.mark.asyncio
    async def test_get_branches_includes_main(self, brain_cls):
        brain = brain_cls()
        branches = await brain.get_branches()
        assert any(b["branch_id"] == "main" for b in branches)
