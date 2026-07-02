import asyncio
import logging
import os
import time
import datetime
from typing import Optional

from playwright.async_api import async_playwright

from config.settings import WORKSPACE_ROOT
from core.ledger import get_ledger

logger = logging.getLogger(__name__)


class BrowserControl:
    """Headless browser automation via Playwright with ledger gating.

    All mutating actions pass through ``_gate()`` with an appropriate
    ``risk_level`` so the autonomy profile is respected.
    """

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.audit_log = "browser_control_audit.log"
        self.ledger = get_ledger()

    def _log_action(self, action, params):
        try:
            with open(self.audit_log, "a") as f:
                f.write(
                    f"{datetime.datetime.now().isoformat()} | Action: {action} | Params: {params}\n"
                )
        except Exception:
            logger.exception("Failed to write browser audit log")

    async def _get_screenshot_receipt(self):
        if self.page is None:
            return {
                "type": "screenshot_unavailable",
                "data": "no_page",
                "timestamp": datetime.datetime.now().isoformat(),
            }
        shot_path = os.path.join(
            WORKSPACE_ROOT,
            f"browser_receipt_{int(time.time())}.png",
        )
        try:
            await self.page.screenshot(path=shot_path)
        except Exception:
            logger.exception("Browser screenshot failed")
            return {
                "type": "screenshot_error",
                "data": "capture_failed",
                "timestamp": datetime.datetime.now().isoformat(),
            }
        return {
            "type": "screenshot",
            "data": shot_path,
            "timestamp": datetime.datetime.now().isoformat(),
        }

    async def _gate(self, action, params, risk_level="medium"):
        """Request approval from the action ledger.

        All mutating browser actions **must** call this and pass an
        appropriate risk level.
        """
        action_id = self.ledger.queue_action(
            "BrowserControl", action, params, risk_level=risk_level
        )
        if await self.ledger.wait_for_approval(action_id):
            return True
        return False

    async def start(self, headless=False):
        self._log_action("start_browser", {"headless": headless})
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=headless)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()

    async def navigate(self, url):
        if not self.page:
            await self.start()
        if not await self._gate("navigate", {"url": url}, risk_level="low"):
            return {"status": "error", "message": "Action rejected by user."}
        self._log_action("navigate", {"url": url})
        await self.page.goto(url)
        return {
            "status": "success",
            "message": f"Navigated to {url}",
            "receipt": await self._get_screenshot_receipt(),
        }

    async def click_element(self, selector):
        if not await self._gate(
            "click_element", {"selector": selector}, risk_level="medium"
        ):
            return {"status": "error", "message": "Action rejected by user."}
        self._log_action("click_element", {"selector": selector})
        await self.page.click(selector)
        return {
            "status": "success",
            "message": f"Clicked {selector}",
            "receipt": await self._get_screenshot_receipt(),
        }

    async def type_text(self, selector, text):
        if not await self._gate(
            "type_text", {"selector": selector, "text": text}, risk_level="medium"
        ):
            return {"status": "error", "message": "Action rejected by user."}
        self._log_action("type_text", {"selector": selector, "text": text})
        await self.page.fill(selector, text)
        return {
            "status": "success",
            "message": f"Typed into {selector}",
            "receipt": await self._get_screenshot_receipt(),
        }

    async def execute_script(self, script: str):
        """Execute arbitrary JavaScript in the browser context.

        This is inherently **high** risk so it always requires explicit
        approval regardless of autonomy profile.
        """
        if not await self._gate(
            "execute_script", {"script": script}, risk_level="critical"
        ):
            return {"status": "error", "message": "Action rejected by user."}
        self._log_action("execute_script", {"script": script[:200]})
        result = await self.page.evaluate(script)
        return {
            "status": "success",
            "message": "Script executed.",
            "receipt": await self._get_screenshot_receipt(),
            "result": result,
        }

    async def close(self):
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            logger.exception("Error closing browser")
        finally:
            self.browser = None
            self.playwright = None
            self.page = None
            self.context = None
