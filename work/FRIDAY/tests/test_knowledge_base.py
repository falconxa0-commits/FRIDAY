"""Tests for the engineering knowledge base."""
import asyncio
import json
import pytest

from core.knowledge_base import (
    KnowledgeBase, KnowledgeEntry, EntryType, EntryStatus,
    get_knowledge_base,
)


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    import core.knowledge_base
    core.knowledge_base._kb = None
    base = KnowledgeBase(base_dir=tmp_path / "knowledge")
    yield base
    core.knowledge_base._kb = None


class TestEntryCreation:
    def test_create_entry_returns_entry_with_id(self, kb):
        entry = asyncio.run(kb.create_entry(
            type=EntryType.ADR,
            title="Use HMAC-SHA256 for audit chain",
            summary="Decision to switch from bare SHA-256 to HMAC-SHA256",
            content="## Context\nThe audit chain was forgeable...",
        ))
        assert entry.id
        assert entry.type == EntryType.ADR
        assert entry.title.startswith("Use HMAC")
        assert entry.status == EntryStatus.ACCEPTED

    def test_create_entry_persists_to_disk(self, kb, tmp_path):
        asyncio.run(kb.create_entry(
            type=EntryType.LESSON,
            title="Test persistence",
            summary="Summary",
        ))
        index_path = tmp_path / "knowledge" / "index.json"
        assert index_path.exists()
        with open(index_path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["title"] == "Test persistence"

    def test_create_entry_with_tags(self, kb):
        entry = asyncio.run(kb.create_entry(
            type=EntryType.STANDARD,
            title="Naming convention",
            summary="Use snake_case for functions",
            tags=["python", "style", "naming"],
        ))
        assert "python" in entry.tags
        assert "naming" in entry.tags


class TestEntryRetrieval:
    def test_get_entry_by_id(self, kb):
        created = asyncio.run(kb.create_entry(
            type=EntryType.DESIGN,
            title="Design doc",
        ))
        retrieved = asyncio.run(kb.get_entry(created.id))
        assert retrieved is not None
        assert retrieved.id == created.id

    def test_get_nonexistent_entry_returns_none(self, kb):
        result = asyncio.run(kb.get_entry("nonexistent-id"))
        assert result is None

    def test_list_entries_by_type(self, kb):
        asyncio.run(kb.create_entry(type=EntryType.ADR, title="ADR 1"))
        asyncio.run(kb.create_entry(type=EntryType.LESSON, title="Lesson 1"))
        asyncio.run(kb.create_entry(type=EntryType.ADR, title="ADR 2"))

        adrs = asyncio.run(kb.list_entries(type=EntryType.ADR))
        lessons = asyncio.run(kb.list_entries(type=EntryType.LESSON))
        assert len(adrs) == 2
        assert len(lessons) == 1

    def test_list_entries_by_tag(self, kb):
        asyncio.run(kb.create_entry(
            type=EntryType.STANDARD, title="Standard 1", tags=["security"]
        ))
        asyncio.run(kb.create_entry(
            type=EntryType.STANDARD, title="Standard 2", tags=["performance"]
        ))

        security = asyncio.run(kb.list_entries(tag="security"))
        assert len(security) == 1
        assert security[0].title == "Standard 1"


class TestSearch:
    def test_search_finds_by_title(self, kb):
        asyncio.run(kb.create_entry(
            type=EntryType.ADR,
            title="Use HMAC for audit chain",
            summary="Security improvement",
        ))
        results = asyncio.run(kb.search("HMAC"))
        assert len(results) >= 1
        assert "HMAC" in results[0].title

    def test_search_finds_by_content(self, kb):
        asyncio.run(kb.create_entry(
            type=EntryType.LESSON,
            title="Performance lesson",
            summary="Vector search is O(n)",
            content="The InMemoryVectorStore rebuilds the matrix on every search call.",
        ))
        results = asyncio.run(kb.search("matrix rebuild"))
        assert len(results) >= 1

    def test_search_finds_by_tag(self, kb):
        asyncio.run(kb.create_entry(
            type=EntryType.STANDARD,
            title="Test standard",
            summary="A standard",
            tags=["benchmarking", "performance"],
        ))
        results = asyncio.run(kb.search("benchmarking"))
        assert len(results) >= 1

    def test_search_returns_empty_for_no_match(self, kb):
        asyncio.run(kb.create_entry(
            type=EntryType.ADR,
            title="Unrelated entry",
        ))
        results = asyncio.run(kb.search("quantumcomputing"))
        assert len(results) == 0

    def test_search_ranks_title_higher_than_content(self, kb):
        asyncio.run(kb.create_entry(
            type=EntryType.ADR,
            title="Important decision about caching",
            summary="Some summary",
            content="mentions caching in passing",
        ))
        asyncio.run(kb.create_entry(
            type=EntryType.LESSON,
            title="Some lesson",
            summary="Some summary",
            content="caching caching caching caching caching",
        ))
        results = asyncio.run(kb.search("caching"))
        assert len(results) >= 2
        # Title match should rank higher
        assert "caching" in results[0].title.lower()


class TestUpdate:
    def test_update_entry_status(self, kb):
        entry = asyncio.run(kb.create_entry(
            type=EntryType.ADR,
            title="Deprecate old pattern",
            status=EntryStatus.PROPOSED,
        ))
        updated = asyncio.run(kb.update_entry(entry.id, status=EntryStatus.ACCEPTED))
        assert updated.status == EntryStatus.ACCEPTED

    def test_update_entry_content(self, kb):
        entry = asyncio.run(kb.create_entry(
            type=EntryType.LESSON,
            title="Lesson",
            content="Initial content",
        ))
        updated = asyncio.run(kb.update_entry(entry.id, content="Updated content"))
        assert updated.content == "Updated content"


class TestPersistence:
    def test_kb_survives_restart(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
        import core.knowledge_base
        core.knowledge_base._kb = None

        kb1 = KnowledgeBase(base_dir=tmp_path / "knowledge")
        entry = asyncio.run(kb1.create_entry(
            type=EntryType.ADR,
            title="Survive restart",
        ))

        # Restart
        core.knowledge_base._kb = None
        kb2 = KnowledgeBase(base_dir=tmp_path / "knowledge")
        retrieved = asyncio.run(kb2.get_entry(entry.id))
        assert retrieved is not None
        assert retrieved.title == "Survive restart"


class TestStats:
    def test_stats_returns_counts(self, kb):
        asyncio.run(kb.create_entry(type=EntryType.ADR, title="ADR"))
        asyncio.run(kb.create_entry(type=EntryType.LESSON, title="Lesson 1"))
        asyncio.run(kb.create_entry(type=EntryType.LESSON, title="Lesson 2"))

        stats = asyncio.run(kb.get_stats())
        assert stats["total"] == 3
        assert stats["by_type"]["adr"] == 1
        assert stats["by_type"]["lesson"] == 2


class TestMarkdownExport:
    def test_to_markdown_includes_all_fields(self, kb):
        entry = asyncio.run(kb.create_entry(
            type=EntryType.ADR,
            title="Test ADR",
            summary="Test summary",
            content="Test content",
            tags=["test", "adr"],
        ))
        md = entry.to_markdown()
        assert "# [ADR] Test ADR" in md
        assert "Test summary" in md
        assert "Test content" in md
        assert "test" in md
        assert "adr" in md
