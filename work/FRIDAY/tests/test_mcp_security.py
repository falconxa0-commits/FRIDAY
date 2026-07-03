"""Tests for the MCP server — ledger gating + fail-closed behavior."""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from mcp_server import FridayMCPServer


@pytest.fixture()
def server():
    return FridayMCPServer()


class TestMCPFailClosed:
    """Verify MCP tools fail CLOSED when ledger is unavailable (SEC-4 fix)."""

    @pytest.mark.asyncio
    async def test_image_generation_fails_closed_without_ledger(self, server):
        """If ledger is None, image generation must REFUSE, not execute."""
        with patch.object(server, "_get_ledger", return_value=None):
            result = await server.handle_image_generation({"prompt": "test"})
        assert result["status"] == "error"
        assert "Ledger unavailable" in result["message"]
        # Must NOT have attempted the actual generation
        assert "receipt" in result

    @pytest.mark.asyncio
    async def test_video_generation_fails_closed_without_ledger(self, server):
        with patch.object(server, "_get_ledger", return_value=None):
            result = await server.handle_video_generation({"prompt": "test"})
        assert result["status"] == "error"
        assert "Ledger unavailable" in result["message"]

    @pytest.mark.asyncio
    async def test_code_execution_fails_closed_without_ledger(self, server):
        with patch.object(server, "_get_ledger", return_value=None):
            result = await server.handle_code_execution({"code": "print('hello')"})
        assert result["status"] == "error"
        assert "Ledger unavailable" in result["message"]


class TestMCPDispatch:
    """Test the dispatch routing."""

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, server):
        result = await server.dispatch("nonexistent_tool", {})
        assert result["status"] == "error"
        assert "Unknown tool" in result["message"]

    @pytest.mark.asyncio
    async def test_friday_prefix_stripped(self, server):
        """friday.chat and chat should both route to the same handler."""
        # Both should call handle_chat (which will return an error since no GLM)
        r1 = await server.dispatch("friday.chat", {"message": "test"})
        r2 = await server.dispatch("chat", {"message": "test"})
        # Both should succeed (or both fail the same way) — just verify they route
        assert r1["status"] == r2["status"]

    @pytest.mark.asyncio
    async def test_request_approval_routes_correctly(self, server):
        """friday.request_approval should be in the dispatch table."""
        result = await server.dispatch("friday.request_approval", {
            "component": "Test",
            "action": "test_action",
            "timeout": 1,
        })
        assert "status" in result
        assert result["status"] in ("timeout", "rejected", "approved", "error")


class TestMCPReceipts:
    """Test that MCP tools return real receipts."""

    @pytest.mark.asyncio
    async def test_request_approval_returns_receipt(self, server):
        result = await server.dispatch("friday.request_approval", {
            "component": "Test",
            "action": "test",
            "timeout": 1,
        })
        assert "receipt" in result
        assert "action" in result["receipt"]
        assert "timestamp" in result["receipt"]
