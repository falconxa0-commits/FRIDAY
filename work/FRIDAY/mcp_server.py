#!/usr/bin/env python3
"""FRIDAY MCP Server — Model Context Protocol wrapper.

Exposes FRIDAY's action layer as an MCP server with tool descriptions
that include the Z.ai capability set.  All actions are gated through
the ledger/auth/receipt system.

Supported capabilities:
  - chat           : Stream a conversation with FRIDAY
  - vision         : Analyze an image using GLM-4V
  - web_search     : Search the web via ZhipuAI
  - image_generation: Generate images via CogView-3
  - video_generation: Generate videos via CogVideoX
  - code_execution : Execute code via ZhipuAI Code Interpreter

Every action returns a real receipt with timestamp, action details,
and response data.

Run:  python mcp_server.py
"""

import asyncio
import datetime
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

logger = logging.getLogger("friday_mcp")
logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")


# ──────────────────────────────────────────────────────────────────────────────
# Tool definitions
# ──────────────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "chat",
        "description": (
            "Stream a conversation with FRIDAY AI assistant. "
            "Uses GLM-4 (Z.ai free tier) by default, with fallback to "
            "Claude, Gemini, or Ollama based on configuration. "
            "Returns the assistant's response and a receipt."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The user message to send to FRIDAY.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "vision",
        "description": (
            "Analyze an image using GLM-4V vision model. "
            "Provide a base64-encoded image and an optional prompt. "
            "Returns a textual description and receipt."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "image_base64": {
                    "type": "string",
                    "description": "Base64-encoded image (without data URI prefix).",
                },
                "prompt": {
                    "type": "string",
                    "description": "Question or instruction about the image.",
                    "default": "Describe this image in detail.",
                },
            },
            "required": ["image_base64"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the web using ZhipuAI's built-in web search tool. "
            "Returns real search results with titles, URLs, and snippets."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "image_generation",
        "description": (
            "Generate an image from a text description using CogView-3 "
            "via Z.ai.  Returns the image URL and receipt. "
            "NOTE: This action is never auto-approved — requires explicit consent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the desired image.",
                },
                "size": {
                    "type": "string",
                    "description": "Image size, e.g. '1024x1024'.",
                    "default": "1024x1024",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "video_generation",
        "description": (
            "Generate a video from a text description using CogVideoX "
            "via Z.ai.  Video generation takes 1-3 minutes. "
            "Returns a task ID and receipt. "
            "NOTE: This action is never auto-approved — requires explicit consent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the desired video.",
                },
                "quality": {
                    "type": "string",
                    "description": "'quality' or 'speed'.",
                    "default": "quality",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "code_execution",
        "description": (
            "Execute code using the ZhipuAI Code Interpreter. "
            "Returns the code output and receipt. "
            "NOTE: This action is never auto-approved — requires explicit consent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The source code to execute.",
                },
                "language": {
                    "type": "string",
                    "description": "Programming language (default: python).",
                    "default": "python",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "execute_action",
        "description": (
            "Route any integration action through Friday's Universal "
            "Connector with the human-in-the-loop approval gate. "
            "Examples: Weather.get_weather, Finance.transfer, "
            "Printer.print_file. The action is queued in the ledger "
            "and waits up to 5 seconds for approval (use "
            "request_approval for longer timeouts)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "description": "Integration name (e.g. 'Weather', 'Finance').",
                },
                "action": {
                    "type": "string",
                    "description": "Action name to execute (e.g. 'get_weather').",
                },
                "params": {
                    "type": "object",
                    "description": "Action parameters as a JSON object.",
                    "default": {},
                },
            },
            "required": ["component", "action"],
        },
    },
    {
        "name": "request_approval",
        "description": (
            "THE KILLER FEATURE: an external AI agent (Claude Code, "
            "Cursor, etc.) submits an action for human approval via "
            "Friday's tamper-evident ledger. Friday speaks the "
            "request (optional voice), waits for the human's "
            "yes/no, and returns approved/rejected/timeout. The "
            "risk_level is computed by Friday's EthicalSentinel — "
            "caller-supplied risk_level is IGNORED for security."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "description": "Integration component (e.g. 'Filesystem').",
                },
                "action": {
                    "type": "string",
                    "description": "Action name (e.g. 'delete_file').",
                },
                "params": {
                    "type": "object",
                    "description": "Action parameters.",
                    "default": {},
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable description of what the agent wants to do.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Seconds to wait for approval (default 60).",
                    "default": 60,
                },
                "use_voice": {
                    "type": "boolean",
                    "description": "If True, use voice-native approval loop.",
                    "default": False,
                },
            },
            "required": ["component", "action"],
        },
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# MCP Server implementation (stdio transport)
# ──────────────────────────────────────────────────────────────────────────────

class FridayMCPServer:
    """FRIDAY MCP Server — wraps the action layer with ledger/auth/receipt."""

    def __init__(self):
        self._glm_brain = None
        self._ledger = None

    def _get_glm_brain(self):
        """Lazy-initialize GLMBrain."""
        if self._glm_brain is None:
            try:
                from core.glm_brain import GLMBrain
                self._glm_brain = GLMBrain()
            except Exception as exc:
                logger.error(f"Failed to initialize GLMBrain: {exc}")
        return self._glm_brain

    def _get_ledger(self):
        """Lazy-initialize the action ledger."""
        if self._ledger is None:
            try:
                from core.ledger import get_ledger
                self._ledger = get_ledger()
            except Exception as exc:
                logger.error(f"Failed to initialize ledger: {exc}")
        return self._ledger

    def _make_receipt(self, action: str, result: dict) -> dict:
        """Create a receipt for an MCP tool call."""
        return {
            "action": action,
            "timestamp": datetime.datetime.now().isoformat(),
            "status": result.get("status", "unknown"),
            "data": result.get("receipt", result.get("message", "")),
        }

    def _compute_risk_level(
        self, component: str, action: str, params: dict
    ) -> str:
        """Compute the risk level for an action using EthicalSentinel.

        SECURITY: This is the single source of truth for risk
        classification on the MCP layer. Caller-supplied risk_level
        is always ignored. Falls back to UniversalConnector._classify_risk
        if the Sentinel is unavailable.

        Returns one of: "low", "medium", "high", "critical".
        """
        # Try EthicalSentinel first (deeper analysis)
        try:
            from core.sentinel import EthicalSentinel
            sentinel = EthicalSentinel()
            context = {
                "component": component,
                "params": params or {},
            }
            result = sentinel.evaluate_action(action, context=context)
            classification = result.get("classification", "")
            if isinstance(classification, str):
                classification = classification.upper()
            elif hasattr(classification, "value"):
                classification = classification.value.upper()
            else:
                classification = str(classification).upper()

            mapping = {
                "SAFE": "low",
                "CAUTIOUS": "medium",
                "DANGEROUS": "high",
                "CRITICAL": "critical",
            }
            mapped = mapping.get(classification)
            if mapped:
                return mapped
        except Exception as exc:
            logger.debug(f"Sentinel unavailable, falling back to keyword classifier: {exc}")

        # Fallback: keyword-based classifier from UniversalConnector
        try:
            from core.universal_connector import UniversalConnector
            return UniversalConnector._classify_risk(action)
        except Exception:
            # Last resort: assume high risk
            return "high"

    # ─── Tool handlers ──────────────────────────────────────────────────

    async def handle_chat(self, params: dict) -> dict:
        """Handle the 'chat' tool — logged in audit trail (read-only, no ledger gate needed)."""
        message = params.get("message", "")
        if not message:
            return {"status": "error", "message": "No message provided."}

        # Log in audit trail (read-only calls don't need approval gate,
        # but must be logged for transparency)
        ledger = self._get_ledger()
        if ledger:
            try:
                action_id = ledger.queue_action(
                    "MCPChat", "chat", {"message_length": len(message)},
                    risk_level="low",
                )
                # Read-only calls auto-approve at STANDARD+ profile
                ledger.approve_action(action_id)
            except Exception as e:
                logger.debug(f"Non-critical error: {e}")

        glm = self._get_glm_brain()
        if glm and glm.available():
            full_response = ""
            async for chunk in glm.chat_stream(message):
                full_response += chunk
            return {
                "status": "success",
                "message": full_response,
                "receipt": self._make_receipt("chat", {"status": "success", "receipt": {
                    "provider": "glm",
                    "response_length": len(full_response),
                }}),
            }

        # Fallback to FridayBrain
        try:
            from core.brain import FridayBrain
            brain = FridayBrain()
            full_response = ""
            async for chunk in brain.chat_stream(message):
                full_response += chunk
            return {
                "status": "success",
                "message": full_response,
                "receipt": self._make_receipt("chat", {"status": "success", "receipt": {
                    "provider": brain.get_stats().get("provider", "unknown"),
                    "response_length": len(full_response),
                }}),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    async def handle_vision(self, params: dict) -> dict:
        """Handle the 'vision' tool — logged in audit trail."""
        image_base64 = params.get("image_base64", "")
        prompt = params.get("prompt", "Describe this image in detail.")

        if not image_base64:
            return {"status": "error", "message": "No image provided."}

        # Log in audit trail
        ledger = self._get_ledger()
        if ledger:
            try:
                action_id = ledger.queue_action(
                    "MCPVision", "vision_analyze", {"prompt": prompt[:100]},
                    risk_level="low",
                )
                ledger.approve_action(action_id)
            except Exception as e:
                logger.debug(f"Non-critical error: {e}")

        glm = self._get_glm_brain()
        if not glm or not glm.available():
            return {"status": "error", "message": "GLM not available for vision analysis."}

        result = await glm.vision_analyze(image_base64, prompt)
        return {
            "status": "success",
            "message": result,
            "receipt": self._make_receipt("vision", {"status": "success", "receipt": {
                "model": "glm-4v",
                "response_length": len(result),
            }}),
        }

    async def handle_web_search(self, params: dict) -> dict:
        """Handle the 'web_search' tool — logged in audit trail."""
        query = params.get("query", "")
        max_results = params.get("max_results", 5)

        if not query:
            return {"status": "error", "message": "No search query provided."}

        # Log in audit trail
        ledger = self._get_ledger()
        if ledger:
            try:
                action_id = ledger.queue_action(
                    "MCPSearch", "web_search", {"query": query[:100]},
                    risk_level="low",
                )
                ledger.approve_action(action_id)
            except Exception as e:
                logger.debug(f"Non-critical error: {e}")

        glm = self._get_glm_brain()
        if not glm or not glm.available():
            return {"status": "error", "message": "GLM not available for web search."}

        results = await glm.web_search(query, max_results=max_results)
        return {
            "status": "success",
            "message": f"Found {len(results)} results for: {query}",
            "results": results,
            "receipt": self._make_receipt("web_search", {"status": "success", "receipt": {
                "query": query,
                "result_count": len(results),
            }}),
        }

    async def handle_image_generation(self, params: dict) -> dict:
        """Handle the 'image_generation' tool — gated through ledger."""
        prompt = params.get("prompt", "")
        size = params.get("size", "1024x1024")

        if not prompt:
            return {"status": "error", "message": "No prompt provided."}

        # Gate through ledger — FAIL CLOSED if ledger unavailable
        ledger = self._get_ledger()
        if ledger is None:
            return {
                "status": "error",
                "message": "Ledger unavailable — refusing to execute without approval gate.",
                "receipt": self._make_receipt("image_generation", {"status": "error"}),
            }
        action_id = ledger.queue_action(
            "ImageGen", "generate_image",
            {"prompt": prompt, "size": size},
            risk_level="high",
        )
        # In MCP context, auto-approve is blocked by NEVER_AUTO_APPROVE_COMPONENTS
        # We need explicit approval — for now, reject if not approved
        approved = await ledger.wait_for_approval(action_id, timeout=30)
        if not approved:
            return {
                "status": "error",
                "message": "Image generation requires explicit approval. Action queued but not approved within timeout.",
                "receipt": self._make_receipt("image_generation", {"status": "rejected"}),
            }

        try:
            from integrations.image_gen import ImageGen
            ig = ImageGen()
            result = await ig.execute("generate_image", {"prompt": prompt, "size": size})
            return {
                "status": result.get("status", "unknown"),
                "message": result.get("message", ""),
                "receipt": self._make_receipt("image_generation", result),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    async def handle_video_generation(self, params: dict) -> dict:
        """Handle the 'video_generation' tool — gated through ledger."""
        prompt = params.get("prompt", "")
        quality = params.get("quality", "quality")

        if not prompt:
            return {"status": "error", "message": "No prompt provided."}

        # Gate through ledger — FAIL CLOSED if ledger unavailable
        ledger = self._get_ledger()
        if ledger is None:
            return {
                "status": "error",
                "message": "Ledger unavailable — refusing to execute without approval gate.",
                "receipt": self._make_receipt("video_generation", {"status": "error"}),
            }
        action_id = ledger.queue_action(
            "VideoGen", "generate_video",
            {"prompt": prompt, "quality": quality},
            risk_level="high",
        )
        approved = await ledger.wait_for_approval(action_id, timeout=30)
        if not approved:
            return {
                "status": "error",
                "message": "Video generation requires explicit approval. Action queued but not approved within timeout.",
                "receipt": self._make_receipt("video_generation", {"status": "rejected"}),
            }

        try:
            from integrations.video_gen import VideoGen
            vg = VideoGen()
            result = await vg.execute("generate_video", {"prompt": prompt, "quality": quality})
            return {
                "status": result.get("status", "unknown"),
                "message": result.get("message", ""),
                "receipt": self._make_receipt("video_generation", result),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    async def handle_code_execution(self, params: dict) -> dict:
        """Handle the 'code_execution' tool — gated through ledger."""
        code = params.get("code", "")
        language = params.get("language", "python")

        if not code:
            return {"status": "error", "message": "No code provided."}

        # Gate through ledger — FAIL CLOSED if ledger unavailable
        ledger = self._get_ledger()
        if ledger is None:
            return {
                "status": "error",
                "message": "Ledger unavailable — refusing to execute without approval gate.",
                "receipt": self._make_receipt("code_execution", {"status": "error"}),
            }
        action_id = ledger.queue_action(
            "CodeExecution", "execute_code",
            {"code": code, "language": language},
            risk_level="critical",
        )
        approved = await ledger.wait_for_approval(action_id, timeout=30)
        if not approved:
            return {
                "status": "error",
                "message": "Code execution requires explicit approval. Action queued but not approved within timeout.",
                "receipt": self._make_receipt("code_execution", {"status": "rejected"}),
            }

        glm = self._get_glm_brain()
        if not glm or not glm.available():
            return {"status": "error", "message": "GLM not available for code execution."}

        result = await glm.code_interpreter(code, language)
        return {
            "status": result.get("status", "unknown"),
            "message": result.get("output", ""),
            "receipt": self._make_receipt("code_execution", {"status": result.get("status"), "receipt": result}),
        }

    async def handle_execute_action(self, params: dict) -> dict:
        """Handle 'friday.execute_action' — routes to UniversalConnector.

        All actions go through the ledger's NEVER_AUTO_APPROVE_COMPONENTS
        gate. Physical/financial/code-execution actions require explicit
        user approval regardless of autonomy profile.
        """
        service = params.get("service") or params.get("component")
        action = params.get("action")
        action_params = params.get("params") or params.get("args") or {}

        if not service or not action:
            return {
                "status": "error",
                "message": "Missing required parameters: service and action",
            }

        try:
            from core.universal_connector import UniversalConnector
            connector = UniversalConnector()
            # Use a short approval timeout so MCP clients get a clear
            # "pending approval" response quickly rather than hanging.
            result = await connector.execute_action(
                service, action, action_params, approval_timeout=5,
            )
            return {
                "status": result.get("status", "unknown"),
                "message": result.get("message", ""),
                "receipt": self._make_receipt(
                    f"execute_action:{service}.{action}", result,
                ),
            }
        except Exception as exc:
            return {
                "status": "error",
                "message": f"execute_action failed: {exc}",
                "receipt": self._make_receipt("execute_action", {"status": "error"}),
            }

    async def handle_request_approval(self, params: dict) -> dict:
        """Handle 'friday.request_approval' — external agent submits an action
        for human approval via Friday's ledger.

        This is the killer feature for trusted execution: another AI agent
        (Claude Code, Cursor, etc.) can say "I want to delete this file"
        and Friday's human-in-the-loop gate stands between the suggestion
        and the action.

        Friday speaks the request out loud (if voice is configured), waits
        for the human's real voice/keyboard response, and returns
        approved/rejected to the calling agent.

        Params:
            component: The integration component (e.g. "Filesystem")
            action: The action name (e.g. "delete_file")
            params: Action parameters
            risk_level: "low" | "medium" | "high" | "critical"
            description: Human-readable description of what the agent wants to do
            timeout: Seconds to wait for approval (default 60)
            use_voice: If True, use voice-native approval (default False)

        Returns:
            {status: "approved" | "rejected" | "timeout",
             action_id: str,
             receipt: dict}
        """
        component = params.get("component")
        action = params.get("action")
        action_params = params.get("params") or {}
        # SECURITY: Ignore caller-supplied risk_level. An external agent
        # (Claude Code, Cursor) should not be able to self-classify a
        # destructive action as "low" to bypass the approval gate.
        # We compute risk_level ourselves via EthicalSentinel (or the
        # connector's _classify_risk fallback) using the component +
        # action + params.
        caller_risk_level = params.get("risk_level")
        if caller_risk_level is not None:
            logger.warning(
                "MCP request_approval: caller supplied risk_level=%r — "
                "IGNORING (security policy). Risk will be computed by "
                "Friday's EthicalSentinel.",
                caller_risk_level,
            )
        risk_level = self._compute_risk_level(component, action, action_params)
        description = params.get("description", f"{component}.{action}")
        timeout = int(params.get("timeout", 60))
        use_voice = bool(params.get("use_voice", False))

        if not component or not action:
            return {
                "status": "error",
                "message": "Missing required parameters: component and action",
                "receipt": self._make_receipt("request_approval", {"status": "error"}),
            }

        ledger = self._get_ledger()
        if not ledger:
            return {
                "status": "error",
                "message": "Ledger not available",
                "receipt": self._make_receipt("request_approval", {"status": "error"}),
            }

        # Queue the action in the ledger (this is the gate)
        action_id = ledger.queue_action(
            component, action, action_params, risk_level=risk_level,
        )
        logger.info(
            "MCP request_approval: queued %s.%s (id=%s, risk=%s, timeout=%ss)",
            component, action, action_id, risk_level, timeout,
        )

        # Wait for approval
        try:
            if use_voice:
                approved = await ledger.wait_for_voice_approval(
                    action_id, timeout=timeout,
                )
            else:
                approved = await ledger.wait_for_approval(
                    action_id, timeout=timeout,
                )
        except asyncio.TimeoutError:
            approved = False

        status = "approved" if approved else (
            "timeout" if not approved and ledger.pending_actions.get(action_id, {}).get("status") == "pending"
            else "rejected"
        )

        return {
            "status": status,
            "action_id": action_id,
            "component": component,
            "action": action,
            "message": (
                f"Action {action_id} {status} by human"
                if approved
                else f"Action {action_id} not approved within {timeout}s"
            ),
            "receipt": self._make_receipt("request_approval", {
                "status": status,
                "receipt": {
                    "action_id": action_id,
                    "component": component,
                    "action": action,
                    "risk_level": risk_level,
                    "description": description,
                },
            }),
        }

    # ─── Dispatch ───────────────────────────────────────────────────────

    async def dispatch(self, tool_name: str, params: dict) -> dict:
        """Dispatch a tool call to the appropriate handler.

        Accepts both the bare tool name (e.g. 'chat') and the
        namespaced form (e.g. 'friday.chat').
        """
        # Strip the 'friday.' prefix if present
        if tool_name.startswith("friday."):
            tool_name = tool_name[len("friday."):]

        handlers = {
            "chat": self.handle_chat,
            "vision": self.handle_vision,
            "search": self.handle_web_search,
            "web_search": self.handle_web_search,
            "generate_image": self.handle_image_generation,
            "image_generation": self.handle_image_generation,
            "generate_video": self.handle_video_generation,
            "video_generation": self.handle_video_generation,
            "code_execution": self.handle_code_execution,
            "execute_action": self.handle_execute_action,
            "request_approval": self.handle_request_approval,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return {"status": "error", "message": f"Unknown tool: {tool_name}"}

        return await handler(params)


# ──────────────────────────────────────────────────────────────────────────────
# JSON-RPC / stdio transport (MCP protocol)
# ──────────────────────────────────────────────────────────────────────────────

async def serve_stdio():
    """Serve the MCP server over stdio using JSON-RPC."""
    server = FridayMCPServer()

    logger.info("FRIDAY MCP Server starting (stdio transport)")

    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, asyncio.get_event_loop())

    while True:
        try:
            line = await reader.readline()
            if not line:
                break

            line = line.decode("utf-8").strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
                continue

            request_id = request.get("id")
            method = request.get("method", "")
            params = request.get("params", {})

            # ── JSON-RPC method routing ─────────────────────────────
            if method == "initialize":
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {"listChanged": False},
                        },
                        "serverInfo": {
                            "name": "friday-mcp",
                            "version": "1.0.0",
                        },
                    },
                }
            elif method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": TOOLS},
                }
            elif method == "tools/call":
                tool_name = params.get("name", "")
                tool_params = params.get("arguments", {})
                result = await server.dispatch(tool_name, tool_params)
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result, default=str)},
                        ],
                    },
                }
            elif method.startswith("friday."):
                # Direct namespaced method call (e.g. friday.chat)
                # Routes through the same dispatch as tools/call.
                result = await server.dispatch(method, params)
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result, default=str)},
                        ],
                        "receipt": result.get("receipt"),
                    },
                }
            elif method == "notifications/initialized":
                # No response needed for notifications
                continue
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }

            writer.write((json.dumps(response, default=str) + "\n").encode())
            await writer.drain()

        except Exception as exc:
            logger.error(f"MCP server error: {exc}")
            try:
                error_response = {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": "Internal error"},
                    "id": None,
                }
                writer.write((json.dumps(error_response) + "\n").encode())
                await writer.drain()
            except Exception as e:
                logger.debug(f"Non-critical error: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(serve_stdio())
