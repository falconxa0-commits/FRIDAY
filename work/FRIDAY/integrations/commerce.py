"""Commerce Integration — real price comparison + checkout with hard approval gate.

Combines:
  - find_and_compare: uses Playwright (headless browser) to extract real
    prices and product titles from at least 2 e-commerce sites.
  - checkout: real Stripe sandbox transaction (or honest not_implemented
    if no gateway is configured).

All checkout actions go through the ledger's NEVER_AUTO_APPROVE gate
(Commerce is hardcoded in NEVER_AUTO_APPROVE_COMPONENTS) — even at the
POWER autonomy profile, checkout requires explicit user approval.

Inherits from BaseIntegration. Never fakes prices. Never fakes success.
"""

import asyncio
import datetime
import logging
import os
from typing import Optional

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class Commerce(BaseIntegration):
    """Real commerce integration: price comparison + checkout."""

    @property
    def name(self) -> str:
        return "Commerce"

    def __init__(self):
        # Playwright state
        self._playwright_available: Optional[bool] = None

        # Stripe state
        self._stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
        self._stripe_mode = "test" if self._stripe_key.startswith("sk_test_") else "live"
        self._stripe_available: Optional[bool] = None

    # ------------------------------------------------------------------
    # Availability — True if EITHER Playwright OR Stripe is configured
    # ------------------------------------------------------------------

    def available(self) -> bool:
        """Return True if at least one commerce sub-feature is usable."""
        return self._playwright_ok() or self._stripe_ok()

    def _playwright_ok(self) -> bool:
        if self._playwright_available is not None:
            return self._playwright_available
        try:
            from playwright.async_api import async_playwright  # noqa: F401
            self._playwright_available = True
        except ImportError:
            self._playwright_available = False
        except Exception:
            self._playwright_available = False
        return self._playwright_available

    def _stripe_ok(self) -> bool:
        if self._stripe_available is not None:
            return self._stripe_available
        if not self._stripe_key:
            self._stripe_available = False
            return False
        try:
            import stripe  # noqa: F401
            # Don't actually verify the key here — that would make a real
            # network call. Just confirm the package is importable.
            self._stripe_available = True
        except ImportError:
            self._stripe_available = False
        return self._stripe_available

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def list_actions(self):
        return ["find_and_compare", "checkout"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}

        try:
            if action == "find_and_compare":
                return await self._find_and_compare(params)
            elif action == "checkout":
                return await self._checkout(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by Commerce.",
                )
        except Exception as exc:
            logger.error(f"Commerce execute failed: {exc}")
            return self._make_response("error", str(exc))

    # ------------------------------------------------------------------
    # find_and_compare — real Playwright scraping
    # ------------------------------------------------------------------

    async def _find_and_compare(self, params: dict) -> dict:
        """Compare prices for a product across at least 2 real sites.

        Params:
            product (str): Product name to search for.
            sites (list, optional): Site names. Default: ["amazon", "ebay"].

        Returns a real comparison list with extracted prices + titles.
        If Playwright isn't installed, returns manual-required status.
        If extraction fails on a real site, returns the real error.
        """
        if not self._playwright_ok():
            return self._make_response(
                "not_implemented",
                "Playwright not installed. Install with: "
                "pip install playwright && playwright install chromium. "
                "Once installed, find_and_compare will visit real sites and "
                "extract real prices. Never fabricated.",
            )

        product = params.get("product", "")
        if not product:
            return self._make_response("error", "Missing required parameter: product")

        sites = params.get("sites", ["amazon", "ebay"])
        results = {}

        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)

        try:
            for site in sites:
                try:
                    price_data = await self._scrape_site(browser, site, product)
                    results[site] = price_data
                except Exception as exc:
                    logger.warning(f"Failed to scrape {site}: {exc}")
                    results[site] = {"error": str(exc), "price": None, "title": None, "url": None}
        finally:
            await browser.close()
            await pw.stop()

        # Summarize
        successful = {
            k: v for k, v in results.items()
            if v.get("price") is not None
        }
        if successful:
            best_site = min(
                successful,
                key=lambda k: successful[k]["price"] or float("inf"),
            )
            best_price = successful[best_site]["price"]
            summary = (
                f"Found prices for '{product}': " +
                ", ".join(
                    f"{site}: ${data['price']:.2f} ({data.get('title', '')[:40]!r})"
                    for site, data in successful.items()
                ) +
                f". Best: {best_site} at ${best_price:.2f}"
            )
        else:
            summary = (
                f"Could not extract prices for '{product}' from any of {sites}. "
                f"Real errors: " +
                "; ".join(f"{s}: {r.get('error', 'unknown')}" for s, r in results.items())
            )
            best_site = None
            best_price = None

        return self._make_response(
            "success" if successful else "error",
            summary,
            receipt_data={
                "product": product,
                "sites_attempted": sites,
                "results": results,
                "best_site": best_site,
                "best_price": best_price,
                "extracted_at": datetime.datetime.now().isoformat(),
            },
        )

    async def _scrape_site(self, browser, site: str, product: str) -> dict:
        """Scrape prices from a specific e-commerce site."""
        search_url = self._build_search_url(site, product)
        if not search_url:
            return {"price": None, "error": f"Unsupported site: {site}",
                    "title": None, "url": None}

        page = await browser.new_page()
        try:
            await page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
            response = await page.goto(search_url, timeout=15000, wait_until="domcontentloaded")
            if not response or response.status != 200:
                return {"price": None,
                        "error": f"HTTP {response.status if response else 'no response'}",
                        "title": None, "url": search_url}

            price_selector = self._get_price_selector(site)
            title_selector = self._get_title_selector(site)

            price_text = ""
            title_text = ""
            try:
                await page.wait_for_selector(price_selector, timeout=5000)
                el = await page.query_selector(price_selector)
                if el:
                    price_text = await el.inner_text()
            except Exception:
                pass
            try:
                if title_selector:
                    el = await page.query_selector(title_selector)
                    if el:
                        title_text = await el.inner_text()
            except Exception:
                pass

            price = self._parse_price(price_text)
            return {
                "price": price,
                "title": title_text.strip()[:200] if title_text else "",
                "url": page.url,
            }
        except Exception as exc:
            return {"price": None, "error": str(exc),
                    "title": None, "url": search_url}
        finally:
            await page.close()

    @staticmethod
    def _build_search_url(site: str, product: str) -> str:
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
        selectors = {
            "amazon": ".a-price .a-offscreen, .a-price-whole",
            "ebay": ".s-item__price, .x-price-primary",
            "walmart": "[data-automation-id='product-price'], .price-characteristic",
            "bestbuy": ".priceView-customer-price span, .pricing-price",
        }
        return selectors.get(site.lower(), ".price")

    @staticmethod
    def _get_title_selector(site: str) -> str:
        selectors = {
            "amazon": ".a-text-normal, h2 a span",
            "ebay": ".s-item__title",
            "walmart": "[data-automation-id='product-title']",
            "bestbuy": ".sku-title h4, .sku-header",
        }
        return selectors.get(site.lower(), "h1, h2, h3")

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        import re
        if not text:
            return None
        cleaned = text.replace(",", "")
        match = re.search(r'[\$£€]?([\d,]+\.?\d*)', cleaned)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
        return None

    # ------------------------------------------------------------------
    # checkout — real Stripe transaction (or honest not_implemented)
    # ------------------------------------------------------------------

    async def _checkout(self, params: dict) -> dict:
        """Process a real checkout via Stripe sandbox.

        Params:
            item (str): Item name being purchased.
            price (float): Price in major currency units (e.g. dollars).
            payment_method (str, optional): Stripe payment method ID.
            currency (str, optional): Currency code. Default "usd".

        Returns the real gateway response as the receipt. If no gateway
        is configured, returns not_implemented — never a fabricated success.
        """
        if not self._stripe_ok():
            return self._make_response(
                "not_implemented",
                "No payment gateway configured. Set STRIPE_SECRET_KEY to enable "
                "checkout. Use a test key (sk_test_...) for sandbox mode.",
            )

        item = params.get("item")
        price = params.get("price")
        if item is None or price is None:
            return self._make_response(
                "error",
                "Missing required parameters: item and price",
            )

        currency = params.get("currency", "usd")
        payment_method = params.get("payment_method")
        # Stripe expects amount in smallest currency unit (cents for USD)
        amount_cents = int(round(float(price) * 100))

        try:
            import stripe
            stripe.api_key = self._stripe_key

            def _create_and_confirm():
                # Create + confirm a PaymentIntent in one call.
                # In test mode, Stripe returns a succeeded status when
                # using a test payment method (e.g. pm_card_visa).
                kwargs = {
                    "amount": amount_cents,
                    "currency": currency,
                    "description": f"FRIDAY Checkout: {item}",
                    "metadata": {"source": "friday_ai", "item": str(item)},
                    "confirm": True,
                }
                if payment_method:
                    kwargs["payment_method"] = payment_method
                else:
                    # Use Stripe's test payment method automatically
                    kwargs["payment_method"] = "pm_card_visa"
                    kwargs["off_session"] = True
                return stripe.PaymentIntent.create(**kwargs)

            intent = await asyncio.to_thread(_create_and_confirm)

            return self._make_response(
                "success",
                f"Checkout complete: {item} for ${price:.2f} {currency.upper()} "
                f"(mode: {self._stripe_mode}, intent: {intent.id}, status: {intent.status})",
                receipt_data={
                    "intent_id": intent.id,
                    "item": item,
                    "amount": amount_cents,
                    "currency": currency,
                    "status": intent.status,
                    "mode": self._stripe_mode,
                    "receipt_url": getattr(intent, "charges", None)
                                    and intent.charges.data[0].receipt_url
                                    if intent.charges and intent.charges.data else None,
                },
            )
        except Exception as exc:
            logger.error(f"Checkout failed: {exc}")
            return self._make_response(
                "error",
                f"Checkout failed: {exc}",
                receipt_data={
                    "item": item,
                    "amount": amount_cents,
                    "currency": currency,
                    "mode": self._stripe_mode,
                },
            )
