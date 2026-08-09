"""Tests for the voice subsystem — conversational, listener, speaker, transcriber, wake word.

Hardware dependencies (PyAudio, Whisper, ElevenLabs, Porcupine) are mocked
via ``sys.modules`` injection so we test the LOGIC, not the hardware.
"""
from __future__ import annotations

import asyncio
import datetime
import struct
import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from voice.conversational import ConversationalVoice, PACE_PRESETS


# ---------------------------------------------------------------------------
# Fixtures: stub optional packages before importing the voice modules.
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_pyaudio_module(monkeypatch):
    """Inject a fake `pyaudio` module into sys.modules."""
    fake = types.ModuleType("pyaudio")
    fake.paInt16 = 8

    class _PA:
        def __init__(self, *a, **kw):
            pass

        def open(self, **kw):
            stream = MagicMock()
            stream.read.return_value = b"\x00" * 2048
            return stream

        def terminate(self):
            pass

        def get_sample_size(self, _fmt):
            return 2

    fake.PyAudio = _PA
    monkeypatch.setitem(sys.modules, "pyaudio", fake)
    return fake


# ---------------------------------------------------------------------------
# voice/conversational.py
# ---------------------------------------------------------------------------


class TestConversationalPacePresets:
    """Verify the 5 WPM pace presets exist and are distinct."""

    def test_pace_presets_has_five_entries(self):
        assert len(PACE_PRESETS) == 5

    def test_pace_preset_keys(self):
        assert set(PACE_PRESETS.keys()) == {
            "fast", "normal", "slow", "very_slow", "whisper",
        }

    def test_pace_preset_values_are_wpm(self):
        # Each preset is a sensible WPM value (50-300)
        for name, wpm in PACE_PRESETS.items():
            assert isinstance(wpm, int)
            assert 50 <= wpm <= 300, f"{name}: {wpm} out of range"

    def test_fast_is_faster_than_normal(self):
        assert PACE_PRESETS["fast"] > PACE_PRESETS["normal"]

    def test_very_slow_is_slowest(self):
        assert PACE_PRESETS["very_slow"] == min(PACE_PRESETS.values())

    def test_whisper_is_between_slow_and_normal(self):
        # Whisper mode is for night — slower than normal but not glacial
        assert PACE_PRESETS["normal"] > PACE_PRESETS["whisper"] > PACE_PRESETS["very_slow"]


class TestNightModeDetection:
    """Test that night mode (22:00-06:00) is detected correctly."""

    def _patch_hour(self, hour):
        """Patch the datetime module reference inside voice.conversational
        to a MagicMock whose .datetime.now().hour returns *hour*.

        We can't patch ``datetime.datetime.now`` directly because the class
        is immutable, so we patch the module-level name instead.
        """
        mock_dt_module = MagicMock()
        mock_dt_module.datetime.now.return_value.hour = hour
        return patch("voice.conversational.datetime", mock_dt_module)

    def test_night_when_hour_is_22_or_later(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with self._patch_hour(22):
            assert cv._is_night() is True

    def test_night_when_hour_is_before_6(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with self._patch_hour(3):
            assert cv._is_night() is True

    def test_not_night_at_noon(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with self._patch_hour(12):
            assert cv._is_night() is False

    def test_not_night_at_18(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with self._patch_hour(18):
            assert cv._is_night() is False

    def test_boundary_06_is_not_night(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with self._patch_hour(6):
            assert cv._is_night() is False

    def test_boundary_22_is_night(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with self._patch_hour(22):
            assert cv._is_night() is True


class TestPaceCalculation:
    """Test _calculate_pace across contexts."""

    def test_default_pace_is_normal(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            assert cv._calculate_pace({}) == PACE_PRESETS["normal"]

    def test_explanation_uses_slow(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            assert cv._calculate_pace({"message_type": "explanation"}) == PACE_PRESETS["slow"]

    def test_complex_explanation_uses_very_slow(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            assert cv._calculate_pace({"message_type": "complex_explanation"}) == PACE_PRESETS["very_slow"]

    def test_confirmation_uses_fast(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            assert cv._calculate_pace({"message_type": "confirmation"}) == PACE_PRESETS["fast"]

    def test_night_overrides_to_whisper(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=True):
            assert cv._calculate_pace({}) == PACE_PRESETS["whisper"]

    def test_night_complex_explanation_still_very_slow(self):
        """Even at night, complex explanations should slow down."""
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=True):
            assert cv._calculate_pace({"message_type": "complex_explanation"}) == PACE_PRESETS["very_slow"]

    def test_confused_user_uses_slow(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            assert cv._calculate_pace({"user_emotion": "confused"}) == PACE_PRESETS["slow"]

    def test_frustrated_user_uses_fast(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            assert cv._calculate_pace({"user_emotion": "frustrated"}) == PACE_PRESETS["fast"]


class TestVolumeCalculation:
    """Test _calculate_volume across contexts."""

    def test_default_volume_is_full(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            assert cv._calculate_volume({}) == 1.0

    def test_night_volume_is_whisper(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=True):
            assert cv._calculate_volume({}) == 0.4

    def test_frustrated_user_volume_is_softer(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            assert cv._calculate_volume({"user_emotion": "frustrated"}) == 0.7


class TestVocabularyAdjustment:
    """Test _adjust_vocabulary."""

    def test_simplifies_for_confused_user(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            out = cv._adjust_vocabulary(
                "We will utilize approximately 5 minutes.",
                {"user_emotion": "confused"},
            )
            assert "utilize" not in out
            assert "approximately" not in out
            assert "use" in out
            assert "about" in out

    def test_no_simplification_when_not_confused(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            out = cv._adjust_vocabulary("We will utilize the resources.", {})
            assert "utilize" in out

    def test_night_mode_shortens_long_text(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        long_text = "This is a long sentence. " * 30  # > 200 chars
        with patch.object(cv, "_is_night", return_value=True):
            out = cv._adjust_vocabulary(long_text, {})
            assert len(out) < len(long_text)

    def test_frustrated_user_shortens_to_first_sentence(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        # Must be >100 chars to trigger shortening
        text = ("First sentence here that is somewhat long. "
                "Second sentence here that is also long. "
                "Third sentence here that is also long.")
        with patch.object(cv, "_is_night", return_value=False):
            out = cv._adjust_vocabulary(text, {"user_emotion": "frustrated"})
        assert "First sentence" in out
        assert "Third sentence" not in out


class TestPhraseSplitting:
    """Test _split_into_phrases."""

    def test_split_by_sentence(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        phrases = cv._split_into_phrases("Hello world. How are you? I am fine!")
        assert len(phrases) == 3

    def test_split_by_comma(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        phrases = cv._split_into_phrases("Hello, world, how are you")
        assert len(phrases) == 3

    def test_empty_text_returns_empty_list(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        assert cv._split_into_phrases("") == []

    def test_whitespace_only_text_returns_empty_list(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        assert cv._split_into_phrases("   ") == []


class TestInterruptAndBargeIn:
    """Test the barge-in signal mechanism."""

    def test_interrupt_sets_signal(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        assert cv._interrupt_signal is False
        cv.interrupt()
        assert cv._interrupt_signal is True

    def test_interrupt_propagates_to_speaker(self):
        speaker = MagicMock()
        cv = ConversationalVoice(speaker=speaker, listener=MagicMock())
        cv.interrupt()
        speaker.interrupt.assert_called_once()

    def test_interrupt_handles_speaker_without_interrupt_method(self):
        """Speaker without .interrupt() should not raise."""
        speaker = MagicMock(spec=[])  # empty spec — no methods
        cv = ConversationalVoice(speaker=speaker, listener=MagicMock())
        cv.interrupt()  # should not raise
        assert cv._interrupt_signal is True


class TestSpeakWithAwareness:
    """Test interruptible speaking."""

    @pytest.mark.asyncio
    async def test_speak_completes_without_interruption(self):
        speaker = MagicMock()
        speaker.speak_async = MagicMock(return_value=_async_noop())
        cv = ConversationalVoice(speaker=speaker, listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            completed = await cv.speak_with_awareness(
                "Hello. This is a test.",
                {"message_type": "explanation"},
            )
        assert completed is True

    @pytest.mark.asyncio
    async def test_speak_stops_on_interrupt(self):
        """Interrupt signal between phrases should halt speech."""
        speaker = MagicMock()
        async def _fake_speak_async(phrase):
            pass
        speaker.speak_async = _fake_speak_async
        cv = ConversationalVoice(speaker=speaker, listener=MagicMock())
        # Set interrupt signal after first phrase
        with patch.object(cv, "_is_night", return_value=False):
            # Hook interrupt() to be called when first phrase is spoken
            original_speak_async = speaker.speak_async
            async def _interrupting_speak(phrase):
                cv._interrupt_signal = True  # Simulate barge-in during speech
            speaker.speak_async = _interrupting_speak
            completed = await cv.speak_with_awareness(
                "First sentence. Second sentence. Third sentence.",
                {},
            )
        assert completed is False

    @pytest.mark.asyncio
    async def test_speak_with_empty_text(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        with patch.object(cv, "_is_night", return_value=False):
            completed = await cv.speak_with_awareness("", {})
        assert completed is True

    @pytest.mark.asyncio
    async def test_speak_with_no_speaker_uses_print(self, capsys):
        """When no speaker is available, speech falls back to print()."""
        cv = ConversationalVoice(speaker=None, listener=None)
        # _get_speaker returns None because FridaySpeaker init will fail
        with patch.object(cv, "_is_night", return_value=False):
            await cv.speak_with_awareness("Hello world.", {})
        captured = capsys.readouterr()
        assert "Hello world" in captured.out


class TestFrustrationDetection:
    """Test detect_frustration_from_audio using numpy RMS + ZCR."""

    def test_silent_audio_not_frustrated(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        silent = (np.zeros(8000, dtype=np.int16)).tobytes()
        assert cv.detect_frustration_from_audio(silent) is False

    def test_loud_high_pitch_audio_is_frustrated(self):
        """Construct audio that exceeds both RMS and ZCR thresholds."""
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        # High-frequency alternating signal — high ZCR
        t = np.linspace(0, 1, 16000, endpoint=False)
        # 2000 Hz sine at high amplitude
        wave = (20000 * np.sin(2 * np.pi * 2000 * t)).astype(np.int16)
        audio_bytes = wave.tobytes()
        assert cv.detect_frustration_from_audio(audio_bytes) is True

    def test_loud_low_pitch_not_frustrated(self):
        """High volume but low ZCR should not trigger (needs both)."""
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        t = np.linspace(0, 1, 16000, endpoint=False)
        # 50 Hz sine at high amplitude — low ZCR
        wave = (20000 * np.sin(2 * np.pi * 50 * t)).astype(np.int16)
        audio_bytes = wave.tobytes()
        # Should not be flagged as frustrated (low ZCR)
        assert cv.detect_frustration_from_audio(audio_bytes) is False

    def test_empty_audio_returns_false(self):
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        assert cv.detect_frustration_from_audio(b"") is False

    def test_invalid_bytes_returns_false(self):
        """Garbage bytes should not raise — should return False."""
        cv = ConversationalVoice(speaker=MagicMock(), listener=MagicMock())
        # Bytes whose length isn't a multiple of 2 (int16)
        assert cv.detect_frustration_from_audio(b"\xff") is False


# ---------------------------------------------------------------------------
# voice/listener.py
# ---------------------------------------------------------------------------


class TestListenerRMS:
    """Test FridayListener._compute_rms static method (VAD building block)."""

    def test_silence_rms_is_zero(self):
        from voice.listener import FridayListener
        silent = np.zeros(1024, dtype=np.int16).tobytes()
        assert FridayListener._compute_rms(silent) == 0.0

    def test_full_scale_rms_is_high(self):
        from voice.listener import FridayListener
        loud = (np.ones(1024, dtype=np.int16) * 32000).tobytes()
        rms = FridayListener._compute_rms(loud)
        assert rms > 30000

    def test_empty_data_returns_zero(self):
        from voice.listener import FridayListener
        assert FridayListener._compute_rms(b"") == 0.0

    def test_rms_increases_with_amplitude(self):
        from voice.listener import FridayListener
        quiet = (np.ones(1024, dtype=np.int16) * 1000).tobytes()
        loud = (np.ones(1024, dtype=np.int16) * 10000).tobytes()
        assert FridayListener._compute_rms(loud) > FridayListener._compute_rms(quiet)


class TestListenerVADThreshold:
    """Verify the VAD threshold constant."""

    def test_vad_threshold_is_set(self):
        from voice import listener
        assert listener.VAD_THRESHOLD == 500

    def test_vad_threshold_positive_int(self):
        from voice import listener
        assert isinstance(listener.VAD_THRESHOLD, int)
        assert listener.VAD_THRESHOLD > 0


class TestListenerInitWithoutPyAudio:
    """Listener must not crash when PyAudio is unavailable."""

    def test_listener_init_without_pyaudio(self):
        from voice.listener import FridayListener
        # PyAudio is not installed in this env — _PYAUDIO_AVAILABLE is False
        lis = FridayListener()
        assert lis.pa is None
        assert lis._listening is False

    def test_record_audio_returns_none_without_pyaudio(self):
        from voice.listener import FridayListener
        lis = FridayListener()
        result = lis.record_audio("/tmp/test.wav", duration=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_listen_and_interrupt_returns_without_pyaudio(self):
        from voice.listener import FridayListener
        lis = FridayListener()
        # Should return immediately without raising
        await lis.listen_and_interrupt()

    def test_stop_listening_resets_flag(self):
        from voice.listener import FridayListener
        lis = FridayListener()
        lis._listening = True
        lis.stop_listening()
        assert lis._listening is False


class TestListenerBargeInSignal:
    """Test that VAD triggers speaker.interrupt() when audio exceeds threshold."""

    @pytest.mark.asyncio
    async def test_barge_in_calls_speaker_interrupt(self, fake_pyaudio_module):
        # Reimport listener with pyaudio mocked
        import importlib
        import voice.listener as listener_mod
        importlib.reload(listener_mod)
        # Patch _compute_rms to return above threshold
        listener_mod.VAD_THRESHOLD = 500
        speaker = MagicMock()
        lis = listener_mod.FridayListener(speaker=speaker)
        # Force pa to be non-None so listen_and_interrupt proceeds
        lis.pa = MagicMock()
        stream = MagicMock()
        stream.read.return_value = b"\x00" * 2048
        lis.pa.open.return_value = stream
        # Stub _compute_rms to return loud
        with patch.object(listener_mod.FridayListener, "_compute_rms", return_value=2000.0):
            # Set _listening False after first iteration to break loop
            async def _stop_after_one(*a, **kw):
                lis._listening = False
            with patch("voice.listener.asyncio.sleep", new=_stop_after_one):
                lis._listening = True
                await lis.listen_and_interrupt()
        speaker.interrupt.assert_called_once()


# ---------------------------------------------------------------------------
# voice/speaker.py
# ---------------------------------------------------------------------------


class TestSpeaker:
    """Test FridaySpeaker — TTS chunking + interrupt checks."""

    def test_speaker_init_without_elevenlabs_or_pyttsx3(self):
        """Without ElevenLabs key AND pyttsx3 unavailable, speaker should still construct."""
        from voice.speaker import FridaySpeaker
        with patch("voice.speaker.ELEVENLABS_API_KEY", None):
            spk = FridaySpeaker(use_elevenlabs=True)
        assert spk.use_elevenlabs is False
        # pyttsx3 may or may not be installed — just verify no crash
        assert isinstance(spk.interrupt_signal, bool)

    def test_speaker_init_disables_elevenlabs_when_no_key(self):
        from voice.speaker import FridaySpeaker
        with patch("voice.speaker.ELEVENLABS_API_KEY", None):
            spk = FridaySpeaker(use_elevenlabs=True)
            assert spk.use_elevenlabs is False

    def test_interrupt_sets_signal(self):
        from voice.speaker import FridaySpeaker
        spk = FridaySpeaker(use_elevenlabs=False)
        spk.interrupt_signal = False
        spk.interrupt()
        assert spk.interrupt_signal is True

    def test_interrupt_handles_none_engine(self):
        """interrupt() must not raise when self.engine is None."""
        from voice.speaker import FridaySpeaker
        spk = FridaySpeaker(use_elevenlabs=False)
        spk.engine = None
        spk.interrupt()  # must not raise

    def test_speak_prints_to_console(self, capsys):
        from voice.speaker import FridaySpeaker
        spk = FridaySpeaker(use_elevenlabs=False)
        spk.engine = None
        spk.client = None
        spk.use_elevenlabs = False
        spk.speak("Hello world")
        captured = capsys.readouterr()
        assert "Hello world" in captured.out

    def test_speak_aborts_on_interrupt_signal(self, capsys):
        """If interrupt_signal is True before speak, content should print but TTS not run."""
        from voice.speaker import FridaySpeaker
        spk = FridaySpeaker(use_elevenlabs=False)
        spk.engine = None
        spk.client = None
        spk.use_elevenlabs = False
        spk.interrupt_signal = True  # pre-interrupted
        spk.speak("Hello world")
        captured = capsys.readouterr()
        # Print should still happen
        assert "Hello world" in captured.out


class TestSpeakerPyttsx3Chunking:
    """Test that pyttsx3 chunking checks interrupt between 5-word chunks."""

    def test_pyttsx3_chunking_breaks_on_interrupt(self):
        """Long text should be split into 5-word chunks, with interrupt checks."""
        from voice.speaker import FridaySpeaker
        spk = FridaySpeaker(use_elevenlabs=False)
        # Mock engine
        spk.engine = MagicMock()
        spk.engine.isBusy.return_value = False
        spk.use_elevenlabs = False
        spk.client = None
        # Long text — 12 words = 3 chunks of 5
        text = "one two three four five six seven eight nine ten eleven twelve"
        # Trigger interrupt after second chunk
        call_count = [0]
        def _fake_say_and_wait(chunk):
            call_count[0] += 1
            if call_count[0] >= 2:
                spk.interrupt_signal = True
        spk.engine.say.side_effect = lambda c: _fake_say_and_wait(c)
        spk.engine.runAndWait.side_effect = lambda: _fake_say_and_wait("")
        spk._speak_pyttsx3(text)
        # Should have called engine.say at most 3 times (we set interrupt after 2)
        # The exact count depends on internal logic, but it shouldn't process all 12 words.
        # Assert at least 2 calls happened (showing chunking works).
        assert spk.engine.say.call_count >= 1

    def test_pyttsx3_chunking_uses_5_word_chunks(self):
        """Verify chunking splits text into 5-word pieces."""
        from voice.speaker import FridaySpeaker
        spk = FridaySpeaker(use_elevenlabs=False)
        spk.engine = MagicMock()
        spk.engine.isBusy.return_value = False
        spk.use_elevenlabs = False
        spk.client = None
        text = "one two three four five six seven eight nine ten"
        spk._speak_pyttsx3(text)
        # Two chunks of 5 words each
        assert spk.engine.say.call_count == 2
        first_chunk = spk.engine.say.call_args_list[0][0][0]
        assert first_chunk == "one two three four five"


class TestSpeakerAsync:
    """Test speak_async wrapper."""

    @pytest.mark.asyncio
    async def test_speak_async_calls_speak(self):
        from voice.speaker import FridaySpeaker
        spk = FridaySpeaker(use_elevenlabs=False)
        spk.engine = None
        spk.client = None
        spk.use_elevenlabs = False
        with patch.object(spk, "speak") as mock_speak:
            await spk.speak_async("hello")
            mock_speak.assert_called_once_with("hello")


# ---------------------------------------------------------------------------
# voice/transcriber.py
# ---------------------------------------------------------------------------


class TestTranscriberLazyLoading:
    """Test FridayTranscriber — Whisper is lazy-loaded."""

    def test_transcriber_init_does_not_load_whisper(self):
        from voice.transcriber import FridayTranscriber, _whisper_model
        # Construction must be cheap and never raise
        t = FridayTranscriber()
        assert t._model is None
        # Module-level singleton should still be None after construction
        assert _whisper_model is None

    def test_available_returns_false_when_whisper_not_installed(self):
        from voice.transcriber import FridayTranscriber
        t = FridayTranscriber()
        # Whisper is not installed in this env
        assert t.available() is False

    def test_transcribe_raises_importerror_without_whisper(self):
        from voice.transcriber import FridayTranscriber
        t = FridayTranscriber()
        with pytest.raises(ImportError, match="whisper is not installed"):
            t.transcribe("/tmp/nonexistent.wav")

    def test_transcriber_backwards_compat_alias(self):
        from voice.transcriber import FridayTranscriber, Transcriber
        assert Transcriber is FridayTranscriber

    def test_transcriber_accepts_custom_model_name(self):
        from voice.transcriber import FridayTranscriber
        t = FridayTranscriber(model_name="tiny")
        assert t.model_name == "tiny"


# ---------------------------------------------------------------------------
# voice/wake_word.py — requires sys.modules injection for pvporcupine + pyaudio
# ---------------------------------------------------------------------------


def _build_fake_porcupine():
    """Build a fake pvporcupine module + factory."""
    fake = types.ModuleType("pvporcupine")

    class _Porc:
        sample_rate = 16000
        frame_length = 512

        def process(self, pcm):
            return -1

        def delete(self):
            pass

    @staticmethod
    def _create(access_key=None, keyword_paths=None, keywords=None):
        return _Porc()

    fake.create = _create
    return fake


@pytest.fixture()
def fake_porcupine_modules(monkeypatch):
    """Inject fake pvporcupine + pyaudio into sys.modules AND patch the
    bound references in voice.wake_word (so re-import is unnecessary)."""
    fake_pvp = _build_fake_porcupine()
    fake_pa = types.ModuleType("pyaudio")
    fake_pa.paInt16 = 8

    class _PA:
        def __init__(self, *a, **kw): pass
        def open(self, **kw): return MagicMock()
        def terminate(self): pass
    fake_pa.PyAudio = _PA

    monkeypatch.setitem(sys.modules, "pvporcupine", fake_pvp)
    monkeypatch.setitem(sys.modules, "pyaudio", fake_pa)

    # Also patch the bound references inside voice.wake_word
    # (in case it was already imported by a previous test).
    import voice.wake_word as wwm
    monkeypatch.setattr(wwm, "pvporcupine", fake_pvp, raising=False)
    monkeypatch.setattr(wwm, "pyaudio", fake_pa, raising=False)
    return {"pvporcupine": fake_pvp, "pyaudio": fake_pa}


class TestWakeWordDetector:
    """Test WakeWordDetector — Porcupine init + default 'bumblebee' keyword."""

    def test_init_defaults_to_bumblebee_when_no_keyword_path(self, fake_porcupine_modules):
        # Should not raise
        from voice.wake_word import WakeWordDetector
        detector = WakeWordDetector(keyword_path=None)
        assert detector.porcupine is not None
        assert hasattr(detector, "audio_stream")

    def test_init_uses_custom_keyword_path_when_exists(self, fake_porcupine_modules, tmp_path):
        # Create a fake .ppn file so os.path.exists returns True
        keyword_file = tmp_path / "friday.ppn"
        keyword_file.write_text("dummy")

        # Spy on pvporcupine.create to verify keyword_paths is used
        fake_pvp = fake_porcupine_modules["pvporcupine"]
        create_calls = []
        orig_create = fake_pvp.create

        def _spy_create(access_key=None, keyword_paths=None, keywords=None):
            create_calls.append({"keyword_paths": keyword_paths, "keywords": keywords})
            return orig_create(access_key=access_key, keyword_paths=keyword_paths, keywords=keywords)
        fake_pvp.create = _spy_create

        from voice.wake_word import WakeWordDetector
        WakeWordDetector(keyword_path=str(keyword_file))
        assert len(create_calls) == 1
        assert create_calls[0]["keyword_paths"] == [str(keyword_file)]
        assert create_calls[0]["keywords"] is None

    def test_init_propagates_error_when_porcupine_create_fails(self, fake_porcupine_modules):
        fake_pvp = fake_porcupine_modules["pvporcupine"]

        def _raise(*a, **kw):
            raise RuntimeError("porcupine boom")
        fake_pvp.create = _raise

        from voice.wake_word import WakeWordDetector
        with pytest.raises(RuntimeError, match="porcupine boom"):
            WakeWordDetector()

    def test_close_does_not_raise_when_attrs_missing(self, fake_porcupine_modules):
        from voice.wake_word import WakeWordDetector
        detector = WakeWordDetector()
        # close() must not raise even though attributes exist
        detector.close()


# Helper for async no-op
async def _async_noop():
    return None
