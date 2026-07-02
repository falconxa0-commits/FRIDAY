import io
import logging
import asyncio
from typing import Optional

from config.settings import ELEVENLABS_API_KEY

logger = logging.getLogger(__name__)


class FridaySpeaker:
    """Text-to-speech speaker with ElevenLabs (primary) and pyttsx3 (fallback).

    Features:
    - ElevenLabs streaming synthesis via ``generate()`` API.
    - pyttsx3 local synthesis when ElevenLabs is unavailable.
    - Safe interrupt handling that copes with ``self.engine`` being None.
    """

    def __init__(self, use_elevenlabs: bool = True) -> None:
        self.use_elevenlabs: bool = use_elevenlabs
        self.client = None
        self.engine = None
        self.interrupt_signal: bool = False

        # ElevenLabs client
        if use_elevenlabs and ELEVENLABS_API_KEY:
            try:
                from elevenlabs.client import ElevenLabs
                self.client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
                logger.info("ElevenLabs client initialized.")
            except Exception as exc:
                logger.warning(f"ElevenLabs init failed: {exc}")
                self.use_elevenlabs = False
        else:
            self.use_elevenlabs = False

        # pyttsx3 fallback (non-fatal)
        try:
            import pyttsx3
            self.engine = pyttsx3.init()
            logger.info("pyttsx3 engine initialized.")
        except Exception as exc:
            logger.warning(f"pyttsx3 init failed (non-fatal): {exc}")
            self.engine = None

    # ------------------------------------------------------------------
    # Interrupt
    # ------------------------------------------------------------------

    def interrupt(self) -> None:
        """Signal the speaker to stop current playback."""
        self.interrupt_signal = True
        if self.engine is not None:
            try:
                if self.engine.isBusy():
                    self.engine.stop()
            except Exception as exc:
                logger.debug(f"Interrupt error on pyttsx3: {exc}")

    # ------------------------------------------------------------------
    # Speak
    # ------------------------------------------------------------------

    def speak(self, text: str) -> None:
        """Speak *text* aloud.  Prints to console regardless of TTS availability."""
        self.interrupt_signal = False
        print(f"Friday: {text}")

        if self.interrupt_signal:
            return

        # Try ElevenLabs first
        if self.use_elevenlabs and self.client:
            try:
                self._speak_elevenlabs(text)
                return
            except Exception as exc:
                logger.warning(f"ElevenLabs TTS failed, falling back to pyttsx3: {exc}")

        # Fallback: pyttsx3
        if self.engine is not None:
            try:
                self._speak_pyttsx3(text)
                return
            except Exception as exc:
                logger.warning(f"pyttsx3 TTS failed: {exc}")

        # Last resort: already printed above

    # ------------------------------------------------------------------
    # ElevenLabs streaming
    # ------------------------------------------------------------------

    def _speak_elevenlabs(self, text: str) -> None:
        """Stream audio from ElevenLabs using the ``generate`` API.

        The ``elevenlabs`` package provides ``generate()`` which yields audio
        chunks when ``stream=True``.  We collect them into a WAV buffer and
        play via ``pyaudio`` (if available) or write to a temp file.
        """
        from elevenlabs import generate

        audio_stream = generate(
            text=text,
            voice="Rachel",
            model="eleven_monolingual_v1",
            stream=True,
        )

        # Attempt playback via pyaudio
        try:
            import pyaudio

            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=22050,
                output=True,
            )

            for chunk in audio_stream:
                if self.interrupt_signal:
                    logger.info("[ElevenLabs Playback Interrupted]")
                    break
                if chunk:
                    stream.write(chunk)

            stream.stop_stream()
            stream.close()
            p.terminate()

        except ImportError:
            # pyaudio not available — just consume the stream silently
            for chunk in audio_stream:
                if self.interrupt_signal:
                    break
            logger.debug("pyaudio unavailable; ElevenLabs audio was not played aloud.")

    # ------------------------------------------------------------------
    # pyttsx3 local synthesis
    # ------------------------------------------------------------------

    def _speak_pyttsx3(self, text: str) -> None:
        """Speak using the local pyttsx3 engine with chunked interrupt checks."""
        words = text.split()
        for i in range(0, len(words), 5):
            if self.interrupt_signal:
                print("[Playback Interrupted]")
                break
            chunk = " ".join(words[i : i + 5])
            self.engine.say(chunk)
            self.engine.runAndWait()

    # ------------------------------------------------------------------
    # Async wrapper
    # ------------------------------------------------------------------

    async def speak_async(self, text: str) -> None:
        """Async wrapper around ``speak`` for use in async contexts."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.speak, text)
