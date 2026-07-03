"""Chat API routes — SSE streaming, non-streaming POST, history, and stats."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, AsyncGenerator
import json
import logging

logger = logging.getLogger("friday.api.chat")

router = APIRouter()


# Rate limiting (slowapi) — protects the GLM free tier from runaway clients.
# We import the limiter lazily so the route still works if slowapi isn't installed.
try:
    from api.main import limiter
except Exception:
    limiter = None


# ---------------------------------------------------------------------------
# Use the singleton brain from api.main instead of creating a new one per request
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    user_name: Optional[str] = "User"


async def _get_brain():
    """Return the singleton FridayBrain (delegates to main._get_brain)."""
    from api.main import _get_brain
    return await _get_brain()


def _record_chat_request(provider: str, model: str, prompt: str, response: str) -> None:
    """Estimate token counts and record a chat request in the stats log.

    Uses a 4-char-per-token approximation when tiktoken isn't available.
    Cost is computed from the per-provider rate table in api.routes.stats.
    """
    try:
        from api.routes.stats import record_request, _estimate_cost
        # Rough token estimate: 1 token ≈ 4 chars (industry-standard approximation)
        tokens_in = max(1, len(prompt) // 4)
        tokens_out = max(1, len(response) // 4)
        cost = _estimate_cost(provider, tokens_in, tokens_out)
        record_request(provider, model, tokens_in, tokens_out, cost)
    except Exception as exc:
        logger.debug("Failed to record chat request stats: %s", exc)


def _resolve_provider_and_model(brain) -> tuple:
    """Return (provider_name, model_name) for the active brain."""
    try:
        provider = getattr(brain, "provider", "unknown")
        # Try common attribute names for the model
        for attr in ("model", "model_name", "_model"):
            model = getattr(brain, attr, None)
            if model:
                return provider, str(model)
        # Per-provider model lookup
        if provider == "glm" and getattr(brain, "glm_brain", None):
            return provider, getattr(brain.glm_brain, "model", "glm-4-flash")
        if provider == "ollama" and getattr(brain, "local_brain", None):
            return provider, getattr(brain.local_brain, "model", "llama3")
        if provider == "claude":
            return provider, getattr(brain, "claude_model", "claude-3-5-sonnet")
        return provider, "unknown"
    except Exception:
        return "unknown", "unknown"


# ---------------------------------------------------------------------------
# POST /api/chat  — non-streaming (collects full response then returns JSON)
# ---------------------------------------------------------------------------

# Apply rate limiting if slowapi is available. We use a conditional decorator
# pattern so the route still works if slowapi isn't installed.
def _rate_limited(limit_str: str):
    """Decorator that applies slowapi rate limiting if available."""
    def decorator(func):
        if limiter is not None:
            return limiter.limit(limit_str)(func)
        return func
    return decorator


@router.post("/chat")
@_rate_limited("30/minute")
async def chat(request: ChatRequest, http_request: Request):
    try:
        brain = await _get_brain()
        responses = []
        async for chunk in brain.chat_stream(request.message, request.user_name):
            responses.append(chunk)
        full_response = "".join(responses)
        provider, model = _resolve_provider_and_model(brain)
        _record_chat_request(provider, model, request.message, full_response)
        return {"response": full_response}
    except Exception as e:
        logger.exception("Chat error")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /api/chat/stream?message=...  — SSE streaming
# ---------------------------------------------------------------------------

@router.get("/chat/stream")
@_rate_limited("30/minute")
async def chat_stream(
    http_request: Request,
    message: str = Query(...),
    user_name: str = Query("User"),
):
    """Server-Sent Events endpoint for streaming chat responses."""

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            brain = await _get_brain()
            collected = []

            async for chunk in brain.chat_stream(message, user_name):
                collected.append(chunk)
                # Detect tool-call markers from the brain
                # Brain emits things like "\n[System: tool_name(...)]\n"
                # We wrap text and tool calls in structured SSE payloads
                if chunk.startswith("\n[System:") and chunk.endswith("]\n"):
                    # Tool call notification
                    tool_info = chunk.strip()
                    payload = json.dumps({"type": "tool_call", "raw": tool_info})
                    yield f"data: {payload}\n\n"
                else:
                    # Regular text chunk
                    payload = json.dumps({"type": "text", "content": chunk})
                    yield f"data: {payload}\n\n"

            # Signal completion
            yield "data: [DONE]\n\n"

            # Record stats after streaming completes
            provider, model = _resolve_provider_and_model(brain)
            _record_chat_request(provider, model, message, "".join(collected))

        except Exception as e:
            logger.exception("SSE stream error")
            error_payload = json.dumps({"type": "error", "message": str(e)})
            yield f"data: {error_payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/chat/history  — conversation history
# ---------------------------------------------------------------------------

@router.get("/chat/history")
async def get_history():
    try:
        brain = await _get_brain()
        return {"history": brain.conversation_history}
    except Exception as e:
        logger.exception("History error")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /api/chat/clear  — clear conversation context
# ---------------------------------------------------------------------------

@router.post("/chat/clear")
async def clear_context():
    try:
        brain = await _get_brain()
        brain.clear_context()
        return {"status": "success", "message": "Conversation context cleared."}
    except Exception as e:
        logger.exception("Clear context error")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /api/chat/stats  — brain statistics
# ---------------------------------------------------------------------------

@router.get("/chat/stats")
async def get_stats():
    try:
        brain = await _get_brain()
        return brain.get_stats()
    except Exception as e:
        logger.exception("Stats error")
        raise HTTPException(status_code=500, detail=str(e))
