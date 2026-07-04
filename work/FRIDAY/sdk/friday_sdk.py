"""Friday SDK — clean Python client for the Friday API.

Usage:
    from sdk.friday_sdk import FridayClient
    client = FridayClient(base_url="http://localhost:8000", api_token=os.environ["FRIDAY_API_TOKEN"])
    response = await client.chat("hello")
    image = await client.generate_image("sunset over Lagos")
"""
import asyncio
import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger("friday_sdk")


class FridayClient:
    """Async client for the Friday AI Assistant API."""

    def __init__(self, base_url: str = "http://localhost:8000", api_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self._headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}

    async def chat(self, message: str, user_name: str = "User") -> str:
        """Send a chat message and get a response."""
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/api/chat",
                json={"message": message, "user_name": user_name},
                headers=self._headers,
                timeout=120,
            )
            r.raise_for_status()
            return r.json().get("response", "")

    async def chat_stream(self, message: str):
        """Stream a chat response via SSE."""
        url = f"{self.base_url}/api/chat/stream?message={message}"
        if self.api_token:
            url += f"&token={self.api_token}"
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url, timeout=120) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        payload = line[6:].strip()
                        if payload == "[DONE]":
                            return
                        try:
                            evt = json.loads(payload)
                            if evt.get("type") == "text":
                                yield evt.get("content", "")
                        except json.JSONDecodeError:
                            pass

    async def generate_image(self, prompt: str) -> dict:
        """Generate an image via CogView-3."""
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/api/integrations/execute",
                json={"service": "ImageGen", "action": "generate_image",
                      "params": {"prompt": prompt}},
                headers=self._headers,
                timeout=60,
            )
            r.raise_for_status()
            return r.json()

    async def run_skill(self, skill_name: str, params: Optional[dict] = None) -> dict:
        """Run a registered skill."""
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/api/integrations/execute",
                json={"service": "SkillRunner", "action": "run_skill",
                      "params": {"skill": skill_name, **(params or {})}},
                headers=self._headers,
                timeout=120,
            )
            r.raise_for_status()
            return r.json()

    async def get_memory(self) -> list:
        """List all stored memories."""
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/api/memory/all",
                headers=self._headers,
            )
            r.raise_for_status()
            return r.json()

    async def set_identity(self, mode: str) -> dict:
        """Switch identity mode."""
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/api/identity/{mode}",
                headers=self._headers,
            )
            r.raise_for_status()
            return r.json()

    async def set_goal(self, description: str, target_date: str, goal_type: str = "project") -> dict:
        """Set a new goal."""
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/api/goals",
                json={"description": description, "target_date": target_date, "type": goal_type},
                headers=self._headers,
            )
            r.raise_for_status()
            return r.json()

    async def notify(self, message: str) -> dict:
        """Send a push notification."""
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/api/notify",
                json={"message": message},
                headers=self._headers,
            )
            r.raise_for_status()
            return r.json()

    async def health(self) -> dict:
        """Check Friday's deep health."""
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self.base_url}/api/health/deep", timeout=30)
            r.raise_for_status()
            return r.json()
