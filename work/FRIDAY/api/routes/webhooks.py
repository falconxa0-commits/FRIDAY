"""Webhook receiver API — external triggers via signed webhooks.

Endpoints:
    POST /api/webhooks/{source}  — receive a webhook from an external service

Supported sources: github, stripe, custom.

Security model (WAVE1-SEC Task 3):
  - GitHub: HMAC-SHA256 verification of ``X-Hub-Signature-256`` using
    ``GITHUB_WEBHOOK_SECRET``. **Fails closed** — if the secret is not
    configured, the webhook is rejected with 503 (rather than silently
    accepted, which would let an attacker impersonate GitHub).
  - Stripe: signature verification via ``stripe.Webhook.construct_event``
    using ``STRIPE_WEBHOOK_SECRET``. **Fails closed** — if the secret
    is not set or the stripe library is not installed, the webhook is
    rejected with 503.
  - Custom: no signature verification (use only for trusted internal
    callers).
"""
import hashlib
import hmac
import json
import logging
import os
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


# ---------------------------------------------------------------------------
# GitHub — HMAC-SHA256 verification, fail-closed if secret unset
# ---------------------------------------------------------------------------

async def _handle_github(body: bytes, signature: str, event: str) -> dict:
    """Handle GitHub webhook — verifies HMAC-SHA256 signature.

    Security model:
      - If ``GITHUB_WEBHOOK_SECRET`` is not set → REJECT with 503 (fail
        closed). Processing an unverified GitHub webhook would let any
        attacker who can reach the endpoint impersonate GitHub and
        trigger PR/push handlers.
      - If ``X-Hub-Signature-256`` header is missing → REJECT with 401.
      - If signature does not match the recomputed HMAC-SHA256 → REJECT
        with 401.
      - Otherwise: parse the payload and route by event type.
    """
    webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")

    # Fail closed: if no secret is configured, refuse to process.
    if not webhook_secret:
        logger.error(
            "GitHub webhook REJECTED (fail-closed): GITHUB_WEBHOOK_SECRET "
            "is not set. Configure it to enable webhook processing."
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "GitHub webhook processing disabled: GITHUB_WEBHOOK_SECRET "
                "not configured. Set this env var to the secret configured "
                "in your GitHub webhook settings to enable processing."
            ),
        )

    # Require the signature header.
    if not signature:
        logger.warning("GitHub webhook REJECTED: missing X-Hub-Signature-256 header.")
        raise HTTPException(
            status_code=401,
            detail="Missing X-Hub-Signature-256 header. GitHub webhooks must be signed.",
        )

    # Verify the HMAC-SHA256 signature.
    # GitHub signs the raw request body with the webhook secret and sends
    # the result as "sha256=<hex_digest>" in the X-Hub-Signature-256 header.
    expected = "sha256=" + hmac.new(
        webhook_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    # Timing-safe comparison to prevent signature oracle attacks.
    if not hmac.compare_digest(signature, expected):
        logger.warning(
            "GitHub webhook REJECTED: invalid X-Hub-Signature-256 signature."
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid GitHub webhook signature.",
        )

    # Signature verified — safe to parse the payload.
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400, detail="Invalid JSON in GitHub webhook payload."
        )

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


# ---------------------------------------------------------------------------
# Stripe — stripe.Webhook.construct_event verification, fail-closed
# ---------------------------------------------------------------------------

async def _handle_stripe(body: bytes, signature: str) -> dict:
    """Handle Stripe webhook — verifies Stripe-Signature header.

    Security model:
      - If ``STRIPE_WEBHOOK_SECRET`` is not set → REJECT with 503 (fail
        closed). Same rationale as GitHub — an unverified Stripe webhook
        could let an attacker forge payment events.
      - If ``stripe`` library is not installed → REJECT with 503.
      - If ``Stripe-Signature`` header is missing → REJECT with 400.
      - If ``stripe.Webhook.construct_event`` raises (invalid signature,
        malformed payload, replay) → REJECT with 400.
      - Otherwise: extract the event type and return.
    """
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    # Fail closed: if no secret is configured, refuse to process.
    if not webhook_secret:
        logger.error(
            "Stripe webhook REJECTED (fail-closed): STRIPE_WEBHOOK_SECRET "
            "is not set. Configure it to enable webhook processing."
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Stripe webhook processing disabled: STRIPE_WEBHOOK_SECRET "
                "not configured. Set this env var to the signing secret "
                "from your Stripe dashboard to enable processing."
            ),
        )

    # Lazy-import stripe (it's an optional dependency).
    try:
        import stripe
    except ImportError:
        logger.error(
            "Stripe webhook REJECTED: stripe library not installed on the server."
        )
        raise HTTPException(
            status_code=503,
            detail="stripe library not installed on the server. Run `pip install stripe`.",
        )

    if not signature:
        logger.warning("Stripe webhook REJECTED: missing Stripe-Signature header.")
        raise HTTPException(
            status_code=400,
            detail="Missing Stripe-Signature header. Stripe webhooks must be signed.",
        )

    # Verify the signature and construct the event in one step.
    # stripe.Webhook.construct_event raises:
    #   - ValueError for malformed payloads
    #   - stripe.error.SignatureVerificationError for invalid signatures
    # Both are caught and converted to 400.
    try:
        event = stripe.Webhook.construct_event(
            body, signature, webhook_secret,
        )
    except ValueError as exc:
        logger.warning(f"Stripe webhook REJECTED: invalid payload ({exc}).")
        raise HTTPException(
            status_code=400,
            detail=f"Stripe webhook payload invalid: {exc}",
        )
    except stripe.error.SignatureVerificationError as exc:
        logger.warning(f"Stripe webhook REJECTED: signature verification failed ({exc}).")
        raise HTTPException(
            status_code=400,
            detail=f"Stripe signature verification failed: {exc}",
        )
    except Exception as exc:
        # Catch-all for any other stripe-side error (e.g. malformed header).
        logger.warning(f"Stripe webhook REJECTED: construction error ({exc}).")
        raise HTTPException(
            status_code=400,
            detail=f"Stripe event construction failed: {exc}",
        )

    # Extract event type — event is a stripe.Event (dict-like).
    if isinstance(event, dict):
        event_type = event.get("type", "unknown")
        event_id = event.get("id", "")
    else:
        event_type = getattr(event, "type", "unknown")
        event_id = getattr(event, "id", "")

    return {
        "status": "received",
        "source": "stripe",
        "event_type": event_type,
        "event_id": event_id,
        "message": f"Stripe event: {event_type}",
    }


# ---------------------------------------------------------------------------
# Custom — no signature verification (internal callers only)
# ---------------------------------------------------------------------------

async def _handle_custom(body: bytes, source: str) -> dict:
    """Handle generic/custom webhook.

    No signature verification is performed — only use this for trusted
    internal callers. External services should use the github/stripe
    endpoints (which verify signatures) or be fronted by an API gateway
    that performs its own HMAC verification.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {"raw": body.decode("utf-8", errors="replace")[:500]}

    return {
        "status": "received",
        "source": source,
        "payload_keys": list(payload.keys()) if isinstance(payload, dict) else "non-dict",
    }
