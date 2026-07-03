#!/usr/bin/env python3
"""Section 10c — Trust Report endpoint verification.

Verifies that GET /api/trust/report returns a structured result with
each hellfire audit check + status + details.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["FRIDAY_API_TOKEN"] = "test"


def main():
    print("=" * 70)
    print("SECTION 10c — Trust Report endpoint verification")
    print("=" * 70)

    from fastapi.testclient import TestClient
    import api.routes.stats as stats_mod
    stats_mod._request_log.clear()  # reset state
    from api.main import app
    client = TestClient(app)
    H = {"Authorization": "Bearer test"}

    print("\n[1] Calling GET /api/trust/report…")
    r = client.get("/api/trust/report", headers=H)
    print(f"  HTTP {r.status_code}")
    assert r.status_code == 200
    data = r.json()
    print(f"  timestamp: {data.get('timestamp')}")
    print(f"  total_checks: {data.get('total_checks')}")
    print(f"  passed: {data.get('passed')}")
    print(f"  failed: {data.get('failed')}")
    print(f"\n  Per-check results:")
    for check in data.get("checks", []):
        marker = "✓" if check["passed"] else "✗"
        print(f"    {marker} {check['name']}")
    if data.get("failures"):
        print(f"\n  Failures:")
        for f in data["failures"]:
            print(f"    - {f['label']}: {f['detail']}")

    assert data.get("total_checks", 0) >= 8, \
        f"Expected >= 8 checks, got {data.get('total_checks')}"
    # All checks should pass in the current state
    assert data.get("failed", 1) == 0, \
        f"Expected 0 failures, got {data.get('failed')}"
    print("\n  PASS — All audit checks pass via the API endpoint")

    # Verify all required checks are present
    required_checks = [
        "commented_out_calls",
        "eager_client_construction",
        "double_run",
        "auth_rejection",
        "never_auto_approve",
        "no_hardcoded_secrets",
        "glm_key_from_env",
        "zhipu_client_guarded",
    ]
    actual_check_names = [c["name"] for c in data.get("checks", [])]
    print(f"\n[2] Verifying all 8 required checks are present…")
    for name in required_checks:
        assert name in actual_check_names, f"Missing check: {name}"
        print(f"  PASS — {name} present")
    print("  PASS — All 8 required audit checks present in the response")

    print("\n[3] Full JSON response (pretty-printed):")
    print(json.dumps(data, indent=2)[:2000])

    print("\n" + "=" * 70)
    print("SECTION 10c VERIFIED")
    print("  - GET /api/trust/report returns structured audit results")
    print("  - All 8 hellfire audit checks are included")
    print("  - Each check has a 'passed' boolean + details")
    print("  - In the current state, all checks pass (0 failures)")
    print("  - This endpoint powers the Trust Report button in the dashboard")
    print("=" * 70)


if __name__ == "__main__":
    main()
