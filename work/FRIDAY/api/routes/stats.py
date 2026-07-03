"""Stats API route — real usage statistics.

Shows which model handled last N requests, real token counts,
real cost per provider, and rate-limit indicators.
"""

from fastapi import APIRouter, HTTPException
import datetime
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("friday.api.stats")

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory request log — populated by the chat route when it processes
# requests.  In production this would be backed by a database.
# ---------------------------------------------------------------------------

_request_log: List[Dict] = []
MAX_LOG_ENTRIES = 200


def record_request(provider: str, model: str, tokens_in: int, tokens_out: int, cost: float):
    """Record a completed request for stats tracking."""
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "provider": provider,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
    }
    _request_log.append(entry)
    if len(_request_log) > MAX_LOG_ENTRIES:
        _request_log[:] = _request_log[-MAX_LOG_ENTRIES:]


# ---------------------------------------------------------------------------
# Cost model (per 1K tokens)
# ---------------------------------------------------------------------------

_COST_PER_1K = {
    "glm": {"input": 0.0, "output": 0.0},       # GLM free tier
    "claude": {"input": 0.003, "output": 0.015},  # Claude Sonnet approx
    "gemini": {"input": 0.00025, "output": 0.0005},  # Gemini Flash approx
    "ollama": {"input": 0.0, "output": 0.0},      # Local = free
}


def _estimate_cost(provider: str, tokens_in: int, tokens_out: int) -> float:
    rates = _COST_PER_1K.get(provider, {"input": 0.0, "output": 0.0})
    return (tokens_in / 1000 * rates["input"]) + (tokens_out / 1000 * rates["output"])


# ---------------------------------------------------------------------------
# Rate limit indicators for Z.ai free tier
# ---------------------------------------------------------------------------

_ZAI_FREE_TIER_LIMITS = {
    "requests_per_minute": 60,
    "tokens_per_day": 100_000,
    "description": "Z.ai free tier: ~60 RPM, 100K tokens/day",
}


# ---------------------------------------------------------------------------
# GET /api/stats  — full usage statistics
# ---------------------------------------------------------------------------

@router.get("")
async def get_stats():
    """Return real usage statistics."""
    from config.settings import BRAIN_PROVIDER

    total_requests = len(_request_log)
    total_tokens_in = sum(r.get("tokens_in", 0) for r in _request_log)
    total_tokens_out = sum(r.get("tokens_out", 0) for r in _request_log)
    total_cost = sum(r.get("cost_usd", 0.0) for r in _request_log)

    # Breakdown by provider
    provider_stats: Dict[str, Dict] = {}
    for r in _request_log:
        prov = r.get("provider", "unknown")
        if prov not in provider_stats:
            provider_stats[prov] = {
                "requests": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "models_used": set(),
            }
        ps = provider_stats[prov]
        ps["requests"] += 1
        ps["tokens_in"] += r.get("tokens_in", 0)
        ps["tokens_out"] += r.get("tokens_out", 0)
        ps["cost_usd"] += r.get("cost_usd", 0.0)
        ps["models_used"].add(r.get("model", ""))

    # Convert sets to lists for JSON serialization
    for ps in provider_stats.values():
        ps["models_used"] = sorted(ps["models_used"])
        ps["cost_usd"] = round(ps["cost_usd"], 6)

    # Last N requests
    recent = _request_log[-20:] if _request_log else []

    # Current provider info
    current_provider = BRAIN_PROVIDER
    provider_cost_note = ""
    if current_provider == "glm":
        provider_cost_note = "GLM free tier — $0.00 cost"
    elif current_provider == "ollama":
        provider_cost_note = "Ollama local — $0.00 cost"
    else:
        provider_cost_note = f"{current_provider} — paid API"

    # ---- Rate-limit warning (Z.ai free tier) --------------------------
    # Compute usage in the last 60 seconds vs RPM limit, and today's
    # total tokens vs the daily token cap.
    rate_limit_warning = None
    zai_limits = _ZAI_FREE_TIER_LIMITS if current_provider == "glm" else None
    if zai_limits:
        now = datetime.datetime.now()
        one_minute_ago = now - datetime.timedelta(seconds=60)
        requests_last_minute = sum(
            1 for r in _request_log
            if datetime.datetime.fromisoformat(r["timestamp"]) >= one_minute_ago
        )
        today = now.date()
        tokens_today = sum(
            r.get("tokens_in", 0) + r.get("tokens_out", 0)
            for r in _request_log
            if datetime.datetime.fromisoformat(r["timestamp"]).date() == today
        )
        rpm_limit = zai_limits["requests_per_minute"]
        tpd_limit = zai_limits["tokens_per_day"]
        rpm_pct = (requests_last_minute / rpm_limit * 100) if rpm_limit else 0
        tpd_pct = (tokens_today / tpd_limit * 100) if tpd_limit else 0

        warnings = []
        if rpm_pct >= 80:
            warnings.append(
                f"RPM at {rpm_pct:.0f}% of free-tier limit "
                f"({requests_last_minute}/{rpm_limit} requests/min)"
            )
        if tpd_pct >= 80:
            warnings.append(
                f"Daily tokens at {tpd_pct:.0f}% of free-tier limit "
                f"({tokens_today}/{tpd_limit} tokens/day)"
            )
        if warnings:
            rate_limit_warning = {
                "level": "warning" if (rpm_pct < 100 and tpd_pct < 100) else "critical",
                "messages": warnings,
                "rpm_pct": round(rpm_pct, 1),
                "tpd_pct": round(tpd_pct, 1),
                "requests_last_minute": requests_last_minute,
                "tokens_today": tokens_today,
            }

    return {
        "current_provider": current_provider,
        "provider_cost_note": provider_cost_note,
        "total_requests": total_requests,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_cost_usd": round(total_cost, 6),
        "provider_breakdown": provider_stats,
        "recent_requests": recent,
        "zai_rate_limits": zai_limits,
        "rate_limit_warning": rate_limit_warning,
        "optimization_suggestions": _generate_optimization_suggestions(provider_stats),
    }


def _generate_optimization_suggestions(provider_stats: Dict[str, Dict]) -> List[dict]:
    """Generate real optimization suggestions based on real usage data.

    Only suggests things that would actually save money given the
    observed usage pattern. Never suggests switching if a provider
    isn't being used.
    """
    suggestions: List[dict] = []

    # If user is paying for Claude/GPT on simple questions, suggest GLM
    for paid_provider in ("claude", "gpt", "gemini"):
        ps = provider_stats.get(paid_provider)
        if not ps:
            continue
        # If the paid provider has many small requests (low token counts),
        # those could likely be served by GLM for free
        avg_tokens = (ps["tokens_in"] + ps["tokens_out"]) / max(ps["requests"], 1)
        if avg_tokens < 200 and ps["requests"] >= 5:
            # Estimate savings: paid_provider cost × 0.8 (assume 80% could move to GLM)
            cost = ps["cost_usd"]
            est_savings = cost * 0.8
            if est_savings > 0.001:  # only suggest if savings > 0.1 cents
                suggestions.append({
                    "type": "switch_to_glm",
                    "message": (
                        f"{ps['requests']} of your {paid_provider} calls had small "
                        f"token counts (<200 avg) — these are answerable by GLM-4-Flash "
                        f"for free. Estimated savings: ${est_savings:.2f}"
                    ),
                    "estimated_savings_usd": round(est_savings, 2),
                    "affected_provider": paid_provider,
                    "affected_requests": ps["requests"],
                })

    # If GLM is being used heavily but rate limits are close, suggest local Ollama
    glm_ps = provider_stats.get("glm")
    if glm_ps and glm_ps["requests"] > 100:
        suggestions.append({
            "type": "consider_local",
            "message": (
                f"You've made {glm_ps['requests']} GLM requests. Consider installing "
                f"Ollama locally for offline/low-latency queries to reduce GLM rate-limit usage."
            ),
            "estimated_savings_usd": 0.0,  # GLM is free, but rate-limit headroom has value
            "affected_provider": "glm",
            "affected_requests": glm_ps["requests"],
        })

    # If video generation is used heavily, suggest batching
    # (CogVideoX is slow — batch when possible)
    # (This would need a separate per-action log; skipped for now.)

    return suggestions
