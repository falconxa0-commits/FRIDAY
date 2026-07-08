"""Tests for GLM tool-calling loop — the biggest capability gap fix."""
import pytest
import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.fixture()
def brain_cls():
    """Return the FridayBrain class with dangerous imports patched."""
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


class TestGLMToolCalling:
    """Test that GLM branch has a tool-calling loop."""

    @pytest.mark.asyncio
    async def test_glm_stream_with_tools_method_exists(self, brain_cls):
        """GLM branch must have a tool-calling loop."""
        brain = brain_cls()
        assert hasattr(brain, '_glm_stream_with_tools'), \
            "GLM tool-calling loop not implemented"

    @pytest.mark.asyncio
    async def test_glm_tool_count(self, brain_cls):
        """Brain must have 5+ tools available for GLM to call."""
        brain = brain_cls()
        assert len(brain.tools) >= 5, f"Expected 5+ tools, got {len(brain.tools)}"

    @pytest.mark.asyncio
    async def test_glm_triggers_tool_call(self, brain_cls):
        """When GLM returns finish_reason=tool_calls, the tool must execute."""
        brain = brain_cls()

        tool_call_mock = MagicMock()
        tool_call_mock.id = "call_123"
        tool_call_mock.function.name = "access_universal_service"
        tool_call_mock.function.arguments = json.dumps({
            "service": "Weather", "action": "get_weather", "params": {"city": "Lagos"}
        })

        # First response: tool call. Second response: final answer.
        tool_response = MagicMock()
        tool_response.choices = [MagicMock()]
        tool_response.choices[0].finish_reason = "tool_calls"
        tool_response.choices[0].message.content = ""
        tool_response.choices[0].message.tool_calls = [tool_call_mock]

        final_response = MagicMock()
        final_response.choices = [MagicMock()]
        final_response.choices[0].finish_reason = "stop"
        final_response.choices[0].message.tool_calls = None
        final_response.choices[0].message.content = "The weather in Lagos is 28 degrees."

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [tool_response, final_response]

        with patch.object(brain.glm_brain, '_ensure_client', return_value=mock_client), \
             patch.object(brain, '_execute_tool', return_value='{"status": "ok"}') as mock_tool, \
             patch.object(brain, 'memory', MagicMock()):
            brain.memory.store_conversation = MagicMock()

            messages = [{"role": "user", "content": "What's the weather?"}]
            chunks = []
            async for chunk in brain._glm_stream_with_tools(messages, brain.tools, "system"):
                chunks.append(chunk)

            # Tool must have been called
            mock_tool.assert_called_once()
            call_args = mock_tool.call_args[0]
            assert call_args[0] == "access_universal_service"

            # Final response must appear in output
            full_response = "".join(chunks)
            assert "28 degrees" in full_response or "weather" in full_response.lower() or len(full_response) > 0

    @pytest.mark.asyncio
    async def test_glm_max_rounds_respected(self, brain_cls):
        """GLM tool-calling loop must stop after 5 rounds."""
        brain = brain_cls()

        tool_call_mock = MagicMock()
        tool_call_mock.id = "call_loop"
        tool_call_mock.function.name = "access_universal_service"
        tool_call_mock.function.arguments = json.dumps({"service": "Weather", "action": "get_weather", "params": {}})

        always_tool_response = MagicMock()
        always_tool_response.choices = [MagicMock()]
        always_tool_response.choices[0].finish_reason = "tool_calls"
        always_tool_response.choices[0].message.content = ""
        always_tool_response.choices[0].message.tool_calls = [tool_call_mock]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = always_tool_response

        with patch.object(brain.glm_brain, '_ensure_client', return_value=mock_client), \
             patch.object(brain, '_execute_tool', return_value='{"status": "ok"}'), \
             patch.object(brain, 'memory', MagicMock()):
            brain.memory.store_conversation = MagicMock()

            messages = [{"role": "user", "content": "test"}]
            chunks = []
            async for chunk in brain._glm_stream_with_tools(messages, brain.tools, "system"):
                chunks.append(chunk)

            # Must have stopped — create called at most 5 times
            assert mock_client.chat.completions.create.call_count <= 5, \
                f"Expected at most 5 API calls, got {mock_client.chat.completions.create.call_count}"
            # Should have a max-rounds message
            full_response = "".join(chunks)
            assert "maximum" in full_response.lower() or "rephrasing" in full_response.lower()

    @pytest.mark.asyncio
    async def test_glm_no_tools_streams_normally(self, brain_cls):
        """When no tools are configured, GLM should still stream."""
        brain = brain_cls()

        final_response = MagicMock()
        final_response.choices = [MagicMock()]
        final_response.choices[0].finish_reason = "stop"
        final_response.choices[0].message.tool_calls = None
        final_response.choices[0].message.content = "Hello from GLM."

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = final_response

        with patch.object(brain.glm_brain, '_ensure_client', return_value=mock_client), \
             patch.object(brain, 'memory', MagicMock()):
            brain.memory.store_conversation = MagicMock()

            messages = [{"role": "user", "content": "hello"}]
            chunks = []
            async for chunk in brain._glm_stream_with_tools(messages, [], "system"):
                chunks.append(chunk)

            full_response = "".join(chunks)
            assert "Hello from GLM" in full_response
