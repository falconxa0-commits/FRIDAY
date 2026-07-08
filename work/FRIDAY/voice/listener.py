import asyncio
import io
import logging
import wave
from typing import Optional

try:
    import pyaudio
    _PYAUDIO_AVAILABLE = True
except ImportError:
    pyaudio = None
    _PYAUDIO_AVAILABLE = False

logger = logging.getLogger(__name__)

# Audio configuration
CHUNK_SIZE = 1024
CHANNELS = 1
RATE = 16000
FORMAT = None  # set after pyaudio init

if _PYAUDIO_AVAILABLE:
    FORMAT = pyaudio.paInt16
VAD_THRESHOLD = 500  # RMS threshold for voice activity detection


class FridayListener:
    """Captures audio from the microphone and provides voice-activity detection.

    Features:
    - ``record_audio()`` — record a fixed-duration WAV file.
    - ``listen_and_interrupt()`` — continuous VAD loop that signals the
      speaker to stop when voice is detected (barge-in).
    - ``stream_audio()`` — async generator yielding raw audio chunks for
      real-time processing (e.g., Whisper streaming).
    """

    def __init__(self, speaker=None) -> None:
        self.speaker = speaker  # Link to FridaySpeaker for barge-in
        self.pa = None
        self._listening = False
        if not _PYAUDIO_AVAILABLE:
            logger.warning("pyaudio not installed — voice input unavailable.")
            return
        try:
            self.pa = pyaudio.PyAudio()
            logger.info("PyAudio initialized.")
        except Exception as exc:
            logger.warning(f"PyAudio init failed: {exc}. Voice input unavailable.")

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_audio(
        self,
        filename: str = "temp_input.wav",
        duration: int = 5,
    ) -> Optional[str]:
        """Record *duration* seconds of audio and save to *filename*.

        Args:
            filename: Path to write the WAV file.
            duration: Recording length in seconds.

        Returns:
            The filename on success, or None on failure.
        """
        if self.pa is None:
            logger.warning("record_audio: PyAudio not available.")
            return None

        try:
            stream = self.pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
            logger.info(f"Recording {duration}s of audio…")

            frames = []
            for _ in range(0, int(RATE / CHUNK_SIZE * duration)):
                data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                frames.append(data)

            stream.stop_stream()
            stream.close()

            # Write WAV file
            wf = wave.open(filename, "wb")
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(self.pa.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b"".join(frames))
            wf.close()

            logger.info(f"Audio saved to {filename}")
            return filename

        except Exception as exc:
            logger.error(f"record_audio error: {exc}")
            return None

    # ------------------------------------------------------------------
    # Voice Activity Detection & barge-in
    # ------------------------------------------------------------------

    async def listen_and_interrupt(self) -> None:
        """Continuously listen and signal the speaker to stop if voice is detected.

        Uses a simple RMS-based VAD: if the volume exceeds
        ``VAD_THRESHOLD``, the speaker is interrupted.
        """
        if self.pa is None:
            logger.warning("listen_and_interrupt: PyAudio not available.")
            return

        self._listening = True
        logger.info("VAD listener started.")

        try:
            stream = self.pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )

            while self._listening:
                try:
                    data = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: stream.read(CHUNK_SIZE, exception_on_overflow=False),
                    )
                except Exception as exc:
                    logger.debug(f"VAD read error: {exc}")
                    continue

                # Compute RMS
                rms = self._compute_rms(data)
                if rms > VAD_THRESHOLD:
                    logger.debug(f"Voice detected (RMS={rms:.0f}), interrupting speaker.")
                    if self.speaker is not None:
                        self.speaker.interrupt()

                # Small sleep to avoid busy-looping
                await asyncio.sleep(0.05)

        except Exception as exc:
            logger.error(f"listen_and_interrupt error: {exc}")
        finally:
            self._listening = False

    def stop_listening(self) -> None:
        """Stop the VAD listener loop."""
        self._listening = False
        logger.info("VAD listener stopped.")

    # ------------------------------------------------------------------
    # Streaming audio chunks
    # ------------------------------------------------------------------

    async def stream_audio(self, chunk_duration_ms: int = 100):
        """Async generator yielding raw audio chunks for real-time processing.

        Args:
            chunk_duration_ms: Duration of each yielded chunk in milliseconds.

        Yields:
            Raw PCM audio bytes.
        """
        if self.pa is None:
            logger.warning("stream_audio: PyAudio not available.")
            return

        chunks_per_read = max(1, int(RATE * chunk_duration_ms / 1000 / CHUNK_SIZE))

        try:
            stream = self.pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )

            while self._listening:
                chunk = b""
                for _ in range(chunks_per_read):
                    data = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: stream.read(CHUNK_SIZE, exception_on_overflow=False),
                    )
                    chunk += data
                yield chunk

            stream.stop_stream()
            stream.close()

        except Exception as exc:
            logger.error(f"stream_audio error: {exc}")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_rms(data: bytes) -> float:
        """Compute the Root Mean Square of raw PCM audio data."""
        import numpy as np

        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples ** 2)))

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def __del__(self) -> None:
        if self.pa is not None:
            try:
                self.pa.terminate()
            except Exception as e:
                logger.debug(f"Non-critical error: {e}")
