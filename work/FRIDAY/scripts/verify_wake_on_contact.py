#!/usr/bin/env python3
"""Section 4b — Wake-on-first-contact verification.

Simulates a first-activation scenario where ``last_interaction_time`` is
set to 7 hours ago. Triggers a wake event and confirms the morning
briefing fires unprompted.
"""
import asyncio
import sys
import os
import time
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.context import ContextAwareness
from core.wake_on_contact import WakeOnContact


async def main():
    print("=" * 70)
    print("SECTION 4b — Wake-on-First-Contact Verification")
    print("=" * 70)

    # Build a real context with the default 6h idle threshold
    ctx = ContextAwareness()
    print(f"\n[1] Initial state:")
    print(f"  last_interaction_time = {time.ctime(ctx.last_interaction_time)}")
    print(f"  idle_threshold_seconds = {ctx.idle_threshold_seconds}")
    print(f"  seconds_since_last_interaction = {ctx.seconds_since_last_interaction():.1f}")
    print(f"  is_idle() = {ctx.is_idle()}")

    # Backdate last interaction by 7 hours
    seven_hours_ago = time.time() - (7 * 3600)
    ctx.set_last_interaction(seven_hours_ago)
    print(f"\n[2] After backdating last_interaction_time to 7h ago:")
    print(f"  last_interaction_time = {time.ctime(ctx.last_interaction_time)}")
    print(f"  seconds_since_last_interaction = {ctx.seconds_since_last_interaction():.1f}")
    print(f"  is_idle() = {ctx.is_idle()}")
    assert ctx.is_idle(), "Context should be idle after 7h of no interaction"
    print("  PASS — Real time-based idle check returns True after 7h")

    # Mock the morning-briefing callback
    briefing_fired = MagicMock()
    async def _fire_briefing():
        print("  >>> Morning Briefing skill would now execute <<<")
        briefing_fired()
        return "briefing_complete"

    # Build WakeOnContact with the context attached
    woc = WakeOnContact(
        morning_start=0,   # bypass morning-hours filter so we test the idle branch
        morning_end=0,
        on_trigger=_fire_briefing,
        context=ctx,
    )
    print(f"\n[3] WakeOnContact configured with real idle-threshold context")
    print(f"  _is_idle() = {woc._is_idle()}")

    # First wake-word — should fire because idle > threshold
    print(f"\n[4] Triggering wake-word event (first call)…")
    result = await woc.check_and_trigger_async()
    print(f"  check_and_trigger_async returned: {result}")
    print(f"  briefing_fired call count: {briefing_fired.call_count}")
    assert result is True, "Expected wake-on-contact to trigger"
    assert briefing_fired.call_count == 1, "Expected briefing to fire exactly once"
    print("  PASS — Wake event triggers briefing when idle > threshold")

    # After firing, interaction timestamp should be updated
    elapsed = ctx.seconds_since_last_interaction()
    print(f"\n[5] After firing, seconds_since_last_interaction = {elapsed:.1f}")
    assert elapsed < 5, f"Expected interaction time to be reset, got {elapsed}s"
    print("  PASS — Interaction timestamp updated after wake event")

    # Second wake-word immediately — should NOT fire (not idle, first-contact already used)
    print(f"\n[6] Second wake-word immediately (no idle)…")
    briefing_fired.reset_mock()
    result2 = await woc.check_and_trigger_async()
    print(f"  check_and_trigger_async returned: {result2}")
    print(f"  briefing_fired call count: {briefing_fired.call_count}")
    assert result2 is False, "Expected no trigger when not idle and first-contact already used"
    assert briefing_fired.call_count == 0, "Briefing should NOT fire when not idle"
    print("  PASS — No trigger when freshly interacted")

    # Now backdate again and trigger — should fire again on idle branch
    ctx.set_last_interaction(time.time() - (7 * 3600))
    print(f"\n[7] Backdate again to 7h ago, then trigger wake-word…")
    briefing_fired.reset_mock()
    result3 = await woc.check_and_trigger_async()
    print(f"  check_and_trigger_async returned: {result3}")
    print(f"  briefing_fired call count: {briefing_fired.call_count}")
    assert result3 is True, "Expected trigger after idle threshold re-exceeded"
    assert briefing_fired.call_count == 1, "Briefing should fire again after new idle period"
    print("  PASS — Idle-branch fires repeatedly (once per idle window)")

    print("\n" + "=" * 70)
    print("SECTION 4b VERIFIED — Wake-on-first-contact is a real time-based condition")
    print("  - Idle threshold (6h default) is checked via ContextAwareness.is_idle()")
    print("  - is_idle() reads actual wall-clock time, not a flag")
    print("  - Briefing fires when wake-word + idle threshold exceeded")
    print("  - Interaction timestamp resets on every wake event")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
