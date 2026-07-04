"""Notifications integration — Telegram + desktop push notifications.

Sends real notifications via Telegram Bot API and cross-platform desktop
notifications (plyer). Falls back to console output if neither is available.
"""
import asyncio
import logging
import os
from typing import Optional

import httpx

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class TelegramNotifier(BaseIntegration):
    """Real Telegram Bot API integration for push notifications."""

    @property
    def name(self) -> str:
        return "Telegram"

    def __init__(self):
        self._bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    def available(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    def list_actions(self):
        return ["send"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}
        if not self.available():
            return self._make_response(
                "not_implemented",
                "Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.",
            )
        if action == "send":
            return await self._send(params)
        return self._make_response("not_implemented", f"Unknown action: {action}")

    async def _send(self, params: dict) -> dict:
        message = params.get("message", "")
        chat_id = params.get("chat_id", self._chat_id)
        if not message:
            return self._make_response("error", "Missing 'message' parameter")

        try:
            def _call():
                resp = httpx.post(
                    f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                    },
                    timeout=10,
                )
                return resp

            resp = await asyncio.to_thread(_call)
            if resp.status_code == 200:
                data = resp.json()
                return self._make_response(
                    "success",
                    f"Telegram message sent (message_id: {data.get('result', {}).get('message_id')})",
                    receipt_data={
                        "message_id": data.get("result", {}).get("message_id"),
                        "chat_id": chat_id,
                    },
                )
            return self._make_response("error", f"Telegram API error: {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            return self._make_response("error", f"Telegram send failed: {exc}")


class DesktopNotifier:
    """Cross-platform desktop notifications using plyer."""

    def __init__(self):
        try:
            from plyer import notification as _n  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
            logger.info("plyer not installed — desktop notifications unavailable")

    def available(self) -> bool:
        return self._available

    def notify(self, title: str, message: str, timeout: int = 10) -> dict:
        if not self._available:
            print(f"[Desktop Notification] {title}: {message}")
            return {"status": "not_implemented", "message": "plyer not installed"}
        try:
            from plyer import notification
            notification.notify(
                title=title,
                message=message,
                app_name="Friday",
                timeout=timeout,
            )
            return {"status": "success", "message": f"Desktop notification sent: {title}"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
