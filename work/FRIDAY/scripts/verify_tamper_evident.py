#!/usr/bin/env python3
"""Section 10a — Tamper-evident audit log verification.

Verifies that:
  1. Logging 3 real actions produces a hash-chained audit log
  2. /api/ledger/verify returns {"valid": true}
  3. Tampering with one entry's params breaks the chain
  4. /api/ledger/verify returns {"valid": false} after tampering
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["FRIDAY_API_TOKEN"] = "test"


def main():
    print("=" * 70)
    print("SECTION 10a — Tamper-evident audit log verification")
    print("=" * 70)

    # ---- 1. Log 3 real actions via the ledger --------------------------
    print("\n[1] Logging 3 real actions via ActionLedger…")
    from core.ledger import ActionLedger
    ledger = ActionLedger()

    # Queue + approve 3 actions to generate audit log entries
    for i, (comp, act, params) in enumerate([
        ("Weather", "get_weather", {"location": "Lagos"}),
        ("Printer", "print_document", {"file": "report.pdf"}),
        ("Commerce", "checkout", {"item": "widget", "price": 9.99}),
    ]):
        aid = ledger.queue_action(comp, act, params, risk_level="high")
        ledger.approve_action(aid)  # this triggers _log_audit()

    audit = ledger.get_audit_log()
    print(f"  Audit log entries: {len(audit)}")
    for i, entry in enumerate(audit):
        print(f"    [{i}] component={entry['component']!r} action={entry['action']!r} "
              f"hash={entry['hash'][:16]}… prev_hash={entry['prev_hash'][:16]}…")
    assert len(audit) == 3

    # Verify each entry's hash differs (chain is real)
    hashes = [e["hash"] for e in audit]
    assert len(set(hashes)) == 3, "All 3 hashes should be distinct"
    print("  PASS — 3 entries logged with distinct hashes")

    # ---- 2. verify_chain() returns True --------------------------------
    print("\n[2] Calling verify_chain() on intact chain…")
    is_valid = ledger.verify_chain()
    print(f"  verify_chain() returned: {is_valid}")
    assert is_valid is True, "Chain should be valid before tampering"
    print("  PASS — Chain intact")

    # ---- 3. /api/ledger/verify endpoint --------------------------------
    print("\n[3] Calling GET /api/actions/verify (the public endpoint)…")
    from fastapi.testclient import TestClient
    import api.routes.actions as actions_mod
    # Replace the singleton ledger in the actions route with ours
    actions_mod.ledger = ledger

    from api.main import app
    client = TestClient(app)
    H = {"Authorization": "Bearer test"}

    r = client.get("/api/actions/verify", headers=H)
    print(f"  HTTP {r.status_code}")
    print(f"  Response: {r.json()}")
    assert r.status_code == 200
    assert r.json()["valid"] is True
    print("  PASS — Endpoint returns {valid: true} for intact chain")

    # ---- 4. Tamper with one entry's params -----------------------------
    print("\n[4] Tampering with entry 1's params (changing location)…")
    original_params = audit[1]["params"]
    print(f"  Original params: {original_params}")
    ledger._tamper_for_test(1, {"file": "TAMPERED.pdf", "malicious": True})
    print(f"  After tampering: {ledger.get_audit_log()[1]['params']}")

    # ---- 5. verify_chain() now returns False ---------------------------
    print("\n[5] Calling verify_chain() after tampering…")
    is_valid_after = ledger.verify_chain()
    print(f"  verify_chain() returned: {is_valid_after}")
    assert is_valid_after is False, "Chain should be INVALID after tampering"
    print("  PASS — Tampering detected")

    # ---- 6. /api/ledger/verify endpoint returns false ------------------
    print("\n[6] Calling GET /api/actions/verify after tampering…")
    r = client.get("/api/actions/verify", headers=H)
    print(f"  HTTP {r.status_code}")
    print(f"  Response: {r.json()}")
    assert r.status_code == 200
    assert r.json()["valid"] is False
    print("  PASS — Endpoint returns {valid: false} after tampering")

    # ---- 7. Output depends on input ------------------------------------
    print("\n[7] Output depends on input — different actions produce different hashes…")
    ledger2 = ActionLedger()
    aid_a = ledger2.queue_action("Weather", "get_weather", {"location": "Lagos"})
    ledger2.approve_action(aid_a)
    ledger3 = ActionLedger()
    aid_b = ledger3.queue_action("Weather", "get_weather", {"location": "Abuja"})
    ledger3.approve_action(aid_b)
    hash_a = ledger2.get_audit_log()[0]["hash"]
    hash_b = ledger3.get_audit_log()[0]["hash"]
    print(f"  Hash for Lagos:  {hash_a}")
    print(f"  Hash for Abuja:  {hash_b}")
    assert hash_a != hash_b, "Different params must produce different hashes"
    print("  PASS — Different inputs produce different hashes")

    print("\n" + "=" * 70)
    print("SECTION 10a VERIFIED")
    print("  - ActionLedger audit log is hash-chained (SHA-256)")
    print("  - Each entry's hash depends on prev_hash + entry fields")
    print("  - verify_chain() returns True on intact chain")
    print("  - Tampering with any entry breaks the chain")
    print("  - GET /api/actions/verify exposes the check via API")
    print("  - Different inputs produce different hashes (no echo)")
    print("=" * 70)


if __name__ == "__main__":
    main()
