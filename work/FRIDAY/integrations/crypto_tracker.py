import logging
import datetime
from typing import Optional

import httpx

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class CryptoTrackerIntegration(BaseIntegration):
    """Fetch live cryptocurrency prices from CoinGecko (no API key required)."""

    COINGECKO_BASE = "https://api.coingecko.com/api/v3"

    @property
    def name(self) -> str:
        return "CryptoTracker"

    def available(self) -> bool:
        # CoinGecko public API needs no key — always available unless
        # we detect we're in a fully-offline environment (handled by
        # execute's try/except).
        return True

    def list_actions(self):
        return ["get_price", "get_market_data"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}

        try:
            if action == "get_price":
                return await self._get_price(params)
            elif action == "get_market_data":
                return await self._get_market_data(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by CryptoTracker.",
                )
        except Exception as exc:
            logger.error("CryptoTracker execute failed: %s", exc)
            return self._make_response("error", str(exc))

    # ---- action implementations -------------------------------------

    async def _get_price(self, params: dict) -> dict:
        coin = params.get("coin", "bitcoin")
        vs_currencies = params.get("vs_currencies", "usd")
        url = f"{self.COINGECKO_BASE}/simple/price"
        query_params = {"ids": coin, "vs_currencies": vs_currencies}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=query_params)
            resp.raise_for_status()
            data = resp.json()

        price = data.get(coin, {}).get(vs_currencies)
        if price is not None:
            return self._make_response(
                "success",
                f"The current price of {coin} is ${price} {vs_currencies.upper()}.",
                receipt_data=data,
            )
        return self._make_response(
            "error",
            f"Could not fetch price for '{coin}'. Check the coin identifier.",
        )

    async def _get_market_data(self, params: dict) -> dict:
        coin = params.get("coin", "bitcoin")
        url = f"{self.COINGECKO_BASE}/coins/{coin}"
        query_params = {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=query_params)
            resp.raise_for_status()
            data = resp.json()

        md = data.get("market_data", {})
        current_price = md.get("current_price", {}).get("usd")
        change_24h = md.get("price_change_percentage_24h")
        market_cap = md.get("market_cap", {}).get("usd")

        return self._make_response(
            "success",
            (
                f"{coin.capitalize()}: ${current_price:,.2f} USD | "
                f"24h change: {change_24h:+.2f}% | "
                f"Market cap: ${market_cap:,.0f} USD"
                if current_price is not None
                else f"Market data unavailable for {coin}."
            ),
            receipt_data={
                "current_price_usd": current_price,
                "change_24h_pct": change_24h,
                "market_cap_usd": market_cap,
            },
        )
