import hmac
import os
import asyncio
import logging
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.routes import (
    chat, memory, agents, integrations, actions, scheduler, stats, trust,
    team, health, patterns, visual_memory, nigeria,
    identity, subconscious, persona, goals,
    notify, webhooks, learning, self_improvement, privacy, proactive, branching,
    metrics as metrics_route,
    dashboard,
)
from config.settings import FRIDAY_API_TOKEN, BRAIN_PROVIDER, FRIDAY_DEV_MODE

# Observability: structured logging, Prometheus metrics, correlation IDs,
# and optional Sentry init. Imported here so ``init_sentry`` runs at startup.
from core.observability import CorrelationIdMiddleware, init_sentry

logger = logging.getLogger("friday.api")

# ------------------------------------------------------------------
# Rate limiting (slowapi)
# ------------------------------------------------------------------
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded

    limiter = Limiter(key_func=get_remote_address)
except ImportError:
    limiter = None
    logger.warning("slowapi not installed — rate limiting disabled")

# ------------------------------------------------------------------
# App initialisation
# ------------------------------------------------------------------
app = FastAPI(title="Project FRIDAY API")

# Rate-limit middleware (must be added before CORS)
if limiter is not None:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    # Add the SlowAPIMiddleware so rate limits are actually enforced
    from slowapi.middleware import SlowAPIMiddleware
    app.add_middleware(SlowAPIMiddleware)

# CORS — configurable via ALLOWED_ORIGINS env var
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3001,http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed_origins if o.strip()],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Correlation ID middleware — added LAST so it is the OUTERMOST middleware
# (Starlette runs the last-added middleware first). It sets a request-scoped
# correlation ID from the ``X-Request-ID`` header (or a fresh UUID4) so that
# every structured log line and Sentry event can be traced back to the
# request that produced it. The ID is echoed back via the response header.
app.add_middleware(CorrelationIdMiddleware)

# Sentry — initialised at import time so errors are captured from the very
# first request. No-ops gracefully if SENTRY_DSN is not set.
init_sentry()

# ------------------------------------------------------------------
# Authentication
# ------------------------------------------------------------------
auth_scheme = HTTPBearer(auto_error=False)


async def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(auth_scheme),
):
    """Timing-safe token comparison.

    Also checks the ``token`` query parameter so that ``EventSource``
    (which cannot send custom headers) can still authenticate.

    Security model:
      - If FRIDAY_DEV_MODE=1 is explicitly set → no auth (local dev only).
      - Otherwise FRIDAY_API_TOKEN is required (auto-generated if missing).
    """
    if FRIDAY_DEV_MODE and not FRIDAY_API_TOKEN:
        # Explicit dev mode — no token required
        return "dev_token"

    # 1. Check Authorization header
    if credentials and credentials.credentials and hmac.compare_digest(credentials.credentials, FRIDAY_API_TOKEN):
        return credentials.credentials

    # 2. Check query parameter (fallback for EventSource / SSE)
    qp_token = request.query_params.get("token")
    if qp_token and hmac.compare_digest(qp_token, FRIDAY_API_TOKEN):
        return qp_token

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid or missing Friday Token. Set FRIDAY_API_TOKEN env var or FRIDAY_DEV_MODE=1 for local dev.",
    )


# ------------------------------------------------------------------
# Static file serving — must be mounted BEFORE routes that need auth
# so that static files are accessible without authentication.
# ------------------------------------------------------------------

_STATIC_DIR = Path(__file__).resolve().parent.parent / "apps" / "web" / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    logger.info(f"Serving static files from {_STATIC_DIR}")
else:
    logger.warning(f"Static directory not found: {_STATIC_DIR}")


# ------------------------------------------------------------------
# Routes (with auth)
# ------------------------------------------------------------------
app.include_router(chat.router, prefix="/api", dependencies=[Depends(verify_token)])
app.include_router(memory.router, prefix="/api/memory", dependencies=[Depends(verify_token)])
app.include_router(agents.router, prefix="/api", dependencies=[Depends(verify_token)])
app.include_router(integrations.router, prefix="/api/integrations", dependencies=[Depends(verify_token)])
app.include_router(actions.router, prefix="/api/actions", dependencies=[Depends(verify_token)])
app.include_router(scheduler.router, prefix="/api/scheduler", dependencies=[Depends(verify_token)])
app.include_router(stats.router, prefix="/api/stats", dependencies=[Depends(verify_token)])
app.include_router(trust.router, prefix="/api", dependencies=[Depends(verify_token)])
# Team routes use their own FRIDAY_USER_TOKEN auth (per-user tokens via
# the Authorization header) — NOT the global FRIDAY_API_TOKEN. This
# avoids the dual-token confusion where one header had to match two
# different secrets.
app.include_router(team.router, prefix="/api/team")
# /api/health router: SHALLOW /api/health and /api/ping are public (liveness
# probes), but the DEEP /api/health/deep endpoint is gated per-endpoint via
# core.auth.require_auth (applied in api/routes/health.py) because it exposes
# the full system map (integrations, ledger state, memory count).
app.include_router(health.router, prefix="/api/health")
app.include_router(patterns.router, prefix="/api/patterns", dependencies=[Depends(verify_token)])
app.include_router(visual_memory.router, prefix="/api/visual-memory", dependencies=[Depends(verify_token)])
app.include_router(nigeria.router, prefix="/api/nigeria", dependencies=[Depends(verify_token)])
app.include_router(identity.router, prefix="/api/identity", dependencies=[Depends(verify_token)])
app.include_router(subconscious.router, prefix="/api/subconscious", dependencies=[Depends(verify_token)])
app.include_router(persona.router, prefix="/api/persona", dependencies=[Depends(verify_token)])
app.include_router(goals.router, prefix="/api/goals", dependencies=[Depends(verify_token)])
app.include_router(notify.router, prefix="/api/notify", dependencies=[Depends(verify_token)])
app.include_router(webhooks.router, prefix="/api/webhooks")  # no auth — webhooks verify their own signatures
app.include_router(learning.router, prefix="/api/learning", dependencies=[Depends(verify_token)])
app.include_router(self_improvement.router, prefix="/api/self-improvement", dependencies=[Depends(verify_token)])
app.include_router(privacy.router, prefix="/api/privacy", dependencies=[Depends(verify_token)])
app.include_router(proactive.router, prefix="/api/proactive", dependencies=[Depends(verify_token)])
app.include_router(branching.router, prefix="/api", dependencies=[Depends(verify_token)])

# Dashboard router — Mission Control. Auth is enforced per-endpoint via
# ``core.auth.require_auth`` (returns 401 on missing token), so we don't
# attach the global ``verify_token`` dependency here.
app.include_router(dashboard.router, prefix="/api/dashboard")

# Prometheus metrics endpoint — UNAUTHENTICATED (Prometheus scrapers need
# anonymous access). The route itself restricts access to localhost unless
# ``METRICS_ALLOW_EXTERNAL=1`` is set, so it is safe to expose without auth.
app.include_router(metrics_route.router)

# Apply rate limits after all routers are loaded (avoids circular import)
try:
    from api.routes.chat import _apply_rate_limits
    _apply_rate_limits()
except Exception as exc:
    logger.warning(f"Could not apply chat rate limits: {exc}")


# ------------------------------------------------------------------
# Root route — serve index.html (no auth required)
# ------------------------------------------------------------------

@app.get("/")
async def root():
    """Serve the main web UI."""
    index_path = _STATIC_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(str(index_path))
    return {"status": "online", "message": "Friday API is running. (Web UI not found)"}


@app.get("/health")
async def health():
    """Lightweight health check — no auth required."""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "rate_limiting": limiter is not None,
    }


if limiter is not None:
    @app.get("/api/ping")
    @limiter.limit("30/minute")
    async def ping(request: Request):
        return {"pong": True}


# ------------------------------------------------------------------
# WebSocket — singleton brain + heartbeat
# ------------------------------------------------------------------

# Singleton brain instance (created once, reused across connections)
_brain_instance: Optional[object] = None
_brain_lock = asyncio.Lock()


async def _get_brain():
    """Lazy-initialise and return the singleton FridayBrain."""
    global _brain_instance
    if _brain_instance is not None:
        return _brain_instance

    async with _brain_lock:
        # Double-check after acquiring lock
        if _brain_instance is not None:
            return _brain_instance

        from core.brain import FridayBrain
        from core.memory import FridayMemory
        from core.emotions import EmotionsEngine
        from core.personality import FridayPersonality

        _memory = None
        _emotions = None
        _personality = None
        try:
            _memory = FridayMemory()
        except Exception:
            logger.warning("FridayMemory unavailable", exc_info=True)
        try:
            _emotions = EmotionsEngine()
        except Exception:
            logger.warning("EmotionsEngine unavailable", exc_info=True)
        try:
            _personality = FridayPersonality()
        except Exception:
            logger.warning("FridayPersonality unavailable", exc_info=True)

        _brain_instance = FridayBrain(
            memory=_memory,
            emotions=_emotions,
            personality=_personality,
        )
        return _brain_instance


WS_HEARTBEAT_INTERVAL = 30  # seconds
WS_HEARTBEAT_TIMEOUT = 10  # seconds to wait for pong


@app.websocket("/api/stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    if not await _authenticate_websocket(websocket):
        return

    try:
        # --- get singleton brain ------------------------------------
        brain = await _get_brain()

        # --- determine if GLM native streaming should be used ------
        glm_brain = _init_glm_brain()

        # --- ledger -------------------------------------------------
        from core.ledger import get_ledger
        ledger = get_ledger()

        # --- heartbeat task -----------------------------------------
        _ws_alive = True

        async def _heartbeat():
            """Periodically ping the client; close if no pong."""
            while _ws_alive:
                await asyncio.sleep(WS_HEARTBEAT_INTERVAL)
                if not _ws_alive:
                    return
                try:
                    await websocket.send_json({"type": "ping"})
                    # We don't actively wait for a pong here —
                    # if the next receive_text() times out the
                    # connection will be closed by the outer loop.
                except Exception:
                    break

        hb_task = asyncio.create_task(_heartbeat())

        # --- main receive loop --------------------------------------
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=WS_HEARTBEAT_INTERVAL + WS_HEARTBEAT_TIMEOUT
                )
            except asyncio.TimeoutError:
                # No message within heartbeat window — assume dead
                logger.info("WS heartbeat timeout — closing connection")
                break

            await _handle_ws_message(websocket, data, glm_brain, brain, ledger)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception:
        logger.exception("WebSocket error")
    finally:
        _ws_alive = False
        if hb_task and not hb_task.done():
            hb_task.cancel()
        try:
            await websocket.close()
        except Exception as e:
            logger.debug(f"Non-critical error: {e}")


# ------------------------------------------------------------------
# websocket_endpoint() helpers — each has a single responsibility.
# ------------------------------------------------------------------
async def _authenticate_websocket(websocket: WebSocket) -> bool:
    """Receive the auth token and validate it.

    Returns ``True`` when the caller should proceed with the session,
    ``False`` when the connection has been rejected (and closed) —
    either because no token arrived within 10s or because the supplied
    token did not match :data:`FRIDAY_API_TOKEN`.
    """
    token_msg = await asyncio.wait_for(
        websocket.receive_json(), timeout=10
    )
    if FRIDAY_API_TOKEN and not hmac.compare_digest(
        token_msg.get("token", ""), FRIDAY_API_TOKEN
    ):
        await websocket.send_json({"error": "Unauthorized"})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    return True


def _init_glm_brain():
    """Construct a :class:`GLMBrain` if GLM streaming is configured.

    Returns ``None`` when GLM isn't the configured provider or when the
    GLM client can't be initialised — callers should fall back to the
    default ``brain.chat_stream`` path in that case.
    """
    if BRAIN_PROVIDER != "glm":
        return None
    try:
        from core.glm_brain import GLMBrain
        glm_brain = GLMBrain()
        if not glm_brain.available():
            logger.info("GLM not available, falling back to brain.chat_stream")
            return None
        return glm_brain
    except Exception:
        logger.warning("GLMBrain import failed, falling back to brain.chat_stream")
        return None


async def _handle_ws_message(
    websocket: WebSocket,
    data: str,
    glm_brain,
    brain,
    ledger,
) -> None:
    """Dispatch one inbound WebSocket message.

    Branches on the message body:

    * ``"__pong__"`` — heartbeat response, nothing to do;
    * anything else — route to the chat handler and then push pending
      ledger actions back to the UI.

    Both downstream helpers swallow their own transport errors via the
    outer ``try/except WebSocketDisconnect`` in
    :func:`websocket_endpoint`.
    """
    if data == "__pong__":
        return  # heartbeat response
    await _process_chat_request(websocket, data, glm_brain, brain)
    await _process_branch_request(websocket, ledger)


async def _process_chat_request(
    websocket: WebSocket,
    data: str,
    glm_brain,
    brain,
) -> None:
    """Stream the brain's response to the client.

    Uses the native GLM streamer when ``glm_brain`` is available;
    otherwise falls back to the default brain stream. Each yielded
    chunk is written to the websocket as plain text so the client can
    render tokens incrementally.
    """
    if glm_brain is not None:
        async for chunk in glm_brain.chat_stream(data):
            await websocket.send_text(chunk)
    else:
        async for chunk in brain.chat_stream(data):
            await websocket.send_text(chunk)


async def _process_branch_request(
    websocket: WebSocket,
    ledger,
) -> None:
    """Push pending ledger actions back to the UI.

    Named "branch" because this is the hook where conversation-branch
    routing would dispatch — today it simply forwards the current set
    of pending actions awaiting user approval.
    """
    await websocket.send_json(
        {"pending_actions": list(ledger.pending_actions.values())}
    )
