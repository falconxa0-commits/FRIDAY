"""Notifications API route — send push notifications via all configured channels.

Endpoints:
    POST /api/notify  — send a notification via Telegram + desktop
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("friday.api.notify")

router = APIRouter()


class NotifyRequest(BaseModel):
    message: str
    title: str = "Friday"
    channels: list = ["telegram", "desktop"]


@router.post("")
async def notify(req: NotifyRequest):
    """Send a notification via all configured channels."""
    results = []

    if "telegram" in req.channels:
        try:
            from integrations.notifications import TelegramNotifier
            notifier = TelegramNotifier()
            if notifier.available():
                result = await notifier.execute("send", {"message": req.message})
                results.append({"channel": "telegram", **result})
            else:
                results.append({"channel": "telegram", "status": "not_implemented",
                                "message": "Telegram not configured"})
        except Exception as exc:
            results.append({"channel": "telegram", "status": "error", "message": str(exc)})

    if "desktop" in req.channels:
        try:
            from integrations.notifications import DesktopNotifier
            dn = DesktopNotifier()
            result = dn.notify(req.title, req.message)
            results.append({"channel": "desktop", **result})
        except Exception as exc:
            results.append({"channel": "desktop", "status": "error", "message": str(exc)})

    return {"results": results, "total_channels": len(results)}
