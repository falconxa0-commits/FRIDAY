"""FRIDAY Age V — Milestone 4: Knowledge Graph Ω

A knowledge graph layer that composes with M3 Living Memory. Extracts
entities + relationships from text, stores them via M3's SemanticMemory +
AssociationMemory, and provides SPARQL-inspired queries.

This package is 100% additive — does NOT modify M3 or any earlier milestone.

Modules:
    - entity:                Entity + EntityType
    - relationship:          Relationship + RelationshipType
    - extractor:             EntityExtractor (rule-based, pluggable)
    - relationship_detector: RelationshipDetector (co-occurrence + syntax)
    - graph:                 KnowledgeGraph (per-tenant in-memory index)
    - query:                 KnowledgeQuery + QueryEngine
    - merge:                 MergeConflict + MergeResolver (governed)
    - manager:               KnowledgeGraphManager (facade)

Composes with M3:
    - Entity anchors stored as M3 SemanticMemory records
    - Relationships stored as M3 AssociationMemory records
    - Evidence stored as M3 EpisodicMemory records
    - Provenance, authz, immune validation inherited from M3
"""
from .entity import Entity, EntityType, canonicalize_name, derive_entity_id
from .relationship import (
    Relationship, RelationshipType, BIDIRECTIONAL_TYPES,
    derive_relationship_id,
)
from .extractor import EntityExtractor, ExtractedMention, ExtractionResult
from .relationship_detector import RelationshipDetector, DetectionResult
from .graph import KnowledgeGraph, GraphStats
from .query import (
    EntityFilter, RelationshipFilter, TraversalSpec,
    KnowledgeQuery, QueryResult, QueryEngine,
)
from .merge import (
    ConflictType, ConflictResolution, MergeConflict, MergeResolver,
    HIGH_STAKES_TYPES,
)
from .manager import KnowledgeGraphManager, KnowledgeGraphConfig, TenantKGState

__all__ = [
    # Entity
    "Entity", "EntityType", "canonicalize_name", "derive_entity_id",
    # Relationship
    "Relationship", "RelationshipType", "BIDIRECTIONAL_TYPES",
    "derive_relationship_id",
    # Extractor
    "EntityExtractor", "ExtractedMention", "ExtractionResult",
    # Detector
    "RelationshipDetector", "DetectionResult",
    # Graph
    "KnowledgeGraph", "GraphStats",
    # Query
    "EntityFilter", "RelationshipFilter", "TraversalSpec",
    "KnowledgeQuery", "QueryResult", "QueryEngine",
    # Merge
    "ConflictType", "ConflictResolution", "MergeConflict", "MergeResolver",
    "HIGH_STAKES_TYPES",
    # Manager
    "KnowledgeGraphManager", "KnowledgeGraphConfig", "TenantKGState",
]

__version__ = "1.0.0"
