"""GLM Brain — ZhipuAI-powered LLM backend for FRIDAY.

Provides chat streaming, vision analysis, and web search via the
ZhipuAI SDK (zhipuai package).  Falls back gracefully when the
package or API key is unavailable.
"""

import asyncio
import base64
import logging
import os
from typing import AsyncGenerator, Dict, List, Optional

from config.settings import GLM_API_KEY

logger = logging.getLogger("GLMBrain")

# ---------------------------------------------------------------------------
# Lazy import — mark as unavailable if zhipuai is not installed
# ---------------------------------------------------------------------------

_ZhipuAI = None
_ZHIPU_AVAILABLE = False

try:
    from zhipuai import ZhipuAI as _ZhipuClient  # noqa: N813
    _ZhipuAI = _ZhipuClient
    _ZHIPU_AVAILABLE = True
except ImportError:
    logger.info(
        "zhipuai package not installed. GLMBrain will be non-functional. "
        "Install with: pip install zhipuai"
    )
except Exception as exc:
    logger.warning(f"Unexpected error importing zhipuai: {exc}")


class GLMBrain:
    """Async GLM brain with streaming chat, vision, and web search.

    Supported model tiers:
        free    -> glm-4-flash   (free tier)
        standard -> glm-4
        plus    -> glm-4-plus
        vision  -> glm-4v        (image understanding)
        long    -> glm-4-long    (long-context)
    """

    MODELS = {
        "free": "glm-4-flash",
        "standard": "glm-4",
        "plus": "glm-4-plus",
        "vision": "glm-4v",
        "long": "glm-4-long",
    }

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or GLM_API_KEY
        self._client = None
        self._model_name = os.getenv("GLM_MODEL", self.MODELS["free"])
        self._history: List[Dict] = []

        if not _ZHIPU_AVAILABLE:
            logger.warning(
                "zhipuai package not installed — GLMBrain will be non-functional"
            )
            return

        if not self.api_key:
            logger.warning(
                "No GLM_API_KEY provided — GLMBrain will be non-functional"
            )
            return

    # ------------------------------------------------------------------
    # Lazy client construction (guarded by API key)
    # ------------------------------------------------------------------

    def _ensure_client(self):
        """Build the ZhipuAI client on first use, guarded by the API key."""
        if self._client is not None:
            return self._client

        if not _ZHIPU_AVAILABLE or not self.api_key:
            return None

        try:
            self._client = _ZhipuAI(api_key=self.api_key)
            logger.info(f"GLMBrain initialised with model={self._model_name}")
            return self._client
        except Exception as exc:
            logger.error(f"GLMBrain client construction failed: {exc}")
            return None

    def available(self) -> bool:
        """Return True if the GLM brain is ready to use."""
        if not _ZHIPU_AVAILABLE or not self.api_key:
            return False
        return self._ensure_client() is not None

    # ------------------------------------------------------------------
    # System prompt support
    # ------------------------------------------------------------------

    _system_prompt: str = ""

    def set_system_prompt(self, prompt: str):
        """Set a system prompt that will be prepended to every request."""
        self._system_prompt = prompt

    # ------------------------------------------------------------------
    # Conversation history
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
        model_tier: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat response from the GLM model.

        Uses ``asyncio.to_thread`` so that the synchronous ZhipuAI
        SDK call does not block the event loop.
        """
        client = self._ensure_client()
        if client is None:
            yield "Error: GLM client not initialised (missing API key or package)."
            return

        model = self.MODELS.get(model_tier, self._model_name) if model_tier else self._model_name
        sys_prompt = system_prompt or self._system_prompt

        # Build messages payload
        messages: List[Dict] = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})

        for entry in self._history:
            messages.append(entry)

        messages.append({"role": "user", "content": message})

        try:
            def _call_stream():
                """Synchronous streaming call — run in a thread."""
                chunks = []
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=True,
                )
                for chunk in response:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, "content") and delta.content:
                        chunks.append(delta.content)
                return chunks

            chunk_list = await asyncio.to_thread(_call_stream)

            full_response = ""
            for text in chunk_list:
                full_response += text
                yield text

            # Store in history
            self._history.append({"role": "user", "content": message})
            self._history.append({"role": "assistant", "content": full_response})

            # Keep history manageable
            if len(self._history) > 40:
                self._history = self._history[-20:]

        except Exception as exc:
            logger.error(f"GLM streaming error: {exc}")
            yield f"Error communicating with GLM: {exc}"

    # ------------------------------------------------------------------
    # Vision analysis (glm-4v)
    # ------------------------------------------------------------------

    async def vision_analyze(
        self,
        image_base64: str,
        prompt: str = "Describe this image in detail.",
        model_tier: str = "vision",
    ) -> str:
        """Analyze an image using the GLM-4V vision model.

        Args:
            image_base64: Base64-encoded image string (without data URI prefix).
            prompt: The question or instruction about the image.
            model_tier: Model tier to use (default: "vision" -> glm-4v).

        Returns:
            The model's text response.
        """
        client = self._ensure_client()
        if client is None:
            return "Error: GLM client not initialised (missing API key or package)."

        model = self.MODELS.get(model_tier, self.MODELS["vision"])

        try:
            def _call_vision():
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_base64}"
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": prompt,
                                },
                            ],
                        }
                    ],
                )
                return response.choices[0].message.content

            result = await asyncio.to_thread(_call_vision)
            return result or ""

        except Exception as exc:
            logger.error(f"GLM vision analysis error: {exc}")
            return f"Error: GLM vision analysis failed — {exc}"

    # ------------------------------------------------------------------
    # Web search (via ZhipuAI web_search tool)
    # ------------------------------------------------------------------

    async def web_search(self, query: str, max_results: int = 5) -> List[Dict]:
        """Perform web search using the ZhipuAI built-in web_search tool.

        Returns a list of dicts with 'title', 'url', 'snippet' keys.

        ZhipuAI's web_search tool returns results in the response's
        ``web_search`` field (a list of search-result objects), NOT
        in ``tool_calls``. The previous implementation parsed
        ``tool_calls`` (which contains the LLM's *function-call
        arguments*, not search results) and fell back to returning
        the LLM's synthesised prose answer as a fake "search result" —
        effectively returning hallucinations labelled as search.

        This corrected implementation:
            1. Extracts real search results from ``response.web_search``
            2. Falls back to ``choice.message.content`` ONLY if no
               web_search field is present (and labels it clearly as
               "LLM synthesised answer" — not a search result)
        """
        client = self._ensure_client()
        if client is None:
            return []

        try:
            def _call_search():
                response = client.chat.completions.create(
                    model=self._model_name,
                    messages=[
                        {"role": "user", "content": query},
                    ],
                    tools=[{"type": "web_search", "web_search": {"enable": True}}],
                )
                return response

            response = await asyncio.to_thread(_call_search)

            results: List[Dict] = []

            # PRIMARY: ZhipuAI returns real search results in
            # ``response.web_search`` (a list of dicts with
            # title/url/snippet/content keys).
            web_search_data = getattr(response, "web_search", None)
            if web_search_data and isinstance(web_search_data, list):
                for item in web_search_data[:max_results]:
                    if isinstance(item, dict):
                        results.append({
                            "title": item.get("title", "") or item.get("media", ""),
                            "url": item.get("link", "") or item.get("url", ""),
                            "snippet": (item.get("content", "") or
                                        item.get("snippet", "") or
                                        item.get("summary", ""))[:500],
                            "source": "zhipuai_web_search",
                        })

            # FALLBACK 1: Some ZhipuAI SDK versions put results in
            # ``choices[0].message.tool_calls`` with name="web_search".
            # We extract the *search-result array* (not the function
            # arguments) if the tool-call result is structured that way.
            if not results:
                choice = response.choices[0] if response.choices else None
                if choice and hasattr(choice, "message"):
                    msg = choice.message
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        import json as _json
                        for tool_call in msg.tool_calls:
                            # Look for a tool_call with name == "web_search"
                            func = getattr(tool_call, "function", None)
                            if not func:
                                continue
                            tool_name = getattr(func, "name", "") or ""
                            if "search" not in tool_name.lower():
                                continue
                            try:
                                data = _json.loads(func.arguments or "{}")
                                if isinstance(data, list):
                                    for item in data[:max_results]:
                                        if isinstance(item, dict):
                                            results.append({
                                                "title": item.get("title", ""),
                                                "url": item.get("url", "") or item.get("link", ""),
                                                "snippet": (item.get("snippet", "") or
                                                            item.get("content", ""))[:500],
                                                "source": "zhipuai_tool_call",
                                            })
                                elif isinstance(data, dict) and "results" in data:
                                    for item in (data.get("results") or [])[:max_results]:
                                        if isinstance(item, dict):
                                            results.append({
                                                "title": item.get("title", ""),
                                                "url": item.get("url", "") or item.get("link", ""),
                                                "snippet": (item.get("snippet", "") or
                                                            item.get("content", ""))[:500],
                                                "source": "zhipuai_tool_call",
                                            })
                            except Exception as e:
                                logger.debug(f"Non-critical error parsing tool_call: {e}")

            # FALLBACK 2: If we still have no real search results,
            # return the LLM's synthesised answer but LABEL IT CLEARLY
            # as "LLM synthesised answer" — not as a search result.
            # This prevents downstream code from treating hallucinated
            # text as if it were a real web-search hit.
            if not results:
                choice = response.choices[0] if response.choices else None
                if choice and hasattr(choice, "message") and choice.message.content:
                    results.append({
                        "title": "GLM Synthesised Answer (NOT a real search result)",
                        "url": "",
                        "snippet": choice.message.content[:500],
                        "source": "llm_synthesised",
                        "warning": (
                            "No web_search results were returned by the API. "
                            "This is the LLM's synthesised answer, not a "
                            "real search result. Treat as LLM output, not "
                            "as a cited source."
                        ),
                    })

            return results[:max_results]

        except Exception as exc:
            logger.error(f"GLM web search error: {exc}")
            return []

    # ------------------------------------------------------------------
    # Code interpreter (via ZhipuAI code_interpreter tool)
    # ------------------------------------------------------------------

    async def code_interpreter(
        self,
        code: str,
        language: str = "python",
    ) -> Dict:
        """Execute code using the ZhipuAI Code Interpreter tool.

        Args:
            code: The source code to execute.
            language: Programming language (default: python).

        Returns:
            Dict with 'output', 'status', and optional 'error' keys.
        """
        client = self._ensure_client()
        if client is None:
            return {"status": "error", "output": "", "error": "GLM client not initialised"}

        try:
            def _call_code():
                response = client.chat.completions.create(
                    model=self._model_name,
                    messages=[
                        {
                            "role": "user",
                            "content": f"Execute the following {language} code and return the output:\n\n```{language}\n{code}\n```",
                        },
                    ],
                    tools=[{"type": "code_interpreter", "code_interpreter": {"enable": True}}],
                )
                return response

            response = await asyncio.to_thread(_call_code)

            choice = response.choices[0] if response.choices else None
            if choice and hasattr(choice, "message"):
                msg = choice.message
                # Extract code interpreter output
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tool_call in msg.tool_calls:
                        if hasattr(tool_call, "function") and tool_call.function:
                            import json as _json
                            try:
                                return {
                                    "status": "success",
                                    "output": tool_call.function.arguments,
                                }
                            except Exception as e:
                                logger.debug(f"Non-critical error: {e}")

                # Fallback: return the text content
                content = msg.content if hasattr(msg, "content") else str(msg)
                return {"status": "success", "output": content}

            return {"status": "error", "output": "", "error": "No response from code interpreter"}

        except Exception as exc:
            logger.error(f"GLM code interpreter error: {exc}")
            return {"status": "error", "output": "", "error": str(exc)}
