"""CryptoPrice integration — fetches real current crypto prices from CoinGecko.

Built strictly by following docs/PLUGINS.md — no other files modified.
Uses the free public CoinGecko API (no API key required).
"""

import asyncio
import datetime
import logging
from typing import Optional

import httpx

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class CryptoPrice(BaseIntegration):
    """Real-time crypto price lookup via CoinGecko's free public API."""

    BASE_URL = "https://api.coingecko.com/api/v3"

    @property
    def name(self) -> str:
        return "CryptoPrice"

    def available(self) -> bool:
        """Return True if CoinGecko's API is reachable."""
        try:
            resp = httpx.get(f"{self.BASE_URL}/ping", timeout=5)
            return resp.status_code == 200
        except Exception as exc:
            logger.debug(f"CryptoPrice available() check failed: {exc}")
            return False

    def list_actions(self):
        return ["get_price", "list_top_coins"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}

        if action == "get_price":
            return await self._get_price(params)
        elif action == "list_top_coins":
            return await self._list_top_coins(params)
        else:
            return self._make_response(
                "not_implemented",
                f"Action '{action}' is not supported by CryptoPrice.",
            )

    async def _get_price(self, params: dict) -> dict:
        """Get the current price of a coin in a given currency.

        Params:
            coin (str): CoinGecko coin id (e.g. 'bitcoin', 'ethereum')
            vs_currency (str, optional): Currency code (e.g. 'usd', 'eur'). Default 'usd'.
        """
        coin = params.get("coin")
        if not coin:
            return self._make_response("error", "Missing required parameter: coin")

        vs_currency = params.get("vs_currency", "usd")

        def _fetch():
            url = f"{self.BASE_URL}/simple/price"
            return httpx.get(
                url,
                params={
                    "ids": coin,
                    "vs_currencies": vs_currency,
                    "include_market_cap": "true",
                    "include_24hr_change": "true",
                },
                timeout=10,
            )

        try:
            resp = await asyncio.to_thread(_fetch)
            if resp.status_code != 200:
                return self._make_response(
                    "error",
                    f"CoinGecko API error: {resp.status_code} {resp.text[:200]}",
                )

            data = resp.json()
            if coin not in data:
                return self._make_response(
                    "error",
                    f"Coin '{coin}' not found. Use list_top_coins to see valid ids.",
                )

            coin_data = data[coin]
            return self._make_response(
                "success",
                f"{coin} = {coin_data.get(vs_currency, 'N/A')} {vs_currency.upper()}",
                receipt_data={
                    "coin": coin,
                    "currency": vs_currency,
                    "price": coin_data.get(vs_currency),
                    "market_cap": coin_data.get(f"{vs_currency}_market_cap"),
                    "24h_change_pct": coin_data.get(f"{vs_currency}_24h_change"),
                    "fetched_at": datetime.datetime.now().isoformat(),
                },
            )
        except Exception as exc:
            logger.error(f"CryptoPrice get_price failed: {exc}")
            return self._make_response("error", f"Fetch failed: {exc}")

    async def _list_top_coins(self, params: dict) -> dict:
        """List the top N coins by market cap.

        Params:
            limit (int, optional): Number of coins to return. Default 10.
            vs_currency (str, optional): Currency for prices. Default 'usd'.
        """
        limit = int(params.get("limit", 10))
        vs_currency = params.get("vs_currency", "usd")

        def _fetch():
            url = f"{self.BASE_URL}/coins/markets"
            return httpx.get(
                url,
                params={
                    "vs_currency": vs_currency,
                    "order": "market_cap_desc",
                    "per_page": limit,
                    "page": 1,
                },
                timeout=10,
            )

        try:
            resp = await asyncio.to_thread(_fetch)
            if resp.status_code != 200:
                return self._make_response(
                    "error",
                    f"CoinGecko API error: {resp.status_code}",
                )

            coins = resp.json()
            coin_list = [
                {
                    "id": c.get("id"),
                    "symbol": c.get("symbol"),
                    "name": c.get("name"),
                    "price": c.get("current_price"),
                    "market_cap": c.get("market_cap"),
                    "24h_change_pct": c.get("price_change_percentage_24h"),
                }
                for c in coins
            ]
            return self._make_response(
                "success",
                f"Top {len(coin_list)} coins by market cap",
                receipt_data={
                    "coins": coin_list,
                    "currency": vs_currency,
                    "fetched_at": datetime.datetime.now().isoformat(),
                },
            )
        except Exception as exc:
            logger.error(f"CryptoPrice list_top_coins failed: {exc}")
            return self._make_response("error", f"Fetch failed: {exc}")
