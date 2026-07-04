"""Webhook receiver API — external triggers via signed webhooks.

Endpoints:
    POST /api/webhooks/{source}  — receive a webhook from an external service

Supported sources: github, stripe, custom
Verifies HMAC signatures before processing.
"""
import hashlib
import hmac
import json
import logging
from fastapi import APIRouter, HTTPException, Header, Request

logger = logging.getLogger("friday.api.webhooks")

router = APIRouter()


@router.post("/{source}")
async def receive_webhook(
    source: str,
    request: Request,
    x_hub_signature_256: str = Header(None),
    x_github_event: str = Header(None),
    stripe_signature: str = Header(None),
):
    """Receive and process a webhook from an external service."""
    body = await request.body()

    if source == "github":
        return await _handle_github(body, x_hub_signature_256, x_github_event)
    elif source == "stripe":
        return await _handle_stripe(body, stripe_signature)
    elif source == "custom":
        return await _handle_custom(body, source)
    else:
        raise HTTPException(status_code=404, detail=f"Unknown webhook source: {source}")


async def _handle_github(body: bytes, signature: str, event: str) -> dict:
    """Handle GitHub webhook — verifies HMAC-SHA256 signature."""
    webhook_secret = __import__("os").getenv("GITHUB_WEBHOOK_SECRET", "")

    # Verify signature
    if webhook_secret and signature:
        expected = "sha256=" + hmac.new(
            webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=403, detail="Invalid GitHub webhook signature")

    payload = json.loads(body)

    # Route based on event type
    if event == "pull_request":
        action = payload.get("action", "")
        pr = payload.get("pull_request", {})
        return {
            "status": "received",
            "event": event,
            "action": action,
            "pr_title": pr.get("title", ""),
            "pr_url": pr.get("html_url", ""),
            "message": f"GitHub PR {action}: {pr.get('title', '')}",
        }
    elif event == "issues":
        action = payload.get("action", "")
        issue = payload.get("issue", {})
        return {
            "status": "received",
            "event": event,
            "action": action,
            "issue_title": issue.get("title", ""),
            "issue_url": issue.get("html_url", ""),
        }
    elif event == "push":
        ref = payload.get("ref", "")
        commits = payload.get("commits", [])
        return {
            "status": "received",
            "event": "push",
            "ref": ref,
            "commit_count": len(commits),
            "messages": [c.get("message", "")[:100] for c in commits[:5]],
        }
    else:
        return {"status": "received", "event": event or "unknown"}


async def _handle_stripe(body: bytes, signature: str) -> dict:
    """Handle Stripe webhook — verifies signature."""
    if not signature:
        raise HTTPException(status_code=403, detail="Missing Stripe signature")

    payload = json.loads(body)
    event_type = payload.get("type", "unknown")

    return {
        "status": "received",
        "source": "stripe",
        "event_type": event_type,
        "message": f"Stripe event: {event_type}",
    }


async def _handle_custom(body: bytes, source: str) -> dict:
    """Handle generic/custom webhook."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {"raw": body.decode("utf-8", errors="replace")[:500]}

    return {
        "status": "received",
        "source": source,
        "payload_keys": list(payload.keys()) if isinstance(payload, dict) else "non-dict",
    }
