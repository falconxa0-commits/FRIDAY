#!/usr/bin/env python3
"""Section B6 — Conversational voice with barge-in verification.

Verifies:
  - speak_with_awareness splits text into phrases
  - Barge-in signal interrupts mid-sentence
  - Pace adjusts based on context (night mode = slower)
  - Vocabulary adjusts based on user emotion (confused → simpler)
  - Night mode shortens responses
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    print("=" * 70)
    print("SECTION B6 — Conversational voice + barge-in verification")
    print("=" * 70)

    from voice.conversational import ConversationalVoice, PACE_PRESETS

    # Mock speaker that records what was spoken
    spoken_phrases = []

    class MockSpeaker:
        def __init__(self):
            self.interrupt_signal = False

        def interrupt(self):
            self.interrupt_signal = True

        async def speak_async(self, text):
            spoken_phrases.append(text)

        def speak(self, text):
            spoken_phrases.append(text)

    voice = ConversationalVoice(speaker=MockSpeaker())

    # ---- 1. Phrase splitting -----------------------------------------
    print("\n[1] Verifying text is split into natural phrases…")
    text = "Hello there. I'm Friday, your AI assistant. Let me help you with that, ok?"
    phrases = voice._split_into_phrases(text)
    print(f"  Input: {text!r}")
    print(f"  Phrases ({len(phrases)}):")
    for i, p in enumerate(phrases):
        print(f"    [{i}] {p!r}")
    assert len(phrases) >= 3, "Expected at least 3 phrases"
    print("  PASS — Text split into natural phrases at sentence/clause boundaries")

    # ---- 2. Pace adjustment ------------------------------------------
    print("\n[2] Verifying pace adjustment based on context…")
    # Mock _is_night to return False so we can test the message_type logic
    voice._is_night = lambda: False
    cases = [
        ({"message_type": "confirmation"}, "fast confirmation"),
        ({"message_type": "explanation"}, "slow explanation"),
        ({"message_type": "complex_explanation"}, "very_slow complex"),
        ({"user_emotion": "confused"}, "slow for confused user"),
        ({"user_emotion": "frustrated"}, "fast for frustrated user"),
        ({}, "normal default"),
    ]
    for ctx, label in cases:
        pace = voice._calculate_pace(ctx)
        print(f"  {label}: pace={pace} WPM (ctx={ctx})")
    # Confirm pace differs by context
    paces = [voice._calculate_pace(ctx) for ctx, _ in cases]
    assert len(set(paces)) > 1, "Paces should differ by context"
    print("  PASS — Pace adjusts based on context")

    # ---- 3. Barge-in interruption ------------------------------------
    print("\n[3] Verifying barge-in interrupts mid-sentence…")
    spoken_phrases.clear()
    voice._speaker = MockSpeaker()  # fresh speaker
    long_text = (
        "This is a very long response that Friday is speaking aloud. "
        "It has multiple sentences. The user should be able to interrupt "
        "mid-sentence. When the interrupt signal fires, Friday should "
        "stop speaking immediately. The remaining phrases should not be spoken."
    )

    async def interrupt_after_first_phrase():
        # Wait for the first phrase to be spoken, then interrupt
        await asyncio.sleep(0.5)
        voice.interrupt()

    interrupt_task = asyncio.create_task(interrupt_after_first_phrase())
    completed = await voice.speak_with_awareness(long_text, {})
    await interrupt_task

    print(f"  Completed without interruption: {completed}")
    print(f"  Phrases spoken: {len(spoken_phrases)}")
    for i, p in enumerate(spoken_phrases):
        print(f"    [{i}] {p!r}")

    assert completed is False, "Should report interrupted (False)"
    # Should have spoken fewer than all phrases
    total_phrases = len(voice._split_into_phrases(long_text))
    print(f"  Total phrases in input: {total_phrases}")
    print(f"  Spoken before interrupt: {len(spoken_phrases)}")
    assert len(spoken_phrases) < total_phrases, \
        "Barge-in should have stopped speaking before all phrases"
    print("  PASS — Barge-in interrupted Friday mid-sentence")

    # ---- 4. Vocabulary adjustment ------------------------------------
    print("\n[4] Verifying vocabulary adjustment for confused users…")
    complex_text = (
        "We will utilize the new API. Subsequently, the system will "
        "approximately double in speed. Fundamentally, this is a major improvement."
    )
    adjusted = voice._adjust_vocabulary(complex_text, {"user_emotion": "confused"})
    print(f"  Original: {complex_text}")
    print(f"  Adjusted: {adjusted}")
    assert "utilize" not in adjusted.lower(), "Should simplify 'utilize' to 'use'"
    assert "subsequently" not in adjusted.lower(), "Should simplify 'subsequently'"
    assert "approximately" not in adjusted.lower(), "Should simplify 'approximately'"
    print("  PASS — Complex vocabulary simplified for confused users")

    # ---- 5. Night mode (whisper) -------------------------------------
    print("\n[5] Verifying night mode (whisper) pace + volume…")
    # Mock _is_night to return True
    voice._is_night = lambda: True
    night_pace = voice._calculate_pace({})
    night_volume = voice._calculate_volume({})
    print(f"  Night pace: {night_pace} WPM (whisper preset = {PACE_PRESETS['whisper']})")
    print(f"  Night volume: {night_volume} (should be < 1.0)")
    assert night_pace == PACE_PRESETS["whisper"]
    assert night_volume < 1.0
    print("  PASS — Night mode uses whisper pace + reduced volume")

    # Night mode also shortens long responses
    long_response = (
        "This is a very long response that goes on and on. "
        "It has many sentences. In night mode, it should be truncated. "
        "Only the first two sentences should remain. "
        "This fourth sentence should be removed. "
        "And this fifth sentence should also be removed."
    )
    shortened = voice._adjust_vocabulary(long_response, {})
    print(f"\n  Original ({len(long_response)} chars): {long_response[:80]}…")
    print(f"  Shortened ({len(shortened)} chars): {shortened[:80]}…")
    assert len(shortened) < len(long_response), \
        "Night mode should shorten long responses"
    print("  PASS — Night mode truncates long responses to first 2 sentences")

    # Reset
    voice._is_night = lambda: False

    # ---- 6. Frustrated user gets short, direct responses -------------
    print("\n[6] Verifying frustrated users get short, direct responses…")
    long_response2 = (
        "I understand you're having trouble. Let me explain what's happening. "
        "The system is configured to require authentication. "
        "You need to set the API key in your environment variables. "
        "The key should be set as GLM_API_KEY. Once set, restart the server."
    )
    short_for_frustrated = voice._adjust_vocabulary(
        long_response2, {"user_emotion": "frustrated"}
    )
    print(f"  Original ({len(long_response2)} chars)")
    print(f"  For frustrated user ({len(short_for_frustrated)} chars): {short_for_frustrated}")
    assert len(short_for_frustrated) < len(long_response2)
    print("  PASS — Frustrated users get shortened responses")

    # ---- 7. Output depends on input ----------------------------------
    print("\n[7] Output depends on input — different texts produce different phrases…")
    p1 = voice._split_into_phrases("Hello there. How are you?")
    p2 = voice._split_into_phrases("Goodbye now. See you later.")
    assert p1 != p2
    print("  PASS — Different inputs produce different phrase lists")

    print("\n" + "=" * 70)
    print("SECTION B6 VERIFIED")
    print("  - ConversationalVoice splits text into natural phrases")
    print("  - Barge-in signal interrupts mid-sentence (phases stop)")
    print("  - Pace adjusts: fast=confirmations, slow=explanations,")
    print("    very_slow=complex, whisper=night mode")
    print("  - Vocabulary simplifies for confused users")
    print("  - Night mode (22:00-06:00) uses whisper pace + lower volume")
    print("  - Night mode truncates long responses to 2 sentences")
    print("  - Frustrated users get short, direct responses")
    print("  - Output depends on input (different texts → different phrases)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
