"""Provider router — dispatch chat requests to the appropriate LLM provider.

Extracted from ``core/brain.py`` (WAVE2-ARCH refactor). ``ProviderRouter``
encapsulates the if/elif provider dispatch chain that used to live inline
in ``FridayBrain.chat_stream``.

Provider precedence
-------------------
1. ``glm``     → Z.ai GLM-4-Flash (free default; tool-calling loop)
2. ``ollama``  → LocalBrain (Ollama)
3. ``gemini``  → GeminiBrain (also auto-selected for very long messages)
4. fallback    → Claude (Anthropic) with tool-calling loop

If ``ANTHROPIC_API_KEY`` is not configured, the fallback path tries
GLM → LocalBrain → a hard error message.

Backward compatibility
----------------------
``FridayBrain.chat_stream`` delegates to ``ProviderRouter.route`` but
retains its original signature and behaviour. ``_glm_stream_with_tools``
stays on ``FridayBrain`` because it relies on ``brain._execute_tool`` and
``brain.memory``; the router invokes it via ``brain._glm_stream_with_tools``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator, Optional

# Lazy import of optional LLM SDKs — these are NOT required for the
# default GLM-only install, so we must not fail at module import time.
try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

from config.settings import ANTHROPIC_API_KEY

logger = logging.getLogger("FridayBrain")


class ProviderRouter:
    """Routes chat requests to the appropriate LLM provider."""

    def __init__(self, glm_brain=None, claude_client=None, gemini_brain=None, local_brain=None):
        # The brains are stored for standalone use / testing. In normal
        # operation the router reads fresh state off the ``brain`` argument
        # passed to ``route()``, so stale references here do not cause bugs.
        self.glm_brain = glm_brain
        self._claude_client = claude_client
        self.gemini_brain = gemini_brain
        self.local_brain = local_brain

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def route(
        self,
        provider: str,
        message: str,
        system_prompt: str,
        tools: list,
        brain,
    ) -> AsyncGenerator[str, None]:
        """Route to the appropriate provider and yield response chunks.

        Parameters
        ----------
        provider:
            The provider name ("glm", "ollama", "gemini", "claude").
        message:
            The user's message text.
        system_prompt:
            Pre-built system prompt (constructed by ``FridayBrain``).
        tools:
            The tool definitions list (Claude / GLM tool schemas).
        brain:
            The owning ``FridayBrain`` instance — used to access lazy
            clients (``brain.claude_client``), shared state
            (``brain.conversation_history``), and helper methods
            (``brain._inject_rag_context``, ``brain._glm_stream_with_tools``,
            ``brain._execute_tool``).

        Dispatches to provider-specific helpers:
            - :meth:`_route_glm` for the GLM (Z.ai) path
            - :meth:`_route_ollama` for the Ollama / local path
            - inline Gemini dispatch (auto-selected for long messages)
            - :meth:`_route_fallback_no_anthropic` when no Anthropic key
            - :meth:`_route_claude` for the Claude (Anthropic) path
        """
        prov = provider

        # GLM (Z.ai) — default, free-tier provider.
        # If GLM is unavailable we log and fall through to other providers.
        if prov == "glm":
            glm_brain = brain.glm_brain or self.glm_brain
            if glm_brain and glm_brain.available():
                async for chunk in self._route_glm(
                    message, system_prompt, tools, brain
                ):
                    yield chunk
                return
            brain.logger.info("GLM not available, trying fallback providers")

        # Ollama / local
        if prov == "ollama":
            async for chunk in self._route_ollama(message, brain):
                yield chunk
            return

        # Gemini — also auto-selected for very long messages
        if self._should_route_gemini(prov, message, brain):
            gemini_brain = brain.gemini_brain or self.gemini_brain
            async for chunk in gemini_brain.chat_stream(message):
                yield chunk
            return

        # No Anthropic key — try GLM / LocalBrain fallback chain
        if not ANTHROPIC_API_KEY:
            async for chunk in self._route_fallback_no_anthropic(
                message, brain
            ):
                yield chunk
            return

        # Claude (Anthropic) — full tool-calling loop
        async for chunk in self._route_claude(message, system_prompt, tools, brain):
            yield chunk

    def _should_route_gemini(self, prov: str, message: str, brain) -> bool:
        """Return True if the Gemini provider should handle this request.

        Gemini is selected when *both* of these hold:
            - ``provider == "gemini"`` OR the message is very long
              (> 5000 chars)
            - a Gemini brain is resolvable from either ``brain`` or
              this router's stored ``gemini_brain``

        Args:
            prov: The provider string passed to :meth:`route`.
            message: The user's message text.
            brain: The owning ``FridayBrain`` (used to resolve the
                ``gemini_brain`` instance if not already on the router).

        Returns:
            True if Gemini should be used for this request.
        """
        gemini_brain = brain.gemini_brain or self.gemini_brain
        if not gemini_brain:
            return False
        return prov == "gemini" or len(message) > 5000

    # ------------------------------------------------------------------
    # Per-provider dispatch helpers
    # ------------------------------------------------------------------

    async def _route_glm(
        self,
        message: str,
        system_prompt: str,
        tools: list,
        brain,
    ) -> AsyncGenerator[str, None]:
        """Stream a GLM response, optionally with the tool-calling loop.

        Performs:
            - RAG context injection
            - Persistence of the user message to ``conversation_history``
              and (optionally) long-term memory
            - Tool-calling loop when ``tools`` is non-empty, otherwise
              simple streaming via ``glm_brain.chat_stream``
            - Persistence of the assistant response (no-tools path only;
              the tool-calling loop persists it itself)
        """
        glm_brain = brain.glm_brain or self.glm_brain
        glm_system_prompt = system_prompt

        # Inject RAG context
        rag_ctx = await brain._inject_rag_context(message)
        glm_message = (
            f"{rag_ctx}\n\nUser's current message: {message}"
            if rag_ctx
            else message
        )

        # Persist the user's message to conversation history so
        # that branching, summarisation, and history-based
        # features work on the default GLM path (previously
        # only the Claude path updated conversation_history).
        brain.conversation_history.append(
            {"role": "user", "content": message}
        )
        # Also store in long-term memory if available
        if brain.memory:
            try:
                brain.memory.store_conversation("user", message)
            except Exception as e:
                brain.logger.debug(f"Memory store failed: {e}")

        # Use tool-calling loop when tools are configured
        if tools:
            messages = [{"role": "user", "content": glm_message}]
            async for chunk in brain._glm_stream_with_tools(
                messages, tools, glm_system_prompt
            ):
                yield chunk
            return

        # No tools — simple streaming
        full_response = ""
        async for chunk in glm_brain.chat_stream(
            glm_message, system_prompt=glm_system_prompt
        ):
            full_response += chunk
            yield chunk

        # Store assistant response in memory + conversation history
        if full_response.strip():
            brain.conversation_history.append(
                {"role": "assistant", "content": full_response}
            )
            if brain.memory:
                try:
                    brain.memory.store_conversation(
                        "assistant", full_response
                    )
                except Exception as e:
                    brain.logger.warning(f"Memory store failed: {e}")

    async def _route_ollama(self, message: str, brain) -> AsyncGenerator[str, None]:
        """Stream a response from the local Ollama brain.

        Args:
            message: The user's message text.
            brain: The owning ``FridayBrain`` (used to resolve the
                ``local_brain`` instance if not already on the router).
        """
        local_brain = brain.local_brain or self.local_brain
        async for chunk in local_brain.chat_stream(message):
            yield chunk

    async def _route_claude(
        self,
        message: str,
        system_prompt: str,
        tools: list,
        brain,
    ) -> AsyncGenerator[str, None]:
        """Stream a Claude response with the full tool-calling loop.

        Thin wrapper around :meth:`_claude_stream_with_tools` so that
        ``route()`` can uniformly delegate via ``async for chunk in …``.
        """
        async for chunk in self._claude_stream_with_tools(
            message, system_prompt, tools, brain
        ):
            yield chunk

    async def _route_fallback_no_anthropic(
        self, message: str, brain
    ) -> AsyncGenerator[str, None]:
        """Fall back to GLM / LocalBrain when no Anthropic key is configured.

        Yields an error message if neither GLM nor the local brain is
        available.
        """
        glm_brain = brain.glm_brain or self.glm_brain
        local_brain = brain.local_brain or self.local_brain
        if glm_brain and glm_brain.available():
            async for chunk in glm_brain.chat_stream(message):
                yield chunk
        elif await local_brain.available():
            async for chunk in local_brain.chat_stream(message):
                yield chunk
        else:
            yield (
                "Error: No brain providers available. "
                "Please set GLM_API_KEY, ANTHROPIC_API_KEY, or run Ollama locally."
            )

    # ------------------------------------------------------------------
    # Claude tool-calling loop
    # ------------------------------------------------------------------

    async def _claude_stream_with_tools(
        self,
        message: str,
        system_prompt: str,
        tools: list,
        brain,
    ) -> AsyncGenerator[str, None]:
        """Run the Claude streaming + tool-calling loop.

        This was previously inlined at the end of ``FridayBrain.chat_stream``.
        """
        # Build enriched message with RAG context
        rag_context = await brain._inject_rag_context(message)
        if rag_context:
            enriched_message = f"{rag_context}\n\nUser's current message: {message}"
        else:
            enriched_message = message

        brain.conversation_history.append({
            "role": "user",
            "content": enriched_message,
        })

        # Summarize older messages
        brain.conversation_history = (
            await brain.summarizer.summarize_older_messages(
                brain.conversation_history
            )
        )

        # Trim if still too long
        if len(brain.conversation_history) > brain.history_limit:
            brain.conversation_history = brain.conversation_history[-brain.history_limit:]

        # Tool-calling loop
        max_tool_rounds = 5  # Prevent infinite loops
        for round_num in range(max_tool_rounds):
            full_response = ""
            tool_calls = []

            try:
                client = brain.claude_client
                if client is None:
                    yield "Error: Claude client unavailable (no API key)"
                    return

                async with client.messages.stream(
                    model=brain.claude_model,
                    max_tokens=4096,
                    system=system_prompt,
                    tools=tools,
                    messages=brain.conversation_history,
                ) as stream:
                    async for event in stream:
                        if (
                            event.type == "content_block_delta"
                            and event.delta.type == "text_delta"
                        ):
                            text = event.delta.text
                            full_response += text
                            yield text

                    final_msg = await stream.get_final_message()
                    for content in final_msg.content:
                        if content.type == "tool_use":
                            tool_calls.append(content)

                if not tool_calls:
                    # No tools called — we're done
                    brain.conversation_history.append({
                        "role": "assistant",
                        "content": full_response,
                    })

                    # Store assistant response in memory
                    if brain.memory:
                        try:
                            brain.memory.store_conversation(
                                "assistant", full_response
                            )
                        except Exception as e:
                            logger.debug(f"Non-critical error: {e}")

                    break

                # Process tool calls
                brain.conversation_history.append({
                    "role": "assistant",
                    "content": final_msg.content,
                })

                tool_results = []
                for tool in tool_calls:
                    yield f"\n[System: {tool.name}(...)]\n"
                    result = await brain._execute_tool(tool.name, tool.input)
                    tool_results.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tool.id,
                            "content": result,
                        }],
                    })

                brain.conversation_history.extend(tool_results)

            except (anthropic.RateLimitError if anthropic else Exception) as e:
                yield "\n[System: Rate limited, waiting...]\n"
                await asyncio.sleep(2)
                continue
            except (anthropic.APIError if anthropic else Exception) as e:
                brain.logger.error(f"API error: {e}")
                yield f"\n[System: API error - {str(e)}]\n"
                break
            except Exception as e:
                brain.logger.error(f"Error in chat_stream: {e}", exc_info=True)
                yield f"\n[System error: {str(e)}]\n"
                break
