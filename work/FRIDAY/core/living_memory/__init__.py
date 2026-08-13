"""FRIDAY Age V — Milestone 3: Living Memory Ω

Biological memory infrastructure for the FRIDAY civilization.

This package is 100% additive — it does NOT modify Age IV's `core/memory.py`.
It composes with:
    - core/civilization/* (Citizen, Identity, Reputation, Manager)
    - core/governance/* (Constitution, PolicyEngine, ApprovalGate)
    - core/runtime/v5/* (DistributedEventBus, DistributedRuntime)

Modules:
    - base:               MemoryID, MemoryType, MemoryState, Memory, MemoryMetadata,
                          errors, lifecycle transition table
    - provenance:         Hash-chained provenance (WHO/WHEN/WHERE/WHAT/VERSION)
    - authz:              Capability-based authorization (fail-closed)
    - immune:             MemoryImmuneSystem (validates every incoming memory)
    - episodic:           EpisodicMemory (experiences + context)
    - semantic:          SemanticMemory (facts + predicate_key + value)
    - working:           WorkingMemory (bounded, TTL, priority-ordered)
    - procedural:        ProceduralMemory (workflows + success rates)
    - association:       AssociationGraph (weighted, bounded degree+depth)
    - consolidation:     ConsolidationEngine (governed promotion to CONSOLIDATED)
    - decay:              DecayEngine (governed decay + archival + forced eviction)
    - contradiction:     ContradictionDetector (never silent overwrite)
    - persistence:       InMemory / JSONFile / Redis adapters
    - observability:    MemoryMetrics + MemoryEvent emission
    - manager:           LivingMemoryManager (unified facade)
    - governance_compat: ApprovalGate adapter

Feature flag: FRIDAY_LIVING_MEMORY=1 enables distributed mode (publishes
events on DistributedEventBus). Default local mode is fully functional.
"""
from .base import (
    LivingMemoryError, AuthorizationError, LifecycleError, ValidationError,
    ContradictionError, ImmuneRejection, PersistenceError, ResourceLimitError,
    MemoryID, MemoryType, MemoryState, MemoryMetadata, Memory,
    HealthState, ALLOWED_TRANSITIONS, validate_transition,
    now_utc, new_correlation_id, payload_size_bytes, MAX_PAYLOAD_BYTES,
)
from .provenance import Provenance, ProvenanceEntry
from .authz import (
    MemoryCapability, AuthorizationContext, AuthorizationGate,
    REQUIRES_FOUNDER_APPROVAL,
)
from .immune import MemoryImmuneSystem, ImmuneReport
from .episodic import EpisodicMemory
from .semantic import SemanticMemory
from .working import WorkingMemory, WorkingMemoryItem
from .procedural import ProceduralMemory
from .association import AssociationEdge, AssociationMemory, AssociationGraph
from .consolidation import (
    ConsolidationConfig, ConsolidationCandidate, ConsolidationResult,
    ConsolidationEngine,
)
from .decay import DecayConfig, DecayCandidate, DecayResult, DecayEngine
from .contradiction import (
    ContradictionType, ContradictionStatus, ContradictionRecord,
    ContradictionDetector,
)
from .persistence import (
    PersistenceAdapter, InMemoryPersistence, JSONFilePersistence,
    RedisPersistence, RecoveryReport,
)
from .observability import MemoryEvent, MemoryMetrics, ObservabilityHub
from .manager import LivingMemoryManager, LivingMemoryConfig, TenantState
from .governance_compat import is_approval_approved

__all__ = [
    # Errors
    "LivingMemoryError", "AuthorizationError", "LifecycleError",
    "ValidationError", "ContradictionError", "ImmuneRejection",
    "PersistenceError", "ResourceLimitError",
    # Base
    "MemoryID", "MemoryType", "MemoryState", "MemoryMetadata", "Memory",
    "HealthState", "ALLOWED_TRANSITIONS", "validate_transition",
    "now_utc", "new_correlation_id", "payload_size_bytes", "MAX_PAYLOAD_BYTES",
    # Provenance
    "Provenance", "ProvenanceEntry",
    # Authz
    "MemoryCapability", "AuthorizationContext", "AuthorizationGate",
    "REQUIRES_FOUNDER_APPROVAL",
    # Immune
    "MemoryImmuneSystem", "ImmuneReport",
    # Memory types
    "EpisodicMemory", "SemanticMemory", "WorkingMemory", "WorkingMemoryItem",
    "ProceduralMemory", "AssociationMemory", "AssociationEdge", "AssociationGraph",
    # Engines
    "ConsolidationConfig", "ConsolidationCandidate", "ConsolidationResult",
    "ConsolidationEngine",
    "DecayConfig", "DecayCandidate", "DecayResult", "DecayEngine",
    "ContradictionType", "ContradictionStatus", "ContradictionRecord",
    "ContradictionDetector",
    # Persistence
    "PersistenceAdapter", "InMemoryPersistence", "JSONFilePersistence",
    "RedisPersistence", "RecoveryReport",
    # Observability
    "MemoryEvent", "MemoryMetrics", "ObservabilityHub",
    # Manager
    "LivingMemoryManager", "LivingMemoryConfig", "TenantState",
    # Compat
    "is_approval_approved",
]

__version__ = "1.0.0"
