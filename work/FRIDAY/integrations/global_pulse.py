import logging
import datetime
from typing import Optional
from xml.etree import ElementTree

import httpx

from integrations.base import BaseIntegration
from config.settings import TAVILY_API_KEY

logger = logging.getLogger(__name__)

# Public RSS feeds for real news — no API key required.
_RSS_FEEDS = {
    "world": "http://rss.cnn.com/rss/edition_world.rss",
    "technology": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "business": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "science": "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",
}


class GlobalPulseIntegration(BaseIntegration):
    """Aggregates real headlines from public RSS feeds.

    If a ``NEWSAPI_KEY`` or ``TAVILY_API_KEY`` is available the
    integration will also try those sources, but it degrades gracefully
    to RSS-only when keys are absent.
    """

    @property
    def name(self) -> str:
        return "GlobalPulse"

    def available(self) -> bool:
        # Always available — RSS feeds need no key.
        return True

    def list_actions(self):
        return ["get_world_status", "get_headlines"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}

        try:
            if action == "get_world_status":
                return await self._get_world_status(params)
            elif action == "get_headlines":
                return await self._get_headlines(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by GlobalPulse.",
                )
        except Exception as exc:
            logger.error("GlobalPulse execute failed: %s", exc)
            return self._make_response("error", str(exc))

    # ---- action implementations -------------------------------------

    async def _get_world_status(self, params: dict) -> dict:
        """Fetch headlines from multiple RSS categories and summarise."""
        categories = params.get("categories", list(_RSS_FEEDS.keys()))
        limit = params.get("limit", 5)

        all_headlines: list[dict] = []
        for cat in categories:
            feed_url = _RSS_FEEDS.get(cat)
            if not feed_url:
                continue
            headlines = await self._fetch_rss(feed_url, limit=limit)
            all_headlines.extend(headlines)

        if not all_headlines:
            # Fallback: attempt Tavily if key is configured
            if TAVILY_API_KEY:
                tavily_result = await self._fetch_tavily(
                    "current world news today", max_results=5
                )
                if tavily_result:
                    all_headlines = tavily_result

        if not all_headlines:
            return self._make_response(
                "demo_mode",
                "No live headlines could be fetched. Check network connectivity.",
            )

        # Build a human-readable summary
        summary_lines = [
            f"  [{h.get('category', 'news')}] {h['title']}"
            for h in all_headlines[:15]
        ]
        message = "Global Pulse — latest headlines:\n" + "\n".join(summary_lines)

        return self._make_response(
            "success",
            message,
            receipt_data={
                "headlines": all_headlines,
                "source_count": len(categories),
                "fetched_at": datetime.datetime.now().isoformat(),
            },
        )

    async def _get_headlines(self, params: dict) -> dict:
        """Fetch headlines for a single category."""
        category = params.get("category", "world")
        limit = params.get("limit", 10)
        feed_url = _RSS_FEEDS.get(category)

        if not feed_url:
            return self._make_response(
                "error",
                f"Unknown category '{category}'. Choose from: {', '.join(_RSS_FEEDS)}",
            )

        headlines = await self._fetch_rss(feed_url, limit=limit)

        # Tavily fallback
        if not headlines and TAVILY_API_KEY:
            headlines = await self._fetch_tavily(
                f"{category} news today", max_results=limit
            )

        if not headlines:
            return self._make_response(
                "demo_mode",
                f"No headlines available for '{category}'. Network may be unavailable.",
            )

        return self._make_response(
            "success",
            f"{category.capitalize()} headlines: "
            + "; ".join(h["title"] for h in headlines),
            receipt_data={"headlines": headlines, "category": category},
        )

    # ---- data fetching helpers --------------------------------------

    @staticmethod
    async def _fetch_rss(feed_url: str, limit: int = 5) -> list[dict]:
        """Parse an RSS feed and return up to *limit* headline dicts."""
        headlines: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(feed_url)
                resp.raise_for_status()

            root = ElementTree.fromstring(resp.text)
            # RSS 2.0 items
            for item in root.iter("item"):
                if len(headlines) >= limit:
                    break
                title_el = item.find("title")
                link_el = item.find("link")
                desc_el = item.find("description")
                if title_el is not None and title_el.text:
                    headlines.append(
                        {
                            "title": title_el.text.strip(),
                            "link": link_el.text.strip() if link_el is not None and link_el.text else "",
                            "description": (desc_el.text or "").strip() if desc_el is not None else "",
                            "source": feed_url,
                        }
                    )
        except Exception:
            logger.exception("Failed to fetch RSS from %s", feed_url)

        return headlines

    @staticmethod
    async def _fetch_tavily(query: str, max_results: int = 5) -> list[dict]:
        """Optional Tavily search fallback (requires TAVILY_API_KEY)."""
        if not TAVILY_API_KEY:
            return []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": TAVILY_API_KEY,
                        "query": query,
                        "max_results": max_results,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            return [
                {
                    "title": r.get("title", ""),
                    "link": r.get("url", ""),
                    "description": r.get("content", ""),
                    "source": "tavily",
                }
                for r in data.get("results", [])
            ]
        except Exception:
            logger.exception("Tavily search failed")
            return []
