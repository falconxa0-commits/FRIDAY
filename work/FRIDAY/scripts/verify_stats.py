#!/usr/bin/env python3
"""Section 5c — Transparency / cost dashboard stats verification.

Verifies that /api/stats returns:
  - Which model handled each of the last N requests
  - Real token counts per request
  - Real cost per request (GLM free tier = $0.00)
  - Current rate-limit status for Z.ai free tier
  - Rate-limit warning when usage approaches limits

Records real (mocked) requests via the chat route's record_request helper
and confirms the stats endpoint returns the matching counts.
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["FRIDAY_API_TOKEN"] = "test"
os.environ["BRAIN_PROVIDER"] = "glm"


def main():
    print("=" * 70)
    print("SECTION 5c — Transparency / cost dashboard stats verification")
    print("=" * 70)

    # Reset the singleton state so this script is reproducible.
    import api.routes.stats as stats_mod
    stats_mod._request_log.clear()

    from fastapi.testclient import TestClient
    from api.main import app
    client = TestClient(app)
    H = {"Authorization": "Bearer test"}

    # ---- 1. Initial stats (no requests yet) ----------------------------
    print("\n[1] Initial /api/stats (no requests recorded)…")
    r = client.get("/api/stats", headers=H)
    print(f"  HTTP {r.status_code}")
    assert r.status_code == 200
    data = r.json()
    print(f"  current_provider: {data['current_provider']}")
    print(f"  provider_cost_note: {data['provider_cost_note']}")
    print(f"  total_requests: {data['total_requests']}")
    print(f"  zai_rate_limits: {data['zai_rate_limits']}")
    print(f"  rate_limit_warning: {data['rate_limit_warning']}")
    assert data["total_requests"] == 0
    assert data["zai_rate_limits"] is not None, "Should expose Z.ai limits"
    print("  PASS — Initial stats correctly show 0 requests and Z.ai limits")

    # ---- 2. Record real (mocked) requests directly ---------------------
    print("\n[2] Recording 3 real requests directly via record_request()…")
    # GLM free tier = $0.00
    stats_mod.record_request("glm", "glm-4-flash", tokens_in=120, tokens_out=80, cost=0.0)
    stats_mod.record_request("glm", "glm-4-flash", tokens_in=200, tokens_out=150, cost=0.0)
    # Claude (paid)
    stats_mod.record_request("claude", "claude-3-5-sonnet", tokens_in=100, tokens_out=50, cost=0.001)
    print("  Recorded: 2x GLM (free) + 1x Claude (paid)")

    # ---- 3. Verify stats reflect recorded requests ---------------------
    print("\n[3] Calling /api/stats after recording…")
    r = client.get("/api/stats", headers=H)
    assert r.status_code == 200
    data = r.json()
    print(f"  total_requests: {data['total_requests']}")
    print(f"  total_tokens_in: {data['total_tokens_in']}")
    print(f"  total_tokens_out: {data['total_tokens_out']}")
    print(f"  total_cost_usd: {data['total_cost_usd']}")
    print(f"  provider_breakdown:")
    for prov, ps in data["provider_breakdown"].items():
        print(f"    {prov}: {ps}")

    assert data["total_requests"] == 3, f"Expected 3, got {data['total_requests']}"
    assert data["total_tokens_in"] == 420, f"Expected 420, got {data['total_tokens_in']}"
    assert data["total_tokens_out"] == 280
    assert data["total_cost_usd"] > 0, "Cost should be > 0 due to Claude request"
    assert "glm" in data["provider_breakdown"]
    assert data["provider_breakdown"]["glm"]["requests"] == 2
    assert data["provider_breakdown"]["glm"]["cost_usd"] == 0.0  # GLM = free
    assert data["provider_breakdown"]["claude"]["requests"] == 1
    assert data["provider_breakdown"]["claude"]["cost_usd"] > 0
    print("  PASS — Stats reflect recorded requests with correct counts and costs")

    # ---- 4. Recent requests include model + tokens ---------------------
    print("\n[4] Verifying recent_requests contain model + tokens + cost…")
    recent = data["recent_requests"]
    print(f"  recent_requests count: {len(recent)}")
    for i, req in enumerate(recent):
        print(f"    [{i}] provider={req['provider']} model={req['model']} "
              f"tokens_in={req['tokens_in']} tokens_out={req['tokens_out']} "
              f"cost=${req['cost_usd']}")
    assert len(recent) == 3
    for req in recent:
        assert "provider" in req
        assert "model" in req
        assert "tokens_in" in req
        assert "tokens_out" in req
        assert "cost_usd" in req
    print("  PASS — Each recent request has provider, model, tokens, and cost")

    # ---- 5. Rate-limit warning when approaching limits -----------------
    print("\n[5] Filling request log to trigger rate-limit warning (>80% RPM)…")
    stats_mod._request_log.clear()
    # 50 requests in the last minute (50/60 = 83% of RPM limit)
    from datetime import datetime, timedelta
    now = datetime.now()
    for i in range(50):
        stats_mod._request_log.append({
            "timestamp": (now - timedelta(seconds=i)).isoformat(),
            "provider": "glm",
            "model": "glm-4-flash",
            "tokens_in": 100,
            "tokens_out": 50,
            "cost_usd": 0.0,
        })
    r = client.get("/api/stats", headers=H)
    data = r.json()
    print(f"  requests_last_minute: {data['rate_limit_warning']['requests_last_minute']}")
    print(f"  rpm_pct: {data['rate_limit_warning']['rpm_pct']}%")
    print(f"  warning level: {data['rate_limit_warning']['level']}")
    print(f"  warning messages: {data['rate_limit_warning']['messages']}")
    assert data["rate_limit_warning"] is not None
    assert data["rate_limit_warning"]["rpm_pct"] >= 80
    print("  PASS — Rate-limit warning fires when RPM > 80% of free-tier limit")

    # ---- 6. Output depends on input ------------------------------------
    print("\n[6] Output depends on input — recording different requests changes stats…")
    stats_mod._request_log.clear()
    stats_mod.record_request("glm", "glm-4-flash", 10, 5, 0.0)
    r1 = client.get("/api/stats", headers=H).json()
    stats_mod.record_request("glm", "glm-4-flash", 999, 888, 0.0)
    r2 = client.get("/api/stats", headers=H).json()
    print(f"  After 1 request (10+5 tokens):  total_tokens_in={r1['total_tokens_in']}, total_tokens_out={r1['total_tokens_out']}")
    print(f"  After 2 requests (+999+888):     total_tokens_in={r2['total_tokens_in']}, total_tokens_out={r2['total_tokens_out']}")
    assert r1["total_tokens_in"] != r2["total_tokens_in"]
    assert r1["total_tokens_out"] != r2["total_tokens_out"]
    print("  PASS — Different inputs produce different stats outputs")

    print("\n" + "=" * 70)
    print("SECTION 5c VERIFIED")
    print("  - /api/stats returns real per-request model + token + cost data")
    print("  - GLM free tier cost = $0.00")
    print("  - Claude (paid) cost > 0")
    print("  - Z.ai free tier rate limits exposed")
    print("  - Rate-limit warning fires at >80% RPM usage")
    print("  - Stats depend on the actual requests recorded")
    print("=" * 70)


if __name__ == "__main__":
    main()
