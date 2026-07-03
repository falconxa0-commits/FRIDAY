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
)
from config.settings import FRIDAY_API_TOKEN, BRAIN_PROVIDER, FRIDAY_DEV_MODE

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

# CORS — configurable via ALLOWED_ORIGINS env var
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3001,http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed_origins if o.strip()],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

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
app.include_router(health.router, prefix="/api/health")  # no auth — health checks are public
app.include_router(patterns.router, prefix="/api/patterns", dependencies=[Depends(verify_token)])
app.include_router(visual_memory.router, prefix="/api/visual-memory", dependencies=[Depends(verify_token)])
app.include_router(nigeria.router, prefix="/api/nigeria", dependencies=[Depends(verify_token)])


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
    try:
        # --- authenticate -------------------------------------------
        token_msg = await asyncio.wait_for(
            websocket.receive_json(), timeout=10
        )
        if FRIDAY_API_TOKEN and not hmac.compare_digest(
            token_msg.get("token", ""), FRIDAY_API_TOKEN
        ):
            await websocket.send_json({"error": "Unauthorized"})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        # --- get singleton brain ------------------------------------
        brain = await _get_brain()

        # --- determine if GLM native streaming should be used ------
        use_glm_stream = (BRAIN_PROVIDER == "glm")
        glm_brain = None
        if use_glm_stream:
            try:
                from core.glm_brain import GLMBrain
                glm_brain = GLMBrain()
                if not glm_brain.available():
                    glm_brain = None
                    logger.info("GLM not available, falling back to brain.chat_stream")
            except Exception:
                glm_brain = None
                logger.warning("GLMBrain import failed, falling back to brain.chat_stream")

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

            if data == "__pong__":
                continue  # heartbeat response

            # Route to GLM native streaming or default brain stream
            if glm_brain is not None:
                async for chunk in glm_brain.chat_stream(data):
                    await websocket.send_text(chunk)
            else:
                async for chunk in brain.chat_stream(data):
                    await websocket.send_text(chunk)

            # Push pending actions to UI
            await websocket.send_json(
                {"pending_actions": list(ledger.pending_actions.values())}
            )

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
        except Exception:
            pass
