"""MCP Transport — JSON-RPC stdio transport layer.

Extracted from mcp_server.py to reduce file size and improve
maintainability. This module handles the wire protocol (reading
JSON-RPC requests from stdin, dispatching them, and writing
responses to stdout).

The FridayMCPServer class and all tool handlers remain in
mcp_server.py. This module only contains the transport glue.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger("friday.mcp.transport")


async def serve_stdio(server) -> None:
    """Serve the MCP server over stdio using JSON-RPC.

    Args:
        server: A FridayMCPServer instance with `dispatch()` and
                `authenticate()` methods.
    """
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
                await send_response(
                    writer, None,
                    {"error": {"code": -32700, "message": "Parse error"}},
                )
                continue

            result = await handle_request(server, request)
            if result is None:
                # Notification — no response required.
                continue

            request_id = request.get("id")
            await send_response(writer, request_id, result)

        except Exception as exc:
            logger.error(f"MCP server error: {exc}")
            try:
                await send_response(
                    writer, None,
                    {"error": {"code": -32603, "message": "Internal error"}},
                )
            except Exception as e:
                logger.debug("Non-critical error: %s", e)


async def handle_request(server, request: Dict) -> Optional[Dict]:
    """Dispatch a single parsed JSON-RPC request.

    Returns:
        - ``None`` for notifications (no response should be sent).
        - A dict with ``{"result": ...}`` or ``{"error": ...}``.
    """
    request_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})

    if method == "initialize":
        return {"result": _build_initialize_response(server)}

    if method in ("friday/authenticate", "authenticate"):
        return _handle_authenticate(server, params)

    if method == "tools/list":
        return {"result": {"tools": server._get_tools() if hasattr(server, "_get_tools") else _get_tools_list(server)}}

    if method == "tools/call":
        return await _handle_tools_call(server, params)

    if method.startswith("friday.") and method != "friday/authenticate":
        return await _handle_namespaced_call(server, method, params)

    if method == "notifications/initialized":
        return None

    return {"error": {"code": -32601, "message": f"Method not found: {method}"}}


def _build_initialize_response(server) -> Dict:
    """Build the initialize response with capabilities."""
    from mcp_server import AUTH_REQUIRED
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {"listChanged": False},
            "auth": {
                "requiresAuth": AUTH_REQUIRED,
                "method": "friday/authenticate",
                "tokenEnvVar": "FRIDAY_MCP_TOKEN",
            },
        },
        "serverInfo": {
            "name": "friday-mcp",
            "version": "1.0.0",
        },
    }


def _handle_authenticate(server, params) -> Dict:
    """Handle the authenticate handshake."""
    token = params.get("token") if isinstance(params, dict) else None
    if server.authenticate(token):
        return {"result": {
            "authenticated": True,
            "methods": ["tools/list", "tools/call"],
        }}
    return {"error": {
        "code": -32001,
        "message": (
            "Unauthorized: invalid or missing MCP token. "
            "Set FRIDAY_MCP_TOKEN env var on the server, "
            "pass it as params.token to friday/authenticate."
        ),
    }}


def _get_tools_list(server):
    """Get the tools list from the server."""
    from mcp_server import TOOLS
    return TOOLS


async def _handle_tools_call(server, params) -> Dict:
    """Handle tools/call method."""
    auth_error = server._check_authenticated()
    if auth_error is not None:
        return {"error": auth_error["error"]}
    tool_name = params.get("name", "")
    tool_params = params.get("arguments", {})
    result = await server.dispatch(tool_name, tool_params)
    return {"result": {
        "content": [
            {"type": "text", "text": json.dumps(result, default=str)},
        ],
    }}


async def _handle_namespaced_call(server, method, params) -> Dict:
    """Handle friday.* namespaced method calls."""
    auth_error = server._check_authenticated()
    if auth_error is not None:
        return {"error": auth_error["error"]}
    result = await server.dispatch(method, params)
    return {"result": {
        "content": [
            {"type": "text", "text": json.dumps(result, default=str)},
        ],
        "receipt": result.get("receipt"),
    }}


async def send_response(writer, request_id, result) -> None:
    """Send a JSON-RPC response over the given writer."""
    response = {"jsonrpc": "2.0", "id": request_id, **result}
    writer.write((json.dumps(response, default=str) + "\n").encode())
    await writer.drain()
