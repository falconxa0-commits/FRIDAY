"""Tests for mcp_server.py — TOOLS list, _compute_risk_level, dispatch routing.

REGRESSION TESTS:
- TOOLS list has 8 entries (was 6 before the killer-feature addition)
- request_approval and execute_action are advertised in TOOLS
- _compute_risk_level IGNORES caller-supplied risk_level (security fix)
- _compute_risk_level uses EthicalSentinel when available
- handle_request_approval returns error when component/action missing
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from mcp_server import TOOLS, FridayMCPServer


# ---------------------------------------------------------------------------
# TOOLS list — regression test for the 8-tool requirement
# ---------------------------------------------------------------------------


class TestToolsList:
    """REGRESSION: TOOLS must have 8 entries (was 6 before adding
    execute_action + request_approval)."""

    def test_tools_list_has_eight_entries(self):
        assert len(TOOLS) == 8, (
            f"Expected 8 tools, got {len(TOOLS)}. "
            "execute_action + request_approval must be advertised."
        )

    def test_all_tools_have_name_and_description(self):
        for tool in TOOLS:
            assert "name" in tool, f"Tool missing name: {tool}"
            assert "description" in tool, f"Tool {tool.get('name')} missing description"
            assert isinstance(tool["description"], str)
            assert len(tool["description"]) > 0

    def test_all_tools_have_input_schema(self):
        for tool in TOOLS:
            assert "inputSchema" in tool, f"Tool {tool['name']} missing inputSchema"

    def test_tool_names_are_unique(self):
        names = [t["name"] for t in TOOLS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"

    def test_expected_tool_names_present(self):
        names = {t["name"] for t in TOOLS}
        expected = {
            "chat", "vision", "web_search",
            "image_generation", "video_generation",
            "code_execution",
        }
        assert expected.issubset(names), (
            f"Missing expected tools. Got: {names}, expected at least: {expected}"
        )


class TestKillerFeatureToolsAdvertised:
    """REGRESSION: execute_action and request_approval MUST be advertised."""

    def test_execute_action_is_in_tools(self):
        names = [t["name"] for t in TOOLS]
        assert "execute_action" in names, (
            "execute_action must be in TOOLS — this is the human-in-the-loop gate "
            "for external AI agents."
        )

    def test_request_approval_is_in_tools(self):
        names = [t["name"] for t in TOOLS]
        assert "request_approval" in names, (
            "request_approval must be in TOOLS — this is the killer feature."
        )

    def test_request_approval_description_mentions_risk_level_ignored(self):
        """The request_approval tool description should warn callers that
        caller-supplied risk_level is ignored for security."""
        tool = next(t for t in TOOLS if t["name"] == "request_approval")
        # The description should mention the security policy
        assert "risk_level" in tool["description"].lower() or "ignored" in tool["description"].lower()

    def test_execute_action_has_required_params(self):
        tool = next(t for t in TOOLS if t["name"] == "execute_action")
        required = tool["inputSchema"].get("required", [])
        assert "component" in required
        assert "action" in required

    def test_request_approval_has_required_params(self):
        tool = next(t for t in TOOLS if t["name"] == "request_approval")
        required = tool["inputSchema"].get("required", [])
        assert "component" in required
        assert "action" in required


# ---------------------------------------------------------------------------
# _compute_risk_level — security regression tests
# ---------------------------------------------------------------------------


class TestComputeRiskLevel:
    """SECURITY: _compute_risk_level must IGNORE caller-supplied risk_level
    and use EthicalSentinel (or keyword fallback) instead."""

    def test_compute_risk_level_returns_string(self):
        server = FridayMCPServer()
        result = server._compute_risk_level("Weather", "get_weather", {})
        assert isinstance(result, str)
        assert result in {"low", "medium", "high", "critical"}

    def test_compute_risk_level_ignores_caller_supplied_risk_level(self):
        """SECURITY: The function takes (component, action, params) — there's
        NO risk_level parameter, so callers cannot influence the result."""
        server = FridayMCPServer()
        # The function signature is (component, action, params) — no risk_level arg.
        # Verify by introspection.
        import inspect
        sig = inspect.signature(server._compute_risk_level)
        assert "risk_level" not in sig.parameters, (
            "_compute_risk_level must NOT accept a risk_level parameter — "
            "it would defeat the security fix."
        )

    def test_compute_risk_level_uses_sentinel_when_available(self):
        """When EthicalSentinel is available, _compute_risk_level should call it."""
        server = FridayMCPServer()
        with patch("core.sentinel.EthicalSentinel") as MockSentinel:
            mock_inst = MagicMock()
            mock_inst.evaluate_action.return_value = {
                "classification": "DANGEROUS",
                "overall_score": 8.5,
            }
            MockSentinel.return_value = mock_inst
            result = server._compute_risk_level("Filesystem", "delete_file", {"path": "/x"})
        assert result == "high"
        mock_inst.evaluate_action.assert_called_once()

    def test_compute_risk_level_maps_safe_to_low(self):
        server = FridayMCPServer()
        with patch("core.sentinel.EthicalSentinel") as MockSentinel:
            mock_inst = MagicMock()
            mock_inst.evaluate_action.return_value = {"classification": "SAFE"}
            MockSentinel.return_value = mock_inst
            result = server._compute_risk_level("Weather", "get_weather", {})
        assert result == "low"

    def test_compute_risk_level_maps_cautious_to_medium(self):
        server = FridayMCPServer()
        with patch("core.sentinel.EthicalSentinel") as MockSentinel:
            mock_inst = MagicMock()
            mock_inst.evaluate_action.return_value = {"classification": "CAUTIOUS"}
            MockSentinel.return_value = mock_inst
            result = server._compute_risk_level("Calendar", "create_event", {})
        assert result == "medium"

    def test_compute_risk_level_maps_dangerous_to_high(self):
        server = FridayMCPServer()
        with patch("core.sentinel.EthicalSentinel") as MockSentinel:
            mock_inst = MagicMock()
            mock_inst.evaluate_action.return_value = {"classification": "DANGEROUS"}
            MockSentinel.return_value = mock_inst
            result = server._compute_risk_level("Filesystem", "delete_file", {})
        assert result == "high"

    def test_compute_risk_level_maps_critical_to_critical(self):
        server = FridayMCPServer()
        with patch("core.sentinel.EthicalSentinel") as MockSentinel:
            mock_inst = MagicMock()
            mock_inst.evaluate_action.return_value = {"classification": "CRITICAL"}
            MockSentinel.return_value = mock_inst
            result = server._compute_risk_level("CodeExecution", "execute", {})
        assert result == "critical"

    def test_compute_risk_level_falls_back_to_keyword_classifier(self):
        """If Sentinel raises, fall back to UniversalConnector._classify_risk."""
        server = FridayMCPServer()
        with patch("core.sentinel.EthicalSentinel", side_effect=RuntimeError("sentinel boom")), \
             patch("core.universal_connector.UniversalConnector._classify_risk",
                   return_value="critical") as mock_classify:
            result = server._compute_risk_level("Filesystem", "delete_file", {})
        assert result == "critical"
        mock_classify.assert_called_once_with("delete_file")

    def test_compute_risk_level_returns_high_on_total_failure(self):
        """If both Sentinel AND keyword classifier fail, default to 'high'."""
        server = FridayMCPServer()
        with patch("core.sentinel.EthicalSentinel", side_effect=RuntimeError("boom")), \
             patch("core.universal_connector.UniversalConnector._classify_risk",
                   side_effect=RuntimeError("boom")):
            result = server._compute_risk_level("X", "do_thing", {})
        assert result == "high"

    def test_compute_risk_level_handles_enum_classification(self):
        """Sentinel may return a RiskLevel enum instead of a string."""
        server = FridayMCPServer()
        from enum import Enum

        class FakeRiskLevel(Enum):
            CRITICAL = "critical"

        with patch("core.sentinel.EthicalSentinel") as MockSentinel:
            mock_inst = MagicMock()
            # Return an object with .value (like an enum)
            class FakeClassification:
                value = "CRITICAL"
            mock_inst.evaluate_action.return_value = {"classification": FakeClassification()}
            MockSentinel.return_value = mock_inst
            result = server._compute_risk_level("X", "do_critical_thing", {})
        assert result == "critical"


# ---------------------------------------------------------------------------
# dispatch routing — all 8 tool names
# ---------------------------------------------------------------------------


class TestDispatchRouting:
    """Verify dispatch routes all 8 tool names correctly."""

    @pytest.mark.asyncio
    async def test_dispatch_chat(self):
        server = FridayMCPServer()
        with patch.object(server, "handle_chat", new=AsyncMock(return_value={"status": "ok"})) as m:
            result = await server.dispatch("chat", {"message": "hi"})
        assert result["status"] == "ok"
        m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_vision(self):
        server = FridayMCPServer()
        with patch.object(server, "handle_vision", new=AsyncMock(return_value={"status": "ok"})) as m:
            await server.dispatch("vision", {"image_base64": "x"})
        m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_web_search(self):
        server = FridayMCPServer()
        with patch.object(server, "handle_web_search", new=AsyncMock(return_value={"status": "ok"})) as m:
            await server.dispatch("web_search", {"query": "x"})
        m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_image_generation(self):
        server = FridayMCPServer()
        with patch.object(server, "handle_image_generation", new=AsyncMock(return_value={"status": "ok"})) as m:
            await server.dispatch("image_generation", {"prompt": "x"})
        m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_video_generation(self):
        server = FridayMCPServer()
        with patch.object(server, "handle_video_generation", new=AsyncMock(return_value={"status": "ok"})) as m:
            await server.dispatch("video_generation", {"prompt": "x"})
        m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_code_execution(self):
        server = FridayMCPServer()
        with patch.object(server, "handle_code_execution", new=AsyncMock(return_value={"status": "ok"})) as m:
            await server.dispatch("code_execution", {"code": "print('x')"})
        m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_execute_action(self):
        server = FridayMCPServer()
        with patch.object(server, "handle_execute_action", new=AsyncMock(return_value={"status": "ok"})) as m:
            await server.dispatch("execute_action", {"component": "X", "action": "y"})
        m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_request_approval(self):
        server = FridayMCPServer()
        with patch.object(server, "handle_request_approval", new=AsyncMock(return_value={"status": "approved"})) as m:
            await server.dispatch("request_approval", {"component": "X", "action": "y", "timeout": 1})
        m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool_returns_error(self):
        server = FridayMCPServer()
        result = await server.dispatch("totally_bogus_tool", {})
        assert result["status"] == "error"
        assert "Unknown tool" in result["message"]

    @pytest.mark.asyncio
    async def test_dispatch_strips_friday_prefix(self):
        """'friday.chat' and 'chat' both route to handle_chat."""
        server = FridayMCPServer()
        with patch.object(server, "handle_chat", new=AsyncMock(return_value={"status": "ok"})) as m:
            await server.dispatch("friday.chat", {"message": "hi"})
        m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_alias_search_routes_to_web_search(self):
        """Both 'search' and 'web_search' should route to handle_web_search."""
        server = FridayMCPServer()
        with patch.object(server, "handle_web_search", new=AsyncMock(return_value={"status": "ok"})) as m:
            await server.dispatch("search", {"query": "x"})
        m.assert_awaited_once()


# ---------------------------------------------------------------------------
# handle_request_approval — input validation
# ---------------------------------------------------------------------------


class TestRequestApprovalValidation:
    """Test handle_request_approval input validation."""

    @pytest.mark.asyncio
    async def test_missing_component_returns_error(self):
        server = FridayMCPServer()
        result = await server.handle_request_approval({
            "action": "delete_file",
            # component missing
        })
        assert result["status"] == "error"
        assert "Missing required" in result["message"]
        assert "component" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_missing_action_returns_error(self):
        server = FridayMCPServer()
        result = await server.handle_request_approval({
            "component": "Filesystem",
            # action missing
        })
        assert result["status"] == "error"
        assert "Missing required" in result["message"]
        assert "action" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_missing_both_returns_error(self):
        server = FridayMCPServer()
        result = await server.handle_request_approval({})
        assert result["status"] == "error"
        assert "Missing required" in result["message"]

    @pytest.mark.asyncio
    async def test_caller_supplied_risk_level_is_ignored(self):
        """SECURITY: Even if caller sends risk_level='low', the server
        must compute it via _compute_risk_level (and use the result)."""
        server = FridayMCPServer()
        with patch.object(server, "_compute_risk_level", return_value="critical") as mock_compute, \
             patch.object(server, "_get_ledger") as mock_get_ledger:
            mock_ledger = MagicMock()
            mock_ledger.queue_action.return_value = "aid-123"
            mock_ledger.wait_for_approval = AsyncMock(return_value=False)
            mock_ledger.pending_actions = {"aid-123": {"status": "pending"}}
            mock_get_ledger.return_value = mock_ledger
            result = await server.handle_request_approval({
                "component": "Filesystem",
                "action": "delete_file",
                "risk_level": "low",  # MALICIOUS — should be IGNORED
                "timeout": 1,
            })
        # _compute_risk_level must have been called
        mock_compute.assert_called_once()
        # The risk_level used in queue_action should be "critical" (the computed one)
        _, kwargs = mock_ledger.queue_action.call_args
        assert kwargs["risk_level"] == "critical"

    @pytest.mark.asyncio
    async def test_no_ledger_returns_error(self):
        server = FridayMCPServer()
        with patch.object(server, "_get_ledger", return_value=None):
            result = await server.handle_request_approval({
                "component": "X", "action": "y", "timeout": 1,
            })
        assert result["status"] == "error"
        assert "Ledger not available" in result["message"]

    @pytest.mark.asyncio
    async def test_approved_action_returns_approved_status(self):
        server = FridayMCPServer()
        with patch.object(server, "_get_ledger") as mock_get_ledger:
            mock_ledger = MagicMock()
            mock_ledger.queue_action.return_value = "aid-1"
            mock_ledger.wait_for_approval = AsyncMock(return_value=True)
            mock_get_ledger.return_value = mock_ledger
            result = await server.handle_request_approval({
                "component": "Weather",
                "action": "get_weather",
                "timeout": 1,
            })
        assert result["status"] == "approved"
        assert result["action_id"] == "aid-1"

    @pytest.mark.asyncio
    async def test_rejected_action_returns_timeout_or_rejected(self):
        server = FridayMCPServer()
        with patch.object(server, "_get_ledger") as mock_get_ledger:
            mock_ledger = MagicMock()
            mock_ledger.queue_action.return_value = "aid-2"
            mock_ledger.wait_for_approval = AsyncMock(return_value=False)
            # Status is "pending" → timeout
            mock_ledger.pending_actions = {"aid-2": {"status": "pending"}}
            mock_get_ledger.return_value = mock_ledger
            result = await server.handle_request_approval({
                "component": "X",
                "action": "y",
                "timeout": 1,
            })
        assert result["status"] in ("timeout", "rejected")

    @pytest.mark.asyncio
    async def test_returns_receipt_with_action_id(self):
        server = FridayMCPServer()
        with patch.object(server, "_get_ledger") as mock_get_ledger:
            mock_ledger = MagicMock()
            mock_ledger.queue_action.return_value = "aid-receipt"
            mock_ledger.wait_for_approval = AsyncMock(return_value=True)
            mock_get_ledger.return_value = mock_ledger
            result = await server.handle_request_approval({
                "component": "X",
                "action": "y",
                "timeout": 1,
            })
        assert "receipt" in result
        assert result["receipt"]["action"] == "request_approval"
        assert "timestamp" in result["receipt"]


# ---------------------------------------------------------------------------
# handle_chat — input validation + audit logging
# ---------------------------------------------------------------------------


class TestHandleChat:
    """Test handle_chat input validation."""

    @pytest.mark.asyncio
    async def test_chat_missing_message_returns_error(self):
        server = FridayMCPServer()
        result = await server.handle_chat({})
        assert result["status"] == "error"
        assert "No message" in result["message"]

    @pytest.mark.asyncio
    async def test_chat_empty_message_returns_error(self):
        server = FridayMCPServer()
        result = await server.handle_chat({"message": ""})
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_chat_logs_to_audit_trail(self):
        """handle_chat should log the call to the ledger (even though it's read-only)."""
        server = FridayMCPServer()
        with patch.object(server, "_get_ledger") as mock_get_ledger, \
             patch.object(server, "_get_glm_brain") as mock_get_glm:
            mock_ledger = MagicMock()
            mock_ledger.queue_action.return_value = "aid-chat"
            mock_get_ledger.return_value = mock_ledger
            mock_glm = MagicMock()
            mock_glm.available.return_value = False
            mock_get_glm.return_value = mock_glm
            with patch("core.brain.FridayBrain") as MockBrain:
                mock_brain_inst = MagicMock()
                async def _empty_stream(msg):
                    return
                    yield  # makes it an async generator
                mock_brain_inst.chat_stream = _empty_stream
                MockBrain.return_value = mock_brain_inst
                await server.handle_chat({"message": "hello"})
        # queue_action should have been called for audit
        mock_ledger.queue_action.assert_called_once()
        args, _ = mock_ledger.queue_action.call_args
        assert args[0] == "MCPChat"


# ---------------------------------------------------------------------------
# handle_vision, handle_web_search — input validation
# ---------------------------------------------------------------------------


class TestVisionAndSearchValidation:
    """Test handle_vision and handle_web_search input validation."""

    @pytest.mark.asyncio
    async def test_vision_missing_image_returns_error(self):
        server = FridayMCPServer()
        result = await server.handle_vision({})
        assert result["status"] == "error"
        assert "No image" in result["message"]

    @pytest.mark.asyncio
    async def test_vision_glm_unavailable_returns_error(self):
        server = FridayMCPServer()
        with patch.object(server, "_get_glm_brain") as mock_get_glm:
            mock_glm = MagicMock()
            mock_glm.available.return_value = False
            mock_get_glm.return_value = mock_glm
            result = await server.handle_vision({"image_base64": "abc"})
        assert result["status"] == "error"
        assert "GLM not available" in result["message"]

    @pytest.mark.asyncio
    async def test_web_search_missing_query_returns_error(self):
        server = FridayMCPServer()
        result = await server.handle_web_search({})
        assert result["status"] == "error"
        assert "No search query" in result["message"]

    @pytest.mark.asyncio
    async def test_web_search_glm_unavailable_returns_error(self):
        server = FridayMCPServer()
        with patch.object(server, "_get_glm_brain") as mock_get_glm:
            mock_glm = MagicMock()
            mock_glm.available.return_value = False
            mock_get_glm.return_value = mock_glm
            result = await server.handle_web_search({"query": "test"})
        assert result["status"] == "error"
        assert "GLM not available" in result["message"]


# ---------------------------------------------------------------------------
# Fail-closed behavior for gated tools (regression for SEC-4)
# ---------------------------------------------------------------------------


class TestFailClosed:
    """SECURITY REGRESSION: gated tools MUST fail closed when ledger is None."""

    @pytest.mark.asyncio
    async def test_image_generation_fails_closed_without_ledger(self):
        server = FridayMCPServer()
        with patch.object(server, "_get_ledger", return_value=None):
            result = await server.handle_image_generation({"prompt": "x"})
        assert result["status"] == "error"
        assert "Ledger unavailable" in result["message"]

    @pytest.mark.asyncio
    async def test_video_generation_fails_closed_without_ledger(self):
        server = FridayMCPServer()
        with patch.object(server, "_get_ledger", return_value=None):
            result = await server.handle_video_generation({"prompt": "x"})
        assert result["status"] == "error"
        assert "Ledger unavailable" in result["message"]

    @pytest.mark.asyncio
    async def test_code_execution_fails_closed_without_ledger(self):
        server = FridayMCPServer()
        with patch.object(server, "_get_ledger", return_value=None):
            result = await server.handle_code_execution({"code": "print('x')"})
        assert result["status"] == "error"
        assert "Ledger unavailable" in result["message"]

    @pytest.mark.asyncio
    async def test_image_generation_rejected_returns_error(self):
        """When approval is denied, image generation must NOT proceed."""
        server = FridayMCPServer()
        with patch.object(server, "_get_ledger") as mock_get_ledger:
            mock_ledger = MagicMock()
            mock_ledger.queue_action.return_value = "aid"
            mock_ledger.wait_for_approval = AsyncMock(return_value=False)
            mock_get_ledger.return_value = mock_ledger
            result = await server.handle_image_generation({"prompt": "x"})
        assert result["status"] == "error"
        assert "approval" in result["message"].lower()


# ---------------------------------------------------------------------------
# _make_receipt
# ---------------------------------------------------------------------------


class TestReceiptCreation:
    """Test _make_receipt returns proper receipt structure."""

    def test_receipt_has_required_fields(self):
        server = FridayMCPServer()
        receipt = server._make_receipt("chat", {"status": "success"})
        assert "action" in receipt
        assert "timestamp" in receipt
        assert "status" in receipt
        assert "data" in receipt
        assert receipt["action"] == "chat"
        assert receipt["status"] == "success"

    def test_receipt_extracts_data_from_result(self):
        server = FridayMCPServer()
        receipt = server._make_receipt("vision", {
            "status": "success",
            "receipt": {"model": "glm-4v", "len": 100},
        })
        # "data" should be the inner receipt (or message if no receipt)
        assert receipt["data"] == {"model": "glm-4v", "len": 100}

    def test_receipt_falls_back_to_message_when_no_receipt(self):
        server = FridayMCPServer()
        receipt = server._make_receipt("chat", {
            "status": "error",
            "message": "something failed",
        })
        # When no "receipt" key, use "message"
        assert receipt["data"] == "something failed"

    def test_receipt_timestamp_is_iso_format(self):
        server = FridayMCPServer()
        receipt = server._make_receipt("x", {"status": "ok"})
        # ISO format contains a "T" separator
        assert "T" in receipt["timestamp"]
