"""Knowledge Graph — Relationship Detector.

Detects relationships between entities based on:
    1. Co-occurrence in the same text (within N tokens)
    2. Syntactic patterns ("X works for Y", "X is in Y", "X created Y")
    3. Prepositional patterns ("X's Y", "Y of X")

Returns Relationship objects with weight = confidence in the relationship.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Tuple

from .entity import Entity
from .extractor import ExtractedMention
from .relationship import Relationship, RelationshipType

logger = logging.getLogger("friday.knowledge_graph.relationship_detector")


# Syntactic patterns for relationship detection.
# Each tuple: (regex, relationship_type, weight, group_order)
# group_order: which group is source (1) vs target (2)
_SYNTACTIC_PATTERNS = [
    # "X works for Y" / "X works at Y"
    (re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+works?\s+(?:for|at)\s+([A-Z][A-Za-z0-9 &\-\.]{1,50})\b"),
     RelationshipType.WORKS_FOR, 0.8, (1, 2)),
    # "X is located in Y"
    (re.compile(r"\b([A-Z][A-Za-z0-9 &\-\.]{1,50})\s+is\s+located\s+in\s+([A-Z][A-Za-z ]{2,50})\b"),
     RelationshipType.LOCATED_IN, 0.8, (1, 2)),
    # "X is part of Y"
    (re.compile(r"\b([A-Z][A-Za-z0-9 &\-\.]{1,50})\s+is\s+(?:a\s+)?part\s+of\s+([A-Z][A-Za-z ]{2,50})\b"),
     RelationshipType.PART_OF, 0.8, (1, 2)),
    # "X created Y" / "X developed Y" / "X built Y"
    (re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:created|developed|built|designed)\s+([A-Z][A-Za-z0-9 &\-\.]{1,50})\b"),
     RelationshipType.CREATED_BY, 0.75, (2, 1)),  # Y was created by X
    # "X depends on Y"
    (re.compile(r"\b([A-Z][A-Za-z0-9 &\-\.]{1,50})\s+depends?\s+on\s+([A-Z][A-Za-z0-9 &\-\.]{1,50})\b"),
     RelationshipType.DEPENDS_ON, 0.75, (1, 2)),
    # "X is similar to Y"
    (re.compile(r"\b([A-Z][A-Za-z0-9 &\-\.]{1,50})\s+is\s+similar\s+to\s+([A-Z][A-Za-z0-9 &\-\.]{1,50})\b"),
     RelationshipType.SIMILAR_TO, 0.7, (1, 2)),
    # "X owns Y" / "X acquired Y"
    (re.compile(r"\b([A-Z][A-Za-z0-9 &\-\.]{1,50})\s+(?:owns|acquired|bought)\s+([A-Z][A-Za-z0-9 &\-\.]{1,50})\b"),
     RelationshipType.OWNS, 0.8, (1, 2)),
    # "X is a member of Y"
    (re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+is\s+(?:a\s+)?member\s+of\s+([A-Z][A-Za-z0-9 &\-\.]{1,50})\b"),
     RelationshipType.MEMBER_OF, 0.8, (1, 2)),
]


@dataclass
class DetectionResult:
    """Result of relationship detection."""
    relationships: List[Relationship] = field(default_factory=list)
    pattern_matches: int = 0
    co_occurrence_matches: int = 0

    def to_dict(self):
        return {
            "relationships": [r.to_dict() for r in self.relationships],
            "pattern_matches": self.pattern_matches,
            "co_occurrence_matches": self.co_occurrence_matches,
        }


class RelationshipDetector:
    """Detects relationships between entities in text.

    Two strategies:
        1. Syntactic patterns (high precision, low recall)
        2. Co-occurrence within a window (low precision, high recall)

    Co-occurrence relationships get a low weight (0.3) and the type RELATED_TO.
    Syntactic relationships get the pattern's confidence (0.7-0.85).
    """

    def __init__(
        self,
        co_occurrence_window: int = 5,
        max_relationships_per_text: int = 50,
    ):
        if co_occurrence_window < 1 or co_occurrence_window > 50:
            raise ValueError("co_occurrence_window must be 1..50")
        if max_relationships_per_text < 1 or max_relationships_per_text > 500:
            raise ValueError("max_relationships_per_text must be 1..500")
        self._co_window = co_occurrence_window
        self._max_per_text = max_relationships_per_text

    def detect(
        self,
        text: str,
        entities: List[Entity],
        mentions: List[ExtractedMention],
        tenant_id: str = "default",
    ) -> DetectionResult:
        """Detect relationships in text given extracted entities + mentions."""
        if not entities or len(mentions) < 2:
            return DetectionResult()

        result = DetectionResult()
        seen_pairs: set = set()  # (source_id, target_id, type) tuples

        # 1. Syntactic pattern detection
        for pattern, rel_type, weight, (src_grp, tgt_grp) in _SYNTACTIC_PATTERNS:
            for match in pattern.finditer(text):
                src_text = match.group(src_grp)
                tgt_text = match.group(tgt_grp)
                if not src_text or not tgt_text:
                    continue
                # Find entities matching these texts
                src_entity = self._find_entity_by_text(src_text, entities)
                tgt_entity = self._find_entity_by_text(tgt_text, entities)
                if not src_entity or not tgt_entity:
                    continue
                if src_entity.entity_id == tgt_entity.entity_id:
                    continue
                key = (src_entity.entity_id, tgt_entity.entity_id, rel_type.value)
                if key in seen_pairs:
                    continue
                try:
                    rel = Relationship.create(
                        source_entity_id=src_entity.entity_id,
                        target_entity_id=tgt_entity.entity_id,
                        rel_type=rel_type,
                        weight=weight,
                        tenant_id=tenant_id,
                    )
                    result.relationships.append(rel)
                    result.pattern_matches += 1
                    seen_pairs.add(key)
                except ValueError as e:
                    logger.debug(f"Skipping relationship {src_text}->{tgt_text}: {e}")
                if len(result.relationships) >= self._max_per_text:
                    return result

        # 2. Co-occurrence detection (within window)
        if len(mentions) >= 2 and len(result.relationships) < self._max_per_text:
            # Sort mentions by offset
            sorted_mentions = sorted(mentions, key=lambda m: m.start_offset)
            for i, m1 in enumerate(sorted_mentions):
                if len(result.relationships) >= self._max_per_text:
                    break
                # Look at next N mentions within window
                for j in range(i + 1, min(i + 1 + self._co_window, len(sorted_mentions))):
                    m2 = sorted_mentions[j]
                    # Don't link entities of same type (too noisy)
                    if m1.entity_type == m2.entity_type and m1.entity_type.value in ("date", "money"):
                        continue
                    e1 = self._find_entity_by_canonical(m1.canonical_name, entities)
                    e2 = self._find_entity_by_canonical(m2.canonical_name, entities)
                    if not e1 or not e2 or e1.entity_id == e2.entity_id:
                        continue
                    key = (e1.entity_id, e2.entity_id, RelationshipType.RELATED_TO.value)
                    rev_key = (e2.entity_id, e1.entity_id, RelationshipType.RELATED_TO.value)
                    if key in seen_pairs or rev_key in seen_pairs:
                        continue
                    try:
                        rel = Relationship.create(
                            source_entity_id=e1.entity_id,
                            target_entity_id=e2.entity_id,
                            rel_type=RelationshipType.RELATED_TO,
                            weight=0.3,  # low confidence for co-occurrence
                            bidirectional=True,
                            tenant_id=tenant_id,
                        )
                        result.relationships.append(rel)
                        result.co_occurrence_matches += 1
                        seen_pairs.add(key)
                    except ValueError:
                        continue
                    if len(result.relationships) >= self._max_per_text:
                        break

        return result

    def _find_entity_by_text(self, text: str, entities: List[Entity]) -> Entity | None:
        """Find an entity whose canonical name or alias matches text."""
        from .entity import canonicalize_name
        try:
            canonical = canonicalize_name(text)
        except ValueError:
            return None
        for e in entities:
            if e.canonical_name == canonical or canonical in e.aliases:
                return e
        return None

    def _find_entity_by_canonical(self, canonical: str, entities: List[Entity]) -> Entity | None:
        for e in entities:
            if e.canonical_name == canonical or canonical in e.aliases:
                return e
        return None


__all__ = ["RelationshipDetector", "DetectionResult"]
