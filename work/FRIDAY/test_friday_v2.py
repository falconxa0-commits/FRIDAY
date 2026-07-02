import unittest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
import asyncio
import os
from core.brain import FridayBrain
from control.file_manager import FileManager
from fastapi.testclient import TestClient
from api.main import app
from config.settings import FRIDAY_API_TOKEN

class TestFridayRound2(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("anthropic.AsyncAnthropic")
    async def test_brain_tool_definition(self, mock_anthropic):
        # Force API key check to pass
        with patch("core.brain.ANTHROPIC_API_KEY", "fake_key"):
            brain = FridayBrain()
            self.assertTrue(len(brain.tools) > 0)
            self.assertEqual(brain.tools[0]["name"], "access_universal_service")

    def test_api_auth_enforcement(self):
        # 401 if header is missing (FastAPI default for HTTPBearer)
        response = self.client.post("/api/chat", json={"message": "hello"})
        self.assertEqual(response.status_code, 401)
        
        # 403 if token is wrong (if FRIDAY_API_TOKEN is set)
        if FRIDAY_API_TOKEN:
            response = self.client.post("/api/chat", 
                                        json={"message": "hello"}, 
                                        headers={"Authorization": "Bearer wrong_token"})
            self.assertEqual(response.status_code, 403)

    def test_file_manager_sibling_escape(self):
        fm = FileManager()
        workspace_root = fm.workspace_root
        with self.assertRaises(PermissionError):
            fm._safe_path("../friday_workspace_evil/secret.txt")

    def test_crypto_tracker_logic(self):
        from integrations.crypto_tracker import CryptoTracker
        ct = CryptoTracker()
        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = {"bitcoin": {"usd": 50000}}
            mock_get.return_value.status_code = 200
            res = ct.get_price("bitcoin")
            self.assertIn("$50000", res)

    @patch("core.universal_connector.UniversalConnector.execute_action")
    @patch("anthropic.AsyncAnthropic")
    async def test_tool_call_loop_integration(self, mock_anthropic, mock_execute):
        # Force API key check to pass
        with patch("core.brain.ANTHROPIC_API_KEY", "fake_key"):
            brain = FridayBrain()
            mock_stream = MagicMock()
            mock_anthropic.return_value.messages.stream.return_value.__aenter__.return_value = mock_stream
            
            event1 = MagicMock()
            event1.type = "content_block_delta"
            event1.delta.type = "text_delta"
            event1.delta.text = "Searching..."
            mock_stream.__aiter__.return_value = [event1]
            
            mock_tool_use = MagicMock()
            mock_tool_use.type = "tool_use"
            mock_tool_use.id = "call_1"
            mock_tool_use.input = {"service": "Weather", "action": "get_weather", "params": {"location": "Lagos"}}
            
            mock_final_msg = MagicMock()
            mock_final_msg.content = [mock_tool_use]
            mock_stream.get_final_message = AsyncMock(return_value=mock_final_msg)
            
            mock_execute.return_value = "Sunny, 30C"
            
            mock_stream2 = MagicMock()
            event2 = MagicMock()
            event2.type = "content_block_delta"
            event2.delta.type = "text_delta"
            event2.delta.text = "It is sunny in Lagos."
            mock_stream2.__aiter__.return_value = [event2]
            
            mock_final_msg2 = MagicMock()
            mock_final_msg2.content = [MagicMock(type="text", text="It is sunny in Lagos.")]
            mock_stream2.get_final_message = AsyncMock(return_value=mock_final_msg2)
            
            mock_anthropic.return_value.messages.stream.side_effect = [
                MagicMock(__aenter__=AsyncMock(return_value=mock_stream)),
                MagicMock(__aenter__=AsyncMock(return_value=mock_stream2))
            ]
            
            responses = []
            async for chunk in brain.chat_stream("How is the weather?"):
                responses.append(chunk)
                
            full_resp = "".join(responses)
            self.assertIn("Searching...", full_resp)
            self.assertIn("It is sunny in Lagos.", full_resp)
            mock_execute.assert_called_once()
            self.assertEqual(len(brain.conversation_history), 4)

if __name__ == "__main__":
    unittest.main()
