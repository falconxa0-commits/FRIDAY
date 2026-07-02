import asyncio
import os
import sys
sys.path.append(os.getcwd())
from core.ledger import ActionLedger

async def test_profiles():
    # 1. GUEST Profile (Everything queued)
    l_guest = ActionLedger()
    l_guest.profile = "GUEST"
    aid = l_guest.queue_action("PCControl", "click", {"x":1}, risk_level="high")
    print(f"GUEST Profile Status (Expected pending): {l_guest.pending_actions[aid]['status']}")

    # 2. POWER Profile (PC auto-approves)
    l_power = ActionLedger()
    l_power.profile = "POWER"
    aid2 = l_power.queue_action("PCControl", "click", {"x":1}, risk_level="high")
    print(f"POWER Profile Status (Expected approved): {l_power.pending_actions[aid2]['status']}")

if __name__ == "__main__":
    asyncio.run(test_profiles())
