"""Tests for the database subsystem — supabase_client, vector_store, subconscious, compression.

Tests graceful degradation when Supabase credentials are missing,
in-memory fallback path, and the regression test for permanent
degradation on Supabase error.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, AsyncMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# database/supabase_client.py — graceful degradation
# ---------------------------------------------------------------------------


class TestSupabaseClientDegradation:
    """Verify SupabaseClient degrades gracefully when creds are missing."""

    def test_init_without_credentials_sets_client_none(self, monkeypatch):
        # Clear any pre-existing creds
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        # Reload settings to pick up the cleared env
        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)
        monkeypatch.setattr(settings_mod, "SUPABASE_URL", None, raising=False)
        monkeypatch.setattr(settings_mod, "SUPABASE_KEY", None, raising=False)

        import database.supabase_client as sc_mod
        importlib.reload(sc_mod)
        client = sc_mod.SupabaseClient()
        assert client.client is None
        assert client.url is None
        assert client.key is None

    def test_insert_data_returns_none_when_client_none(self):
        from database.supabase_client import SupabaseClient
        c = SupabaseClient()
        c.client = None
        assert c.insert_data("memories", {"x": 1}) is None

    def test_query_data_returns_empty_list_when_client_none(self):
        from database.supabase_client import SupabaseClient
        c = SupabaseClient()
        c.client = None
        result = c.query_data("memories")
        assert result == []

    def test_update_data_returns_none_when_client_none(self):
        from database.supabase_client import SupabaseClient
        c = SupabaseClient()
        c.client = None
        assert c.update_data("memories", {"id": 1}, {"x": 2}) is None

    def test_delete_data_returns_none_when_client_none(self):
        from database.supabase_client import SupabaseClient
        c = SupabaseClient()
        c.client = None
        assert c.delete_data("memories", {"id": 1}) is None

    def test_get_client_returns_underlying_client(self):
        from database.supabase_client import SupabaseClient
        c = SupabaseClient()
        c.client = "fake-client"
        assert c.get_client() == "fake-client"

    def test_insert_data_returns_response_when_client_present(self):
        from database.supabase_client import SupabaseClient
        c = SupabaseClient()
        mock_inner = MagicMock()
        mock_response = MagicMock()
        mock_inner.table.return_value.insert.return_value.execute.return_value = mock_response
        c.client = mock_inner
        result = c.insert_data("memories", {"x": 1})
        assert result is mock_response
        mock_inner.table.assert_called_with("memories")

    def test_insert_data_swallows_exceptions(self):
        """A Supabase error must not raise — should return None."""
        from database.supabase_client import SupabaseClient
        c = SupabaseClient()
        mock_inner = MagicMock()
        mock_inner.table.side_effect = RuntimeError("supabase error")
        c.client = mock_inner
        result = c.insert_data("memories", {"x": 1})
        assert result is None

    def test_query_data_swallows_exceptions(self):
        from database.supabase_client import SupabaseClient
        c = SupabaseClient()
        mock_inner = MagicMock()
        mock_inner.table.side_effect = RuntimeError("supabase error")
        c.client = mock_inner
        result = c.query_data("memories")
        assert result == []

    def test_query_data_with_filters(self):
        """Verify query_data passes filters via .match()."""
        from database.supabase_client import SupabaseClient
        c = SupabaseClient()
        mock_inner = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [{"id": 1, "content": "hello"}]
        chain = mock_inner.table.return_value.select.return_value
        chain.match.return_value.range.return_value.execute.return_value = mock_response
        c.client = mock_inner
        result = c.query_data("memories", query={"user": "alice"})
        assert result == [{"id": 1, "content": "hello"}]
        chain.match.assert_called_with({"user": "alice"})


# ---------------------------------------------------------------------------
# database/vector_store.py — in-memory fallback + permanent degradation
# ---------------------------------------------------------------------------


class TestInMemoryVectorStore:
    """Test the InMemoryVectorStore fallback directly."""

    def test_empty_store_returns_empty_search(self):
        from database.vector_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        results = store.search(np.array([1.0, 0.0, 0.0]), top_k=5)
        assert results == []

    def test_add_and_search_returns_matching_content(self):
        from database.vector_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        store.add("hello world", np.array([1.0, 0.0]), {"category": "greeting"})
        store.add("goodbye world", np.array([0.0, 1.0]), {"category": "farewell"})
        results = store.search(np.array([1.0, 0.0]), top_k=2)
        assert len(results) == 2
        # Highest similarity first
        assert results[0]["content"] == "hello world"
        assert results[0]["similarity"] > results[1]["similarity"]

    def test_search_respects_top_k(self):
        from database.vector_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        for i in range(5):
            store.add(f"item {i}", np.array([float(i), 0.0]), {})
        results = store.search(np.array([0.0, 0.0]), top_k=3)
        assert len(results) == 3

    def test_search_returns_metadata(self):
        from database.vector_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        meta = {"category": "test", "tags": ["a", "b"]}
        store.add("hello", np.array([1.0]), meta)
        results = store.search(np.array([1.0]), top_k=1)
        assert results[0]["metadata"] == meta

    def test_search_with_zero_norm_does_not_raise(self):
        """Division-by-zero protection in the cosine similarity calc."""
        from database.vector_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        store.add("zero", np.array([0.0, 0.0]), {})
        # Should not raise even though norms are zero
        results = store.search(np.array([0.0, 0.0]), top_k=1)
        assert len(results) == 1


class TestVectorStoreFallback:
    """Test VectorStore uses in-memory fallback when Supabase is unavailable."""

    def test_vector_store_falls_back_to_in_memory_without_supabase(self):
        from database.vector_store import VectorStore
        vs = VectorStore()
        assert vs._use_supabase is False
        assert vs._fallback is not None

    def test_add_memory_uses_in_memory_fallback(self):
        from database.vector_store import VectorStore
        vs = VectorStore()
        vs.add_memory("hello world", {"category": "test"})
        assert len(vs._fallback.texts) == 1
        assert "hello world" in vs._fallback.texts[0]

    def test_search_uses_in_memory_fallback(self):
        from database.vector_store import VectorStore
        vs = VectorStore()
        vs.add_memory("hello world", {"category": "test"})
        results = vs.search("hello", top_k=5)
        assert len(results) >= 1
        assert any("hello" in r["content"] for r in results)

    def test_search_empty_store_returns_empty_list(self):
        from database.vector_store import VectorStore
        vs = VectorStore()
        results = vs.search("anything")
        assert results == []


class TestVectorStorePermanentDegradation:
    """REGRESSION TEST: Supabase error permanently disables Supabase path.

    Previously, every search/add would retry Supabase (slow + noisy logs).
    The fix sets self._use_supabase = False on first error and sticks with
    in-memory for the rest of the instance's lifetime.
    """

    def test_add_memory_supabase_error_disables_supabase_permanently(self):
        from database.vector_store import VectorStore
        vs = VectorStore()
        # Force Supabase path on
        vs._use_supabase = True
        # Make supabase client raise on insert
        mock_sb = MagicMock()
        mock_sb.insert_data.side_effect = RuntimeError("supabase boom")
        vs.supabase = mock_sb
        # Patch _encode to avoid real embedding API calls
        vs._embedder = MagicMock()
        vs._embedder_initialised = True
        vs._embedder.embed.return_value = np.array([1.0, 2.0, 3.0])

        # First call: tries Supabase, fails, falls back to in-memory
        vs.add_memory("first", {})
        assert vs._use_supabase is False  # permanently disabled
        # The fallback got the memory
        assert len(vs._fallback.texts) == 1

        # Second call: should NOT try Supabase again
        mock_sb.insert_data.reset_mock()
        vs.add_memory("second", {})
        mock_sb.insert_data.assert_not_called()
        assert len(vs._fallback.texts) == 2

    def test_search_supabase_error_disables_supabase_permanently(self):
        from database.vector_store import VectorStore
        vs = VectorStore()
        vs._use_supabase = True
        # Mock supabase client whose .rpc().execute() raises
        mock_sb_client = MagicMock()
        mock_sb_client.rpc.side_effect = RuntimeError("supabase rpc boom")
        mock_sb = MagicMock()
        mock_sb.client = mock_sb_client
        vs.supabase = mock_sb
        vs._embedder = MagicMock()
        vs._embedder_initialised = True
        vs._embedder.embed.return_value = np.array([1.0, 2.0, 3.0])

        # First search: tries Supabase RPC, fails, falls back
        results = vs.search("query", top_k=5)
        assert vs._use_supabase is False
        # Second search: must not call rpc again
        mock_sb_client.rpc.reset_mock()
        vs.search("query2", top_k=5)
        mock_sb_client.rpc.assert_not_called()


# ---------------------------------------------------------------------------
# database/subconscious.py — surface_patterns + get_intuition
# ---------------------------------------------------------------------------


class TestSubconsciousMind:
    """Test SubconsciousMind.surface_patterns and get_intuition."""

    def test_surface_patterns_empty_when_vector_store_none(self):
        from database.subconscious import SubconsciousMind
        sm = SubconsciousMind(vector_store=None)
        assert sm.surface_patterns("anything") == []

    def test_get_intuition_with_empty_patterns(self):
        from database.subconscious import SubconsciousMind
        sm = SubconsciousMind(vector_store=None)
        # No patterns surfaced yet
        intuition = sm.get_intuition()
        assert isinstance(intuition, str)
        assert "Neutral" in intuition or "No strong patterns" in intuition

    def test_surface_patterns_with_empty_memory_returns_empty(self):
        from database.subconscious import SubconsciousMind
        mock_vs = MagicMock()
        mock_vs.search.return_value = []
        sm = SubconsciousMind(vector_store=mock_vs)
        result = sm.surface_patterns("test context")
        assert result == []
        assert sm.active_patterns == []

    def test_surface_patterns_dominant_category(self):
        """When multiple memories share a category, it should be surfaced."""
        from database.subconscious import SubconsciousMind
        mock_vs = MagicMock()
        mock_vs.search.return_value = [
            {"content": "a", "metadata": {"category": "work"}},
            {"content": "b", "metadata": {"category": "work"}},
            {"content": "c", "metadata": {"category": "personal"}},
        ]
        sm = SubconsciousMind(vector_store=mock_vs)
        patterns = sm.surface_patterns("context")
        assert "work" in patterns  # 2x weight for category → score 4.0

    def test_surface_patterns_with_tags(self):
        """Tags should also be surfaced if frequent enough."""
        from database.subconscious import SubconsciousMind
        mock_vs = MagicMock()
        mock_vs.search.return_value = [
            {"content": "a", "metadata": {"tags": ["python", "ai"]}},
            {"content": "b", "metadata": {"tags": ["python", "web"]}},
        ]
        sm = SubconsciousMind(vector_store=mock_vs)
        patterns = sm.surface_patterns("context")
        # 'python' appears twice → score 2.0 → dominant
        assert "python" in patterns

    def test_get_intuition_high_priority(self):
        from database.subconscious import SubconsciousMind
        sm = SubconsciousMind(vector_store=None)
        sm.active_patterns = ["High Priority", "urgent"]
        intuition = sm.get_intuition()
        assert "prioritize" in intuition.lower()

    def test_get_intuition_warning(self):
        from database.subconscious import SubconsciousMind
        sm = SubconsciousMind(vector_store=None)
        sm.active_patterns = ["warning", "error"]
        intuition = sm.get_intuition()
        assert "caution" in intuition.lower()

    def test_get_intuition_creative(self):
        from database.subconscious import SubconsciousMind
        sm = SubconsciousMind(vector_store=None)
        sm.active_patterns = ["creative", "brainstorm"]
        intuition = sm.get_intuition()
        assert "creative" in intuition.lower() or "brainstorm" in intuition.lower()

    def test_get_intuition_generic_patterns(self):
        from database.subconscious import SubconsciousMind
        sm = SubconsciousMind(vector_store=None)
        sm.active_patterns = ["random_pattern"]
        sm._pattern_scores = {"random_pattern": 3.0}
        intuition = sm.get_intuition()
        assert "random_pattern" in intuition
        assert "Active patterns" in intuition

    def test_surface_patterns_handles_search_exception(self):
        from database.subconscious import SubconsciousMind
        mock_vs = MagicMock()
        mock_vs.search.side_effect = RuntimeError("search boom")
        sm = SubconsciousMind(vector_store=mock_vs)
        result = sm.surface_patterns("context")
        assert result == []

    def test_get_pattern_scores_returns_dict(self):
        from database.subconscious import SubconsciousMind
        sm = SubconsciousMind(vector_store=None)
        sm._pattern_scores = {"x": 2.0}
        assert sm.get_pattern_scores() == {"x": 2.0}


# ---------------------------------------------------------------------------
# database/compression.py — heuristic compression + brain compression
# ---------------------------------------------------------------------------


class TestMemoryCompressorHeuristic:
    """Test MemoryCompressor._heuristic_compress (no brain required)."""

    def test_heuristic_compress_extracts_keywords(self):
        from database.compression import MemoryCompressor
        memories = [
            {"content": "The python programming language is great for AI"},
            {"content": "Python is widely used in machine learning"},
            {"content": "Machine learning requires good data"},
        ]
        result = MemoryCompressor._heuristic_compress(memories)
        assert "Key themes" in result
        assert "python" in result.lower()
        assert "machine" in result.lower() or "learning" in result.lower()

    def test_heuristic_compress_empty_memories(self):
        from database.compression import MemoryCompressor
        result = MemoryCompressor._heuristic_compress([])
        assert "No significant patterns" in result

    def test_heuristic_compress_memories_with_no_words(self):
        from database.compression import MemoryCompressor
        # Only short words (< 4 chars) → no keywords extracted
        result = MemoryCompressor._heuristic_compress([
            {"content": "a b c d"},
            {"content": "1 2 3"},
        ])
        assert "No significant patterns" in result

    def test_heuristic_compress_returns_string(self):
        from database.compression import MemoryCompressor
        result = MemoryCompressor._heuristic_compress([{"content": "hello world testing"}])
        assert isinstance(result, str)
        assert len(result) > 0


class TestMemoryCompressorWithBrain:
    """Test compress_memories with a brain and vector store."""

    @pytest.mark.asyncio
    async def test_compress_memories_uses_brain_when_available(self):
        from database.compression import MemoryCompressor
        mock_brain = MagicMock()
        async def _stream(prompt):
            for chunk in ["wisdom", " token"]:
                yield chunk
        mock_brain.chat_stream = _stream
        mock_vs = MagicMock()
        compressor = MemoryCompressor(brain=mock_brain, vector_store=mock_vs)
        result = await compressor.compress_memories([
            {"content": "memory one"},
            {"content": "memory two"},
        ])
        assert result == "wisdom token"
        # Verify the wisdom token was stored back to the vector store
        mock_vs.add_memory.assert_called_once()
        args, kwargs = mock_vs.add_memory.call_args
        assert args[0] == "wisdom token"
        assert kwargs["meta"]["category"] == "wisdom"

    @pytest.mark.asyncio
    async def test_compress_memories_falls_back_when_brain_fails(self):
        from database.compression import MemoryCompressor
        mock_brain = MagicMock()
        async def _stream(prompt):
            raise RuntimeError("brain boom")
            yield  # unreachable, makes this an async generator
        mock_brain.chat_stream = _stream
        mock_vs = MagicMock()
        compressor = MemoryCompressor(brain=mock_brain, vector_store=mock_vs)
        result = await compressor.compress_memories([
            {"content": "python testing code"},
            {"content": "python development code"},
        ])
        # Should fall back to heuristic
        assert "Key themes" in result or "patterns" in result.lower()

    @pytest.mark.asyncio
    async def test_compress_memories_no_brain_uses_heuristic(self):
        from database.compression import MemoryCompressor
        mock_vs = MagicMock()
        compressor = MemoryCompressor(brain=None, vector_store=mock_vs)
        result = await compressor.compress_memories([
            {"content": "python is fun"},
        ])
        assert "python" in result.lower()

    @pytest.mark.asyncio
    async def test_compress_memories_empty_input_returns_empty(self):
        from database.compression import MemoryCompressor
        mock_vs = MagicMock()
        compressor = MemoryCompressor(brain=None, vector_store=mock_vs)
        result = await compressor.compress_memories([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_compress_memories_with_only_empty_content(self):
        from database.compression import MemoryCompressor
        mock_vs = MagicMock()
        compressor = MemoryCompressor(brain=None, vector_store=mock_vs)
        # A single memory with empty content produces raw_text == ""
        result = await compressor.compress_memories([{"content": ""}])
        assert result == ""

    @pytest.mark.asyncio
    async def test_compress_memories_with_whitespace_content_uses_heuristic(self):
        """Memories whose content has only the join separator are still
        processed via the heuristic fallback (which returns 'No significant
        patterns detected' because no 4+ char words exist)."""
        from database.compression import MemoryCompressor
        mock_vs = MagicMock()
        compressor = MemoryCompressor(brain=None, vector_store=mock_vs)
        result = await compressor.compress_memories([
            {"content": ""},
            {"content": "   "},
        ])
        # Heuristic fallback fires because raw_text " |    " is truthy
        assert "No significant patterns" in result or result == ""


class TestMemoryCompressorRetrieval:
    """Test wisdom token retrieval and compression ratio."""

    @pytest.mark.asyncio
    async def test_get_wisdom_tokens_returns_session_tokens(self):
        from database.compression import MemoryCompressor
        mock_vs = MagicMock()
        compressor = MemoryCompressor(brain=None, vector_store=mock_vs)
        # Initially empty
        assert compressor.get_wisdom_tokens() == []
        # After compression, tokens should be stored
        await compressor.compress_memories([{"content": "python is great"}])
        tokens = compressor.get_wisdom_tokens()
        assert len(tokens) == 1
        assert tokens[0]["source_count"] == 1

    def test_get_compression_ratio(self):
        from database.compression import MemoryCompressor
        mock_vs = MagicMock()
        compressor = MemoryCompressor(brain=None, vector_store=mock_vs)
        ratio = compressor.get_compression_ratio(10)
        assert ratio == "10:1"

    def test_retrieve_relevant_wisdom_filters_to_wisdom_category(self):
        from database.compression import MemoryCompressor
        mock_vs = MagicMock()
        mock_vs.search.return_value = [
            {"content": "wisdom 1", "metadata": {"category": "wisdom"}},
            {"content": "not wisdom", "metadata": {"category": "other"}},
            {"content": "wisdom 2", "metadata": {"category": "wisdom"}},
        ]
        compressor = MemoryCompressor(brain=None, vector_store=mock_vs)
        results = compressor.retrieve_relevant_wisdom("query")
        assert len(results) == 2
        assert all(r["metadata"]["category"] == "wisdom" for r in results)

    def test_retrieve_relevant_wisdom_handles_search_exception(self):
        from database.compression import MemoryCompressor
        mock_vs = MagicMock()
        mock_vs.search.side_effect = RuntimeError("boom")
        compressor = MemoryCompressor(brain=None, vector_store=mock_vs)
        results = compressor.retrieve_relevant_wisdom("query")
        assert results == []
