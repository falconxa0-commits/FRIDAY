"""FridayBrain — orchestration layer for the FRIDAY assistant.

Refactored in WAVE2-ARCH to delegate responsibilities to focused modules:

* :class:`core.provider_router.ProviderRouter` — LLM provider dispatch
* :class:`core.context_manager.ContextManager` — conversation history, summarisation, branching
* :class:`core.creative_router.CreativeRouter` — image/video generation routing

This module retains the orchestration responsibilities only:

* skill discovery (``_discover_skills``)
* tool building (``_build_universal_tools``)
* tool execution (``_execute_tool``)
* web search (``_web_search``)
* system prompt construction (``_build_system_prompt``)
* RAG injection (``_inject_rag_context``)
* the GLM tool-calling loop (``_glm_stream_with_tools``) — kept here
  because it depends on ``brain._execute_tool`` and ``brain.memory``

Public API is unchanged — every existing method/attribute that callers
or tests rely on (``chat_stream``, ``branch_conversation``, ``conversation_history``,
``summarizer``, ``tools``, ``provider``, ``glm_brain``, …) still works.
"""

import asyncio
import logging
import os
import json
import inspect
import pkgutil
import importlib
from typing import List, Dict, Any, AsyncGenerator, Optional
from datetime import datetime

# Lazy import of optional LLM SDKs — these are NOT required for the
# default GLM-only install, so we must not fail at module import time.
try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

from config.settings import ANTHROPIC_API_KEY, GEMINI_API_KEY, GLM_API_KEY, TAVILY_API_KEY
from config.friday_identity import get_system_prompt
from core.gemini_brain import GeminiBrain
from core.glm_brain import GLMBrain
from core.local_brain import LocalBrain
from integrations.registry import UniversalRegistry
from core.universal_connector import UniversalConnector
from core.context_manager import ContextManager, ConversationSummarizer
from core.creative_router import CreativeRouter
from core.provider_router import ProviderRouter
from skills.base import BaseSkill

logger = logging.getLogger("FridayBrain")


# ---------------------------------------------------------------------------
# RAG pipeline (kept here — tightly coupled to memory + learning system)
# ---------------------------------------------------------------------------


class RAGPipeline:
    """Retrieval-Augmented Generation: inject relevant memories into context."""

    def __init__(self, memory=None, max_results: int = 5, relevance_threshold: float = 0.6):
        self.memory = memory
        self.max_results = max_results
        self.relevance_threshold = relevance_threshold

    async def retrieve_context(self, query: str) -> str:
        """Retrieve relevant memories and format them for injection into system prompt."""
        if not self.memory:
            return ""

        try:
            memories = self.memory.retrieve_relevant_memories(query)
            if not memories:
                return ""

            # Filter by relevance and format
            relevant = []
            for mem in memories[:self.max_results]:
                if isinstance(mem, dict):
                    content = mem.get('content', mem.get('text', str(mem)))
                    score = mem.get('similarity', mem.get('score', 1.0))
                    if score >= self.relevance_threshold:
                        relevant.append(f"- {content}")
                else:
                    relevant.append(f"- {str(mem)}")

            if not relevant:
                return ""

            return "\n\n## Relevant Memories\n" + "\n".join(relevant)
        except Exception as e:
            logger.warning(f"RAG retrieval failed: {e}")
            return ""


# ---------------------------------------------------------------------------
# FridayBrain — orchestration only
# ---------------------------------------------------------------------------


class FridayBrain:
    """Friday's main orchestration brain.

    Public API is preserved exactly. Internal responsibilities
    (provider routing, context management, creative routing) are
    delegated to dedicated collaborator objects.
    """

    # Default provider order: GLM (free, default), Claude, Gemini, GPT, Grok, Local/Ollama
    PROVIDER_ORDER = ["glm", "claude", "gemini", "gpt", "grok", "ollama"]

    def __init__(self, provider=None, memory=None, emotions=None, personality=None):
        self.provider = provider or os.getenv("BRAIN_PROVIDER", "glm")
        self.logger = logging.getLogger("FridayBrain")

        # Feature flag: route through BrainRuntimeAdapter when enabled.
        # Set FRIDAY_USE_RUNTIME=1 to enable runtime-mediated execution.
        # Default: off (backward compatibility — legacy path used).
        self.use_runtime = os.getenv("FRIDAY_USE_RUNTIME", "0") == "1"

        # LLM Clients - lazy init
        self._claude_client = None
        self.gemini_brain = None
        self.glm_brain = GLMBrain()
        self.local_brain = LocalBrain()

        # Model configuration
        self.claude_model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

        # Connected subsystems
        self.memory = memory
        self.emotions = emotions
        self.personality = personality

        # Integration layer
        self.registry = UniversalRegistry()
        self.connector = UniversalConnector()

        # Skills
        self.skills = {}
        self._discover_skills()

        # ------------------------------------------------------------------
        # Conversation management — delegated to ContextManager
        # ------------------------------------------------------------------
        self.context = ContextManager(max_unsummarized=20)
        self.history_limit = int(os.getenv("HISTORY_LIMIT", "50"))

        # RAG pipeline (kept in brain — depends on memory + learning system)
        self.rag_pipeline = RAGPipeline(memory=self.memory)

        # ------------------------------------------------------------------
        # Routers — creative suite + provider dispatch
        # ------------------------------------------------------------------
        self.creative_router = CreativeRouter(self.connector)
        self.router = ProviderRouter(
            glm_brain=self.glm_brain,
            gemini_brain=self.gemini_brain,
            local_brain=self.local_brain,
        )

        # Build tools
        self.tools = self._build_universal_tools()

        self.logger.info(
            f"FridayBrain initialized with provider={self.provider}, "
            f"model={self.claude_model}"
        )

    # ------------------------------------------------------------------
    # Backward-compatible properties delegating to ContextManager
    # ------------------------------------------------------------------
    # Tests and external callers mutate these attributes directly; we
    # expose them as properties so that mutations route through the
    # ContextManager's canonical state.

    @property
    def conversation_history(self) -> List[Dict[str, Any]]:
        return self.context.history

    @conversation_history.setter
    def conversation_history(self, value: List[Dict[str, Any]]) -> None:
        self.context.history = value

    @property
    def summarizer(self) -> ConversationSummarizer:
        return self.context.summarizer

    @summarizer.setter
    def summarizer(self, value: ConversationSummarizer) -> None:
        self.context.summarizer = value

    @property
    def _branches(self) -> Dict[str, Dict[str, Any]]:
        return self.context._branches

    @_branches.setter
    def _branches(self, value: Dict[str, Dict[str, Any]]) -> None:
        self.context._branches = value

    @property
    def _active_branch_id(self) -> Optional[str]:
        return self.context._active_branch_id

    @_active_branch_id.setter
    def _active_branch_id(self, value: Optional[str]) -> None:
        self.context._active_branch_id = value

    # ------------------------------------------------------------------
    # Lazy Claude client
    # ------------------------------------------------------------------

    @property
    def claude_client(self):
        """Lazy-init Claude client to avoid startup crashes."""
        if self._claude_client is None:
            if not ANTHROPIC_API_KEY:
                self.logger.warning("No ANTHROPIC_API_KEY set")
                return None
            if anthropic is None:
                self.logger.warning(
                    "anthropic package not installed — Claude unavailable. "
                    "Install with: pip install anthropic"
                )
                return None
            self._claude_client = anthropic.AsyncAnthropic(
                api_key=ANTHROPIC_API_KEY
            )
            self.summarizer.brain_client = self._claude_client
        return self._claude_client

    # ------------------------------------------------------------------
    # Skill discovery
    # ------------------------------------------------------------------

    def _discover_skills(self):
        """Auto-discover skills from the skills package."""
        import skills
        if not hasattr(skills, "__file__") or skills.__file__ is None:
            path = os.path.join(os.getcwd(), "skills")
            if not os.path.isdir(path):
                path = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "skills"
                )
        else:
            path = os.path.dirname(skills.__file__)

        if not os.path.isdir(path):
            self.logger.warning(f"Skills directory not found: {path}")
            return

        for loader, name, is_pkg in pkgutil.iter_modules([path]):
            if name == 'base':
                continue
            try:
                module = importlib.import_module(f"skills.{name}")
                for n, obj in inspect.getmembers(module):
                    if (
                        inspect.isclass(obj)
                        and issubclass(obj, BaseSkill)
                        and obj is not BaseSkill
                    ):
                        instance = obj()
                        self.skills[instance.name] = instance
                        self.logger.info(f"Discovered skill: {instance.name}")
            except Exception as e:
                self.logger.warning(f"Failed to load skill {name}: {e}")

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def _build_universal_tools(self):
        """Build the tool definitions for Claude's tool-use."""
        tools = [
            {
                "name": "access_universal_service",
                "description": (
                    "Access any integrated service (weather, calendar, email, "
                    "spotify, smart home, crypto, finance, global_pulse, etc.). "
                    "Returns structured receipt with status."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": (
                                "Service name (weather, calendar, gmail, spotify, "
                                "smart_home, crypto, finance, global_pulse)"
                            ),
                        },
                        "action": {
                            "type": "string",
                            "description": "Action to perform on the service",
                        },
                        "params": {
                            "type": "object",
                            "description": "Parameters for the action",
                        },
                    },
                    "required": ["service", "action"],
                },
            },
            {
                "name": "search_web",
                "description": (
                    "Search the web for real-time information. Use this for "
                    "current events, facts you're unsure about, or anything "
                    "requiring up-to-date data."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results (default 5)",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "remember",
                "description": (
                    "Store an important fact or preference about the user for "
                    "future reference. Use this when the user shares personal "
                    "details, preferences, or important context."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "The fact or preference to remember",
                        },
                        "category": {
                            "type": "string",
                            "description": "Category",
                            "enum": [
                                "personal", "preference", "work",
                                "health", "social", "finance",
                            ],
                        },
                    },
                    "required": ["fact", "category"],
                },
            },
            {
                "name": "recall_memories",
                "description": (
                    "Search through stored memories and past conversations. "
                    "Use when you need to recall something the user previously "
                    "mentioned."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to search for in memories",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum memories to return (default 5)",
                        },
                    },
                    "required": ["query"],
                },
            },
        ]

        if self.skills:
            tools.append({
                "name": "run_skill",
                "description": (
                    "Execute a complex skill. Available skills: "
                    + ", ".join(
                        f"{k}: {v.description}" for k, v in self.skills.items()
                    )
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": (
                                "Name of the skill to run. Options: "
                                + ", ".join(self.skills.keys())
                            ),
                        },
                        "params": {"type": "object"},
                    },
                    "required": ["skill_name"],
                },
            })

        return tools

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool(self, tool_name: str, tool_input: Dict) -> str:
        """Execute a tool call and return the result as a string."""
        try:
            if tool_name == "access_universal_service":
                result = await self.connector.execute_action(
                    tool_input["service"],
                    tool_input["action"],
                    tool_input.get("params", {}),
                )
                return json.dumps(result, indent=2, default=str)

            elif tool_name == "search_web":
                return await self._web_search(
                    tool_input["query"],
                    tool_input.get("max_results", 5),
                )

            elif tool_name == "remember":
                if self.memory:
                    self.memory.extract_and_store_facts(
                        tool_input["fact"],
                        category=tool_input.get("category", "personal"),
                    )
                    return json.dumps({
                        "status": "success",
                        "message": f"Remembered: {tool_input['fact']}",
                    })
                return json.dumps({
                    "status": "error",
                    "message": "Memory system unavailable",
                })

            elif tool_name == "recall_memories":
                if self.memory:
                    memories = self.memory.retrieve_relevant_memories(
                        tool_input["query"],
                        top_k=tool_input.get("max_results", 5),
                    )
                    if memories:
                        return json.dumps(memories, indent=2, default=str)
                    return "No relevant memories found."
                return json.dumps({
                    "status": "error",
                    "message": "Memory system unavailable",
                })

            elif tool_name == "run_skill":
                s_name = tool_input["skill_name"]
                if s_name in self.skills:
                    result = await self.skills[s_name].run(
                        self, tool_input.get("params", {})
                    )
                    return json.dumps(result, indent=2, default=str)
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"Skill '{s_name}' not found. Available: "
                        + ", ".join(self.skills.keys())
                    ),
                })

            else:
                return json.dumps({
                    "status": "error",
                    "message": f"Unknown tool: {tool_name}",
                })

        except Exception as e:
            self.logger.error(f"Tool execution error ({tool_name}): {e}")
            return json.dumps({"status": "error", "message": str(e)})

    # ------------------------------------------------------------------
    # Web search
    # ------------------------------------------------------------------

    async def _web_search(self, query: str, max_results: int = 5) -> str:
        """Perform web search — try Z.ai first, then Tavily."""
        # --- Try Z.ai / GLM web search first ---
        try:
            if self.glm_brain and self.glm_brain.available():
                results = await self.glm_brain.web_search(query, max_results=max_results)
                if results:
                    formatted = []
                    for r in results:
                        formatted.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": r.get("snippet", r.get("content", ""))[:300],
                        })
                    return json.dumps(formatted, indent=2)
        except Exception as e:
            self.logger.debug(f"Z.ai web search failed, trying Tavily: {e}")

        # --- Fallback: Tavily ---
        if not TAVILY_API_KEY:
            return json.dumps({
                "status": "error",
                "message": "Web search unavailable (no GLM_API_KEY or TAVILY_API_KEY)",
            })

        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=TAVILY_API_KEY)
            results = client.search(query, max_results=max_results)

            formatted = []
            for r in results.get("results", []):
                formatted.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:300],
                })
            return json.dumps(formatted, indent=2)
        except Exception as e:
            self.logger.error(f"Web search failed: {e}")
            return json.dumps({
                "status": "error",
                "message": f"Search failed: {str(e)}",
            })

    # ------------------------------------------------------------------
    # System prompt construction
    # ------------------------------------------------------------------

    def _build_system_prompt(self, user_name: str = "User") -> str:
        """Build the full system prompt with memory context, mood, and personality."""
        prompt = get_system_prompt(user_name)

        # Add conversation summaries
        summary_block = self.summarizer.get_context_block()
        if summary_block:
            prompt += summary_block

        # Add emotion context
        if self.emotions and hasattr(self.emotions, 'current_emotion'):
            mood = self.emotions.current_emotion
            if mood != "neutral":
                prompt += (
                    f"\n\n## User's Detected Mood: {mood}\n"
                    "Adapt your tone accordingly."
                )
                if self.personality:
                    mood_response = self.personality.adjust_mood(mood)
                    if mood_response:
                        prompt += f"\nSuggested approach: {mood_response}"

        # Add personality context
        if self.personality and hasattr(self.personality, 'get_personality_context'):
            prompt += (
                "\n\n## Your Personality Configuration\n"
                + self.personality.get_personality_context()
            )

        return prompt

    async def _inject_rag_context(self, message: str) -> str:
        """Inject RAG-retrieved memories + subconscious patterns + learning corrections."""
        rag_context = await self.rag_pipeline.retrieve_context(message)

        # Surface subconscious patterns relevant to the current message
        try:
            from database.subconscious import SubconsciousMind
            sub = SubconsciousMind()
            intuition = sub.get_intuition()
            if intuition and len(intuition) > 10:
                rag_context += f"\n\n[Subconscious insight: {intuition[:300]}]"
        except Exception as e:
            self.logger.debug(f"Subconscious mind unavailable: {e}")

        # Check for relevant corrections from the learning system
        try:
            from core.learning import FridayLearningSystem
            ls = FridayLearningSystem()
            corrections = await ls.check_similar_corrections(message)
            if corrections:
                for c in corrections[:3]:
                    rag_context += (
                        f"\n\n[Correction from previous session: "
                        f"you previously said '{c['original'][:100]}' "
                        f"but were corrected to '{c['correction'][:100]}'. "
                        f"Avoid repeating the mistake.]"
                    )
        except Exception as e:
            self.logger.debug(f"Learning system unavailable: {e}")

        return rag_context

    # ------------------------------------------------------------------
    # GLM tool-calling loop — kept in brain (depends on _execute_tool)
    # ------------------------------------------------------------------

    async def _glm_stream_with_tools(
        self,
        messages: list,
        tools: list,
        system_prompt: str,
    ) -> AsyncGenerator[str, None]:
        """GLM tool-calling loop — mirrors the Claude branch.

        GLM-4-Flash supports function calling via the ZhipuAI SDK
        using OpenAI-compatible tools format.
        Max 5 rounds to prevent infinite loops.
        """
        max_rounds = 5
        glm_client = self.glm_brain._ensure_client()
        if glm_client is None:
            yield "Error: GLM client not initialised."
            return

        # Prepend system prompt if provided
        if system_prompt and (not messages or messages[0].get("role") != "system"):
            messages = [{"role": "system", "content": system_prompt}] + messages

        for round_num in range(max_rounds):
            try:
                response = await asyncio.to_thread(
                    glm_client.chat.completions.create,
                    model=self.glm_brain._model_name,
                    messages=messages,
                    tools=tools if tools else None,
                    stream=False,
                )
            except Exception as e:
                self.logger.error(f"GLM tool call error (round {round_num}): {e}")
                yield f"Error in GLM tool calling: {e}"
                return

            choice = response.choices[0]
            finish_reason = getattr(choice, "finish_reason", "stop")
            message = choice.message
            tool_calls = getattr(message, "tool_calls", None)

            # Model wants to call tools
            if finish_reason == "tool_calls" and tool_calls:
                # Add assistant message with tool_calls to history
                messages.append({
                    "role": "assistant",
                    "content": getattr(message, "content", "") or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                })

                # Execute each tool and collect results
                for tool_call in tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        tool_input = json.loads(tool_call.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        tool_input = {}

                    self.logger.info(f"GLM tool call: {tool_name}({tool_input})")
                    yield f"\n[System: {tool_name}(...)]\n"

                    tool_result = await self._execute_tool(tool_name, tool_input)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(tool_result),
                    })

                # Continue to next round with tool results injected
                continue

            # No more tool calls — stream the final text response
            final_content = getattr(message, "content", "") or ""
            # Store assistant response in memory
            if self.memory and final_content.strip():
                try:
                    self.memory.store_conversation("assistant", final_content)
                except Exception as e:
                    self.logger.warning(f"Memory store failed: {e}")
            # Stream word by word for natural feel
            words = final_content.split(" ")
            for i, word in enumerate(words):
                if i < len(words) - 1:
                    yield word + " "
                else:
                    yield word
            return

        # Max rounds reached — yield what we have
        self.logger.warning("GLM tool calling reached max rounds (5)")
        yield "I reached the maximum reasoning steps. Please try rephrasing your request."

    # ------------------------------------------------------------------
    # Creative suite routing — thin delegating wrappers
    # ------------------------------------------------------------------
    # Backward compatibility: ``scripts/verify_creative_routing.py`` and
    # any external callers invoke ``brain._detect_creative_route`` /
    # ``brain._handle_creative_route`` directly. We keep these as thin
    # delegating wrappers around ``CreativeRouter``.

    def _detect_creative_route(self, message: str) -> Optional[Dict[str, Any]]:
        """Check if the message is a creative generation request.

        Returns:
            A dict with ``type`` ("image" or "video") and ``prompt``,
            or ``None`` if the message is not a creative request.
        """
        return self.creative_router.detect(message)

    async def _handle_creative_route(
        self, route: Dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        """Handle an image or video generation request (delegates to CreativeRouter)."""
        async for chunk in self.creative_router.handle(route):
            yield chunk

    # ------------------------------------------------------------------
    # Main chat streaming method — orchestration only
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Runtime-mediated chat (feature flag: FRIDAY_USE_RUNTIME=1)
    # ------------------------------------------------------------------

    async def _runtime_chat_stream(
        self,
        message: str,
        user_name: str,
        provider: str,
    ) -> AsyncGenerator[str, None]:
        """Route chat through BrainRuntimeAdapter when feature flag is on.

        This path applies PromptShield sanitization, PolicyEngine
        capability checks, and EventBus event emission before
        delegating to the legacy execution path.

        The actual LLM call still uses the same ProviderRouter — the
        adapter wraps the INPUT (sanitization + policy) and OUTPUT
        (validation + events), not the LLM call itself.
        """
        # Lazy-load the adapter (avoid circular import)
        try:
            from core.runtime.brain_adapter import BrainRuntimeAdapter
            from core.runtime.security.prompt_shield import PromptShield
            from core.runtime.security.policy_engine import create_default_policy_engine
            from core.runtime.event_bus import EventBus

            # Use a per-brain adapter instance
            if not hasattr(self, "_runtime_adapter"):
                shield = PromptShield()
                engine = create_default_policy_engine()
                engine.grant_capability("brain.chat")
                bus = EventBus()
                self._runtime_adapter = BrainRuntimeAdapter(
                    brain=None,  # we call the legacy path ourselves
                    event_bus=bus,
                    prompt_shield=shield,
                    policy_engine=engine,
                )

            adapter = self._runtime_adapter

            # Step 1: Sanitize input
            sanitization = adapter.prompt_shield.sanitize_input(message)
            if sanitization.was_modified:
                self.logger.warning(
                    f"Prompt injection detected and filtered: "
                    f"{sanitization.threats_detected}"
                )
            safe_message = sanitization.sanitized

            # Step 2: Check policy
            decision = adapter.policy_engine.evaluate("brain.chat")
            if not decision.allowed:
                self.logger.warning(f"Chat blocked by policy: {decision.reason}")
                yield f"[Chat blocked by security policy: {decision.reason}]"
                return

            # Step 3: Emit start event
            await adapter._emit("brain.stream.started", {
                "message_length": len(safe_message),
                "provider": provider,
            })

            # Step 4: Execute via legacy path (with sanitized input)
            # We call the internal legacy execution directly, passing
            # the sanitized message.
            async for chunk in self._legacy_chat_stream(
                safe_message, user_name, provider
            ):
                # Step 5: Validate each output chunk
                validated = adapter.prompt_shield.validate_output(chunk)
                yield validated.sanitized

            # Step 6: Emit completion event
            await adapter._emit("brain.stream.completed", {
                "provider": provider,
            })

        except ImportError:
            # Runtime modules not available — fall back to legacy path
            self.logger.warning(
                "Runtime modules not available, falling back to legacy path"
            )
            async for chunk in self._legacy_chat_stream(message, user_name, provider):
                yield chunk

    async def _legacy_chat_stream(
        self,
        message: str,
        user_name: str,
        provider: str,
    ) -> AsyncGenerator[str, None]:
        """The original chat_stream logic (renamed for clarity).

        This is the direct-execution path that was previously
        the body of chat_stream. It is called by both the legacy
        path (when feature flag is off) and the runtime path
        (after sanitization).
        """
        prov = provider

        # Detect emotion from user message
        if self.emotions:
            try:
                detected_mood = self.emotions.detect_emotion(message)
                self.logger.info(f"Detected mood: {detected_mood}")
            except Exception as e:
                logger.debug(f"Non-critical error: {e}")

        # Store user message in memory
        if self.memory:
            try:
                self.memory.store_conversation("user", message)
            except Exception as e:
                logger.debug(f"Non-critical error: {e}")

        # ------------------------------------------------------------------
        # Creative suite routing — detect image/video generation requests
        # ------------------------------------------------------------------
        creative_route = self._detect_creative_route(message)
        if creative_route is not None:
            async for chunk in self._handle_creative_route(creative_route):
                yield chunk
            return

        # ------------------------------------------------------------------
        # Build system prompt once, delegate provider dispatch to ProviderRouter
        # ------------------------------------------------------------------
        system_prompt = self._build_system_prompt(user_name)

        async for chunk in self.router.route(
            prov, message, system_prompt, self.tools, self
        ):
            yield chunk

    async def chat_stream(
        self,
        message: str,
        user_name: str = "User",
        force_provider=None,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat response with full tool-calling, RAG, and memory integration.

        When ``FRIDAY_USE_RUNTIME=1`` is set, the chat is routed through
        the runtime pipeline (PromptShield → PolicyEngine → execution →
        output validation → EventBus). Otherwise, the legacy direct path
        is used, preserving backward compatibility.
        """
        prov = force_provider or self.provider

        if self.use_runtime:
            async for chunk in self._runtime_chat_stream(message, user_name, prov):
                yield chunk
        else:
            async for chunk in self._legacy_chat_stream(message, user_name, prov):
                yield chunk

    # ------------------------------------------------------------------
    # Conversation branching — delegated to ContextManager
    # ------------------------------------------------------------------

    async def branch_conversation(self, branch_point_message_id: str, new_message: str = "") -> dict:
        """Fork the conversation at a specific message index.

        Creates an independent branch with its own history.
        The original conversation is unchanged.
        """
        # Find the branch point in conversation history
        # message_id is the index as a string
        try:
            idx = int(branch_point_message_id)
        except (ValueError, TypeError):
            idx = len(self.conversation_history) - 1

        branch_id = await self.context.branch(idx)

        return {
            "branch_id": branch_id,
            "parent_message_id": branch_point_message_id,
            "created_at": self.context._branches[branch_id]["created_at"],
            "message_count": len(self.context._branches[branch_id]["history"]),
        }

    async def get_branches(self) -> list:
        """List all active conversation branches with message counts."""
        return self.context.get_branches()

    async def switch_branch(self, branch_id: str) -> bool:
        """Switch the active conversation to a different branch."""
        return self.context.switch_branch(branch_id)

    async def merge_branch_insight(self, branch_id: str) -> str:
        """Summarize what was learned in a branch and bring it back to main."""
        if branch_id not in self._branches:
            return "Branch not found."

        branch_history = self._branches[branch_id]["history"]
        if not branch_history:
            return "Branch has no messages."

        # Summarize the branch's key content
        user_messages = [
            m["content"] for m in branch_history
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        assistant_messages = [
            m["content"] for m in branch_history
            if m.get("role") == "assistant" and isinstance(m.get("content"), str)
        ]

        summary_parts = []
        if user_messages:
            summary_parts.append(
                f"User explored: {' | '.join(m[:100] for m in user_messages[-3:])}"
            )
        if assistant_messages:
            summary_parts.append(
                f"Key findings: {' | '.join(m[:100] for m in assistant_messages[-3:])}"
            )

        insight = f"[Branch {branch_id} insight] " + " ".join(summary_parts)

        # Delegate the branch-switching + append + switch-back dance
        self.context.merge_branch_insight(branch_id, insight)

        return insight

    async def delete_branch(self, branch_id: str) -> bool:
        """Delete a conversation branch."""
        return self.context.delete_branch(branch_id)

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def clear_context(self):
        """Clear conversation history."""
        self.context.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Return brain statistics."""
        return {
            "provider": self.provider,
            "model": self.claude_model,
            "history_length": len(self.conversation_history),
            "summary_count": len(self.summarizer.summaries),
            "skills_loaded": list(self.skills.keys()),
            "tools_available": [t["name"] for t in self.tools],
            "memory_enabled": self.memory is not None,
            "emotions_enabled": self.emotions is not None,
            "personality_enabled": self.personality is not None,
        }
