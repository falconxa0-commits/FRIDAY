"""Price Comparison Integration — real price extraction from web sources.

Uses Playwright (headless browser) to extract real prices from at least
2 e-commerce sites.  Returns real prices or honest errors.

Inherits from BaseIntegration.  Never fakes prices.
"""

import asyncio
import datetime
import logging
import os
from typing import Optional

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class PriceComparison(BaseIntegration):
    """Compare prices across e-commerce sites using Playwright."""

    @property
    def name(self) -> str:
        return "PriceComparison"

    def __init__(self):
        self._playwright_available: Optional[bool] = None

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def available(self) -> bool:
        """Return True if Playwright is installed."""
        if self._playwright_available is not None:
            return self._playwright_available

        try:
            from playwright.async_api import async_playwright
            self._playwright_available = True
        except ImportError:
            logger.info("playwright not installed — PriceComparison unavailable")
            self._playwright_available = False
        except Exception as exc:
            logger.info(f"Playwright check failed: {exc}")
            self._playwright_available = False

        return self._playwright_available

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def list_actions(self):
        return ["compare_prices", "search_price"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}

        if not self.available():
            return self._make_response(
                "not_implemented",
                "Playwright not installed. Install with: pip install playwright && playwright install",
            )

        try:
            if action == "compare_prices":
                return await self._compare_prices(params)
            elif action == "search_price":
                return await self._search_price(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by PriceComparison.",
                )
        except Exception as exc:
            logger.error(f"PriceComparison execute failed: {exc}")
            return self._make_response("error", str(exc))

    # ------------------------------------------------------------------
    # Price comparison
    # ------------------------------------------------------------------

    async def _compare_prices(self, params: dict) -> dict:
        """Compare prices for a product across multiple sites.

        Params:
            product (str): Product name to search for.
            sites (list, optional): List of site names. Default: ["amazon", "ebay"].
        """
        product = params.get("product", "")
        if not product:
            return self._make_response("error", "Missing required parameter: product")

        sites = params.get("sites", ["amazon", "ebay"])
        results = {}

        async with await self._get_browser() as browser:
            for site in sites:
                try:
                    price = await self._scrape_site(browser, site, product)
                    results[site] = price
                except Exception as exc:
                    logger.warning(f"Failed to scrape {site}: {exc}")
                    results[site] = {"error": str(exc), "price": None}

        # Summarize
        successful = {k: v for k, v in results.items() if v.get("price") is not None}
        if successful:
            best_site = min(successful, key=lambda k: successful[k]["price"] or float("inf"))
            best_price = successful[best_site]["price"]
            summary = (
                f"Found prices for '{product}': " +
                ", ".join(f"{site}: ${data['price']:.2f}" for site, data in successful.items()) +
                f". Best: {best_site} at ${best_price:.2f}"
            )
        else:
            summary = f"Could not find prices for '{product}' on any site."
            best_site = None
            best_price = None

        return self._make_response(
            "success" if successful else "error",
            summary,
            receipt_data={
                "product": product,
                "results": results,
                "best_site": best_site,
                "best_price": best_price,
            },
        )

    async def _search_price(self, params: dict) -> dict:
        """Search for a product price on a single site.

        Params:
            product (str): Product name to search for.
            site (str): Site to search. Default: "amazon".
        """
        product = params.get("product", "")
        site = params.get("site", "amazon")

        if not product:
            return self._make_response("error", "Missing required parameter: product")

        async with await self._get_browser() as browser:
            try:
                price_data = await self._scrape_site(browser, site, product)
            except Exception as exc:
                return self._make_response(
                    "error",
                    f"Failed to scrape {site}: {exc}",
                )

        if price_data.get("price") is not None:
            return self._make_response(
                "success",
                f"Price for '{product}' on {site}: ${price_data['price']:.2f}",
                receipt_data={
                    "product": product,
                    "site": site,
                    "price": price_data["price"],
                    "title": price_data.get("title", ""),
                    "url": price_data.get("url", ""),
                },
            )
        else:
            return self._make_response(
                "error",
                f"Could not find price for '{product}' on {site}",
                receipt_data={"product": product, "site": site, "price": None},
            )

    # ------------------------------------------------------------------
    # Browser helpers
    # ------------------------------------------------------------------

    async def _get_browser(self):
        """Launch a Playwright browser instance."""
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        return browser

    async def _scrape_site(self, browser, site: str, product: str) -> dict:
        """Scrape prices from a specific e-commerce site.

        Returns a dict with 'price', 'title', and 'url' keys.
        """
        search_url = self._build_search_url(site, product)
        if not search_url:
            return {"price": None, "error": f"Unsupported site: {site}"}

        page = await browser.new_page()
        try:
            # Set a reasonable timeout and user agent
            await page.set_extra_http_headers({
                "Accept-Language": "en-US,en;q=0.9",
            })

            response = await page.goto(search_url, timeout=15000, wait_until="domcontentloaded")

            if not response or response.status != 200:
                return {"price": None, "error": f"HTTP {response.status if response else 'no response'}"}

            # Wait for price elements to load
            price_selector = self._get_price_selector(site)
            title_selector = self._get_title_selector(site)

            price_text = ""
            title_text = ""

            try:
                await page.wait_for_selector(price_selector, timeout=5000)
                price_element = await page.query_selector(price_selector)
                if price_element:
                    price_text = await price_element.inner_text()
            except Exception:
                logger.debug(f"Price selector timeout for {site}")

            try:
                if title_selector:
                    title_element = await page.query_selector(title_selector)
                    if title_element:
                        title_text = await title_element.inner_text()
            except Exception:
                pass

            # Parse price from text
            price = self._parse_price(price_text)

            return {
                "price": price,
                "title": title_text.strip()[:200] if title_text else "",
                "url": page.url,
            }

        except Exception as exc:
            logger.warning(f"Scraping error for {site}: {exc}")
            return {"price": None, "error": str(exc)}
        finally:
            await page.close()

    # ------------------------------------------------------------------
    # Site-specific configuration
    # ------------------------------------------------------------------

    @staticmethod
    def _build_search_url(site: str, product: str) -> str:
        """Build a search URL for the given site and product."""
        encoded = product.replace(" ", "+")
        urls = {
            "amazon": f"https://www.amazon.com/s?k={encoded}",
            "ebay": f"https://www.ebay.com/sch/i.html?_nkw={encoded}",
            "walmart": f"https://www.walmart.com/search?q={encoded}",
            "bestbuy": f"https://www.bestbuy.com/site/searchpage.jsp?st={encoded}",
        }
        return urls.get(site.lower(), "")

    @staticmethod
    def _get_price_selector(site: str) -> str:
        """Return a CSS selector for price elements on the given site."""
        selectors = {
            "amazon": ".a-price .a-offscreen, .a-price-whole",
            "ebay": ".s-item__price, .x-price-primary",
            "walmart": "[data-automation-id='product-price'], .price-characteristic",
            "bestbuy": ".priceView-customer-price span, .pricing-price",
        }
        return selectors.get(site.lower(), ".price")

    @staticmethod
    def _get_title_selector(site: str) -> str:
        """Return a CSS selector for product title elements."""
        selectors = {
            "amazon": ".a-text-normal, h2 a span",
            "ebay": ".s-item__title",
            "walmart": "[data-automation-id='product-title']",
            "bestbuy": ".sku-title h4, .sku-header",
        }
        return selectors.get(site.lower(), "h1, h2, h3")

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        """Parse a price from text like '$29.99' or '£19.99'."""
        import re
        if not text:
            return None
        match = re.search(r'[\$£€]?([\d,]+\.?\d*)', text.replace(',', ''))
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
        return None
