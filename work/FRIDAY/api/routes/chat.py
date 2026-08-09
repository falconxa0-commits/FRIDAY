"""Chat API routes — SSE streaming, non-streaming POST, history, and stats."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, AsyncGenerator
import json
import logging
import time

from core.observability import metrics as _prom_metrics

logger = logging.getLogger("friday.api.chat")

router = APIRouter()


# ---------------------------------------------------------------------------
# Metrics helpers — sanitize provider names into stable Prometheus labels.
# In production ``provider`` is always a short string ("glm", "claude", …),
# but in tests it can be a MagicMock whose repr is non-deterministic and
# would explode label cardinality. Coerce to ``"unknown"`` in that case.
# ---------------------------------------------------------------------------


def _provider_label(provider) -> str:
    """Coerce a provider identifier into a short, stable string label."""
    if not isinstance(provider, str) or not provider:
        return "unknown"
    if provider.startswith("<") or len(provider) > 32:
        return "unknown"
    return provider.lower()


def _observe_chat_request(provider_label: str, status_label: str, start_time: float) -> None:
    """Record latency + counter for a chat request. Best-effort — never raises."""
    try:
        latency = time.perf_counter() - start_time
        _prom_metrics.chat_latency_seconds.labels(provider=provider_label).observe(latency)
        _prom_metrics.chat_requests_total.labels(
            provider=provider_label, status=status_label
        ).inc()
    except Exception as metric_exc:
        logger.debug("Failed to observe chat metrics: %s", metric_exc)


def _record_chat_error(module: str, error: BaseException) -> None:
    """Increment the errors_total counter. Best-effort — never raises."""
    try:
        _prom_metrics.record_error(module, error)
    except Exception as metric_exc:
        logger.debug("Failed to record chat error metric: %s", metric_exc)


# Rate limiting: we get the limiter at call time (not import time) to
# avoid circular import issues with api.main.
def _get_limiter():
    try:
        from api.main import limiter
        return limiter
    except Exception:
        return None


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
    """Estimate token counts and record a chat request in the stats log + cost tracker.

    Also updates the ``friday_tokens_used_total`` and ``friday_cost_usd_total``
    Prometheus counters so the observability stack has the same data as the
    in-memory stats log.
    """
    try:
        from api.routes.stats import record_request, _estimate_cost
        # Rough token estimate: 1 token ≈ 4 chars (industry-standard approximation)
        tokens_in = max(1, len(prompt) // 4)
        tokens_out = max(1, len(response) // 4)
        cost = _estimate_cost(provider, tokens_in, tokens_out)
        record_request(provider, model, tokens_in, tokens_out, cost)

        # ---- Observability: token + cost counters ---------------------
        # The provider label is sanitized to avoid label cardinality
        # explosions (e.g. MagicMock reprs in tests, long error strings).
        prov_label = _provider_label(provider)
        try:
            _prom_metrics.tokens_used_total.labels(
                provider=prov_label, direction="input"
            ).inc(tokens_in)
            _prom_metrics.tokens_used_total.labels(
                provider=prov_label, direction="output"
            ).inc(tokens_out)
            _prom_metrics.cost_usd_total.labels(provider=prov_label).inc(cost)
        except Exception as metric_exc:
            logger.debug("Failed to record token/cost metrics: %s", metric_exc)

        # Also record in the persistent CostTracker
        try:
            from core.cost_tracker import CostTracker
            tracker = CostTracker()
            # CostTracker.record_usage signature is:
            #   record_usage(provider, input_tokens, output_tokens)
            # Cost is computed internally from the RATES table.
            tracker.record_usage(
                provider=provider,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
            )
        except Exception as cost_exc:
            logger.debug("CostTracker recording failed: %s", cost_exc)
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

@router.post("/chat")
async def chat(chat_req: ChatRequest, request: Request):
    """Chat endpoint with rate limiting."""
    from core.rate_limiter import check_rate_limit
    check_rate_limit(request)
    # Observability: time the full request and record latency + outcome.
    start_time = time.perf_counter()
    provider_label = "unknown"
    status_label = "success"
    try:
        brain = await _get_brain()
        responses = []
        async for chunk in brain.chat_stream(chat_req.message, chat_req.user_name):
            responses.append(chunk)
        full_response = "".join(responses)
        provider, model = _resolve_provider_and_model(brain)
        provider_label = _provider_label(provider)
        _record_chat_request(provider, model, chat_req.message, full_response)
        return {"response": full_response}
    except Exception as e:
        status_label = "error"
        logger.exception("Chat error")
        _record_chat_error("api.routes.chat", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _observe_chat_request(provider_label, status_label, start_time)


# Apply rate limiting after module load (avoids circular import)
def _apply_rate_limits():
    """Apply slowapi rate limits to chat routes after api.main is loaded."""
    limiter = _get_limiter()
    if limiter is None:
        return
    # Decorate the chat function with rate limiting
    limiter.limit("60/minute")(chat)
    if 'chat_stream' in globals():
        limiter.limit("30/minute")(chat_stream)


# ---------------------------------------------------------------------------
# GET /api/chat/stream?message=...  — SSE streaming
# ---------------------------------------------------------------------------

@router.get("/chat/stream")
async def chat_stream(
    request: Request,
    message: str = Query(...),
    user_name: str = Query("User"),
):
    """Server-Sent Events endpoint for streaming chat responses."""
    from core.rate_limiter import check_rate_limit
    check_rate_limit(request)

    async def event_generator() -> AsyncGenerator[str, None]:
        # Observability: time the streaming response from first byte to DONE.
        start_time = time.perf_counter()
        provider_label = "unknown"
        status_label = "success"
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
            provider_label = _provider_label(provider)
            _record_chat_request(provider, model, message, "".join(collected))

        except Exception as e:
            status_label = "error"
            logger.exception("SSE stream error")
            _record_chat_error("api.routes.chat", e)
            error_payload = json.dumps({"type": "error", "message": str(e)})
            yield f"data: {error_payload}\n\n"
        finally:
            _observe_chat_request(provider_label, status_label, start_time)

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
