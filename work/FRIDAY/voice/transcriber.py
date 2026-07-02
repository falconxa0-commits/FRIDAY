"""Speech-to-text transcription via OpenAI Whisper.

Lazy-loads the Whisper model so that importing this module never fails
when Whisper (or its torch dependency) is unavailable. The model is
only loaded on first call to ``transcribe()``.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-loaded singleton
_whisper_model = None
_whisper_available: Optional[bool] = None


def _ensure_whisper():
    """Import whisper and load the base model lazily. Returns the model
    or raises ImportError if whisper isn't installed."""
    global _whisper_model, _whisper_available
    if _whisper_model is not None:
        return _whisper_model
    try:
        import whisper  # type: ignore
    except ImportError as exc:
        _whisper_available = False
        raise ImportError(
            "whisper is not installed. Install with: pip install openai-whisper"
        ) from exc
    logger.info("Loading Whisper base model (first call)…")
    _whisper_model = whisper.load_model("base")
    _whisper_available = True
    return _whisper_model


class FridayTranscriber:
    """Wrap OpenAI Whisper for speech-to-text.

    The model is loaded lazily on the first ``transcribe()`` call so
    that constructing the object is always cheap and never raises.
    """

    def __init__(self, model_name: str = "base") -> None:
        self.model_name = model_name
        self._model = None

    def available(self) -> bool:
        """Return True if Whisper can be loaded in this environment."""
        global _whisper_available
        if _whisper_available is True:
            return True
        if _whisper_available is False:
            return False
        try:
            import whisper  # noqa: F401
            return True
        except ImportError:
            _whisper_available = False
            return False

    def transcribe(self, audio_path: str) -> str:
        """Transcribe the audio file at *audio_path* to text.

        Raises:
            ImportError: if whisper is not installed.
            RuntimeError: if transcription fails for any other reason.
        """
        if self._model is None:
            self._model = _ensure_whisper()
        try:
            result = self._model.transcribe(audio_path)
            text = result.get("text", "") if isinstance(result, dict) else str(result)
            return text.strip()
        except Exception as exc:
            logger.error("Whisper transcription failed: %s", exc)
            raise RuntimeError(f"Transcription failed: {exc}") from exc


# Backwards-compatibility alias — older code imports ``Transcriber``.
Transcriber = FridayTranscriber
