"""Knowledge Graph — Entity Extractor.

Extracts entities from text using rule-based patterns. The extractor is
deterministic and pluggable — application code can register custom patterns
or replace the extractor entirely with an LLM-based one.

Default patterns:
    - PERSON: Capitalized names (First Last), "Mr./Ms./Dr. X"
    - ORGANIZATION: All-caps acronyms (3+ chars), "X Corp/Inc/Ltd/LLC"
    - PLACE: "in <Capitalized>", "City, Country"
    - DATE: ISO dates, "Month DD, YYYY", "DD/MM/YYYY"
    - MONEY: "$X", "X USD/EUR/GBP"
    - CONCEPT: Quoted strings, CamelCase identifiers
    - EMAIL/URL: Standard patterns

Each extraction records:
    - The matched text
    - The entity type
    - The character offset (for relationship detection)
    - The confidence (based on pattern specificity)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .entity import Entity, EntityType, canonicalize_name

logger = logging.getLogger("friday.knowledge_graph.extractor")


@dataclass
class ExtractedMention:
    """A single entity mention found in text."""
    text: str
    entity_type: EntityType
    start_offset: int
    end_offset: int
    confidence: float = 0.5
    pattern_name: str = ""
    canonical_name: str = ""

    def __post_init__(self):
        if not self.canonical_name:
            try:
                self.canonical_name = canonicalize_name(self.text)
            except ValueError:
                self.canonical_name = self.text.lower().strip()


@dataclass
class ExtractionResult:
    """Result of extracting entities + relationships from a text."""
    entities: List[Entity] = field(default_factory=list)
    mentions: List[ExtractedMention] = field(default_factory=list)
    source_text: str = ""
    source_memory_id: str = ""
    extractor_version: str = "rule-based-v1"
    extracted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confidence: float = 0.5
    ambiguous: List[Tuple[Entity, Entity]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "mentions": [
                {
                    "text": m.text, "entity_type": m.entity_type.value,
                    "start_offset": m.start_offset, "end_offset": m.end_offset,
                    "confidence": m.confidence, "pattern_name": m.pattern_name,
                    "canonical_name": m.canonical_name,
                }
                for m in self.mentions
            ],
            "source_text": self.source_text[:500],  # truncate for storage
            "source_memory_id": self.source_memory_id,
            "extractor_version": self.extractor_version,
            "extracted_at": self.extracted_at,
            "confidence": round(self.confidence, 6),
            "ambiguous_count": len(self.ambiguous),
        }


# ----------------------------------------------------------------------
# Patterns
# ----------------------------------------------------------------------

# Pre-compiled regex patterns. Each tuple: (pattern, entity_type, confidence, name)
_EXTRACTION_PATTERNS: List[Tuple[re.Pattern, EntityType, float, str]] = [
    # Email addresses (high confidence — unambiguous)
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
     EntityType.OTHER, 0.95, "email"),
    # URLs (high confidence)
    (re.compile(r"\bhttps?://[^\s<>\"]+\b", re.IGNORECASE),
     EntityType.DOCUMENT, 0.95, "url"),
    # ISO dates (YYYY-MM-DD)
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
     EntityType.DATE, 0.9, "iso_date"),
    # Money ($X or X USD/EUR/GBP)
    (re.compile(r"\b\$\s?\d{1,3}(?:[,\d]{0,9})(?:\.\d{1,2})?\b|\b\d+(?:,\d{3})*(?:\.\d+)?\s(?:USD|EUR|GBP|NGN)\b"),
     EntityType.MONEY, 0.9, "money"),
    # Organizations: X Corp/Inc/Ltd/LLC/GmbH
    # Capture the full org name INCLUDING the suffix (e.g. "Acme Corp")
    # so the entity name matches what relationship detector captures.
    (re.compile(r"\b((?:[A-Z][A-Za-z0-9]+(?:&| )?){1,4}(?:Corp|Corporation|Inc|Incorporated|Ltd|Limited|LLC|GmbH|SA|AG))\b"),
     EntityType.ORGANIZATION, 0.85, "org_suffix"),
    # All-caps acronyms (3-6 chars, must have word boundaries)
    (re.compile(r"\b[A-Z]{3,6}\b"),
     EntityType.ORGANIZATION, 0.7, "acronym"),
    # Person: Mr./Ms./Dr./Prof. Lastname
    (re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof)\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"),
     EntityType.PERSON, 0.85, "person_title"),
    # Person: First Last (two capitalized words, not at start of sentence)
    (re.compile(r"(?<=\. )([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b"),
     EntityType.PERSON, 0.6, "person_firstlast"),
    # Places: in <Capitalized Place>
    (re.compile(r"\b(?:in|from|at|near)\s+([A-Z][A-Za-z ]{2,30})\b"),
     EntityType.PLACE, 0.65, "place_preposition"),
    # Concepts: "quoted strings"
    (re.compile(r'"([^"]{3,80})"'),
     EntityType.CONCEPT, 0.7, "quoted_concept"),
    # Projects: project:X or #project-name
    (re.compile(r"\bproject:([A-Za-z0-9_\-]+)", re.IGNORECASE),
     EntityType.PROJECT, 0.85, "project_tag"),
    # Tools: tool:X
    (re.compile(r"\btool:([A-Za-z0-9_\-]+)", re.IGNORECASE),
     EntityType.TOOL, 0.85, "tool_tag"),
]


class EntityExtractor:
    """Rule-based entity extractor.

    Pluggable: subclasses can override `extract_mentions` to use LLMs.
    """

    def __init__(self, custom_patterns: Optional[List[Tuple[re.Pattern, EntityType, float, str]]] = None):
        self._patterns = list(_EXTRACTION_PATTERNS)
        if custom_patterns:
            self._patterns.extend(custom_patterns)

    @property
    def version(self) -> str:
        return "rule-based-v1"

    def extract_mentions(self, text: str) -> List[ExtractedMention]:
        """Extract all entity mentions from text."""
        if not text or not isinstance(text, str):
            return []
        if len(text) > 10_000:
            # Cap text size to prevent DoS
            text = text[:10_000]
            logger.warning("Text truncated to 10,000 chars for extraction")

        mentions: List[ExtractedMention] = []
        seen_spans: List[Tuple[int, int]] = []

        for pattern, etype, confidence, name in self._patterns:
            for match in pattern.finditer(text):
                start, end = match.span()
                # Skip if this span overlaps with an existing mention
                if any(s <= start < e or s < end <= e for s, e in seen_spans):
                    continue
                # For patterns with groups, use the group; else use full match
                if match.groups():
                    # Use the first non-empty group
                    matched_text = next((g for g in match.groups() if g), match.group(0))
                    # Adjust offsets to the group
                    group_start = match.start(1) if match.group(1) else start
                    group_end = match.end(1) if match.group(1) else end
                else:
                    matched_text = match.group(0)
                    group_start, group_end = start, end

                if not matched_text or len(matched_text.strip()) < 2:
                    continue

                mentions.append(ExtractedMention(
                    text=matched_text.strip(),
                    entity_type=etype,
                    start_offset=group_start,
                    end_offset=group_end,
                    confidence=confidence,
                    pattern_name=name,
                ))
                seen_spans.append((group_start, group_end))

        # Sort by offset
        mentions.sort(key=lambda m: m.start_offset)
        return mentions

    def extract_entities(
        self,
        text: str,
        tenant_id: str = "default",
        source_memory_id: str = "",
    ) -> ExtractionResult:
        """Extract entities from text. Returns ExtractionResult."""
        mentions = self.extract_mentions(text)
        entities: List[Entity] = []
        seen_ids: Dict[str, Entity] = {}

        for m in mentions:
            try:
                entity = Entity.create(
                    name=m.text,
                    entity_type=m.entity_type,
                    tenant_id=tenant_id,
                    confidence=m.confidence,
                )
                if entity.entity_id in seen_ids:
                    # Same entity mentioned multiple times — add as alias if text differs
                    existing = seen_ids[entity.entity_id]
                    if m.canonical_name != existing.canonical_name:
                        try:
                            existing.add_alias(m.canonical_name)
                        except ValueError:
                            pass  # alias cap reached
                else:
                    seen_ids[entity.entity_id] = entity
                    entities.append(entity)
            except (ValueError, TypeError) as e:
                logger.debug(f"Skipping mention {m.text!r}: {e}")
                continue

        # Compute overall confidence
        if entities:
            avg_conf = sum(e.confidence for e in entities) / len(entities)
        else:
            avg_conf = 0.0

        return ExtractionResult(
            entities=entities,
            mentions=mentions,
            source_text=text,
            source_memory_id=source_memory_id,
            extractor_version=self.version,
            confidence=avg_conf,
        )


__all__ = [
    "EntityExtractor", "ExtractedMention", "ExtractionResult",
]
