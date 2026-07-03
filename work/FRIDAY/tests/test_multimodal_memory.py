"""Tests for core/multimodal_memory.py — visual memory storage + search."""
import asyncio
import os
import tempfile
import pytest
from unittest.mock import MagicMock

from core.multimodal_memory import MultiModalMemory, _cosine_similarity


def _make_test_image(path: str, label: str, color: tuple) -> None:
    """Create a real PNG image."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (400, 200), color)
    draw = ImageDraw.Draw(img)
    draw.text((20, 80), label, fill="white")
    img.save(path)


class MockEmbedder:
    """Deterministic bag-of-words embedder for testing."""
    def embed(self, text):
        vec = [0.0] * 100
        for word in text.lower().split():
            idx = hash(word) % 100
            vec[idx] += 1.0
        mag = sum(v * v for v in vec) ** 0.5
        if mag > 0:
            vec = [v / mag for v in vec]
        return vec


@pytest.fixture()
def memory():
    mock_glm = MagicMock()
    mock_glm.available.return_value = False
    return MultiModalMemory(glm_brain=mock_glm, embedder=MockEmbedder())


class TestVisualMemoryStorage:
    """Test storing visual memories."""

    @pytest.mark.asyncio
    async def test_store_visual_creates_memory(self, memory, tmp_path):
        img_path = str(tmp_path / "test.png")
        _make_test_image(img_path, "Error screen", (220, 50, 50))

        mem = await memory.store_visual(img_path, "Error in main.py")
        assert mem["description"] == "Error in main.py"
        assert mem["image_path"] == img_path
        assert mem["embedding"] is not None
        assert memory.count() == 1

    @pytest.mark.asyncio
    async def test_store_visual_raises_on_missing_file(self, memory):
        with pytest.raises(FileNotFoundError):
            await memory.store_visual("/nonexistent/image.png", "test")

    @pytest.mark.asyncio
    async def test_store_visual_records_context(self, memory, tmp_path):
        img_path = str(tmp_path / "test.png")
        _make_test_image(img_path, "Code", (50, 50, 80))

        mem = await memory.store_visual(img_path, "Code editor", context={"app": "vscode"})
        assert mem["context"]["app"] == "vscode"


class TestVisualMemorySearch:
    """Test semantic search."""

    @pytest.mark.asyncio
    async def test_search_returns_relevant_result(self, memory, tmp_path):
        # Store 3 images with different descriptions
        for name, desc, color in [
            ("error.png", "Error traceback in python code", (220, 50, 50)),
            ("code.png", "Python code function definition", (50, 50, 80)),
            ("weather.png", "Weather forecast Lagos temperature", (50, 150, 220)),
        ]:
            p = str(tmp_path / name)
            _make_test_image(p, desc, color)
            await memory.store_visual(p, desc)

        # Search for "error" — should return the error image first
        results = await memory.search_visual("error traceback")
        assert len(results) > 0
        top_desc = results[0]["memory"]["description"]
        assert "error" in top_desc.lower() or "traceback" in top_desc.lower()

    @pytest.mark.asyncio
    async def test_different_queries_return_different_tops(self, memory, tmp_path):
        for name, desc, color in [
            ("error.png", "Error traceback python code", (220, 50, 50)),
            ("weather.png", "Weather forecast Lagos temperature", (50, 150, 220)),
        ]:
            p = str(tmp_path / name)
            _make_test_image(p, desc, color)
            await memory.store_visual(p, desc)

        r_error = await memory.search_visual("error traceback")
        r_weather = await memory.search_visual("weather lagos")
        top_error = r_error[0]["memory"]["description"] if r_error else ""
        top_weather = r_weather[0]["memory"]["description"] if r_weather else ""
        assert top_error != top_weather

    @pytest.mark.asyncio
    async def test_empty_memory_returns_empty(self, memory):
        results = await memory.search_visual("anything")
        assert results == []


class TestCosineSimilarity:
    """Test the cosine similarity helper."""

    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert _cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        v1 = [1.0, 0.0]
        v2 = [0.0, 1.0]
        assert _cosine_similarity(v1, v2) == pytest.approx(0.0)

    def test_empty_vectors(self):
        assert _cosine_similarity([], []) == 0.0

    def test_different_lengths(self):
        assert _cosine_similarity([1.0, 2.0], [1.0]) == 0.0
