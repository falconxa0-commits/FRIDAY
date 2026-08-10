"""Tests for the vision subsystem — screen_analyzer, screen_reader, ocr, presence.

Hardware dependencies (mss, pytesseract binary, OpenCV camera) are mocked so
we test the LOGIC, not the hardware.
"""
from __future__ import annotations

import base64
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_mss(monkeypatch):
    """Inject a fake mss module into sys.modules + patch vision.screen_reader ref."""
    fake = types.ModuleType("mss")

    class _Sct:
        def __init__(self, *a, **kw): pass

        def shot(self, output=None):
            # Write a dummy file so callers see it exists
            Path(output).write_bytes(b"\x89PNG fake")
            return output

        @property
        def monitors(self):
            # screen_reader uses self.sct.monitors[1] (second entry, after the
            # "all monitors" virtual entry at index 0)
            return [
                {"top": 0, "left": 0, "width": 1920, "height": 1080},  # virtual all
                {"top": 0, "left": 0, "width": 1920, "height": 1080},  # primary monitor
            ]

        def grab(self, monitor):
            img = MagicMock()
            img.size = (1920, 1080)
            img.bgra = b"\x00" * (1920 * 1080 * 4)
            return img

    def _mss_factory(*a, **kw):
        return _Sct()

    fake.mss = _mss_factory
    monkeypatch.setitem(sys.modules, "mss", fake)
    # Patch bound reference in screen_reader (in case already imported)
    import vision.screen_reader as sr
    monkeypatch.setattr(sr, "mss", fake, raising=False)
    return fake


# ---------------------------------------------------------------------------
# vision/screen_analyzer.py
# ---------------------------------------------------------------------------


class TestScreenAnalyzerInit:
    """Test ScreenAnalyzer init — GLM primary, OpenAI fallback."""

    def test_init_without_keys_does_not_crash(self):
        """Analyzer should construct even with no API keys configured."""
        from vision.screen_analyzer import ScreenAnalyzer
        # Should not raise regardless of env state
        analyzer = ScreenAnalyzer()
        assert analyzer is not None
        # Either both None (no keys) or one set
        assert hasattr(analyzer, "glm_brain")
        assert hasattr(analyzer, "openai_client")

    def test_init_uses_glm_when_available(self):
        from vision.screen_analyzer import ScreenAnalyzer
        with patch("vision.screen_analyzer.GLMBrain") as MockGLM:
            mock_inst = MagicMock()
            mock_inst.available.return_value = True
            MockGLM.return_value = mock_inst
            analyzer = ScreenAnalyzer()
        assert analyzer.glm_brain is mock_inst

    def test_init_falls_back_when_glm_unavailable(self):
        from vision.screen_analyzer import ScreenAnalyzer
        with patch("vision.screen_analyzer.GLMBrain") as MockGLM:
            mock_inst = MagicMock()
            mock_inst.available.return_value = False
            MockGLM.return_value = mock_inst
            with patch("vision.screen_analyzer.OPENAI_API_KEY", None):
                analyzer = ScreenAnalyzer()
        # GLM brain is set but unavailable, so glm_brain should be None
        assert analyzer.glm_brain is None


class TestExtractJsonCoords:
    """Test ScreenAnalyzer._extract_json_coords."""

    def test_extracts_pure_json(self):
        from vision.screen_analyzer import ScreenAnalyzer
        result = ScreenAnalyzer._extract_json_coords('{"x": 100, "y": 200}')
        assert result == {"x": 100, "y": 200}

    def test_extracts_json_embedded_in_text(self):
        from vision.screen_analyzer import ScreenAnalyzer
        text = 'The button is at {"x": 50, "y": 75} on the screen.'
        result = ScreenAnalyzer._extract_json_coords(text)
        assert result == {"x": 50, "y": 75}

    def test_extracts_json_with_negative_coords(self):
        from vision.screen_analyzer import ScreenAnalyzer
        result = ScreenAnalyzer._extract_json_coords('{"x": -1, "y": -1}')
        assert result == {"x": -1, "y": -1}

    def test_returns_none_for_no_json(self):
        from vision.screen_analyzer import ScreenAnalyzer
        result = ScreenAnalyzer._extract_json_coords("No JSON here at all.")
        assert result is None

    def test_returns_none_for_invalid_json(self):
        from vision.screen_analyzer import ScreenAnalyzer
        result = ScreenAnalyzer._extract_json_coords('{"x": not_a_number, "y": 10}')
        assert result is None

    def test_returns_none_for_missing_x_or_y(self):
        from vision.screen_analyzer import ScreenAnalyzer
        # No "x" or "y" key
        result = ScreenAnalyzer._extract_json_coords('{"a": 1, "b": 2}')
        assert result is None


class TestAnalyzeScreen:
    """Test ScreenAnalyzer.analyze_screen with mocked brains."""

    def test_analyze_screen_uses_glm_first(self, tmp_path):
        from vision.screen_analyzer import ScreenAnalyzer
        # Create a dummy image file
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG fake")

        analyzer = ScreenAnalyzer()
        # Inject a fake GLM brain
        analyzer.glm_brain = MagicMock()
        analyzer.glm_brain.vision_analyze.return_value = "A login screen with two fields."
        analyzer.openai_client = None  # ensure no OpenAI fallback

        result = analyzer.analyze_screen(str(img))
        assert "login screen" in result
        analyzer.glm_brain.vision_analyze.assert_called_once()

    def test_analyze_screen_falls_back_to_openai_on_glm_error(self, tmp_path):
        from vision.screen_analyzer import ScreenAnalyzer
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG fake")

        analyzer = ScreenAnalyzer()
        analyzer.glm_brain = MagicMock()
        analyzer.glm_brain.vision_analyze.return_value = "Error: GLM failed"
        analyzer.openai_client = MagicMock()
        fake_choice = MagicMock()
        fake_choice.message.content = "OpenAI says: a desktop."
        analyzer.openai_client.chat.completions.create.return_value = MagicMock(
            choices=[fake_choice]
        )

        result = analyzer.analyze_screen(str(img))
        assert "desktop" in result
        analyzer.openai_client.chat.completions.create.assert_called_once()

    def test_analyze_screen_falls_back_on_glm_exception(self, tmp_path):
        from vision.screen_analyzer import ScreenAnalyzer
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG fake")

        analyzer = ScreenAnalyzer()
        analyzer.glm_brain = MagicMock()
        analyzer.glm_brain.vision_analyze.side_effect = RuntimeError("GLM boom")
        analyzer.openai_client = MagicMock()
        fake_choice = MagicMock()
        fake_choice.message.content = "OpenAI fallback result."
        analyzer.openai_client.chat.completions.create.return_value = MagicMock(
            choices=[fake_choice]
        )

        result = analyzer.analyze_screen(str(img))
        assert "OpenAI fallback" in result

    def test_analyze_screen_no_providers_returns_not_implemented(self, tmp_path):
        from vision.screen_analyzer import ScreenAnalyzer
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG fake")

        analyzer = ScreenAnalyzer()
        analyzer.glm_brain = None
        analyzer.openai_client = None
        result = analyzer.analyze_screen(str(img))
        assert "not_implemented" in result


class TestFindElement:
    """Test ScreenAnalyzer.find_element returns JSON coords."""

    def test_find_element_returns_coords_from_glm(self, tmp_path):
        from vision.screen_analyzer import ScreenAnalyzer
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG fake")

        analyzer = ScreenAnalyzer()
        analyzer.glm_brain = MagicMock()
        analyzer.glm_brain.vision_analyze.return_value = '{"x": 320, "y": 240}'
        analyzer.openai_client = None

        coords = analyzer.find_element("login button", str(img))
        assert coords == {"x": 320, "y": 240}

    def test_find_element_returns_none_when_not_found(self, tmp_path):
        from vision.screen_analyzer import ScreenAnalyzer
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG fake")

        analyzer = ScreenAnalyzer()
        analyzer.glm_brain = MagicMock()
        analyzer.glm_brain.vision_analyze.return_value = '{"x": -1, "y": -1}'
        analyzer.openai_client = None

        coords = analyzer.find_element("nonexistent element", str(img))
        # x == -1 means not found → return None
        assert coords is None

    def test_find_element_falls_back_to_openai(self, tmp_path):
        from vision.screen_analyzer import ScreenAnalyzer
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG fake")

        analyzer = ScreenAnalyzer()
        analyzer.glm_brain = MagicMock()
        analyzer.glm_brain.vision_analyze.return_value = '{"x": -1, "y": -1}'
        analyzer.openai_client = MagicMock()
        fake_choice = MagicMock()
        fake_choice.message.content = '{"x": 640, "y": 480}'
        analyzer.openai_client.chat.completions.create.return_value = MagicMock(
            choices=[fake_choice]
        )

        coords = analyzer.find_element("submit", str(img))
        assert coords == {"x": 640, "y": 480}

    def test_find_element_returns_none_when_no_providers(self, tmp_path):
        from vision.screen_analyzer import ScreenAnalyzer
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG fake")

        analyzer = ScreenAnalyzer()
        analyzer.glm_brain = None
        analyzer.openai_client = None
        coords = analyzer.find_element("anything", str(img))
        assert coords is None


# ---------------------------------------------------------------------------
# vision/screen_reader.py
# ---------------------------------------------------------------------------


class TestScreenReader:
    """Test ScreenReader — mss screenshot capture."""

    def test_capture_screen_writes_file(self, fake_mss, tmp_path):
        from vision.screen_reader import ScreenReader
        sr = ScreenReader()
        out_path = str(tmp_path / "shot.png")
        result = sr.capture_screen(filename=out_path)
        assert result == out_path
        assert os.path.exists(out_path)

    def test_get_screen_data_returns_pil_image(self, fake_mss):
        from vision.screen_reader import ScreenReader
        from PIL import Image
        sr = ScreenReader()
        img = sr.get_screen_data()
        # PIL Image has .size attribute
        assert hasattr(img, "size")
        assert img.size == (1920, 1080)


# ---------------------------------------------------------------------------
# vision/ocr.py
# ---------------------------------------------------------------------------


class TestFridayOCR:
    """Test FridayOCR — pytesseract wrapper."""

    def test_extract_text_calls_pytesseract(self, tmp_path):
        # We can construct FridayOCR because pytesseract is installed.
        from vision.ocr import FridayOCR
        from PIL import Image
        # Create a real small image
        img_path = tmp_path / "img.png"
        Image.new("RGB", (10, 10), "white").save(img_path)

        ocr = FridayOCR()
        with patch("vision.ocr.pytesseract.image_to_string", return_value="hello world") as mock_ts:
            text = ocr.extract_text(str(img_path))
        assert text == "hello world"
        mock_ts.assert_called_once()

    def test_extract_text_passes_pil_image_to_tesseract(self, tmp_path):
        """Verify the OCR wrapper opens the image as a PIL.Image before passing to tesseract."""
        from vision.ocr import FridayOCR
        from PIL import Image
        img_path = tmp_path / "img.png"
        Image.new("RGB", (10, 10), "white").save(img_path)

        ocr = FridayOCR()
        with patch("vision.ocr.pytesseract.image_to_string", return_value="x") as mock_ts:
            ocr.extract_text(str(img_path))
            # First positional arg should be a PIL Image instance
            args, kwargs = mock_ts.call_args
            assert isinstance(args[0], Image.Image)


# ---------------------------------------------------------------------------
# vision/presence.py
# ---------------------------------------------------------------------------


class TestPresenceDetector:
    """Test PresenceDetector — OpenCV face cascade."""

    def test_get_user_attention_level_returns_not_implemented(self):
        """Per the spec, this should always return the 'Not implemented' string."""
        from vision.presence import PresenceDetector
        # Construction requires cv2 (which is installed)
        pd = PresenceDetector()
        result = pd.get_user_attention_level()
        assert isinstance(result, str)
        assert "Not implemented" in result

    def test_detect_user_returns_error_when_camera_not_accessible(self):
        from vision.presence import PresenceDetector
        pd = PresenceDetector()
        # Patch cv2.VideoCapture to return a closed camera
        with patch("vision.presence.cv2.VideoCapture") as mock_vc:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = False
            mock_vc.return_value = mock_cap
            result = pd.detect_user()
        assert result["status"] == "error"
        assert "Camera not accessible" in result["message"]

    def test_detect_user_returns_present_when_face_detected(self):
        from vision.presence import PresenceDetector
        pd = PresenceDetector()
        # Replace the real face_cascade with a mock (can't patch methods on
        # cv2.CascadeClassifier instances because they're C-extension types).
        mock_cascade = MagicMock()
        mock_cascade.detectMultiScale.return_value = [(10, 20, 30, 40)]
        pd.face_cascade = mock_cascade
        with patch("vision.presence.cv2.VideoCapture") as mock_vc, \
             patch("vision.presence.cv2.cvtColor", return_value=MagicMock()):
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = True
            mock_cap.read.return_value = (True, MagicMock())
            mock_vc.return_value = mock_cap
            result = pd.detect_user()
        assert result["status"] == "present"
        assert result["faces_detected"] == 1
        assert "timestamp" in result

    def test_detect_user_returns_away_when_no_face(self):
        from vision.presence import PresenceDetector
        pd = PresenceDetector()
        mock_cascade = MagicMock()
        mock_cascade.detectMultiScale.return_value = []
        pd.face_cascade = mock_cascade
        with patch("vision.presence.cv2.VideoCapture") as mock_vc, \
             patch("vision.presence.cv2.cvtColor", return_value=MagicMock()):
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = True
            mock_cap.read.return_value = (True, MagicMock())
            mock_vc.return_value = mock_cap
            result = pd.detect_user()
        assert result["status"] == "away"
        assert result["faces_detected"] == 0

    def test_detect_user_returns_error_on_capture_failure(self):
        from vision.presence import PresenceDetector
        pd = PresenceDetector()
        with patch("vision.presence.cv2.VideoCapture") as mock_vc:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = True
            mock_cap.read.return_value = (False, None)  # frame capture failed
            mock_vc.return_value = mock_cap
            result = pd.detect_user()
        assert result["status"] == "error"
        assert "Failed to capture" in result["message"]

    def test_detect_user_handles_exception_gracefully(self):
        from vision.presence import PresenceDetector
        pd = PresenceDetector()
        with patch("vision.presence.cv2.VideoCapture", side_effect=RuntimeError("camera boom")):
            result = pd.detect_user()
        assert result["status"] == "error"
        assert "camera boom" in result["message"]
