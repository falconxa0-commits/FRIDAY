"""Nigerian context integration — Friday for Nigerian users specifically.

Features:
  - Naira pricing: when researching products, shows prices in NGN with
    real current exchange rates
  - Local services: knows Nigerian banks, delivery services, NEPA/PHCN
  - Language depth: Pidgin generation in responses when appropriate
  - Local news: pulls from Punch, Vanguard, TechCabal
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Real exchange rate fetching (free, no API key required)
# ---------------------------------------------------------------------------

EXCHANGE_RATE_CACHE: Dict[str, float] = {}
EXCHANGE_RATE_CACHE_TIME: Optional[float] = None


async def get_usd_to_ngn_rate() -> Optional[float]:
    """Fetch the real current USD→NGN exchange rate.

    Uses the free open.er-api.com endpoint (no API key required).
    Returns None if the fetch fails.
    """
    import time
    global EXCHANGE_RATE_CACHE, EXCHANGE_RATE_CACHE_TIME

    # Cache for 1 hour
    if (EXCHANGE_RATE_CACHE_TIME is not None
            and time.time() - EXCHANGE_RATE_CACHE_TIME < 3600
            and "USD_NGN" in EXCHANGE_RATE_CACHE):
        return EXCHANGE_RATE_CACHE["USD_NGN"]

    try:
        import httpx
        resp = await asyncio.to_thread(
            httpx.get, "https://open.er-api.com/v6/latest/USD", timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            rate = data.get("rates", {}).get("NGN")
            if rate:
                EXCHANGE_RATE_CACHE["USD_NGN"] = float(rate)
                EXCHANGE_RATE_CACHE_TIME = time.time()
                return float(rate)
    except Exception as exc:
        logger.debug("Exchange rate fetch failed: %s", exc)
    return None


def convert_usd_to_ngn(usd_amount: float, rate: float) -> float:
    """Convert USD to NGN using the given rate."""
    return usd_amount * rate


def format_ngn(amount: float) -> str:
    """Format a NGN amount with thousand separators."""
    return f"₦{amount:,.0f}"


# ---------------------------------------------------------------------------
# Nigerian local services
# ---------------------------------------------------------------------------

NIGERIAN_BANKS = [
    {"code": "044", "name": "Access Bank"},
    {"code": "058", "name": "GTBank"},
    {"code": "057", "name": "Zenith Bank"},
    {"code": "011", "name": "First Bank of Nigeria"},
    {"code": "033", "name": "United Bank for Africa (UBA)"},
    {"code": "232", "name": "Sterling Bank"},
    {"code": "070", "name": "Fidelity Bank"},
    {"code": "032", "name": "Union Bank"},
    {"code": "035", "name": "Wema Bank"},
    {"code": "076", "name": "Polaris Bank"},
    {"code": "221", "name": "Stanbic IBTC Bank"},
    {"code": "082", "name": "Keystone Bank"},
    {"code": "215", "name": "Unity Bank"},
    {"code": "214", "name": "FCMB"},
    {"code": "024", "name": "Citi Bank Nigeria"},
]


NIGERIAN_DELIVERY_SERVICES = [
    {"name": "GIG Logistics", "url": "https://giglogistics.com", "coverage": "Nationwide"},
    {"name": "Kwik Delivery", "url": "https://kwik.delivery", "coverage": "Lagos, Abuja"},
    {"name": "Max.ng", "url": "https://max.ng", "coverage": "Lagos, Abuja, Ibadan"},
    {"name": "Gokada", "url": "https://gokada.co", "coverage": "Lagos"},
    {"name": "Nipost", "url": "https://nipost.gov.ng", "coverage": "Nationwide"},
    {"name": "DHL Nigeria", "url": "https://dhl.com.ng", "coverage": "Nationwide + International"},
]


# ---------------------------------------------------------------------------
# NEPA / Power status awareness
# ---------------------------------------------------------------------------

async def check_power_status() -> dict:
    """Check for power outage indicators.

    This is a heuristic — checks if common Nigerian power-related APIs
    are reachable. Real implementation would integrate with a paid
    service like Eko Electricity Distribution Company's API.

    Returns:
        {status: "stable" | "outage_likely" | "unknown",
         notes: str}
    """
    # Heuristic — check if any known Nigerian news site is reachable
    # (network instability is often correlated with power outages)
    try:
        import httpx
        resp = await asyncio.to_thread(
            httpx.get, "https://www.punchng.com", timeout=5
        )
        if resp.status_code == 200:
            return {"status": "stable", "notes": "Network reachable — power likely on"}
    except Exception:
        return {"status": "outage_likely",
                "notes": "Network unreachable — possible power/internet outage"}
    return {"status": "unknown", "notes": "Could not determine"}


# ---------------------------------------------------------------------------
# Local news sources
# ---------------------------------------------------------------------------

NIGERIAN_NEWS_SOURCES = [
    {"name": "Punch", "url": "https://www.punchng.com", "rss": "https://www.punchng.com/feed/"},
    {"name": "Vanguard", "url": "https://www.vanguardngr.com", "rss": "https://www.vanguardngr.com/feed/"},
    {"name": "TechCabal", "url": "https://techcabal.com", "rss": "https://techcabal.com/feed/"},
    {"name": "The Guardian Nigeria", "url": "https://guardian.ng", "rss": "https://guardian.ng/feed/"},
    {"name": "Premium Times", "url": "https://www.premiumtimesng.com", "rss": "https://www.premiumtimesng.com/feed"},
    {"name": "Channels TV", "url": "https://www.channelstv.com", "rss": "https://www.channelstv.com/feed/"},
]


async def fetch_nigerian_news(max_per_source: int = 3) -> List[dict]:
    """Fetch real news from Nigerian sources via RSS feeds.

    Returns a flat list of {source, title, url, summary} dicts.
    """
    articles: List[dict] = []
    try:
        import httpx
        import xml.etree.ElementTree as ET
    except ImportError:
        logger.warning("httpx or xml.etree not available")
        return articles

    for source in NIGERIAN_NEWS_SOURCES:
        try:
            resp = await asyncio.to_thread(
                httpx.get, source["rss"], timeout=10,
                headers={"User-Agent": "Friday/2.0 (+https://friday.local)"}
            )
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.text)
            # RSS 2.0 structure
            items = root.findall(".//item")[:max_per_source]
            for item in items:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                desc = item.findtext("description", "")
                articles.append({
                    "source": source["name"],
                    "source_url": source["url"],
                    "title": title,
                    "url": link,
                    "summary": desc[:300] if desc else "",
                })
        except Exception as exc:
            logger.debug("News fetch from %s failed: %s", source["name"], exc)

    return articles


# ---------------------------------------------------------------------------
# Pidgin generation (basic — for full Pidgin, use GLM with a Pidgin system prompt)
# ---------------------------------------------------------------------------

PIDGIN_SYSTEM_PROMPT = (
    "You are Friday, an AI assistant for a Nigerian user. The user is "
    "speaking Nigerian Pidgin English. Respond in natural Nigerian Pidgin. "
    "Use common Pidgin phrases like 'how you dey', 'I dey fine', 'abeg', "
    "'no wahala', 'I wan', 'chop', 'sabi'. Keep it warm and friendly."
)


# ---------------------------------------------------------------------------
# Naira pricing helper — converts USD price to NGN display
# ---------------------------------------------------------------------------

async def price_in_ngn(usd_amount: float) -> dict:
    """Convert a USD price to NGN using real exchange rate.

    Returns:
        {usd: float, ngn: float, rate: float, formatted: str, error: Optional[str]}
    """
    rate = await get_usd_to_ngn_rate()
    if rate is None:
        return {
            "usd": usd_amount,
            "ngn": None,
            "rate": None,
            "formatted": f"${usd_amount:.2f} USD (NGN rate unavailable)",
            "error": "Could not fetch exchange rate",
        }
    ngn = convert_usd_to_ngn(usd_amount, rate)
    return {
        "usd": usd_amount,
        "ngn": ngn,
        "rate": rate,
        "formatted": f"{format_ngn(ngn)} (≈ ${usd_amount:.2f} USD @ ₦{rate:.0f}/$)",
        "error": None,
    }
