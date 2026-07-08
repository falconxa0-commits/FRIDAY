"""Tests for the Friday SDK — verifies correct endpoint construction."""
import pytest


class TestFridaySDK:
    """Test the Friday SDK client."""

    def test_sdk_imports(self):
        from sdk.friday_sdk import FridayClient
        assert FridayClient is not None

    def test_sdk_constructor_sets_base_url(self):
        from sdk.friday_sdk import FridayClient
        client = FridayClient(base_url="http://example.com:8080", api_token="test")
        assert client.base_url == "http://example.com:8080"
        assert client.api_token == "test"
        assert "Authorization" in client._headers

    def test_sdk_has_all_methods(self):
        from sdk.friday_sdk import FridayClient
        client = FridayClient()
        assert hasattr(client, "chat")
        assert hasattr(client, "chat_stream")
        assert hasattr(client, "generate_image")
        assert hasattr(client, "run_skill")
        assert hasattr(client, "get_memory")
        assert hasattr(client, "set_identity")
        assert hasattr(client, "set_goal")
        assert hasattr(client, "notify")
        assert hasattr(client, "health")

    def test_sdk_strips_trailing_slash(self):
        from sdk.friday_sdk import FridayClient
        client = FridayClient(base_url="http://localhost:8000/")
        assert client.base_url == "http://localhost:8000"
