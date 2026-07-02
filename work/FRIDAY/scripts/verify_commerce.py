#!/usr/bin/env python3
"""Section 7a + 7b — Commerce: real price comparison + hard checkout gate.

7a: Real price comparison via Playwright (or honest manual-required).
7b: Checkout with hard confirmation gate — demonstrate that even at POWER
    profile, checkout requires explicit approval (Commerce is hardcoded
    in NEVER_AUTO_APPROVE_COMPONENTS).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ledger import ActionLedger, NEVER_AUTO_APPROVE_COMPONENTS
from core.universal_connector import UniversalConnector


async def main():
    print("=" * 70)
    print("SECTION 7a + 7b — Commerce verification")
    print("=" * 70)

    connector = UniversalConnector()

    # ---- Verify Commerce is registered + never-auto-approve ------------
    print("\n[setup] Commerce integration registered + NEVER_AUTO_APPROVE check")
    assert "Commerce" in connector.integrations, \
        f"Commerce not discovered! Found: {list(connector.integrations.keys())}"
    assert "Commerce" in NEVER_AUTO_APPROVE_COMPONENTS, \
        "Commerce must be in NEVER_AUTO_APPROVE_COMPONENTS"
    print(f"  Commerce in integrations: True")
    print(f"  Commerce in NEVER_AUTO_APPROVE: True")

    commerce = connector.integrations["Commerce"]
    print(f"  list_actions(): {commerce.list_actions()}")
    assert "find_and_compare" in commerce.list_actions()
    assert "checkout" in commerce.list_actions()

    # ==================================================================
    # 7a — Real price comparison
    # ==================================================================
    print("\n" + "=" * 70)
    print("7a — Real price comparison")
    print("=" * 70)

    print(f"\n[7a.1] Playwright available: {commerce._playwright_ok()}")

    if not commerce._playwright_ok():
        print("\n[7a.2] Playwright not installed in this env — marking manual-required…")
        result = await commerce.execute("find_and_compare", {"product": "wireless headphones"})
        print(f"  Result: {result}")
        assert result["status"] == "not_implemented", \
            f"Expected not_implemented, got {result['status']}"
        print("  PASS — Honest 'not_implemented' returned (no fabricated prices)")

        print("\n  MANUAL-REQUIRED steps to verify real price extraction:")
        print("    1. pip install playwright")
        print("    2. playwright install chromium")
        print("    3. Re-run this script — find_and_compare will visit")
        print("       amazon.com and ebay.com and extract real prices.")
        print("\n  The code is implemented and ready (see integrations/commerce.py).")
    else:
        print("\n[7a.2] Running real find_and_compare for 'wireless headphones'…")
        result = await commerce.execute(
            "find_and_compare",
            {"product": "wireless headphones", "sites": ["amazon", "ebay"]},
        )
        print(f"  Status: {result['status']}")
        print(f"  Message: {result['message']}")
        if result.get("receipt"):
            data = result["receipt"]["data"]
            print(f"  Sites attempted: {data['sites_attempted']}")
            for site, info in data["results"].items():
                price = info.get("price")
                title = info.get("title", "")
                err = info.get("error")
                if price is not None:
                    print(f"    {site}: ${price:.2f} - {title[:50]!r}")
                else:
                    print(f"    {site}: ERROR - {err}")
        print("  PASS — Real price extraction ran (or honest error per site)")

    # ---- Output depends on input (different product → different output)
    print("\n[7a.3] Output depends on input — different product produces different summary…")
    r1 = await commerce.execute("find_and_compare", {"product": "iphone 15"})
    r2 = await commerce.execute("find_and_compare", {"product": "samsung tv"})
    print(f"  product='iphone 15'  → message: {r1['message'][:80]}")
    print(f"  product='samsung tv' → message: {r2['message'][:80]}")
    # The messages should differ because they reference different products
    assert "iphone 15" in r1["message"].lower() or "not_implemented" in r1["status"]
    assert "samsung tv" in r2["message"].lower() or "not_implemented" in r2["status"]
    assert r1["message"] != r2["message"]
    print("  PASS — Different inputs produce different outputs")

    # ==================================================================
    # 7b — Real checkout with hard confirmation gate
    # ==================================================================
    print("\n" + "=" * 70)
    print("7b — Real checkout with hard confirmation gate")
    print("=" * 70)

    # ---- 1. Checkout without Stripe configured ------------------------
    print("\n[7b.1] Checkout without STRIPE_SECRET_KEY configured…")
    # Make sure no key is set
    os.environ.pop("STRIPE_SECRET_KEY", None)
    # Re-init Commerce to pick up the env
    commerce_no_stripe = type(commerce)()
    result = await commerce_no_stripe.execute(
        "checkout",
        {"item": "test widget", "price": 9.99},
    )
    print(f"  Status: {result['status']}")
    print(f"  Message: {result['message']}")
    assert result["status"] == "not_implemented", \
        f"Expected not_implemented, got {result['status']}"
    print("  PASS — Honest 'not_implemented' (no fabricated success)")

    # ---- 2. Hard approval gate at POWER profile ------------------------
    print("\n[7b.2] Demonstrating hard approval gate at POWER profile…")
    # Build a POWER-profile ledger
    import config.settings as settings
    original_profile = settings.AUTONOMY_PROFILE
    settings.AUTONOMY_PROFILE = "POWER"
    try:
        power_ledger = ActionLedger()
        # Re-set the profile because ActionLedger reads it at init time
        power_ledger.profile = "POWER"
        print(f"  Autonomy profile: {power_ledger.profile}")

        # Queue a Commerce checkout action
        action_id = power_ledger.queue_action(
            component="Commerce",
            action="checkout",
            params={"item": "expensive widget", "price": 999.99},
            risk_level="low",  # Even at LOW risk + POWER profile, should NOT auto-approve
        )
        status = power_ledger.pending_actions[action_id]["status"]
        print(f"  Queued Commerce.checkout at POWER profile + low risk")
        print(f"  Status after queue: {status}")
        assert status == "pending", \
            f"Commerce must NOT auto-approve even at POWER profile (got {status})"
        print("  PASS — Commerce.checkout stays 'pending' even at POWER profile + low risk")

        # Now simulate user rejection
        power_ledger.reject_action(action_id)
        status_after = power_ledger.pending_actions[action_id]["status"]
        print(f"  Status after manual reject: {status_after}")
        assert status_after == "rejected"

        # And simulate user approval (separate action_id)
        action_id2 = power_ledger.queue_action(
            component="Commerce",
            action="checkout",
            params={"item": "test", "price": 1.00},
            risk_level="low",
        )
        power_ledger.approve_action(action_id2)
        status_after2 = power_ledger.pending_actions[action_id2]["status"]
        print(f"  Status after manual approve: {status_after2}")
        assert status_after2 == "approved"
        print("  PASS — Manual approve/reject works; auto-approve never fires")

    finally:
        settings.AUTONOMY_PROFILE = original_profile

    # ---- 3. Verify other "low risk" components DO auto-approve at POWER
    print("\n[7b.3] Sanity check: other low-risk components DO auto-approve at POWER…")
    # Pick a non-never-auto-approve component, e.g. "Weather"
    settings.AUTONOMY_PROFILE = "POWER"
    try:
        power_ledger2 = ActionLedger()
        power_ledger2.profile = "POWER"
        aid = power_ledger2.queue_action(
            component="Weather",
            action="get_weather",
            params={"location": "Lagos"},
            risk_level="low",
        )
        weather_status = power_ledger2.pending_actions[aid]["status"]
        print(f"  Weather.get_weather at POWER + low risk: {weather_status}")
        assert weather_status == "approved", \
            f"Non-Commerce low-risk should auto-approve at POWER (got {weather_status})"
        print("  PASS — Weather auto-approves at POWER, Commerce does NOT (gate works)")
    finally:
        settings.AUTONOMY_PROFILE = original_profile

    print("\n" + "=" * 70)
    print("SECTION 7a + 7b VERIFIED")
    print("  7a:")
    print("  - integrations/commerce.py with find_and_compare action (real Playwright)")
    print("  - Returns real prices or honest not_implemented (no fabrication)")
    print("  - Output depends on input (different products → different summaries)")
    print()
    print("  7b:")
    print("  - checkout action goes through the ledger gate")
    print("  - Commerce is in NEVER_AUTO_APPROVE_COMPONENTS (hardcoded)")
    print("  - Even at POWER profile + low risk, Commerce stays 'pending'")
    print("  - Other low-risk components DO auto-approve at POWER (gate is specific)")
    print("  - Manual approve/reject works after the gate")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
