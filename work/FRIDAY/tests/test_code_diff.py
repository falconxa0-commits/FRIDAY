"""Tests for code diff/preview before writing."""
import pytest
import asyncio
import tempfile
import os


class TestCodeDiff:
    @pytest.mark.asyncio
    async def test_preview_returns_real_diff(self):
        from agents.coding_agent import CodingAgent
        class FakeBrain:
            async def chat_stream(self, p):
                yield "ok"
        agent = CodingAgent(brain=FakeBrain())

        # Create a temp file with initial content
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def old_function():\n    return 1\n")
            f.flush()
            file_path = f.name

        try:
            preview = await agent.preview_changes(
                file_path,
                "def new_function():\n    return 2\n"
            )
            assert "diff" in preview
            assert "preview_id" in preview
            assert "additions" in preview
            assert "deletions" in preview
            assert preview["additions"] >= 1
            assert preview["deletions"] >= 1
            assert "new_function" in preview["diff"]
            assert "old_function" in preview["diff"]
        finally:
            os.unlink(file_path)

    @pytest.mark.asyncio
    async def test_preview_for_new_file(self):
        from agents.coding_agent import CodingAgent
        class FakeBrain:
            async def chat_stream(self, p):
                yield "ok"
        agent = CodingAgent(brain=FakeBrain())

        preview = await agent.preview_changes(
            "/tmp/nonexistent_file_12345.py",
            "def hello():\n    print('world')\n"
        )
        assert preview["additions"] >= 1
        assert preview["deletions"] == 0  # No existing content

    @pytest.mark.asyncio
    async def test_apply_requires_valid_preview_id(self):
        from agents.coding_agent import CodingAgent
        class FakeBrain:
            async def chat_stream(self, p):
                yield "ok"
        agent = CodingAgent(brain=FakeBrain())
        result = await agent.apply_preview("nonexistent_preview_id")
        assert result["status"] == "error"
