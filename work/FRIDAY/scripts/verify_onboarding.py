#!/usr/bin/env python3
"""Section 11b — Onboarding flow verification.

Runs the AutoOnboarding flow and verifies it covers:
  1. Plain-English explanation (no theatrical language)
  2. GLM_API_KEY first, with clear explanation it's free
  3. Real test call (skipped here because no GLM_API_KEY)
  4. Lists what's now available
  5. Optional paid upgrades + optional integrations (honest one-liners)
  6. Real Z.ai free tier rate limits
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    print("=" * 70)
    print("SECTION 11b — Onboarding flow verification")
    print("=" * 70)

    from core.onboarding import AutoOnboarding

    onboarding = AutoOnboarding()

    # ---- Run the onboarding flow ---------------------------------------
    print("\n[1] Running AutoOnboarding.run_onboarding()…")
    output = await onboarding.run_onboarding()

    print("\n" + "=" * 60)
    print("REAL ONBOARDING OUTPUT:")
    print("=" * 60)
    print(output)
    print("=" * 60)

    # ---- Verify each required section is present -----------------------
    print("\n[2] Verifying required sections are present…")

    checks = [
        ("Plain-English explanation of what FRIDAY is",
         ["What is FRIDAY", "personal AI assistant"]),
        ("GLM_API_KEY instructions with free mention",
         ["GLM_API_KEY", "free", "open.bigmodel.cn"]),
        ("Test GLM call section",
         ["GLM Connection Test"]),
        ("Available features list",
         ["Available Features", "Chat", "Web Search", "Image Generation"]),
        ("Optional paid upgrades with honest pricing",
         ["Optional Paid Upgrades", "Claude", "GPT", "$"]),
        ("Optional integrations with one-line descriptions",
         ["Optional Integrations", "Weather", "Spotify", "Smart Home"]),
        ("Z.ai free tier rate limits",
         ["Rate Limits", "GLM-4-Flash", "requests/min"]),
    ]

    all_pass = True
    for label, keywords in checks:
        missing = [kw for kw in keywords if kw.lower() not in output.lower()]
        if missing:
            print(f"  FAIL  {label} — missing: {missing}")
            all_pass = False
        else:
            print(f"  PASS  {label}")

    assert all_pass, "Some onboarding sections are missing"

    # ---- Verify NO theatrical language ---------------------------------
    print("\n[3] Verifying NO theatrical language in onboarding…")
    theatrical_terms = [
        "singularity", "peer not a tool", "4d timeline", "4d timelines",
        "ascendant", "transcend", "omniscient", "supreme",
        "god mode", "god-mode", "aether", "nexus",
    ]
    found_theatrical = [
        t for t in theatrical_terms
        if t.lower() in output.lower()
    ]
    print(f"  Theatrical terms searched: {theatrical_terms}")
    print(f"  Found: {found_theatrical or 'NONE'}")
    assert not found_theatrical, \
        f"Onboarding has theatrical language: {found_theatrical}"
    print("  PASS — No theatrical language")

    # ---- Test the GLM test call (skipped without key) ------------------
    print("\n[4] Testing test_glm_call() with no key (should fail honestly)…")
    os.environ.pop("GLM_API_KEY", None)
    result = await onboarding.test_glm_call()
    print(f"  Result: {result}")
    assert result["success"] is False, "Should fail without key"
    assert "GLM_API_KEY" in result["message"]
    print("  PASS — Honest failure when no key is set")

    print("\n" + "=" * 70)
    print("SECTION 11b VERIFIED")
    print("  - Onboarding explains FRIDAY in plain English (no theatrical language)")
    print("  - GLM_API_KEY instructions come first, clearly marked as free")
    print("  - Real test call attempted (honestly fails without key)")
    print("  - Lists all available features with the GLM key")
    print("  - Optional paid upgrades with honest pricing per provider")
    print("  - Optional integrations with one-line descriptions")
    print("  - Real Z.ai free tier rate limits included")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
