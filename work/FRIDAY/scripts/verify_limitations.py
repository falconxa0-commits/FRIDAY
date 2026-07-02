#!/usr/bin/env python3
"""Section 5d — Verify docs/LIMITATIONS.md is complete and honest.

Confirms all 6 required topics are covered:
  1. Single-user, single-process architecture
  2. In-memory state without Supabase (data lost on restart)
  3. Hardware integrations require real local hardware
  4. GLM free tier rate limits (with link to current pricing)
  5. Video generation takes 1-3 minutes
  6. Ollama requires local installation and a compatible GPU/CPU
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    print("=" * 70)
    print("SECTION 5d — docs/LIMITATIONS.md completeness verification")
    print("=" * 70)

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docs", "LIMITATIONS.md",
    )
    with open(path) as f:
        content = f.read().lower()

    checks = [
        ("single-user, single-process architecture",
         ["single-user", "single-process"]),
        ("In-memory state without Supabase + data lost on restart",
         ["in-memory", "supabase", "restart"]),
        ("Hardware integrations require real local hardware",
         ["hardware", "local hardware"]),
        ("GLM free tier rate limits + link to pricing page",
         ["glm", "rate limit", "open.bigmodel.cn/pricing"]),
        ("Video generation takes 1-3 minutes",
         ["video", "1-3 minutes"]),
        ("Ollama requires local installation + GPU/CPU compatibility",
         ["ollama", "install", "gpu", "cpu"]),
    ]

    print()
    all_pass = True
    for label, keywords in checks:
        missing = [kw for kw in keywords if kw not in content]
        if missing:
            print(f"  FAIL  {label} — missing keywords: {missing}")
            all_pass = False
        else:
            print(f"  PASS  {label}")

    if not all_pass:
        print("\nLIMITATIONS.md is incomplete.")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("SECTION 5d VERIFIED — LIMITATIONS.md covers all 6 required topics")
    print("=" * 70)
    print("\nFull file contents:")
    print("=" * 70)
    with open(path) as f:
        print(f.read())


if __name__ == "__main__":
    main()
