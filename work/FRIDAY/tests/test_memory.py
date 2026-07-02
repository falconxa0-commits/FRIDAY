"""Tests for FridayMemory class."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def memory_no_supabase():
    """FridayMemory with Supabase unavailable (in-memory fallback)."""
    from core.memory import FridayMemory
    mem = FridayMemory(supabase_client=None, vector_store=None)
    return mem


@pytest.fixture()
def memory_with_mocks():
    """FridayMemory with mocked Supabase and vector store."""
    supabase_mock = MagicMock()
    vector_mock = MagicMock()
    from core.memory import FridayMemory
    mem = FridayMemory(supabase_client=supabase_mock, vector_store=vector_mock)
    return mem


# ---------------------------------------------------------------------------
# Fact extraction patterns
# ---------------------------------------------------------------------------

class TestFactExtraction:
    """Test regex-based fact extraction."""

    def test_name_extraction(self, memory_no_supabase):
        facts = memory_no_supabase.extract_and_store_facts("my name is Alice")
        assert any("name" in f for f in facts)
        assert "name: alice" in facts

    def test_location_extraction(self, memory_no_supabase):
        facts = memory_no_supabase.extract_and_store_facts("I live in Lagos")
        assert any("location" in f for f in facts)

    def test_job_extraction(self, memory_no_supabase):
        facts = memory_no_supabase.extract_and_store_facts("I work at Google")
        assert any("job" in f for f in facts)

    def test_likes_extraction(self, memory_no_supabase):
        facts = memory_no_supabase.extract_and_store_facts("I love pizza")
        assert any("likes" in f for f in facts)

    def test_dislikes_extraction(self, memory_no_supabase):
        facts = memory_no_supabase.extract_and_store_facts("I hate waking up early")
        assert any("dislikes" in f for f in facts)

    def test_favorites_extraction(self, memory_no_supabase):
        facts = memory_no_supabase.extract_and_store_facts("my favorite color is blue")
        assert any("favorites" in f for f in facts)

    def test_birthday_extraction(self, memory_no_supabase):
        facts = memory_no_supabase.extract_and_store_facts("my birthday is June 15th")
        assert any("birthday" in f for f in facts)

    def test_allergy_extraction(self, memory_no_supabase):
        facts = memory_no_supabase.extract_and_store_facts("I'm allergic to peanuts")
        assert any("allergy" in f for f in facts)

    def test_task_extraction(self, memory_no_supabase):
        facts = memory_no_supabase.extract_and_store_facts("I need to finish the report")
        assert any("task" in f for f in facts)

    def test_no_facts_extracted(self, memory_no_supabase):
        facts = memory_no_supabase.extract_and_store_facts("The weather is nice today")
        assert facts == []

    def test_multiple_facts(self, memory_no_supabase):
        text = "my name is Bob and I love coding"
        facts = memory_no_supabase.extract_and_store_facts(text)
        assert len(facts) >= 2


# ---------------------------------------------------------------------------
# Conversation storage
# ---------------------------------------------------------------------------

class TestConversationStorage:
    """Test conversation storage."""

    def test_store_conversation_in_memory(self, memory_no_supabase):
        memory_no_supabase.store_conversation("user", "Hello Friday")
        assert len(memory_no_supabase._memories) == 1
        assert memory_no_supabase._memories[0]["role"] == "user"
        assert memory_no_supabase._memories[0]["content"] == "Hello Friday"

    def test_store_conversation_has_timestamp(self, memory_no_supabase):
        memory_no_supabase.store_conversation("assistant", "Hi there")
        mem = memory_no_supabase._memories[0]
        assert "timestamp" in mem

    def test_store_conversation_with_metadata(self, memory_no_supabase):
        memory_no_supabase.store_conversation("user", "Hello", metadata={"source": "api"})
        mem = memory_no_supabase._memories[0]
        assert mem["metadata"]["source"] == "api"

    def test_store_user_message_extracts_facts(self, memory_no_supabase):
        """User messages should auto-extract facts."""
        memory_no_supabase.store_conversation("user", "my name is Carol")
        assert "name:carol" in memory_no_supabase._session_facts

    def test_store_assistant_message_no_fact_extraction(self, memory_no_supabase):
        """Assistant messages should NOT trigger fact extraction."""
        memory_no_supabase.store_conversation("assistant", "my name is Friday")
        # No personal facts should be extracted from assistant messages
        assert not any("name:friday" in k for k in memory_no_supabase._session_facts)

    def test_store_conversation_with_supabase(self, memory_with_mocks):
        """Should attempt to store in Supabase when available."""
        supabase = memory_with_mocks.supabase
        supabase.client = MagicMock()  # mark as available
        memory_with_mocks.store_conversation("user", "Hello")
        supabase.insert_data.assert_called_once()
        call_args = supabase.insert_data.call_args
        assert call_args[0][0] == "conversations"


# ---------------------------------------------------------------------------
# Memory retrieval
# ---------------------------------------------------------------------------

class TestMemoryRetrieval:
    """Test memory search / retrieval."""

    def test_keyword_search_fallback(self, memory_no_supabase):
        memory_no_supabase.store_conversation("user", "I love pizza and pasta")
        memory_no_supabase.store_conversation("user", "The weather is sunny")

        results = memory_no_supabase.retrieve_relevant_memories("pizza")
        assert len(results) > 0
        assert any("pizza" in r.get("content", "") for r in results)

    def test_retrieve_returns_top_k(self, memory_no_supabase):
        for i in range(20):
            memory_no_supabase.store_conversation("user", f"message number {i}")

        results = memory_no_supabase.retrieve_relevant_memories("message", top_k=3)
        assert len(results) <= 3

    def test_retrieve_empty_memory(self, memory_no_supabase):
        results = memory_no_supabase.retrieve_relevant_memories("anything")
        assert results == []

    def test_retrieve_with_vector_store(self, memory_with_mocks):
        """Should try vector store first when available."""
        vs = memory_with_mocks.vector_store
        vs.search.return_value = [{"content": "matched", "score": 0.9}]
        results = memory_with_mocks.retrieve_relevant_memories("query")
        vs.search.assert_called_once()
        assert len(results) == 1


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------

class TestUserProfile:
    """Test user profile compilation."""

    def test_empty_profile(self, memory_no_supabase):
        profile = memory_no_supabase.get_user_profile()
        assert isinstance(profile, dict)
        assert len(profile) == 0

    def test_profile_with_facts(self, memory_no_supabase):
        memory_no_supabase.extract_and_store_facts("my name is Dave")
        memory_no_supabase.extract_and_store_facts("I love coding")
        profile = memory_no_supabase.get_user_profile()
        assert "personal" in profile or "preference" in profile

    def test_profile_categories(self, memory_no_supabase):
        memory_no_supabase.extract_and_store_facts("my name is Eve")
        memory_no_supabase.extract_and_store_facts("I work at OpenAI")
        profile = memory_no_supabase.get_user_profile()
        assert "personal" in profile
        assert "work" in profile


# ---------------------------------------------------------------------------
# In-memory fallback
# ---------------------------------------------------------------------------

class TestInMemoryFallback:
    """Test that FridayMemory works without Supabase."""

    def test_no_supabase_no_crash(self):
        """Should not crash when Supabase is unavailable."""
        from core.memory import FridayMemory
        mem = FridayMemory(supabase_client=None, vector_store=None)
        # When SupabaseClient is importable but unconfigured, it creates
        # a degraded client.  When it's not importable, it stays None.
        # Either way, in-memory fallback should work.
        assert mem._memories is not None  # in-memory store always exists

    def test_in_memory_storage_works(self, memory_no_supabase):
        """All core operations should work without Supabase."""
        memory_no_supabase.store_conversation("user", "test")
        results = memory_no_supabase.retrieve_relevant_memories("test")
        assert len(results) > 0

    def test_get_recent_conversations(self, memory_no_supabase):
        for i in range(15):
            memory_no_supabase.store_conversation("user", f"msg {i}")
        recent = memory_no_supabase.get_recent_conversations(limit=5)
        assert len(recent) == 5
