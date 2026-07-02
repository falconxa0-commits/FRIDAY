#!/usr/bin/env python3
"""Section 6c — Real time-based context-mode switching verification.

Confirms ContextAwareness switches mode based on real time-of-day:
  - Morning mode (6am-9am)
  - Work mode (9am-6pm)
  - Evening mode (6pm-10pm)
  - Night mode (10pm-6am)

Uses an injected now_provider to mock datetime.now() to specific hours
and confirms the mode switches accordingly.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.context import ContextAwareness


def make_ctx_at(hour: int, minute: int = 0) -> ContextAwareness:
    """Build a ContextAwareness whose clock is mocked to HH:MM."""
    mocked_now = datetime.datetime(2026, 7, 2, hour, minute, 0)
    return ContextAwareness(now_provider=lambda: mocked_now)


def main():
    print("=" * 70)
    print("SECTION 6c — Real time-based context-mode switching verification")
    print("=" * 70)

    test_cases = [
        (7, 0,   "morning", "Morning mode (6am-9am): briefing, weather, calendar"),
        (8, 30,  "morning", "Morning mode (6am-9am)"),
        (14, 0,  "work",    "Work mode (9am-6pm): focused, minimal interruptions"),
        (13, 30, "work",    "Work mode (9am-6pm)"),
        (19, 0,  "evening", "Evening mode (6pm-10pm): wind down, reminders"),
        (21, 0,  "evening", "Evening mode (6pm-10pm)"),
        (23, 0,  "night",   "Night mode (10pm-6am): quiet, only urgent alerts"),
        (2, 0,   "night",   "Night mode (10pm-6am)"),
        (5, 59,  "night",   "Night mode (10pm-6am) — boundary"),
        (6, 0,   "morning", "Boundary: 6am sharp = morning"),
        (9, 0,   "work",    "Boundary: 9am sharp = work"),
        (18, 0,  "evening", "Boundary: 6pm sharp = evening"),
        (22, 0,  "night",   "Boundary: 10pm sharp = night"),
    ]

    print("\n[1] Mocking datetime.now() to specific hours and checking mode…\n")
    all_pass = True
    for hour, minute, expected_mode, label in test_cases:
        ctx = make_ctx_at(hour, minute)
        actual_mode = ctx.get_time_context()
        # Get the full context dict to show real elapsed time
        full_ctx = ctx.get_current_context()
        marker = "PASS" if actual_mode == expected_mode else "FAIL"
        if actual_mode != expected_mode:
            all_pass = False
        time_str = f"{hour:02d}:{minute:02d}"
        print(f"  {marker}  at {time_str} → mode={actual_mode!r:10s}  expected={expected_mode!r:10s}  ({label})")

    if not all_pass:
        print("\nFAIL — Some mode switches did not match expected.")
        sys.exit(1)

    # ---- 2. Mode is computed on every call (not cached) ----------------
    print("\n[2] Mode is computed fresh on every call (not cached)…")
    mocked = datetime.datetime(2026, 7, 2, 7, 0, 0)  # 7am = morning
    ctx = ContextAwareness(now_provider=lambda: mocked)
    m1 = ctx.get_time_context()
    assert m1 == "morning", f"At 7am should be morning, got {m1}"

    # Now change the clock to afternoon and re-check
    mocked = datetime.datetime(2026, 7, 2, 14, 0, 0)  # 2pm = work
    ctx._now_provider = lambda: mocked
    m2 = ctx.get_time_context()
    assert m2 == "work", f"At 2pm should be work, got {m2}"
    print(f"  First call (7am):  {m1}")
    print(f"  After clock change to 2pm: {m2}")
    assert m1 != m2, "Mode must change when clock changes"
    print("  PASS — Mode is computed fresh from the actual time on each call")

    # ---- 3. Output depends on input ------------------------------------
    print("\n[3] Output depends on input — different times yield different modes…")
    ctx_morning = make_ctx_at(7, 0)
    ctx_night = make_ctx_at(23, 0)
    assert ctx_morning.get_time_context() != ctx_night.get_time_context()
    print(f"  At 7am:  {ctx_morning.get_time_context()}")
    print(f"  At 11pm: {ctx_night.get_time_context()}")
    print("  PASS — Different times produce different modes")

    # ---- 4. Default now_provider uses real wall-clock ------------------
    print("\n[4] Default now_provider uses real wall-clock time…")
    real_ctx = ContextAwareness()
    real_mode = real_ctx.get_time_context()
    real_hour = datetime.datetime.now().hour
    print(f"  Real current hour: {real_hour:02d}")
    print(f"  Real current mode: {real_mode}")
    # Sanity: the mode should be one of the 4 valid modes
    assert real_mode in ("morning", "work", "evening", "night"), \
        f"Invalid mode: {real_mode}"
    print("  PASS — Default context returns a real mode based on actual time")

    print("\n" + "=" * 70)
    print("SECTION 6c VERIFIED")
    print("  - Mode switches based on REAL time-of-day (not a flag)")
    print("  - Morning (6-9), Work (9-18), Evening (18-22), Night (22-6)")
    print("  - Boundaries handled correctly (sharp transitions at 6/9/18/22)")
    print("  - Mode computed fresh on every call")
    print("  - Different times produce different modes (output depends on input)")
    print("=" * 70)


if __name__ == "__main__":
    main()
