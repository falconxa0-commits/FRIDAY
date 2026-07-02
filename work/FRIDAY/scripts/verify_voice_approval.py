#!/usr/bin/env python3
"""Section 4a — Voice-native approval verification.

Queues a real Printer action, mocks the transcriber to return "yes go ahead",
and confirms the ledger transitions from `pending` -> `approved` as a direct
result of the transcription.

Also tests the no/unclear/timeout branches for completeness.
"""
import asyncio
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ledger import ActionLedger, NEVER_AUTO_APPROVE_COMPONENTS


async def main():
    print("=" * 70)
    print("SECTION 4a — Voice-Native Approval Verification")
    print("=" * 70)

    # ---- Setup ----------------------------------------------------------
    ledger = ActionLedger()
    print(f"\n[1] Printer in NEVER_AUTO_APPROVE_COMPONENTS: "
          f"{'Printer' in NEVER_AUTO_APPROVE_COMPONENTS}")

    # Mock speaker — just print
    speaker = MagicMock()
    async def _speak_async(text):
        print(f"  [SPEAKER] {text}")
    speaker.speak_async = _speak_async
    speaker.speak = lambda t: print(f"  [SPEAKER-sync] {t}")

    # Mock listener — always returns a fake filename
    listener = MagicMock()
    listener.record_audio = lambda path, dur: path  # returns the filename

    # ---- Case 1: Clear "yes" -> approve --------------------------------
    print("\n[Case 1] Transcriber returns 'yes go ahead'")
    action_id = ledger.queue_action(
        component="Printer",
        action="print_document",
        params={"file": "report.pdf"},
        risk_level="high",
    )
    print(f"  Queued action: {action_id}")
    print(f"  Initial status: {ledger.pending_actions[action_id]['status']}")

    with patch("voice.transcriber.FridayTranscriber") as MockTrans:
        MockTrans.return_value.transcribe.return_value = "yes go ahead"
        result = await ledger.wait_for_voice_approval(
            action_id, speaker=speaker, listener=listener, timeout=5,
        )

    print(f"  wait_for_voice_approval returned: {result}")
    print(f"  Final status: {ledger.pending_actions[action_id]['status']}")
    assert result is True, f"Expected True, got {result}"
    assert ledger.pending_actions[action_id]["status"] == "approved", \
        f"Expected approved, got {ledger.pending_actions[action_id]['status']}"
    print("  PASS — Clear 'yes' transitions ledger from pending -> approved")

    # ---- Case 2: Clear "no" -> reject ----------------------------------
    print("\n[Case 2] Transcriber returns 'no don\\'t'")
    action_id2 = ledger.queue_action(
        component="Printer",
        action="print_document",
        params={"file": "secret.pdf"},
        risk_level="high",
    )
    with patch("voice.transcriber.FridayTranscriber") as MockTrans:
        MockTrans.return_value.transcribe.return_value = "no don't"
        result = await ledger.wait_for_voice_approval(
            action_id2, speaker=speaker, listener=listener, timeout=5,
        )
    print(f"  wait_for_voice_approval returned: {result}")
    print(f"  Final status: {ledger.pending_actions[action_id2]['status']}")
    assert result is False
    assert ledger.pending_actions[action_id2]["status"] == "rejected"
    print("  PASS — Clear 'no' transitions ledger to rejected")

    # ---- Case 3: Ambiguous -> ask once more -> reject ------------------
    print("\n[Case 3] Transcriber returns 'maybe' twice (ambiguous)")
    action_id3 = ledger.queue_action(
        component="Printer",
        action="print_document",
        params={"file": "memo.txt"},
        risk_level="high",
    )
    with patch("voice.transcriber.FridayTranscriber") as MockTrans:
        MockTrans.return_value.transcribe.return_value = "maybe later"
        result = await ledger.wait_for_voice_approval(
            action_id3, speaker=speaker, listener=listener, timeout=5,
            max_retries=1,
        )
    print(f"  wait_for_voice_approval returned: {result}")
    print(f"  Final status: {ledger.pending_actions[action_id3]['status']}")
    assert result is False
    assert ledger.pending_actions[action_id3]["status"] == "rejected"
    print("  PASS — Ambiguous response defaults to reject after retry")

    # ---- Case 4: Timeout (no audio captured) -> reject -----------------
    print("\n[Case 4] Listener returns None (no audio captured / timeout)")
    action_id4 = ledger.queue_action(
        component="Printer",
        action="print_document",
        params={"file": "report.pdf"},
        risk_level="high",
    )
    silent_listener = MagicMock()
    silent_listener.record_audio = lambda path, dur: None  # no audio captured

    with patch("voice.transcriber.FridayTranscriber") as MockTrans:
        MockTrans.return_value.transcribe.return_value = ""
        result = await ledger.wait_for_voice_approval(
            action_id4, speaker=speaker, listener=silent_listener, timeout=2,
        )
    print(f"  wait_for_voice_approval returned: {result}")
    print(f"  Final status: {ledger.pending_actions[action_id4]['status']}")
    assert result is False
    assert ledger.pending_actions[action_id4]["status"] == "rejected"
    print("  PASS — Timeout / no response defaults to reject with spoken message")

    # ---- Summary --------------------------------------------------------
    print("\n" + "=" * 70)
    print("ALL 4 CASES PASS — Voice-native approval is wired correctly.")
    print("  - Clear yes -> approve + spoken confirmation")
    print("  - Clear no -> reject + spoken confirmation")
    print("  - Ambiguous -> ask once more -> reject")
    print("  - Timeout -> reject + spoken 'no response detected' message")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
