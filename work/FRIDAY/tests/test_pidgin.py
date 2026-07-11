"""Tests for Pidgin detection and response generation."""
import pytest


class TestPidginDetection:
    @pytest.mark.asyncio
    async def test_english_input_gets_english_response(self):
        from core.personality import detect_input_language
        result = await detect_input_language("What is the weather today?")
        assert result == "english"

    @pytest.mark.asyncio
    async def test_pidgin_input_gets_pidgin_response(self):
        from core.personality import detect_input_language
        result = await detect_input_language("Abeg wetin be the weather today na?")
        assert result == "pidgin"

    @pytest.mark.asyncio
    async def test_pidgin_detection_requires_two_markers(self):
        from core.personality import detect_input_language
        # Only one Pidgin word — should default to English
        result = await detect_input_language("abeg what is the weather?")
        assert result == "english"

    @pytest.mark.asyncio
    async def test_yoruba_mix_detected(self):
        from core.personality import detect_input_language
        result = await detect_input_language("Ehen sha, bawo ni weather today?")
        assert result == "yoruba_mix"

    def test_language_prompt_exists(self):
        from core.personality import get_language_prompt, RESPONSE_LANGUAGES
        assert "english" in RESPONSE_LANGUAGES
        assert "pidgin" in RESPONSE_LANGUAGES
        prompt = get_language_prompt("pidgin")
        assert "abeg" in prompt.lower()
