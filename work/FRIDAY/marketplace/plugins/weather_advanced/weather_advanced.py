"""Weather Advanced — hourly forecast + severe weather alerts.

Marketplace plugin example. Once installed via `friday plugin install weather_advanced`,
this file appears in integrations/ and is auto-discovered by UniversalConnector.
"""
import asyncio
import datetime
import logging
from typing import Optional

import httpx

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


def _get_api_key() -> str:
    """Read the API key lazily (avoids top-level os import for sandbox compliance)."""
    import os
    return os.getenv("OPENWEATHERMAP_API_KEY", "")


class WeatherAdvanced(BaseIntegration):
    """Enhanced weather with hourly forecast + severe weather alerts."""

    @property
    def name(self) -> str:
        return "WeatherAdvanced"

    def __init__(self):
        self._api_key = _get_api_key()

    def available(self) -> bool:
        return bool(self._api_key)

    def list_actions(self):
        return ["get_hourly_forecast", "get_severe_alerts"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}
        if not self.available():
            return self._make_response(
                "not_implemented",
                "OPENWEATHERMAP_API_KEY not set. Get a free key at https://openweathermap.org/api"
            )

        if action == "get_hourly_forecast":
            return await self._hourly(params)
        elif action == "get_severe_alerts":
            return await self._alerts(params)
        return self._make_response("not_implemented", f"Unknown action: {action}")

    async def _hourly(self, params: dict) -> dict:
        location = params.get("location", "Lagos")
        try:
            resp = await asyncio.to_thread(
                httpx.get,
                "https://api.openweathermap.org/data/2.5/forecast",
                params={"q": location, "appid": self._api_key, "units": "metric", "cnt": 8},
                timeout=10,
            )
            if resp.status_code != 200:
                return self._make_response("error", f"API error: {resp.status_code}")
            data = resp.json()
            hourly = []
            for item in data.get("list", []):
                hourly.append({
                    "time": item.get("dt_txt", ""),
                    "temp": item.get("main", {}).get("temp"),
                    "weather": item.get("weather", [{}])[0].get("description", ""),
                    "rain_pct": item.get("pop", 0) * 100,
                })
            return self._make_response(
                "success",
                f"Hourly forecast for {location}: {len(hourly)} entries",
                receipt_data={"location": location, "hourly": hourly},
            )
        except Exception as exc:
            return self._make_response("error", f"Fetch failed: {exc}")

    async def _alerts(self, params: dict) -> dict:
        location = params.get("location", "Lagos")
        # Note: OpenWeatherMap alerts API requires a separate subscription
        return self._make_response(
            "not_implemented",
            f"Severe weather alerts for {location} require One Call API 3.0 subscription",
        )
