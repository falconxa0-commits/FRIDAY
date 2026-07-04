"""Friday Learning System — learns from user corrections.

When the user says "no, that's wrong" or "actually it's...", Friday:
1. Records the correction in memory with high importance
2. Updates confidence on similar future queries
3. Surfaces the correction when a similar topic comes up
"""
import datetime
import logging
import uuid
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class FridayLearningSystem:
    """Friday learns from its mistakes via memory-based correction."""

    def __init__(self):
        self._corrections: List[dict] = []

    async def record_correction(self, original: str, correction: str, context: Optional[dict] = None):
        """Record a user correction."""
        entry = {
            "id": f"corr_{uuid.uuid4().hex[:8]}",
            "original": original,
            "correction": correction,
            "context": context or {},
            "timestamp": datetime.datetime.now().isoformat(),
        }
        self._corrections.append(entry)
        logger.info(f"Correction recorded: {entry['id']}")
        return entry

    async def get_confidence(self, topic: str) -> float:
        """Return confidence score (0-1) based on correction history for this topic."""
        if not self._corrections:
            return 1.0
        topic_lower = topic.lower()
        related = [c for c in self._corrections
                   if topic_lower in c["original"].lower() or topic_lower in c["correction"].lower()]
        if not related:
            return 1.0
        # More corrections = lower confidence
        return max(0.3, 1.0 - (len(related) * 0.15))

    async def check_similar_corrections(self, query: str) -> List[dict]:
        """Check if similar topics have been corrected before."""
        query_words = set(query.lower().split())
        results = []
        for c in self._corrections:
            corr_words = set(c["original"].lower().split()) | set(c["correction"].lower().split())
            overlap = len(query_words & corr_words)
            if overlap > 0:
                results.append({**c, "relevance": overlap / max(len(query_words), 1)})
        results.sort(key=lambda x: -x["relevance"])
        return results[:5]

    def get_all_corrections(self) -> List[dict]:
        return list(self._corrections)
