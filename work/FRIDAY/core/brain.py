import asyncio
import logging
import os
import json
import inspect
import pkgutil
import importlib
import hashlib
import re
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
from skills.base import BaseSkill

logger = logging.getLogger("FridayBrain")


class ConversationSummarizer:
    """Summarizes old conversation history to maintain context within token limits."""

    def __init__(self, brain_client=None, max_unsummarized: int = 20, summary_max_tokens: int = 500):
        self.brain_client = brain_client
        self.max_unsummarized = max_unsummarized
        self.summary_max_tokens = summary_max_tokens
        self.summaries: List[str] = []

    async def summarize_older_messages(self, messages: List[Dict]) -> List[Dict]:
        """Keep recent messages intact, summarize older ones into a condensed block."""
        if len(messages) <= self.max_unsummarized:
            return messages

        older = messages[:-self.max_unsummarized]
        recent = messages[-self.max_unsummarized:]

        # Build text to summarize
        older_text = "\n".join(
            f"{m.get('role', 'unknown')}: {m.get('content', '')}"
            for m in older
        )

        summary = await self._generate_summary(older_text)
        if summary:
            self.summaries.append(summary)

        return recent

    async def _generate_summary(self, text: str) -> Optional[str]:
        """Use the LLM to generate a conversation summary."""
        if not self.brain_client:
            # Heuristic fallback: just keep key facts
            return self._heuristic_summary(text)

        try:
            response = await self.brain_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=self.summary_max_tokens,
                messages=[{
                    "role": "user",
                    "content": (
                        "Summarize the key points, decisions, and facts from this "
                        "conversation excerpt in a concise paragraph:\n\n" + text
                    )
                }]
            )
            return response.content[0].text
        except Exception as e:
            logger.warning(f"Summary generation failed: {e}")
            return self._heuristic_summary(text)

    def _heuristic_summary(self, text: str) -> str:
        """Extract key sentences when LLM is unavailable."""
        sentences = text.split('.')
        keywords = [
            'important', 'decided', 'remember', 'prefer', 'need', 'want',
            'fact', 'name is', 'my name'
        ]
        key_sentences = [
            s.strip() for s in sentences
            if any(kw in s.lower() for kw in keywords)
        ]
        if key_sentences:
            return "Key points: " + "; ".join(key_sentences[:5])
        return ""

    def get_context_block(self) -> str:
        """Return all summaries as a context block for the system prompt."""
        if not self.summaries:
            return ""
        return (
            "\n\n## Previous Conversation Summaries\n"
            + "\n---\n".join(self.summaries)
        )


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


class FridayBrain:
    # Default provider order: GLM (free, default), Claude, Gemini, GPT, Grok, Local/Ollama
    PROVIDER_ORDER = ["glm", "claude", "gemini", "gpt", "grok", "ollama"]

    def __init__(self, provider=None, memory=None, emotions=None, personality=None):
        self.provider = provider or os.getenv("BRAIN_PROVIDER", "glm")
        self.logger = logging.getLogger("FridayBrain")

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

        # Conversation management
        self.conversation_history = []
        self.history_limit = int(os.getenv("HISTORY_LIMIT", "50"))
        self.summarizer = ConversationSummarizer()
        self.rag_pipeline = RAGPipeline(memory=self.memory)

        # Conversation branching
        self._branches: Dict[str, dict] = {}  # branch_id → {history, parent_message_id, created_at}
        self._active_branch_id: Optional[str] = None  # None = main conversation

        # Build tools
        self.tools = self._build_universal_tools()

        self.logger.info(
            f"FridayBrain initialized with provider={self.provider}, "
            f"model={self.claude_model}"
        )

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
    # GLM tool-calling loop — mirrors the Claude branch
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
    # Creative suite routing — image / video generation
    # ------------------------------------------------------------------

    # Patterns that indicate the user wants an image generated
    _IMAGE_PATTERNS = [
        re.compile(r"generate\s+(?:an?\s+)?image\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"create\s+(?:an?\s+)?image\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"draw\s+(?:an?\s+)?(.+)", re.IGNORECASE),
        re.compile(r"make\s+(?:an?\s+)?image\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"illustrate\s+(.+)", re.IGNORECASE),
        re.compile(r"picture\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"show\s+(?:me\s+)?(?:an?\s+)?image\s+(?:of\s+)?(.+)", re.IGNORECASE),
    ]

    # Patterns that indicate the user wants a video generated
    _VIDEO_PATTERNS = [
        re.compile(r"generate\s+(?:an?\s+)?video\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"create\s+(?:an?\s+)?video\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"make\s+(?:an?\s+)?video\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"animate\s+(.+)", re.IGNORECASE),
    ]

    def _detect_creative_route(self, message: str) -> Optional[Dict[str, Any]]:
        """Check if the message is a creative generation request.

        Returns:
            A dict with ``type`` ("image" or "video") and ``prompt``,
            or ``None`` if the message is not a creative request.
        """
        for pattern in self._IMAGE_PATTERNS:
            match = pattern.search(message)
            if match:
                prompt = match.group(1).strip()
                if prompt:
                    return {"type": "image", "prompt": prompt}

        for pattern in self._VIDEO_PATTERNS:
            match = pattern.search(message)
            if match:
                prompt = match.group(1).strip()
                if prompt:
                    return {"type": "video", "prompt": prompt}

        return None

    async def _handle_creative_route(
        self, route: Dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        """Handle an image or video generation request.

        Uses the UniversalConnector to dispatch to the ImageGen or
        VideoGen integration, which in turn uses GLM's CogView-3 or
        CogVideoX.
        """
        route_type = route["type"]
        prompt = route["prompt"]

        self.logger.info("Creative route: %s generation for prompt: %s", route_type, prompt)

        if route_type == "image":
            result = await self.connector.execute_action(
                "image_gen", "generate_image", {"prompt": prompt}
            )
        elif route_type == "video":
            result = await self.connector.execute_action(
                "video_gen", "generate_video", {"prompt": prompt}
            )
        else:
            yield f"Unknown creative route type: {route_type}"
            return

        if result.get("status") == "success":
            receipt = result.get("receipt", result)
            # Try to extract URL from receipt
            url = ""
            if isinstance(receipt, dict):
                url = receipt.get("image_url", receipt.get("video_url", ""))

            if url:
                yield f"Here's your {route_type}: {url}"
            else:
                yield f"{route_type.capitalize()} generated successfully! Details: {json.dumps(receipt, indent=2, default=str)}"
        else:
            error_msg = result.get("message", result.get("error", "Unknown error"))
            yield f"Sorry, {route_type} generation failed: {error_msg}"

    # ------------------------------------------------------------------
    # Main chat streaming method
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        message: str,
        user_name: str = "User",
        force_provider=None,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat response with full tool-calling, RAG, and memory integration."""
        prov = force_provider or self.provider

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

        # Route to alternative providers
        # GLM (Z.ai) — default, free-tier provider
        if prov == "glm":
            if self.glm_brain and self.glm_brain.available():
                glm_system_prompt = self._build_system_prompt(user_name)
                # Inject RAG context
                rag_ctx = await self._inject_rag_context(message)
                glm_message = f"{rag_ctx}\n\nUser's current message: {message}" if rag_ctx else message

                # Persist the user's message to conversation history so
                # that branching, summarisation, and history-based
                # features work on the default GLM path (previously
                # only the Claude path updated conversation_history).
                self.conversation_history.append(
                    {"role": "user", "content": message}
                )
                # Also store in long-term memory if available
                if self.memory:
                    try:
                        self.memory.store_conversation("user", message)
                    except Exception as e:
                        self.logger.debug(f"Memory store failed: {e}")

                # Use tool-calling loop when tools are configured
                if self.tools:
                    messages = [{"role": "user", "content": glm_message}]
                    full_response = ""
                    async for chunk in self._glm_stream_with_tools(
                        messages, self.tools, glm_system_prompt
                    ):
                        full_response += chunk
                        yield chunk
                else:
                    # No tools — simple streaming
                    full_response = ""
                    async for chunk in self.glm_brain.chat_stream(
                        glm_message, system_prompt=glm_system_prompt
                    ):
                        full_response += chunk
                        yield chunk
                    # Store assistant response in memory + conversation history
                    if full_response.strip():
                        self.conversation_history.append(
                            {"role": "assistant", "content": full_response}
                        )
                        if self.memory:
                            try:
                                self.memory.store_conversation("assistant", full_response)
                            except Exception as e:
                                self.logger.warning(f"Memory store failed: {e}")
                return
            # GLM not available — fall through to other providers
            self.logger.info("GLM not available, trying fallback providers")

        if prov == "ollama":
            async for chunk in self.local_brain.chat_stream(message):
                yield chunk
            return

        if (prov == "gemini" or len(message) > 5000) and self.gemini_brain:
            async for chunk in self.gemini_brain.chat_stream(message):
                yield chunk
            return

        if not ANTHROPIC_API_KEY:
            # Try GLM as fallback before local brain
            if self.glm_brain and self.glm_brain.available():
                async for chunk in self.glm_brain.chat_stream(message):
                    yield chunk
            elif await self.local_brain.available():
                async for chunk in self.local_brain.chat_stream(message):
                    yield chunk
            else:
                yield (
                    "Error: No brain providers available. "
                    "Please set GLM_API_KEY, ANTHROPIC_API_KEY, or run Ollama locally."
                )
            return

        # Build enriched system prompt
        system_prompt = self._build_system_prompt(user_name)

        # RAG: inject relevant memories
        rag_context = await self._inject_rag_context(message)
        if rag_context:
            enriched_message = (
                f"{rag_context}\n\nUser's current message: {message}"
            )
        else:
            enriched_message = message

        self.conversation_history.append({
            "role": "user",
            "content": enriched_message,
        })

        # Summarize older messages
        self.conversation_history = (
            await self.summarizer.summarize_older_messages(
                self.conversation_history
            )
        )

        # Trim if still too long
        if len(self.conversation_history) > self.history_limit:
            self.conversation_history = self.conversation_history[-self.history_limit:]

        # Tool-calling loop
        max_tool_rounds = 5  # Prevent infinite loops
        for round_num in range(max_tool_rounds):
            full_response = ""
            tool_calls = []

            try:
                client = self.claude_client
                if client is None:
                    yield "Error: Claude client unavailable (no API key)"
                    return

                async with client.messages.stream(
                    model=self.claude_model,
                    max_tokens=4096,
                    system=system_prompt,
                    tools=self.tools,
                    messages=self.conversation_history,
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
                    self.conversation_history.append({
                        "role": "assistant",
                        "content": full_response,
                    })

                    # Store assistant response in memory
                    if self.memory:
                        try:
                            self.memory.store_conversation(
                                "assistant", full_response
                            )
                        except Exception as e:
                            logger.debug(f"Non-critical error: {e}")

                    break

                # Process tool calls
                self.conversation_history.append({
                    "role": "assistant",
                    "content": final_msg.content,
                })

                tool_results = []
                for tool in tool_calls:
                    yield f"\n[System: {tool.name}(...)]\n"
                    result = await self._execute_tool(tool.name, tool.input)
                    tool_results.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tool.id,
                            "content": result,
                        }],
                    })

                self.conversation_history.extend(tool_results)

            except (anthropic.RateLimitError if anthropic else Exception) as e:
                yield "\n[System: Rate limited, waiting...]\n"
                await asyncio.sleep(2)
                continue
            except (anthropic.APIError if anthropic else Exception) as e:
                self.logger.error(f"API error: {e}")
                yield f"\n[System: API error - {str(e)}]\n"
                break
            except Exception as e:
                self.logger.error(f"Error in chat_stream: {e}", exc_info=True)
                yield f"\n[System error: {str(e)}]\n"
                break

    # ------------------------------------------------------------------
    # Conversation branching
    # ------------------------------------------------------------------

    async def branch_conversation(self, branch_point_message_id: str, new_message: str = "") -> dict:
        """Fork the conversation at a specific message index.

        Creates an independent branch with its own history.
        The original conversation is unchanged.
        """
        import uuid as _uuid

        # Find the branch point in conversation history
        # message_id is the index as a string
        try:
            idx = int(branch_point_message_id)
        except (ValueError, TypeError):
            idx = len(self.conversation_history) - 1

        # Clamp to valid range
        idx = max(0, min(idx, len(self.conversation_history) - 1))

        # Copy history up to and including the branch point
        branch_history = list(self.conversation_history[:idx + 1])

        branch_id = f"branch_{_uuid.uuid4().hex[:8]}"
        self._branches[branch_id] = {
            "history": branch_history,
            "parent_message_id": branch_point_message_id,
            "created_at": datetime.now().isoformat(),
            "summaries": list(self.summarizer.summaries),
        }

        # Save main history before switching
        if self._active_branch_id is None:
            self._main_history = list(self.conversation_history)
            self._main_summaries = list(self.summarizer.summaries)

        # Switch to the new branch
        self._active_branch_id = branch_id
        self.conversation_history = branch_history
        self.summarizer.summaries = list(self._branches[branch_id]["summaries"])

        self.logger.info(
            f"Created branch {branch_id} at message {idx} "
            f"({len(branch_history)} messages copied)"
        )

        return {
            "branch_id": branch_id,
            "parent_message_id": branch_point_message_id,
            "created_at": self._branches[branch_id]["created_at"],
            "message_count": len(branch_history),
        }

    async def get_branches(self) -> list:
        """List all active conversation branches with message counts."""
        result = []
        for bid, bdata in self._branches.items():
            result.append({
                "branch_id": bid,
                "parent_message_id": bdata["parent_message_id"],
                "created_at": bdata["created_at"],
                "message_count": len(bdata["history"]),
                "is_active": bid == self._active_branch_id,
            })
        # Include main conversation
        result.append({
            "branch_id": "main",
            "parent_message_id": None,
            "created_at": None,
            "message_count": len(self.conversation_history) if self._active_branch_id is None else len(self._branches.get(self._active_branch_id, {}).get("history", [])),
            "is_active": self._active_branch_id is None,
        })
        return result

    async def switch_branch(self, branch_id: str) -> bool:
        """Switch the active conversation to a different branch."""
        if branch_id == "main":
            # Save current branch state
            if self._active_branch_id and self._active_branch_id in self._branches:
                self._branches[self._active_branch_id]["history"] = list(self.conversation_history)
                self._branches[self._active_branch_id]["summaries"] = list(self.summarizer.summaries)
            self._active_branch_id = None
            # Restore main history (stored separately)
            if hasattr(self, "_main_history"):
                self.conversation_history = list(self._main_history)
                self.summarizer.summaries = list(getattr(self, "_main_summaries", []))
            return True

        if branch_id not in self._branches:
            return False

        # Save current state
        if self._active_branch_id is None:
            self._main_history = list(self.conversation_history)
            self._main_summaries = list(self.summarizer.summaries)
        elif self._active_branch_id in self._branches:
            self._branches[self._active_branch_id]["history"] = list(self.conversation_history)
            self._branches[self._active_branch_id]["summaries"] = list(self.summarizer.summaries)

        # Switch to target branch
        self._active_branch_id = branch_id
        self.conversation_history = list(self._branches[branch_id]["history"])
        self.summarizer.summaries = list(self._branches[branch_id].get("summaries", []))
        self.logger.info(f"Switched to branch {branch_id}")
        return True

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
            summary_parts.append(f"User explored: {' | '.join(m[:100] for m in user_messages[-3:])}")
        if assistant_messages:
            summary_parts.append(f"Key findings: {' | '.join(m[:100] for m in assistant_messages[-3:])}")

        insight = f"[Branch {branch_id} insight] " + " ".join(summary_parts)

        # Add to main conversation as context
        was_in_branch = self._active_branch_id == branch_id
        if was_in_branch:
            await self.switch_branch("main")
        elif self._active_branch_id is not None:
            # Save current, switch to main, add insight, switch back
            await self.switch_branch("main")

        self.conversation_history.append({
            "role": "system",
            "content": insight,
        })

        if was_in_branch:
            await self.switch_branch(branch_id)

        return insight

    async def delete_branch(self, branch_id: str) -> bool:
        """Delete a conversation branch."""
        if branch_id == "main" or branch_id not in self._branches:
            return False
        if self._active_branch_id == branch_id:
            await self.switch_branch("main")
        del self._branches[branch_id]
        self.logger.info(f"Deleted branch {branch_id}")
        return True

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def clear_context(self):
        """Clear conversation history."""
        self.conversation_history = []
        self.summarizer.summaries = []

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
