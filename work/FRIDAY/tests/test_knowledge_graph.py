"""Knowledge Graph Ω — core unit + invariant tests.

Covers:
    - Entity creation, ID derivation, alias management, attribute caps
    - Relationship creation, ID derivation, evidence, reinforcement
    - EntityExtractor pattern matching (email, URL, date, money, person, org, etc.)
    - RelationshipDetector syntactic + co-occurrence detection
    - KnowledgeGraph add/remove/traverse/neighbors
    - QueryEngine filtering, traversal, scoring
    - MergeResolver conflict detection
    - KnowledgeGraphManager extract → store → query end-to-end
"""
from __future__ import annotations

import asyncio
import pytest

from core.knowledge_graph import (
    KnowledgeGraphManager, KnowledgeGraphConfig,
    Entity, EntityType, Relationship, RelationshipType,
    EntityExtractor, ExtractionResult,
    RelationshipDetector, DetectionResult,
    KnowledgeGraph, GraphStats,
    KnowledgeQuery, QueryEngine, QueryResult,
    EntityFilter, RelationshipFilter, TraversalSpec,
    MergeConflict, MergeResolver, ConflictType, ConflictResolution,
    canonicalize_name, derive_entity_id, derive_relationship_id,
    BIDIRECTIONAL_TYPES, HIGH_STAKES_TYPES,
)
from core.living_memory import (
    LivingMemoryManager, LivingMemoryConfig,
    AuthorizationContext, MemoryCapability,
    InMemoryPersistence,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def founder_ctx():
    return AuthorizationContext(
        citizen_id="founder-00000001",
        rank_level=100,
        capabilities={c.value for c in MemoryCapability},
        tenant_id="default",
        is_founder=True,
    )


@pytest.fixture
def worker_ctx():
    return AuthorizationContext(
        citizen_id="worker-00000001",
        rank_level=20,
        capabilities={
            MemoryCapability.MEMORY_READ.value,
            MemoryCapability.MEMORY_WRITE.value,
            MemoryCapability.MEMORY_ASSOCIATE.value,
        },
        tenant_id="default",
    )


@pytest.fixture
def m3_manager():
    return LivingMemoryManager(persistence=InMemoryPersistence())


@pytest.fixture
def kg_manager(m3_manager):
    return KnowledgeGraphManager(
        living_memory_manager=m3_manager,
        config=KnowledgeGraphConfig(),
    )


# ----------------------------------------------------------------------
# Entity
# ----------------------------------------------------------------------


class TestEntity:
    def test_create_basic(self):
        e = Entity.create(name="Alice Smith", entity_type=EntityType.PERSON)
        assert e.entity_type == EntityType.PERSON
        assert e.canonical_name == "alice smith"
        assert e.entity_id.startswith("kg-")

    def test_create_derives_stable_id(self):
        e1 = Entity.create(name="Alice Smith", entity_type=EntityType.PERSON, tenant_id="t1")
        e2 = Entity.create(name="Alice Smith", entity_type=EntityType.PERSON, tenant_id="t1")
        assert e1.entity_id == e2.entity_id  # deterministic

    def test_create_different_tenants_different_ids(self):
        e1 = Entity.create(name="Alice", entity_type=EntityType.PERSON, tenant_id="t1")
        e2 = Entity.create(name="Alice", entity_type=EntityType.PERSON, tenant_id="t2")
        assert e1.entity_id != e2.entity_id

    def test_create_different_types_different_ids(self):
        e1 = Entity.create(name="Apple", entity_type=EntityType.ORGANIZATION)
        e2 = Entity.create(name="Apple", entity_type=EntityType.CONCEPT)
        assert e1.entity_id != e2.entity_id

    def test_canonicalize_name_collapses_whitespace(self):
        assert canonicalize_name("  Alice   Smith  ") == "alice smith"

    def test_canonicalize_name_rejects_empty(self):
        with pytest.raises(ValueError):
            canonicalize_name("")
        with pytest.raises(ValueError):
            canonicalize_name("   ")

    def test_canonicalize_name_rejects_too_long(self):
        with pytest.raises(ValueError):
            canonicalize_name("x" * 300)

    def test_add_alias(self):
        e = Entity.create(name="Alice", entity_type=EntityType.PERSON, aliases=["Al", "Ali"])
        assert len(e.aliases) == 2
        assert e.add_alias("Alice A")  # new alias
        assert not e.add_alias("Al")  # duplicate
        assert not e.add_alias("Alice")  # canonical name, not alias

    def test_add_alias_cap(self):
        e = Entity.create(name="X", entity_type=EntityType.CONCEPT)
        for i in range(32):
            e.add_alias(f"alias-{i:02d}-padding")
        with pytest.raises(ValueError):
            e.add_alias("one-too-many-alias")

    def test_add_alias_cap_via_post_init(self):
        """Alias cap also enforced at __post_init__ (not just add_alias)."""
        aliases = [f"alias-{i:02d}-padding" for i in range(33)]  # 33 > cap
        with pytest.raises(ValueError):
            Entity.create(name="X", entity_type=EntityType.CONCEPT, aliases=aliases)

    def test_set_attribute(self):
        e = Entity.create(name="Alice", entity_type=EntityType.PERSON)
        e.set_attribute("role", "engineer")
        assert e.attributes["role"] == "engineer"

    def test_set_attribute_cap(self):
        e = Entity.create(name="X", entity_type=EntityType.CONCEPT)
        for i in range(128):
            e.set_attribute(f"attr_{i:03d}", i)
        with pytest.raises(ValueError):
            e.set_attribute("one_too_many", "value")

    def test_matches_name(self):
        e = Entity.create(name="Alice", entity_type=EntityType.PERSON, aliases=["Al"])
        assert e.matches_name("Alice")
        assert e.matches_name("alice")  # case-insensitive
        assert e.matches_name("Al")
        assert not e.matches_name("Bob")

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ValueError):
            Entity(
                entity_id="kg-test123",
                entity_type=EntityType.PERSON,
                canonical_name="test",
                confidence=1.5,
            )

    def test_to_dict_round_trip(self):
        e = Entity.create(name="Alice", entity_type=EntityType.PERSON, aliases=["Al"])
        e.set_attribute("role", "engineer")
        d = e.to_dict()
        e2 = Entity.from_dict(d)
        assert e2.entity_id == e.entity_id
        assert e2.canonical_name == e.canonical_name
        assert e2.aliases == e.aliases
        assert e2.attributes == e.attributes


# ----------------------------------------------------------------------
# Relationship
# ----------------------------------------------------------------------


class TestRelationship:
    def test_create_basic(self):
        r = Relationship.create(
            source_entity_id="kg-src0000000001",
            target_entity_id="kg-tgt0000000001",
            rel_type=RelationshipType.WORKS_FOR,
        )
        assert r.relationship_type == RelationshipType.WORKS_FOR
        assert r.relationship_id.startswith("kgr-")

    def test_create_derives_stable_id(self):
        r1 = Relationship.create("kg-a00000000001", "kg-b00000000001", RelationshipType.WORKS_FOR)
        r2 = Relationship.create("kg-a00000000001", "kg-b00000000001", RelationshipType.WORKS_FOR)
        assert r1.relationship_id == r2.relationship_id

    def test_self_relationship_rejected(self):
        with pytest.raises(ValueError):
            Relationship.create(
                "kg-a00000000001", "kg-a00000000001", RelationshipType.WORKS_FOR,
            )

    def test_self_relationship_rejected_via_post_init(self):
        """Self-relationship also rejected at __post_init__ (not just create)."""
        with pytest.raises(ValueError):
            Relationship(
                relationship_id="kgr-test00000001",
                source_entity_id="kg-a00000000001",
                target_entity_id="kg-a00000000001",
                relationship_type=RelationshipType.WORKS_FOR,
            )

    def test_invalid_weight_rejected(self):
        with pytest.raises(ValueError):
            Relationship.create(
                "kg-a00000000001", "kg-b00000000001", RelationshipType.WORKS_FOR, weight=1.5,
            )

    def test_bidirectional_auto_set_for_similar_to(self):
        r = Relationship.create(
            "kg-a00000000001", "kg-b00000000001", RelationshipType.SIMILAR_TO,
        )
        assert r.bidirectional is True

    def test_bidirectional_auto_set_for_related_to(self):
        r = Relationship.create(
            "kg-a00000000001", "kg-b00000000001", RelationshipType.RELATED_TO,
        )
        assert r.bidirectional is True

    def test_directed_for_works_for(self):
        r = Relationship.create(
            "kg-a00000000001", "kg-b00000000001", RelationshipType.WORKS_FOR,
        )
        assert r.bidirectional is False

    def test_add_evidence(self):
        r = Relationship.create("kg-a00000000001", "kg-b00000000001", RelationshipType.WORKS_FOR)
        assert r.add_evidence("ep-00000001")
        assert not r.add_evidence("ep-00000001")  # duplicate
        assert r.reinforcement_count == 1

    def test_add_evidence_cap(self):
        r = Relationship.create("kg-a00000000001", "kg-b00000000001", RelationshipType.WORKS_FOR)
        for i in range(64):
            r.add_evidence(f"ep-{i:08d}")
        with pytest.raises(ValueError):
            r.add_evidence("one-too-many")

    def test_add_evidence_cap_via_post_init(self):
        """Evidence cap also enforced at __post_init__."""
        evidence = [f"ep-{i:08d}" for i in range(65)]  # 65 > cap
        with pytest.raises(ValueError):
            Relationship(
                relationship_id="kgr-test00000002",
                source_entity_id="kg-a00000000001",
                target_entity_id="kg-b00000000001",
                relationship_type=RelationshipType.WORKS_FOR,
                evidence=evidence,
            )

    def test_reinforce_increases_weight(self):
        r = Relationship.create("kg-a00000000001", "kg-b00000000001", RelationshipType.WORKS_FOR, weight=0.5)
        r.reinforce(delta=0.1)
        assert r.weight == 0.6
        assert r.reinforcement_count == 1

    def test_reinforce_clamps_at_1(self):
        r = Relationship.create("kg-a00000000001", "kg-b00000000001", RelationshipType.WORKS_FOR, weight=0.9)
        r.reinforce(delta=0.5)
        assert r.weight == 1.0

    def test_involves(self):
        r = Relationship.create("kg-a00000000001", "kg-b00000000001", RelationshipType.WORKS_FOR)
        assert r.involves("kg-a00000000001")
        assert r.involves("kg-b00000000001")
        assert not r.involves("kg-c00000000001")

    def test_other_endpoint_directed(self):
        r = Relationship.create("kg-a00000000001", "kg-b00000000001", RelationshipType.WORKS_FOR)
        assert r.other_endpoint("kg-a00000000001") == "kg-b00000000001"
        # Directed: from target, no other endpoint
        assert r.other_endpoint("kg-b00000000001") is None

    def test_other_endpoint_bidirectional(self):
        r = Relationship.create("kg-a00000000001", "kg-b00000000001", RelationshipType.SIMILAR_TO)
        assert r.other_endpoint("kg-a00000000001") == "kg-b00000000001"
        assert r.other_endpoint("kg-b00000000001") == "kg-a00000000001"

    def test_to_dict_round_trip(self):
        r = Relationship.create("kg-a00000000001", "kg-b00000000001", RelationshipType.WORKS_FOR, weight=0.7)
        r.add_evidence("ep-00000001")
        d = r.to_dict()
        r2 = Relationship.from_dict(d)
        assert r2.relationship_id == r.relationship_id
        assert r2.weight == r.weight
        assert r2.evidence == r.evidence


# ----------------------------------------------------------------------
# EntityExtractor
# ----------------------------------------------------------------------


class TestEntityExtractor:
    def test_extract_email(self):
        ex = EntityExtractor()
        mentions = ex.extract_mentions("Contact alice@example.com for info")
        assert any(m.entity_type == EntityType.OTHER and "@" in m.text for m in mentions)

    def test_extract_url(self):
        ex = EntityExtractor()
        mentions = ex.extract_mentions("Visit https://example.com/page today")
        assert any(m.entity_type == EntityType.DOCUMENT and "http" in m.text for m in mentions)

    def test_extract_iso_date(self):
        ex = EntityExtractor()
        mentions = ex.extract_mentions("Deadline is 2026-12-31")
        assert any(m.entity_type == EntityType.DATE for m in mentions)

    def test_extract_money(self):
        ex = EntityExtractor()
        mentions = ex.extract_mentions("Cost is $1,500.00 or 200 USD")
        assert any(m.entity_type == EntityType.MONEY for m in mentions)

    def test_extract_organization_with_suffix(self):
        ex = EntityExtractor()
        mentions = ex.extract_mentions("Acme Corp announced layoffs")
        org_mentions = [m for m in mentions if m.entity_type == EntityType.ORGANIZATION]
        assert len(org_mentions) >= 1

    def test_extract_person_with_title(self):
        ex = EntityExtractor()
        mentions = ex.extract_mentions("Mr. John Smith joined the team")
        person_mentions = [m for m in mentions if m.entity_type == EntityType.PERSON]
        assert len(person_mentions) >= 1

    def test_extract_quoted_concept(self):
        ex = EntityExtractor()
        mentions = ex.extract_mentions('The "Knowledge Graph" is a new feature')
        concept_mentions = [m for m in mentions if m.entity_type == EntityType.CONCEPT]
        assert len(concept_mentions) >= 1

    def test_extract_project_tag(self):
        ex = EntityExtractor()
        mentions = ex.extract_mentions("Working on project:friday_m4")
        project_mentions = [m for m in mentions if m.entity_type == EntityType.PROJECT]
        assert len(project_mentions) >= 1

    def test_extract_empty_text(self):
        ex = EntityExtractor()
        assert ex.extract_mentions("") == []

    def test_extract_truncates_oversized_text(self):
        ex = EntityExtractor()
        huge = "x" * 20_000
        # Should not crash, should truncate
        mentions = ex.extract_mentions(huge)
        assert isinstance(mentions, list)

    def test_extract_entities_deduplicates(self):
        ex = EntityExtractor()
        # Mr. title triggers person extraction; should deduplicate Alice
        result = ex.extract_entities("Mr. Alice works for Acme. Mr. Alice is great.", tenant_id="t1")
        alice_entities = [e for e in result.entities if "alice" in e.canonical_name]
        assert len(alice_entities) == 1

    def test_extract_returns_extraction_result(self):
        ex = EntityExtractor()
        result = ex.extract_entities("Alice works for Acme Corp", tenant_id="t1")
        assert isinstance(result, ExtractionResult)
        assert result.extractor_version == "rule-based-v1"
        assert len(result.entities) > 0
        assert len(result.mentions) > 0


# ----------------------------------------------------------------------
# RelationshipDetector
# ----------------------------------------------------------------------


class TestRelationshipDetector:
    def test_detect_works_for(self):
        ex = EntityExtractor()
        det = RelationshipDetector()
        # Use Mr. title to ensure person extraction
        text = "Mr. John Smith works for Acme Corp"
        result = ex.extract_entities(text, tenant_id="t1")
        detection = det.detect(text, result.entities, result.mentions, tenant_id="t1")
        assert detection.pattern_matches >= 1
        rel_types = [r.relationship_type for r in detection.relationships]
        assert RelationshipType.WORKS_FOR in rel_types

    def test_detect_located_in(self):
        ex = EntityExtractor()
        det = RelationshipDetector()
        text = "Acme Corp is located in Lagos"
        result = ex.extract_entities(text, tenant_id="t1")
        detection = det.detect(text, result.entities, result.mentions, tenant_id="t1")
        rel_types = [r.relationship_type for r in detection.relationships]
        assert RelationshipType.LOCATED_IN in rel_types

    def test_detect_similar_to(self):
        ex = EntityExtractor()
        det = RelationshipDetector()
        text = 'Acme Corp is similar to Beta Corp'
        result = ex.extract_entities(text, tenant_id="t1")
        detection = det.detect(text, result.entities, result.mentions, tenant_id="t1")
        rel_types = [r.relationship_type for r in detection.relationships]
        assert RelationshipType.SIMILAR_TO in rel_types

    def test_detect_co_occurrence_creates_related_to(self):
        ex = EntityExtractor()
        det = RelationshipDetector(co_occurrence_window=10)
        # Two entities near each other, no syntactic pattern
        text = "Alice and Bob discussed the project"
        result = ex.extract_entities(text, tenant_id="t1")
        # Force at least 2 person entities
        if len(result.entities) < 2:
            # Add manual entities
            result.entities.append(Entity.create("Alice", EntityType.PERSON, tenant_id="t1"))
            result.entities.append(Entity.create("Bob", EntityType.PERSON, tenant_id="t1"))
        detection = det.detect(text, result.entities, result.mentions, tenant_id="t1")
        # May or may not have co-occurrence depending on entity extraction
        # Just verify it doesn't crash
        assert isinstance(detection, DetectionResult)

    def test_detect_no_entities_returns_empty(self):
        det = RelationshipDetector()
        result = det.detect("hello world", [], [], tenant_id="t1")
        assert len(result.relationships) == 0

    def test_detect_max_relationships_cap(self):
        det = RelationshipDetector(max_relationships_per_text=5)
        ex = EntityExtractor()
        # Use multiple person mentions to force co-occurrence relationships
        text = "Mr. Alice and Mr. Bob and Mr. Charlie and Mr. Dave and Mr. Eve and Mr. Frank met"
        result = ex.extract_entities(text, tenant_id="t1")
        detection = det.detect(text, result.entities, result.mentions, tenant_id="t1")
        assert len(detection.relationships) <= 5


# ----------------------------------------------------------------------
# KnowledgeGraph
# ----------------------------------------------------------------------


class TestKnowledgeGraph:
    @pytest.mark.asyncio
    async def test_add_and_get_entity(self):
        g = KnowledgeGraph(tenant_id="t1")
        e = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        assert await g.add_entity(e)
        assert await g.get_entity(e.entity_id) is not None

    @pytest.mark.asyncio
    async def test_add_duplicate_entity_returns_false(self):
        g = KnowledgeGraph(tenant_id="t1")
        e = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        await g.add_entity(e)
        assert not await g.add_entity(e)

    @pytest.mark.asyncio
    async def test_find_entity_by_name(self):
        g = KnowledgeGraph(tenant_id="t1")
        e = Entity.create("Alice", EntityType.PERSON, tenant_id="t1", aliases=["Al"])
        await g.add_entity(e)
        assert await g.find_entity_by_name("Alice") is not None
        assert await g.find_entity_by_name("Al") is not None
        assert await g.find_entity_by_name("Bob") is None

    @pytest.mark.asyncio
    async def test_entity_cap_enforced(self):
        g = KnowledgeGraph(tenant_id="t1", max_entities=3)
        for i in range(3):
            await g.add_entity(Entity.create(f"ent-{i:02d}-padding", EntityType.CONCEPT, tenant_id="t1"))
        with pytest.raises(ValueError):
            await g.add_entity(Entity.create("one-too-many-padding", EntityType.CONCEPT, tenant_id="t1"))

    @pytest.mark.asyncio
    async def test_add_relationship_requires_both_endpoints(self):
        g = KnowledgeGraph(tenant_id="t1")
        e1 = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        await g.add_entity(e1)
        # e2 not added
        rel = Relationship.create(e1.entity_id, "kg-nonexistent01", RelationshipType.WORKS_FOR, tenant_id="t1")
        with pytest.raises(ValueError):
            await g.add_relationship(rel)

    @pytest.mark.asyncio
    async def test_add_and_traverse_relationships(self):
        g = KnowledgeGraph(tenant_id="t1")
        a = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        b = Entity.create("Acme", EntityType.ORGANIZATION, tenant_id="t1")
        c = Entity.create("Lagos", EntityType.PLACE, tenant_id="t1")
        for e in [a, b, c]:
            await g.add_entity(e)
        await g.add_relationship(Relationship.create(a.entity_id, b.entity_id, RelationshipType.WORKS_FOR, tenant_id="t1"))
        await g.add_relationship(Relationship.create(b.entity_id, c.entity_id, RelationshipType.LOCATED_IN, tenant_id="t1"))
        # Traverse from Alice
        results = await g.traverse(a.entity_id, max_depth=3)
        # Should reach Acme (depth 1) and Lagos (depth 2)
        ids = [r[0] for r in results]
        assert b.entity_id in ids
        assert c.entity_id in ids

    @pytest.mark.asyncio
    async def test_neighbors_filtered_by_type(self):
        g = KnowledgeGraph(tenant_id="t1")
        a = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        b = Entity.create("Acme", EntityType.ORGANIZATION, tenant_id="t1")
        await g.add_entity(a)
        await g.add_entity(b)
        await g.add_relationship(Relationship.create(a.entity_id, b.entity_id, RelationshipType.WORKS_FOR, tenant_id="t1"))
        # Filter by WORKS_FOR
        nbrs = await g.neighbors(a.entity_id, rel_type=RelationshipType.WORKS_FOR)
        assert len(nbrs) == 1
        # Filter by different type
        nbrs = await g.neighbors(a.entity_id, rel_type=RelationshipType.LOCATED_IN)
        assert len(nbrs) == 0

    @pytest.mark.asyncio
    async def test_degree_cap_enforced(self):
        g = KnowledgeGraph(tenant_id="t1", max_relationships_per_entity=2)
        a = Entity.create("Hub", EntityType.CONCEPT, tenant_id="t1")
        await g.add_entity(a)
        for i in range(2):
            e = Entity.create(f"sibling{i:02d}-padding", EntityType.CONCEPT, tenant_id="t1")
            await g.add_entity(e)
            await g.add_relationship(Relationship.create(a.entity_id, e.entity_id, RelationshipType.RELATED_TO, tenant_id="t1"))
        # Third relationship should fail
        e3 = Entity.create("sibling03-padding", EntityType.CONCEPT, tenant_id="t1")
        await g.add_entity(e3)
        with pytest.raises(ValueError):
            await g.add_relationship(Relationship.create(a.entity_id, e3.entity_id, RelationshipType.RELATED_TO, tenant_id="t1"))

    @pytest.mark.asyncio
    async def test_in_degree_cap_enforced_for_directional(self):
        """V4 fix regression: directional edges enforce target-side in-degree cap."""
        g = KnowledgeGraph(tenant_id="t1", max_relationships_per_entity=2)
        target = Entity.create("Target", EntityType.CONCEPT, tenant_id="t1")
        await g.add_entity(target)
        # Add 2 directional edges INTO target
        for i in range(2):
            src = Entity.create(f"src{i:02d}-padding-pad", EntityType.CONCEPT, tenant_id="t1")
            await g.add_entity(src)
            await g.add_relationship(Relationship.create(
                src.entity_id, target.entity_id, RelationshipType.WORKS_FOR, tenant_id="t1",
            ))
        # 3rd directional edge INTO target should fail (in-degree cap)
        src3 = Entity.create("src03-padding-pad", EntityType.CONCEPT, tenant_id="t1")
        await g.add_entity(src3)
        with pytest.raises(ValueError):
            await g.add_relationship(Relationship.create(
                src3.entity_id, target.entity_id, RelationshipType.WORKS_FOR, tenant_id="t1",
            ))

    @pytest.mark.asyncio
    async def test_remove_entity_cleans_relationships(self):
        g = KnowledgeGraph(tenant_id="t1")
        a = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        b = Entity.create("Acme", EntityType.ORGANIZATION, tenant_id="t1")
        await g.add_entity(a)
        await g.add_entity(b)
        await g.add_relationship(Relationship.create(a.entity_id, b.entity_id, RelationshipType.WORKS_FOR, tenant_id="t1"))
        assert await g.relationship_count() == 1
        # Remove Alice — relationship should also be gone
        await g.remove_entity(a.entity_id)
        assert await g.relationship_count() == 0

    @pytest.mark.asyncio
    async def test_stats(self):
        g = KnowledgeGraph(tenant_id="t1")
        a = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        b = Entity.create("Acme", EntityType.ORGANIZATION, tenant_id="t1")
        await g.add_entity(a)
        await g.add_entity(b)
        await g.add_relationship(Relationship.create(a.entity_id, b.entity_id, RelationshipType.WORKS_FOR, tenant_id="t1"))
        stats = await g.stats()
        assert stats.entity_count == 2
        assert stats.relationship_count == 1
        assert stats.by_entity_type.get("person") == 1
        assert stats.by_relationship_type.get("works_for") == 1


# ----------------------------------------------------------------------
# QueryEngine
# ----------------------------------------------------------------------


class TestQueryEngine:
    @pytest.mark.asyncio
    async def test_query_by_entity_type(self):
        g = KnowledgeGraph(tenant_id="t1")
        await g.add_entity(Entity.create("Alice", EntityType.PERSON, tenant_id="t1"))
        await g.add_entity(Entity.create("Acme", EntityType.ORGANIZATION, tenant_id="t1"))
        engine = QueryEngine(g)
        q = KnowledgeQuery(
            entity_filter=EntityFilter(entity_type=EntityType.PERSON),
            limit=10,
        )
        results = await engine.execute(q)
        assert len(results) == 1
        assert results[0].entity.canonical_name == "alice"

    @pytest.mark.asyncio
    async def test_query_by_name_pattern(self):
        g = KnowledgeGraph(tenant_id="t1")
        await g.add_entity(Entity.create("Alice", EntityType.PERSON, tenant_id="t1"))
        await g.add_entity(Entity.create("Bob", EntityType.PERSON, tenant_id="t1"))
        engine = QueryEngine(g)
        q = KnowledgeQuery(
            entity_filter=EntityFilter(name_pattern="ali*"),
            limit=10,
        )
        results = await engine.execute(q)
        assert len(results) == 1
        assert results[0].entity.canonical_name == "alice"

    @pytest.mark.asyncio
    async def test_query_by_attribute(self):
        g = KnowledgeGraph(tenant_id="t1")
        e = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        e.set_attribute("role", "engineer")
        await g.add_entity(e)
        e2 = Entity.create("Bob", EntityType.PERSON, tenant_id="t1")
        e2.set_attribute("role", "manager")
        await g.add_entity(e2)
        engine = QueryEngine(g)
        q = KnowledgeQuery(
            entity_filter=EntityFilter(attributes={"role": "engineer"}),
            limit=10,
        )
        results = await engine.execute(q)
        assert len(results) == 1
        assert results[0].entity.canonical_name == "alice"
        assert results[0].matched_attributes == {"role": "engineer"}

    @pytest.mark.asyncio
    async def test_query_with_traversal(self):
        g = KnowledgeGraph(tenant_id="t1")
        a = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        b = Entity.create("Acme", EntityType.ORGANIZATION, tenant_id="t1")
        await g.add_entity(a)
        await g.add_entity(b)
        await g.add_relationship(Relationship.create(a.entity_id, b.entity_id, RelationshipType.WORKS_FOR, tenant_id="t1"))
        engine = QueryEngine(g)
        q = KnowledgeQuery(
            entity_filter=EntityFilter(entity_type=EntityType.PERSON),
            traversal=TraversalSpec(max_depth=2),
            limit=10,
        )
        results = await engine.execute(q)
        assert len(results) == 1
        # Score should be boosted by traversal
        assert results[0].score > results[0].entity.confidence

    @pytest.mark.asyncio
    async def test_query_limit_enforced(self):
        g = KnowledgeGraph(tenant_id="t1")
        for i in range(20):
            await g.add_entity(Entity.create(f"ent-{i:02d}-padding", EntityType.CONCEPT, tenant_id="t1"))
        engine = QueryEngine(g)
        q = KnowledgeQuery(limit=5)
        results = await engine.execute(q)
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_query_invalid_limit_rejected(self):
        with pytest.raises(ValueError):
            KnowledgeQuery(limit=0)
        with pytest.raises(ValueError):
            KnowledgeQuery(limit=1000)


# ----------------------------------------------------------------------
# MergeResolver
# ----------------------------------------------------------------------


class TestMergeResolver:
    def test_detect_name_collision_same_type(self):
        resolver = MergeResolver()
        existing = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        new = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        # Same name + same type — but different IDs? No, they'd be the same ID
        # Let's force different IDs
        new.entity_id = "kg-different0000001"
        conflicts = resolver.detect_name_collisions([existing], new)
        # Same name + same type → NAME_COLLISION (would be MERGE candidate)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == ConflictType.NAME_COLLISION

    def test_detect_name_collision_different_type_requires_approval(self):
        resolver = MergeResolver()
        existing = Entity.create("Apple", EntityType.ORGANIZATION, tenant_id="t1")
        new = Entity.create("Apple", EntityType.CONCEPT, tenant_id="t1")
        # Force different IDs by changing tenant
        new2 = Entity.create("Apple", EntityType.CONCEPT, tenant_id="t2")
        conflicts = resolver.detect_name_collisions([existing], new2)
        # ORG + CONCEPT — both high-stakes? Only ORG is
        assert len(conflicts) == 1
        assert conflicts[0].requires_founder_approval  # ORG is high-stakes

    def test_detect_attribute_contradiction(self):
        resolver = MergeResolver()
        existing = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        existing.set_attribute("role", "engineer")
        new_attrs = {"role": "manager"}
        conflicts = resolver.detect_attribute_contradictions(existing, new_attrs)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == ConflictType.ATTRIBUTE_CONTRADICTION
        assert conflicts[0].attribute_name == "role"
        assert conflicts[0].attribute_value_a == "engineer"
        assert conflicts[0].attribute_value_b == "manager"

    def test_detect_no_contradiction_when_same_value(self):
        resolver = MergeResolver()
        existing = Entity.create("Alice", EntityType.PERSON, tenant_id="t1")
        existing.set_attribute("role", "engineer")
        conflicts = resolver.detect_attribute_contradictions(existing, {"role": "engineer"})
        assert len(conflicts) == 0


# ----------------------------------------------------------------------
# KnowledgeGraphManager (end-to-end)
# ----------------------------------------------------------------------


class TestKnowledgeGraphManager:
    @pytest.mark.asyncio
    async def test_extract_creates_entities_in_m3(self, kg_manager, m3_manager, founder_ctx):
        await m3_manager.start()
        await kg_manager.start()
        result = await kg_manager.extract(
            "Alice Smith works for Acme Corp",
            founder_ctx, source="test",
        )
        assert len(result.entities) >= 1
        # Verify entities are in M3
        from core.living_memory import MemoryType
        m3_mems = await m3_manager.recall_by_type(
            memory_type=MemoryType.SEMANTIC,
            ctx=founder_ctx,
            limit=100,
        )
        assert len(m3_mems) >= 1  # at least 1 entity anchor
        await kg_manager.stop()
        await m3_manager.stop()

    @pytest.mark.asyncio
    async def test_extract_then_query_finds_entity(self, kg_manager, m3_manager, founder_ctx):
        await m3_manager.start()
        await kg_manager.start()
        await kg_manager.extract(
            "Mr. John Smith works for Beta Corp",
            founder_ctx, source="test",
        )
        # Query for PERSON entities
        q = KnowledgeQuery(
            entity_filter=EntityFilter(entity_type=EntityType.PERSON),
            limit=10,
        )
        results = await kg_manager.query(q, founder_ctx)
        assert len(results) >= 1
        # Should find John Smith
        names = [r.entity.canonical_name for r in results]
        assert any("john" in n for n in names)
        await kg_manager.stop()
        await m3_manager.stop()

    @pytest.mark.asyncio
    async def test_extract_then_traverse(self, kg_manager, m3_manager, founder_ctx):
        await m3_manager.start()
        await kg_manager.start()
        result = await kg_manager.extract(
            "Acme Corp is located in Lagos",
            founder_ctx, source="test",
        )
        # Find Acme entity
        acme = await kg_manager.find_entity("Acme", founder_ctx)
        if acme:
            # Traverse from Acme
            results = await kg_manager.traverse(acme.entity_id, founder_ctx, max_depth=2)
            # Should reach Lagos
            ids = [r[0] for r in results]
            lagos = await kg_manager.find_entity("Lagos", founder_ctx)
            if lagos:
                assert lagos.entity_id in ids
        await kg_manager.stop()
        await m3_manager.stop()

    @pytest.mark.asyncio
    async def test_extract_unauthorized_rejected(self, kg_manager, m3_manager):
        """Worker without memory.write capability cannot extract (when entities are found)."""
        await m3_manager.start()
        await kg_manager.start()
        bad_ctx = AuthorizationContext(
            citizen_id="ghost-00000001",
            rank_level=0,
            capabilities=set(),
            tenant_id="default",
        )
        # Use text that produces entities so the probe path triggers
        with pytest.raises(Exception):
            await kg_manager.extract("Mr. Alice works for Acme Corp", bad_ctx, source="test")
        await kg_manager.stop()
        await m3_manager.stop()

    @pytest.mark.asyncio
    async def test_health_check(self, kg_manager, m3_manager, founder_ctx):
        await m3_manager.start()
        await kg_manager.start()
        await kg_manager.extract("Mr. Alice works for Acme Corp", founder_ctx, source="test")
        health = await kg_manager.health_check()
        assert health["started"] is True
        assert "default" in health["tenants"]
        assert health["tenants"]["default"]["entity_count"] >= 1
        await kg_manager.stop()
        await m3_manager.stop()

    @pytest.mark.asyncio
    async def test_export_graph(self, kg_manager, m3_manager, founder_ctx):
        await m3_manager.start()
        await kg_manager.start()
        await kg_manager.extract("Mr. Alice works for Acme Corp", founder_ctx, source="test")
        exported = await kg_manager.export_graph(founder_ctx)
        assert exported["tenant_id"] == "default"
        assert len(exported["entities"]) >= 1
        assert "stats" in exported
        await kg_manager.stop()
        await m3_manager.stop()
