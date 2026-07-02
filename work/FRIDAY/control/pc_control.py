import logging
import os
import asyncio
import datetime
from typing import Optional

from config.settings import WORKSPACE_ROOT
from core.ledger import get_ledger

logger = logging.getLogger(__name__)


def _is_headless() -> bool:
    """Detect whether we're running in a headless (no-display) environment."""
    return (
        os.getenv("DISPLAY") is None
        and os.getenv("WAYLAND_DISPLAY") is None
        and os.getenv("FRIDAY_FORCE_HEADLESS") != "0"
    )


class PCControl:
    """Desktop automation via pyautogui with ledger gating.

    All GUI operations are guarded:
    - Headless environments: actions return an informative error instead of
      crashing.
    - Ledger: high-risk actions require explicit approval.
    """

    def __init__(self):
        self.headless = _is_headless()
        self.audit_log = "pc_control_audit.log"
        self.ledger = get_ledger()

        if not self.headless:
            try:
                import pyautogui
                pyautogui.FAILSAFE = True
                self._pyautogui = pyautogui
            except Exception:
                logger.warning("pyautogui not available — switching to headless mode")
                self.headless = True
                self._pyautogui = None
        else:
            self._pyautogui = None
            logger.info("PCControl running in headless mode — GUI actions disabled")

        # Optional vision imports — don't crash if missing
        self.reader = None
        self.analyzer = None
        try:
            from vision.screen_reader import ScreenReader
            from vision.screen_analyzer import ScreenAnalyzer
            self.reader = ScreenReader()
            self.analyzer = ScreenAnalyzer()
        except Exception:
            logger.debug("Vision modules not available for PCControl")

    def _log_action(self, action, params):
        try:
            with open(self.audit_log, "a") as f:
                f.write(
                    f"{datetime.datetime.now().isoformat()} | Action: {action} | Params: {params}\n"
                )
        except Exception:
            logger.exception("Failed to write PC audit log")

    async def _get_screenshot_receipt(self) -> dict:
        """Capture a screenshot receipt (async-safe).

        Returns a placeholder in headless mode.
        """
        timestamp = datetime.datetime.now().isoformat()
        if self.headless or self._pyautogui is None:
            return {
                "type": "screenshot_unavailable",
                "data": "headless_mode",
                "timestamp": timestamp,
            }

        try:
            shot_path = os.path.join(
                WORKSPACE_ROOT, f"receipt_{int(datetime.datetime.now().timestamp())}.png"
            )
            # Run blocking pyautogui call in a thread to keep event loop free
            await asyncio.to_thread(self._pyautogui.screenshot, shot_path)
            return {
                "type": "screenshot",
                "data": shot_path,
                "timestamp": timestamp,
            }
        except Exception:
            logger.exception("Screenshot capture failed")
            return {
                "type": "screenshot_error",
                "data": "capture_failed",
                "timestamp": timestamp,
            }

    async def _gate(self, action, params, risk_level="high"):
        action_id = self.ledger.queue_action("PCControl", action, params, risk_level=risk_level)
        if await self.ledger.wait_for_approval(action_id):
            return True
        return False

    def _headless_error(self, action: str) -> dict:
        return {
            "status": "error",
            "message": (
                f"Cannot execute '{action}' — running in headless environment "
                f"(no DISPLAY or WAYLAND_DISPLAY set)."
            ),
        }

    async def move_and_click(self, x, y):
        if self.headless:
            return self._headless_error("move_and_click")
        if not await self._gate("move_and_click", {"x": x, "y": y}):
            return {"status": "error", "message": "Action rejected by user."}
        self._log_action("move_and_click", {"x": x, "y": y})
        await asyncio.to_thread(self._pyautogui.moveTo, x, y, duration=0.5)
        await asyncio.to_thread(self._pyautogui.click)
        return {
            "status": "success",
            "message": f"Clicked at ({x}, {y})",
            "receipt": await self._get_screenshot_receipt(),
        }

    async def click_described(self, description):
        """Vision-grounded click: locate element by description and click it."""
        if self.headless:
            return self._headless_error("click_described")
        if self.reader is None or self.analyzer is None:
            return {
                "status": "error",
                "message": "Vision modules not available for click_described.",
            }

        logger.info("Friday: Locating '%s' on screen...", description)
        try:
            shot_path = self.reader.capture_screen()
            coords = self.analyzer.find_element(description, shot_path)
        except Exception:
            logger.exception("Vision-grounded click failed")
            return {"status": "error", "message": f"Vision system error while locating '{description}'."}

        if not coords:
            return {"status": "error", "message": f"Could not find element: '{description}'"}

        return await self.move_and_click(coords["x"], coords["y"])

    async def type_text(self, text):
        if self.headless:
            return self._headless_error("type_text")
        if not await self._gate("type_text", {"text": text}):
            return {"status": "error", "message": "Action rejected by user."}
        self._log_action("type_text", {"text": text})
        await asyncio.to_thread(self._pyautogui.write, text, interval=0.1)
        return {
            "status": "success",
            "message": f"Typed text: {text}",
            "receipt": await self._get_screenshot_receipt(),
        }

    async def press_shortcut(self, *keys):
        if self.headless:
            return self._headless_error("press_shortcut")
        if not await self._gate("press_shortcut", {"keys": keys}):
            return {"status": "error", "message": "Action rejected by user."}
        self._log_action("press_shortcut", {"keys": keys})
        await asyncio.to_thread(self._pyautogui.hotkey, *keys)
        return {
            "status": "success",
            "message": f"Pressed keys: {keys}",
            "receipt": await self._get_screenshot_receipt(),
        }
