"""Predictive pre-loading — Friday fetches what you'll need before you ask.

Based on patterns from PatternEngine + time-of-day, Friday pre-loads:
  - Weather already fetched by 7am
  - Emails already summarized
  - Calendar already checked
  - Common research topics already cached

When you ask "what's my morning look like?" Friday answers instantly
from pre-loaded data instead of making you wait for API calls.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Predictor:
    """Pre-loads data based on observed patterns + time-of-day."""

    def __init__(self, pattern_engine=None):
        """
        Args:
            pattern_engine: Optional PatternEngine instance for pattern-based
                predictions. If None, only time-of-day predictions are used.
        """
        self.pattern_engine = pattern_engine
        self._cache: Dict[str, dict] = {}  # cache_key → {data, fetched_at}
        self._preload_tasks: List[asyncio.Task] = []
        self._active = False

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def get_cached(self, key: str, max_age_seconds: int = 600) -> Optional[dict]:
        """Get a cached value if it's still fresh."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        age = (datetime.datetime.now() - entry["fetched_at"]).total_seconds()
        if age > max_age_seconds:
            return None
        return entry["data"]

    def set_cached(self, key: str, data: Any) -> None:
        self._cache[key] = {
            "data": data,
            "fetched_at": datetime.datetime.now(),
        }

    # ------------------------------------------------------------------
    # Time-of-day pre-loading
    # ------------------------------------------------------------------

    def get_preload_schedule(self) -> List[dict]:
        """Return the time-of-day preload schedule.

        Each entry has:
            - hour: the hour (0-23) at which to preload
            - key: cache key to populate
            - action: a callable that returns the data to cache
        """
        return [
            {"hour": 7, "key": "morning_weather", "action": "fetch_weather"},
            {"hour": 7, "key": "morning_calendar", "action": "fetch_calendar"},
            {"hour": 7, "key": "morning_emails_summary", "action": "fetch_email_summary"},
            {"hour": 9, "key": "morning_news", "action": "fetch_news"},
            {"hour": 12, "key": "afternoon_weather", "action": "fetch_weather"},
        ]

    async def run_preload_loop(self) -> None:
        """Continuously check the schedule and pre-load data when due.

        This is the main loop — runs forever, checking every minute.
        """
        self._active = True
        logger.info("Predictor preload loop started")
        last_preloaded_hour: Dict[str, int] = {}  # key → last hour preloaded

        while self._active:
            now = datetime.datetime.now()
            today = now.date()
            for entry in self.get_preload_schedule():
                if now.hour == entry["hour"]:
                    # Only preload once per day per key
                    last_key = f"{entry['key']}_{today}"
                    if last_preloaded_hour.get(last_key) == entry["hour"]:
                        continue
                    try:
                        data = await self._fetch(entry["action"])
                        if data is not None:
                            self.set_cached(entry["key"], data)
                            last_preloaded_hour[last_key] = entry["hour"]
                            logger.info("Pre-loaded %s at %02d:00",
                                        entry["key"], entry["hour"])
                    except Exception as exc:
                        logger.warning("Preload %s failed: %s", entry["key"], exc)
            await asyncio.sleep(60)

    async def _fetch(self, action: str) -> Any:
        """Execute a fetch action and return the data."""
        if action == "fetch_weather":
            return await self._fetch_weather()
        if action == "fetch_calendar":
            return await self._fetch_calendar()
        if action == "fetch_email_summary":
            return await self._fetch_email_summary()
        if action == "fetch_news":
            return await self._fetch_news()
        return None

    async def _fetch_weather(self) -> Optional[dict]:
        try:
            from core.universal_connector import UniversalConnector
            connector = UniversalConnector()
            if "Weather" in connector.integrations:
                result = await connector.integrations["Weather"].execute(
                    "get_weather", {"location": "Lagos"}
                )
                return result
        except Exception as exc:
            logger.debug("Weather fetch failed: %s", exc)
        return None

    async def _fetch_calendar(self) -> Optional[dict]:
        try:
            from core.universal_connector import UniversalConnector
            connector = UniversalConnector()
            if "Calendar" in connector.integrations:
                return await connector.integrations["Calendar"].execute(
                    "get_todays_events", {}
                )
        except Exception as exc:
            logger.debug("Calendar fetch failed: %s", exc)
        return None

    async def _fetch_email_summary(self) -> Optional[dict]:
        try:
            from core.universal_connector import UniversalConnector
            connector = UniversalConnector()
            if "Gmail" in connector.integrations:
                return await connector.integrations["Gmail"].execute(
                    "get_recent", {"count": 10}
                )
        except Exception as exc:
            logger.debug("Email fetch failed: %s", exc)
        return None

    async def _fetch_news(self) -> Optional[dict]:
        try:
            from core.glm_brain import GLMBrain
            brain = GLMBrain()
            if brain.available():
                results = await brain.web_search("latest technology news today", max_results=5)
                return {"results": results}
        except Exception as exc:
            logger.debug("News fetch failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Pattern-based pre-loading
    # ------------------------------------------------------------------

    async def predict_next_need(self, current_context: dict) -> List[str]:
        """Based on patterns + current context, predict what the user will need next.

        Returns a list of cache keys that should be pre-loaded.
        """
        predictions: List[str] = []

        # Time-of-day based
        hour = datetime.datetime.now().hour
        if 6 <= hour < 9:
            predictions.append("morning_weather")
            predictions.append("morning_calendar")
        elif 12 <= hour < 14:
            predictions.append("afternoon_weather")

        # Pattern-based (if PatternEngine is available)
        if self.pattern_engine and self.pattern_engine.patterns:
            suggestions = await self.pattern_engine.get_suggestions(current_context)
            for s in suggestions:
                if "weather" in s["suggestion"].lower():
                    predictions.append("morning_weather")
                if "calendar" in s["suggestion"].lower():
                    predictions.append("morning_calendar")

        return list(set(predictions))

    def stop(self) -> None:
        self._active = False
        for task in self._preload_tasks:
            task.cancel()
