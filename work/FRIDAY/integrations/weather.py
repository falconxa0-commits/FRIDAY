import logging
import datetime
from typing import Optional

import httpx

from config.settings import OPENWEATHERMAP_API_KEY
from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class WeatherIntegration(BaseIntegration):
    """Fetch weather data from OpenWeatherMap (async via httpx)."""

    @property
    def name(self) -> str:
        return "Weather"

    def available(self) -> bool:
        return bool(OPENWEATHERMAP_API_KEY)

    def list_actions(self):
        return ["get_weather", "get_forecast"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        if not self.available():
            return self._make_response(
                "not_implemented",
                "OpenWeatherMap API key not configured.",
            )

        params = params or {}

        try:
            if action == "get_weather":
                return await self._get_weather(params)
            elif action == "get_forecast":
                return await self._get_forecast(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by Weather.",
                )
        except Exception as exc:
            logger.error("Weather execute failed: %s", exc)
            return self._make_response("error", str(exc))

    # ---- action implementations -------------------------------------

    async def _get_weather(self, params: dict) -> dict:
        city = params.get("location", "Lagos")
        url = "http://api.openweathermap.org/data/2.5/weather"
        query = {
            "q": city,
            "appid": OPENWEATHERMAP_API_KEY,
            "units": "metric",
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=query)
            data = resp.json()

        if resp.status_code == 200:
            temp = data["main"]["temp"]
            desc = data["weather"][0]["description"]
            humidity = data["main"]["humidity"]
            wind = data["wind"]["speed"]
            msg = (
                f"Current weather in {city}: {temp}°C, {desc}. "
                f"Humidity: {humidity}%, Wind: {wind} m/s."
            )
            return self._make_response("success", msg, receipt_data=data)
        return self._make_response(
            "error",
            data.get("message", f"API error ({resp.status_code})"),
        )

    async def _get_forecast(self, params: dict) -> dict:
        city = params.get("location", "Lagos")
        url = "http://api.openweathermap.org/data/2.5/forecast"
        query = {
            "q": city,
            "appid": OPENWEATHERMAP_API_KEY,
            "units": "metric",
            "cnt": params.get("cnt", 8),  # 8 × 3h = 24h by default
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=query)
            data = resp.json()

        if resp.status_code == 200:
            items = data.get("list", [])
            summaries = []
            for item in items:
                time_txt = item.get("dt_txt", "")
                temp = item["main"]["temp"]
                desc = item["weather"][0]["description"]
                summaries.append(f"{time_txt}: {temp}°C, {desc}")

            msg = f"Weather forecast for {city}:\n" + "\n".join(summaries)
            return self._make_response("success", msg, receipt_data=data)
        return self._make_response(
            "error",
            data.get("message", f"API error ({resp.status_code})"),
        )
