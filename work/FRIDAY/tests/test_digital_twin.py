"""Tests for the digital twin."""
import asyncio
import pytest

from core.digital_twin import (
    DigitalTwin, Entity, Relationship, EntityType, RelationType,
    get_digital_twin,
)


@pytest.fixture()
def twin(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    import core.digital_twin
    core.digital_twin._twin = None
    twin = DigitalTwin(persist_path=tmp_path / "twin.json")
    yield twin
    core.digital_twin._twin = None


class TestEntityManagement:
    def test_add_entity(self, twin):
        entity = asyncio.run(twin.add_entity(
            EntityType.MODULE, "core.brain", {"loc": 866}
        ))
        assert entity.id == "core.brain"
        assert entity.type == EntityType.MODULE
        assert entity.properties["loc"] == 866

    def test_get_entity(self, twin):
        asyncio.run(twin.add_entity(EntityType.MODULE, "test.mod", {"loc": 100}))
        retrieved = asyncio.run(twin.get_entity("test.mod"))
        assert retrieved is not None
        assert retrieved.id == "test.mod"

    def test_get_nonexistent_entity(self, twin):
        result = asyncio.run(twin.get_entity("nonexistent"))
        assert result is None

    def test_update_entity_properties(self, twin):
        asyncio.run(twin.add_entity(EntityType.MODULE, "test.mod", {"loc": 100}))
        asyncio.run(twin.add_entity(EntityType.MODULE, "test.mod", {"complexity": 15}))
        entity = asyncio.run(twin.get_entity("test.mod"))
        assert entity.properties["loc"] == 100
        assert entity.properties["complexity"] == 15


class TestRelationships:
    def test_add_relationship(self, twin):
        asyncio.run(twin.add_entity(EntityType.MODULE, "a"))
        asyncio.run(twin.add_entity(EntityType.MODULE, "b"))
        asyncio.run(twin.add_relationship("a", "b", RelationType.DEPENDS_ON))
        neighbors = asyncio.run(twin.get_neighbors("a"))
        assert len(neighbors) == 1
        assert neighbors[0][0] == "b"
        assert neighbors[0][1] == RelationType.DEPENDS_ON

    def test_get_neighbors_empty(self, twin):
        asyncio.run(twin.add_entity(EntityType.MODULE, "isolated"))
        neighbors = asyncio.run(twin.get_neighbors("isolated"))
        assert len(neighbors) == 0


class TestSubgraph:
    def test_get_subgraph(self, twin):
        # a → b → c
        asyncio.run(twin.add_entity(EntityType.MODULE, "a"))
        asyncio.run(twin.add_entity(EntityType.MODULE, "b"))
        asyncio.run(twin.add_entity(EntityType.MODULE, "c"))
        asyncio.run(twin.add_relationship("a", "b", RelationType.DEPENDS_ON))
        asyncio.run(twin.add_relationship("b", "c", RelationType.DEPENDS_ON))

        subgraph = asyncio.run(twin.get_subgraph("a", depth=2))
        assert subgraph["root"] == "a"
        assert len(subgraph["entities"]) == 3
        assert len(subgraph["relationships"]) == 2

    def test_find_path(self, twin):
        asyncio.run(twin.add_entity(EntityType.MODULE, "a"))
        asyncio.run(twin.add_entity(EntityType.MODULE, "b"))
        asyncio.run(twin.add_entity(EntityType.MODULE, "c"))
        asyncio.run(twin.add_relationship("a", "b", RelationType.DEPENDS_ON))
        asyncio.run(twin.add_relationship("b", "c", RelationType.DEPENDS_ON))

        path = asyncio.run(twin.find_path("a", "c"))
        assert path is not None
        assert path == ["a", "b", "c"]

    def test_find_path_no_connection(self, twin):
        asyncio.run(twin.add_entity(EntityType.MODULE, "a"))
        asyncio.run(twin.add_entity(EntityType.MODULE, "b"))
        path = asyncio.run(twin.find_path("a", "b"))
        assert path is None


class TestPersistence:
    def test_twin_survives_restart(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
        import core.digital_twin
        core.digital_twin._twin = None

        t1 = DigitalTwin(persist_path=tmp_path / "twin.json")
        asyncio.run(t1.add_entity(EntityType.MODULE, "survive.restart"))

        # Restart
        core.digital_twin._twin = None
        t2 = DigitalTwin(persist_path=tmp_path / "twin.json")
        entity = asyncio.run(t2.get_entity("survive.restart"))
        assert entity is not None


class TestStats:
    def test_stats_returns_counts(self, twin):
        asyncio.run(twin.add_entity(EntityType.MODULE, "mod1"))
        asyncio.run(twin.add_entity(EntityType.TASK, "task1"))
        asyncio.run(twin.add_relationship("mod1", "task1", RelationType.IMPLEMENTS))

        stats = asyncio.run(twin.get_stats())
        assert stats["total_entities"] == 2
        assert stats["total_relationships"] == 1
        assert stats["entities_by_type"]["module"] == 1
        assert stats["entities_by_type"]["task"] == 1
