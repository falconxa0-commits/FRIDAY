"""Gemini Brain — Google GenAI-powered LLM backend for FRIDAY.

Uses the modern ``google.genai`` SDK instead of the deprecated
``google.generativeai`` module.  Fully async — the old sync
``send_message`` call has been replaced with ``await model.generate_content``
inside an async generator.
"""

import asyncio
import logging
import os
from typing import AsyncGenerator, List, Dict, Optional

from config.settings import GEMINI_API_KEY

logger = logging.getLogger("GeminiBrain")


class GeminiBrain:
    """Async Gemini brain with system prompt and conversation history."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or GEMINI_API_KEY
        if not self.api_key:
            logger.warning("No GEMINI_API_KEY provided — GeminiBrain will be non-functional")
            self._client = None
            self._model_name = "gemini-2.0-flash"
            self._history: List[Dict] = []
            return

        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            self._model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
            logger.info(f"GeminiBrain initialised with model={self._model_name}")
        except ImportError:
            logger.error(
                "google-genai package not installed. "
                "Install with: pip install google-genai"
            )
            self._client = None
        except Exception as exc:
            logger.error(f"GeminiBrain init failed: {exc}")
            self._client = None

        self._history: List[Dict] = []

    # ------------------------------------------------------------------
    # System prompt support
    # ------------------------------------------------------------------

    def set_system_prompt(self, prompt: str):
        """Set a system prompt that will be prepended to every request."""
        self._system_prompt = prompt

    _system_prompt: str = ""

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
    # Streaming chat
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        message: str,
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat response from Gemini.

        Uses ``generate_content_stream`` with async iteration so that
        the event loop is never blocked by a synchronous call.
        """
        if self._client is None:
            yield "Error: Gemini client not initialised (missing API key or package)."
            return

        sys_prompt = system_prompt or self._system_prompt

        # Build contents list for the API
        contents: list = []
        for entry in self._history:
            contents.append(entry)
        contents.append({"role": "user", "parts": [{"text": message}]})

        config = {}
        if sys_prompt:
            config["system_instruction"] = sys_prompt

        try:
            response = await asyncio.to_thread(
                self._client.models.generate_content_stream,
                model=self._model_name,
                contents=contents,
                config=config,
            )

            full_response = ""
            for chunk in response:
                if chunk.text:
                    full_response += chunk.text
                    yield chunk.text

            # Store in history
            self._history.append({"role": "user", "parts": [{"text": message}]})
            self._history.append({"role": "model", "parts": [{"text": full_response}]})

            # Keep history manageable
            if len(self._history) > 40:
                self._history = self._history[-20:]

        except Exception as exc:
            logger.error(f"Gemini streaming error: {exc}")
            yield f"Error communicating with Gemini: {exc}"
