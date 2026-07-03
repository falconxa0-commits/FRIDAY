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

    # ─── Tool handlers ──────────────────────────────────────────────────

    async def handle_chat(self, params: dict) -> dict:
        """Handle the 'chat' tool."""
        message = params.get("message", "")
        if not message:
            return {"status": "error", "message": "No message provided."}

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
        """Handle the 'vision' tool."""
        image_base64 = params.get("image_base64", "")
        prompt = params.get("prompt", "Describe this image in detail.")

        if not image_base64:
            return {"status": "error", "message": "No image provided."}

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
        """Handle the 'web_search' tool."""
        query = params.get("query", "")
        max_results = params.get("max_results", 5)

        if not query:
            return {"status": "error", "message": "No search query provided."}

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

        # Gate through ledger
        ledger = self._get_ledger()
        if ledger:
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

        # Gate through ledger
        ledger = self._get_ledger()
        if ledger:
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

        # Gate through ledger
        ledger = self._get_ledger()
        if ledger:
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
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(serve_stdio())
