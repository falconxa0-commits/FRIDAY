# M4 — Knowledge Graph Ω — Architecture Design

**Composes with:** M3 Living Memory (SemanticMemory, EpisodicMemory, AssociationGraph, LivingMemoryManager)
**Does NOT duplicate:** M3 storage, M3 provenance, M3 authz, M3 immune system
**Does NOT modify:** Any Age IV / M1 / M2 / M3 source

## Biological Role

The Knowledge Graph is the **semantic memory tissue** of the FRIDAY organism —
where individual facts (M3 SemanticMemory records) become a connected web of
understandable knowledge. If M3 is "neural tissue storing individual memories,"
M4 is the "cortical map" that links them into a queryable knowledge structure.

## Layered Composition

```
┌──────────────────────────────────────────────────────────────────┐
│              KnowledgeGraphManager (M4 facade)                    │
│   extract_entities / extract_relationships / query / traverse     │
│   resolve_merge_conflict / export_graph / import_graph            │
└──────────────────────────────────────────────────────────────────┘
         │                                              │
         ▼ (composes with, does NOT replace)            ▼
┌──────────────────────┐                  ┌──────────────────────────┐
│   EntityExtractor    │                  │  RelationshipDetector     │
│  (rule-based + LLM)  │                  │  (co-occurrence + syntax) │
└──────────────────────┘                  └──────────────────────────┘
         │                                              │
         └──────────────┬───────────────────────────────┘
                        ▼
              ┌────────────────────┐
              │  KnowledgeGraph    │  ← graph layer (per-tenant)
              │  (nodes + edges)   │
              └────────────────────┘
                        │
                        ▼ (delegates storage to M3)
              ┌──────────────────────────────────────────────┐
              │ M3 LivingMemoryManager (already certified)    │
              │  - SemanticMemory stores entity attributes     │
              │  - AssociationGraph stores relationships       │
              │  - Provenance, immune, authz all reused        │
              └──────────────────────────────────────────────┘
```

## Core Abstractions

### 1. Entity (knowledge graph node)

A typed entity (Person, Place, Concept, Event, Organization, etc.) with:
- `entity_id`: stable hash of (tenant_id, entity_type, canonical_name)
- `entity_type`: Person | Place | Organization | Concept | Event | Document | Tool | custom
- `canonical_name`: normalized name (lowercase, whitespace-collapsed)
- `aliases`: List[str] alternative names
- `attributes`: Dict[str, Any] — stored as M3 SemanticMemory records
- `memory_id`: ID of the M3 SemanticMemory that anchors this entity
- `tenant_id`: from M3

### 2. Relationship (knowledge graph edge)

A typed, weighted, directed (or bidirectional) relationship:
- `source_entity_id`, `target_entity_id`: Entity IDs
- `relationship_type`: WORKS_FOR | LOCATED_IN | CREATED_BY | DEPENDS_ON | SIMILAR_TO | MENTIONS | custom
- `weight`: 0..1 (confidence in the relationship)
- `evidence`: List of M3 EpisodicMemory IDs that observed this relationship
- `first_seen_at` / `last_reinforced_at`
- `tenant_id`

Stored as M3 AssociationMemory.

### 3. KnowledgeQuery

SPARQL-inspired but simplified:
- `entity_filter`: type + name pattern + attribute constraints
- `relationship_filter`: type + direction + weight threshold
- `traversal`: max_depth + min_weight + relationship_type whitelist
- `aggregation`: count | sum | avg | collect
- `limit` + `offset`

### 4. ExtractionResult

Output of entity/relationship extraction from text:
- `entities`: List[Entity]
- `relationships`: List[Relationship]
- `source_memory_id`: M3 memory that was the source text
- `extractor_version`: str
- `confidence`: float
- `ambiguous_entities`: List of entities needing disambiguation

### 5. MergeConflict

When two extraction passes produce conflicting information:
- `conflict_type`: NAME_COLLISION | ATTRIBUTE_CONTRADICTION | RELATIONSHIP_CONFLICT
- `entity_a`, `entity_b`
- `evidence_a`, `evidence_b`
- `resolution`: PENDING | KEEP_A | KEEP_B | MERGE | SPLIT
- `requires_founder_approval`: bool

## M4 Module Layout

```
core/knowledge_graph/
├── __init__.py            — package exports
├── entity.py              — Entity dataclass + EntityType enum
├── relationship.py        — Relationship dataclass + RelationshipType enum
├── extractor.py           — EntityExtractor (rule-based, pluggable)
├── relationship_detector.py — RelationshipDetector (co-occurrence + syntax)
├── graph.py               — KnowledgeGraph (per-tenant node index)
├── query.py               — KnowledgeQuery + QueryEngine
├── merge.py               — MergeConflict + MergeResolver (governed)
├── manager.py             — KnowledgeGraphManager (facade)
└── governance_compat.py   — ApprovalGate adapter
```

## M3 Integration Points

| M4 Operation | M3 Service Used |
|--------------|-----------------|
| Create entity | `LivingMemoryManager.remember(SemanticMemory)` |
| Set entity attribute | `LivingMemoryManager.remember(SemanticMemory)` |
| Create relationship | `LivingMemoryManager.associate(...)` |
| Add evidence | `EpisodicMemory.create(participants=[entity_ids])` |
| Query entity attributes | `LivingMemoryManager.search(...)` |
| Traverse relationships | `LivingMemoryManager.traverse(...)` |
| Resolve merge conflict | `LivingMemoryManager.resolve_contradiction(...)` |
| Provenance | Inherited from M3 |
| Authorization | Inherited from M3 |
| Immune system | Inherited from M3 |
| Tenant isolation | Inherited from M3 |

## Bounded Resources (homeostasis)

| Resource | Default |
|----------|---------|
| Max entities per tenant | 50,000 |
| Max relationships per entity | 256 |
| Max aliases per entity | 32 |
| Max attributes per entity | 128 |
| Max extraction batch size | 1,000 chars / 100 entities |
| Max query traversal depth | 6 |
| Max query result size | 500 |
| Merge conflict queue | 1,000 per tenant |

## Adversarial Attack Surface

1. Entity injection — attacker creates fake entities
2. Relationship poisoning — attacker forges relationships
3. Alias hijacking — attacker adds legitimate name as alias to fake entity
4. Attribute overwrite — attacker tries to overwrite high-authority attribute
5. Merge conflict abuse — attacker triggers many merge conflicts
6. Query injection — attacker crafts queries to traverse sensitive subgraphs
7. Extraction poisoning — attacker submits text with adversarial entity mentions
8. Disambiguation bypass — attacker exploits ambiguous entity resolution

## Fitness Score Targets (M4)

| Domain | Target |
|--------|--------|
| Security | ≥ 9.5 |
| Recovery | ≥ 9.0 |
| Consistency | ≥ 9.5 |
| Concurrency | ≥ 9.0 |
| Performance | ≥ 9.0 |
| Observability | ≥ 9.0 |
| Resource Homeostasis | ≥ 9.5 |
| Architecture | ≥ 9.0 |
| Backward Compatibility | 10.0 |
| Test Quality | ≥ 9.5 |
| Operational Readiness | ≥ 8.5 |
| **WEIGHTED TOTAL** | **≥ 9.0** |
