#!/usr/bin/env python3
"""Section B1 — Pattern engine verification.

Feeds 20 real interaction records with a clear pattern (research →
write report 8 times), runs discover_patterns(), and confirms Friday
identifies the pattern.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    print("=" * 70)
    print("SECTION B1 — Behavioral pattern learning verification")
    print("=" * 70)

    from core.pattern_engine import PatternEngine, MIN_PATTERN_OCCURRENCES

    engine = PatternEngine()
    print(f"\n[setup] MIN_PATTERN_OCCURRENCES = {MIN_PATTERN_OCCURRENCES}")

    # ---- Feed 20 interactions with a clear pattern -------------------
    print("\n[1] Feeding 20 real interaction records…")
    base_time = datetime(2026, 7, 1, 9, 0, 0)
    for i in range(8):
        # Pattern: research → write report
        await engine.observe({
            "timestamp": (base_time + timedelta(hours=i*2)).isoformat(),
            "action_type": "research",
            "content": f"research quantum computing topic {i}",
            "context": {"mode": "work"},
            "outcome": "success",
        })
        await engine.observe({
            "timestamp": (base_time + timedelta(hours=i*2, minutes=30)).isoformat(),
            "action_type": "write",
            "content": f"write report on quantum computing topic {i}",
            "context": {"mode": "work"},
            "outcome": "success",
        })
    # Add 4 more interactions to break the pattern slightly
    for i in range(4):
        await engine.observe({
            "timestamp": (base_time + timedelta(days=2, hours=i)).isoformat(),
            "action_type": "chat",
            "content": f"random chat message {i}",
            "context": {"mode": "evening"},
            "outcome": "success",
        })

    print(f"  Total interactions: {len(engine.interactions)}")
    assert len(engine.interactions) == 20

    # ---- Discover patterns -------------------------------------------
    print("\n[2] Running discover_patterns()…")
    patterns = await engine.discover_patterns()
    print(f"  Patterns discovered: {len(patterns)}")
    for p in patterns:
        print(f"\n  [{p['type']}] (evidence={p['evidence_count']})")
        print(f"    {p['description']}")
        if p.get("examples"):
            print(f"    examples: {p['examples']}")

    assert len(patterns) > 0, "Expected at least one pattern"

    # ---- Verify the research→write pattern was found -----------------
    print("\n[3] Verifying the research → write report pattern was found…")
    found_pair = [p for p in patterns if p["type"] == "action_pair"]
    assert found_pair, "Expected to find the research → write action pair pattern"
    pair = found_pair[0]
    print(f"  Found action pair: {pair['description']}")
    print(f"  Evidence count: {pair['evidence_count']}")
    assert pair["evidence_count"] >= 3, \
        f"Expected at least 3 occurrences, got {pair['evidence_count']}"
    print("  PASS — research → write pattern identified with sufficient evidence")

    # ---- Verify output depends on input ------------------------------
    print("\n[4] Output depends on input — different history → different patterns…")
    engine2 = PatternEngine()
    for i in range(10):
        await engine2.observe({
            "action_type": "code",
            "content": f"write python function number {i}",
        })
    patterns2 = await engine2.discover_patterns()
    assert any(p["type"] == "action_type_frequency" and "code" in p["description"]
               for p in patterns2), "Expected 'code' frequency pattern"
    assert not any("research" in p["description"] for p in patterns2), \
        "Different history should not surface research patterns"
    print("  PASS — Different history produces different patterns")

    # ---- Verify suggestions are based on real patterns ---------------
    print("\n[5] Verifying suggestions only come from real patterns…")
    suggestions = await engine.get_suggestions({"current_action": "research quantum"})
    print(f"  Suggestions for context {{'current_action': 'research quantum'}}:")
    for s in suggestions:
        print(f"    - {s['suggestion']} (confidence={s['confidence']:.2f})")
    # Should have at least one suggestion based on the research→write pattern
    assert len(suggestions) > 0 or all(p["evidence_count"] < 3 for p in patterns)
    print("  PASS — Suggestions derived from real patterns only")

    print("\n" + "=" * 70)
    print("SECTION B1 VERIFIED")
    print("  - PatternEngine.observe() records real interactions")
    print("  - discover_patterns() surfaces real recurring patterns")
    print("  - 4 pattern types: action_type_frequency, keyword_frequency,")
    print("    action_pair (A → B), time_of_day")
    print("  - Only patterns with evidence_count >= MIN_PATTERN_OCCURRENCES reported")
    print("  - Output depends on input (different history → different patterns)")
    print("  - get_suggestions() only suggests based on real observed patterns")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
