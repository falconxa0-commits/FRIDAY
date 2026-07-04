"""Simple rate limiting middleware — doesn't require slowapi.

Implements a per-IP, per-endpoint rate limiter using an in-memory
sliding window. Returns 429 with Retry-After header when exceeded.
"""
import time
from collections import defaultdict
from fastapi import Request, HTTPException, status


# Rate limits per endpoint pattern (requests per minute)
RATE_LIMITS = {
    "/api/chat": 60,
    "/api/chat/stream": 30,
    "/api/integrations/execute": 20,
}

# In-memory store: { (ip, path) -> [timestamps] }
_rate_store: dict = defaultdict(list)
_WINDOW_SECONDS = 60  # 1 minute sliding window


def check_rate_limit(request: Request) -> None:
    """Check rate limit for the current request. Raises 429 if exceeded."""
    path = request.url.path
    client_ip = request.client.host if request.client else "unknown"

    # Check if this path has a rate limit
    limit = None
    for pattern, lim in RATE_LIMITS.items():
        if path.startswith(pattern):
            limit = lim
            break

    if limit is None:
        return  # No rate limit for this path

    key = (client_ip, path)
    now = time.time()

    # Remove timestamps older than the window
    _rate_store[key] = [ts for ts in _rate_store[key] if now - ts < _WINDOW_SECONDS]

    # Check if limit exceeded
    if len(_rate_store[key]) >= limit:
        # Calculate retry-after
        oldest = _rate_store[key][0] if _rate_store[key] else now
        retry_after = int(_WINDOW_SECONDS - (now - oldest)) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit} requests per minute for {path}.",
            headers={"Retry-After": str(max(retry_after, 1))},
        )

    # Record this request
    _rate_store[key].append(now)


def get_rate_limit_status(request: Request) -> dict:
    """Return current rate limit status for informational purposes."""
    path = request.url.path
    client_ip = request.client.host if request.client else "unknown"
    limit = None
    for pattern, lim in RATE_LIMITS.items():
        if path.startswith(pattern):
            limit = lim
            break
    if limit is None:
        return {"limited": False}
    key = (client_ip, path)
    now = time.time()
    recent = [ts for ts in _rate_store[key] if now - ts < _WINDOW_SECONDS]
    return {
        "limited": True,
        "limit": limit,
        "remaining": max(0, limit - len(recent)),
        "reset_in_seconds": int(_WINDOW_SECONDS - (now - recent[0])) if recent else _WINDOW_SECONDS,
    }
