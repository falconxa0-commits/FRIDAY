"""Nigerian context API routes — Naira pricing, local services, news.

Endpoints:
    GET  /api/nigeria/naira?usd=10.0       — convert USD to NGN at real rate
    GET  /api/nigeria/banks                — list Nigerian banks
    GET  /api/nigeria/delivery             — list Nigerian delivery services
    GET  /api/nigeria/news                 — fetch real Nigerian news via RSS
    GET  /api/nigeria/power                — check power/network status
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("friday.api.nigeria")

router = APIRouter()


@router.get("/naira")
async def naira_price(usd: float = Query(..., ge=0)):
    """Convert a USD amount to NGN using the real current exchange rate."""
    from integrations.nigerian_context import price_in_ngn
    result = await price_in_ngn(usd)
    return result


@router.get("/banks")
async def list_banks():
    """List known Nigerian banks."""
    from integrations.nigerian_context import NIGERIAN_BANKS
    return {"banks": NIGERIAN_BANKS, "count": len(NIGERIAN_BANKS)}


@router.get("/delivery")
async def list_delivery():
    """List Nigerian delivery services."""
    from integrations.nigerian_context import NIGERIAN_DELIVERY_SERVICES
    return {"services": NIGERIAN_DELIVERY_SERVICES, "count": len(NIGERIAN_DELIVERY_SERVICES)}


@router.get("/news")
async def nigerian_news(max_per_source: int = Query(3, ge=1, le=10)):
    """Fetch real Nigerian news from RSS feeds (Punch, Vanguard, TechCabal, etc.)."""
    from integrations.nigerian_context import fetch_nigerian_news, NIGERIAN_NEWS_SOURCES
    articles = await fetch_nigerian_news(max_per_source=max_per_source)
    return {
        "articles": articles,
        "count": len(articles),
        "sources": [s["name"] for s in NIGERIAN_NEWS_SOURCES],
    }


@router.get("/power")
async def power_status():
    """Check Nigerian power/network stability (heuristic)."""
    from integrations.nigerian_context import check_power_status
    return await check_power_status()
