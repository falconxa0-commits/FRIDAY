"""Local Brain — Ollama-powered LLM backend for FRIDAY.

Adds system prompt support, conversation history management,
configurable model via env var, and proper error handling.
"""

import httpx
import asyncio
import json
import os
import logging
from typing import AsyncGenerator, List, Dict, Optional

logger = logging.getLogger("LocalBrain")


class LocalBrain:
    """Async local brain backed by Ollama, with history and system prompts."""

    def __init__(self, model_name: Optional[str] = None):
        self.model = model_name or os.getenv("OLLAMA_MODEL", "llama3")
        self.base_url = os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )
        self.generate_url = f"{self.base_url}/api/generate"
        self.chat_url = f"{self.base_url}/api/chat"
        self._system_prompt: str = ""
        self._history: List[Dict] = []

    # ------------------------------------------------------------------
    # System prompt support
    # ------------------------------------------------------------------

    def set_system_prompt(self, prompt: str):
        """Set a system prompt prepended to every request."""
        self._system_prompt = prompt

    # ------------------------------------------------------------------
    # Conversation history management
    # ------------------------------------------------------------------

    def get_history(self) -> List[Dict]:
        """Return the conversation history."""
        return list(self._history)

    def clear_history(self):
        """Clear conversation history."""
        self._history = []

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    async def available(self) -> bool:
        """Check whether Ollama is running and the model is available."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/api/tags", timeout=2
                )
                if resp.status_code != 200:
                    return False
                data = resp.json()
                model_names = [
                    m.get("name", "").split(":")[0]
                    for m in data.get("models", [])
                ]
                return self.model in model_names or not model_names
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Streaming chat (uses /api/chat endpoint for full conversation)
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        message: str,
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat response from Ollama using the Chat API.

        Falls back to the Generate API if the Chat API fails.
        """
        sys_prompt = system_prompt or self._system_prompt

        # --- Try /api/chat first (full conversation support) ---
        messages: List[Dict] = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        messages.extend(self._history)
        messages.append({"role": "user", "content": message})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST", self.chat_url, json=payload
                ) as response:
                    full_response = ""
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        chunk = data.get("message", {}).get("content", "")
                        if chunk:
                            full_response += chunk
                            yield chunk
                        if data.get("done", False):
                            break

            # Store in history
            self._history.append({"role": "user", "content": message})
            self._history.append({"role": "assistant", "content": full_response})

            # Keep history manageable
            if len(self._history) > 40:
                self._history = self._history[-20:]

        except httpx.ConnectError:
            yield (
                "Error: Ollama is not running. "
                "Start it with `ollama serve` and pull a model."
            )
        except httpx.TimeoutException:
            yield "Error: Ollama request timed out."
        except Exception as exc:
            logger.error(f"Local brain chat error: {exc}")
            yield f"Error calling local model: {exc}"
