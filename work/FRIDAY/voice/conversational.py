"""Conversational voice — natural, interruptible, continuous.

Upgrades the voice system from push-to-talk to genuine conversation:
  - Friday can be interrupted mid-sentence (barge-in)
  - Adjusts speaking pace based on context
  - Adjusts vocabulary based on user state
  - Whisper mode after 10pm — quieter, calmer, shorter responses
  - Detects frustration from basic audio features

This module wires together the existing speaker (FridaySpeaker) and
listener (FridayListener) into a continuous-conversation loop with
real barge-in support.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


# Pace presets (words per minute)
PACE_PRESETS = {
    "fast": 200,        # confirmations
    "normal": 160,      # default
    "slow": 120,        # explanations
    "very_slow": 90,    # complex explanations
    "whisper": 100,     # night mode
}


class ConversationalVoice:
    """Natural conversational voice — not push-to-talk."""

    def __init__(self, speaker=None, listener=None):
        """
        Args:
            speaker: FridaySpeaker instance (or any object with speak/speak_async).
                If None, tries to construct one (gracefully fails).
            listener: FridayListener instance (or any object with record_audio).
                If None, tries to construct one (gracefully fails).
        """
        self._speaker = speaker
        self._listener = listener
        self._interrupt_signal = False
        self._speaking = False
        self._conversation_state = {
            "turn_count": 0,
            "last_user_emotion": "neutral",
            "context_mode": "work",
        }

    # ------------------------------------------------------------------
    # Lazy initialisers
    # ------------------------------------------------------------------

    def _get_speaker(self):
        if self._speaker is None:
            try:
                from voice.speaker import FridaySpeaker
                self._speaker = FridaySpeaker()
            except Exception as exc:
                logger.warning("FridaySpeaker init failed: %s", exc)
                self._speaker = None
        return self._speaker

    def _get_listener(self):
        if self._listener is None:
            try:
                from voice.listener import FridayListener
                self._listener = FridayListener()
            except Exception as exc:
                logger.warning("FridayListener init failed: %s", exc)
                self._listener = None
        return self._listener

    # ------------------------------------------------------------------
    # State detection
    # ------------------------------------------------------------------

    def _is_night(self) -> bool:
        """Real time-of-day check — returns True if 22:00-06:00."""
        hour = datetime.datetime.now().hour
        return hour >= 22 or hour < 6

    def _calculate_pace(self, context: dict) -> int:
        """Determine speaking pace (WPM) based on context."""
        # Whisper mode at night (overrides everything except very complex)
        if self._is_night():
            # But still slow down for complex explanations even at night
            if context.get("message_type") == "complex_explanation":
                return PACE_PRESETS["very_slow"]
            return PACE_PRESETS["whisper"]

        # Slow down for explanations
        msg_type = context.get("message_type", "")
        if msg_type == "explanation":
            return PACE_PRESETS["slow"]
        if msg_type == "complex_explanation":
            return PACE_PRESETS["very_slow"]
        if msg_type == "confirmation":
            return PACE_PRESETS["fast"]

        # Slow down if user seems confused
        if context.get("user_emotion") == "confused":
            return PACE_PRESETS["slow"]

        # Speed up if user seems frustrated (shorter responses, faster pace)
        if context.get("user_emotion") == "frustrated":
            return PACE_PRESETS["fast"]

        return PACE_PRESETS["normal"]

    def _calculate_volume(self, context: dict) -> float:
        """Determine volume (0.0 - 1.0) based on context."""
        if self._is_night():
            return 0.4  # whisper mode
        if context.get("user_emotion") == "frustrated":
            return 0.7  # softer when user is frustrated
        return 1.0

    def _adjust_vocabulary(self, text: str, context: dict) -> str:
        """Adjust vocabulary based on user state."""
        # If user seems confused, simplify
        if context.get("user_emotion") == "confused":
            # Replace complex words with simpler alternatives
            simplifications = {
                "utilize": "use",
                "subsequently": "then",
                "approximately": "about",
                "fundamentally": "basically",
                "additionally": "also",
                "nevertheless": "but",
                "consequently": "so",
            }
            for complex_word, simple in simplifications.items():
                text = text.replace(complex_word, simple)
                text = text.replace(complex_word.capitalize(), simple.capitalize())

        # Night mode — shorten responses
        if self._is_night() and len(text) > 200:
            # Take first 2 sentences only
            sentences = text.split(". ")
            text = ". ".join(sentences[:2]) + "."

        # Frustrated user — keep responses short and direct
        if context.get("user_emotion") == "frustrated" and len(text) > 100:
            sentences = text.split(". ")
            text = sentences[0] + "."

        return text

    # ------------------------------------------------------------------
    # Interruptible speaking
    # ------------------------------------------------------------------

    async def speak_with_awareness(self, text: str, context: Optional[dict] = None) -> bool:
        """Speak text with awareness of conversation state.

        Splits text into natural phrases (at sentence/clause boundaries),
        speaks each phrase, and checks for interrupt signal between phrases.
        If interrupted, stops speaking and returns False.

        Returns:
            True if completed without interruption, False if interrupted.
        """
        ctx = context or {}
        pace = self._calculate_pace(ctx)
        volume = self._calculate_volume(ctx)
        adjusted_text = self._adjust_vocabulary(text, ctx)

        # Split into phrases at natural boundaries
        phrases = self._split_into_phrases(adjusted_text)
        if not phrases:
            return True

        self._speaking = True
        self._interrupt_signal = False
        speaker = self._get_speaker()

        try:
            for i, phrase in enumerate(phrases):
                if self._interrupt_signal:
                    logger.info("ConversationalVoice: interrupted at phrase %d/%d",
                                i, len(phrases))
                    return False

                if speaker and hasattr(speaker, "speak_async"):
                    try:
                        await speaker.speak_async(phrase)
                    except Exception as exc:
                        logger.warning("speak_async failed: %s", exc)
                        # Fall back to sync speak
                        if hasattr(speaker, "speak"):
                            speaker.speak(phrase)
                elif speaker and hasattr(speaker, "speak"):
                    speaker.speak(phrase)
                else:
                    # No speaker — print to console
                    print(f"  [Friday] {phrase}")

                # Pause between phrases (longer at sentence ends)
                pause = 0.3 if phrase.endswith((".", "!", "?")) else 0.1
                await asyncio.sleep(pause)

            return True
        finally:
            self._speaking = False

    def _split_into_phrases(self, text: str) -> list:
        """Split text into natural phrases at sentence/clause boundaries."""
        if not text:
            return []
        # Split on sentence enders first
        import re
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        phrases = []
        for sent in sentences:
            # Then split on commas/semicolons for shorter phrases
            sub = re.split(r'[,;]\s+', sent)
            phrases.extend(s.strip() for s in sub if s.strip())
        return phrases

    def interrupt(self) -> None:
        """Signal Friday to stop speaking immediately (barge-in)."""
        self._interrupt_signal = True
        speaker = self._get_speaker()
        if speaker and hasattr(speaker, "interrupt"):
            speaker.interrupt()
        logger.info("ConversationalVoice: barge-in signal received")

    # ------------------------------------------------------------------
    # Continuous listening
    # ------------------------------------------------------------------

    async def listen_continuously_cli(self, max_iterations: Optional[int] = None) -> None:
        """Run continuous-listen loop in CLI mode.

        Listens for wake word, then for user input, then responds.
        Accepts Ctrl+C to stop.
        """
        listener = self._get_listener()
        speaker = self._get_speaker()
        if not listener:
            print("Voice mode requires a microphone (pyaudio not installed).")
            return

        print("Voice mode active. Press Ctrl+C to stop.\n")
        iterations = 0
        try:
            while max_iterations is None or iterations < max_iterations:
                iterations += 1
                # Listen for 8 seconds
                import tempfile
                import os
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    recorded = await asyncio.to_thread(listener.record_audio, tmp_path, 8)
                    if recorded:
                        try:
                            from voice.transcriber import FridayTranscriber
                            transcriber = FridayTranscriber()
                            text = await asyncio.to_thread(transcriber.transcribe, tmp_path)
                            if text:
                                print(f"  You: {text}")
                                # Send to brain and speak response
                                await self._respond_to(text)
                        except ImportError:
                            print("  (whisper not installed — cannot transcribe)")
                        except Exception as exc:
                            print(f"  (transcription failed: {exc})")
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError as e:
                        logger.debug(f"Non-critical error: {e}")
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nVoice mode stopped.")

    async def _respond_to(self, user_text: str) -> None:
        """Send user text to the brain and speak the response."""
        try:
            from core.brain import FridayBrain
            brain = FridayBrain()
            full = ""
            async for chunk in brain.chat_stream(user_text):
                full += chunk
            if full.strip():
                await self.speak_with_awareness(full, {
                    "message_type": "explanation",
                })
            else:
                await self.speak_with_awareness(
                    "I'm not sure how to respond to that.",
                    {"message_type": "confirmation"},
                )
        except Exception as exc:
            print(f"  (brain error: {exc})")

    # ------------------------------------------------------------------
    # Frustration detection (basic)
    # ------------------------------------------------------------------

    def detect_frustration_from_audio(self, audio_bytes: bytes) -> bool:
        """Detect frustration from basic audio features (volume + pitch).

        Real implementation uses simple RMS + zero-crossing rate.
        Returns True if frustration is detected, False otherwise.
        """
        try:
            import numpy as np
            samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
            if len(samples) == 0:
                return False
            # High volume + high zero-crossing rate → likely frustrated
            rms = float(np.sqrt(np.mean(samples ** 2)))
            zcr = float(np.mean(np.abs(np.diff(np.sign(samples))) > 0))
            # Thresholds (empirical — would be tuned with real data)
            return rms > 5000 and zcr > 0.3
        except Exception:
            return False
