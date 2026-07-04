"""Self-Improvement Engine — Friday analyzes its own performance and proposes improvements.

Analyzes failed tool calls, user corrections, slow responses, repeated
questions, and skill gaps. Proposes concrete improvements that go
through the ledger for human approval. Does NOT self-modify.
"""
import datetime
import logging
import uuid
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SelfImprovementEngine:
    """Friday analyzes its own performance and proposes improvements."""

    def __init__(self):
        self._proposals: List[dict] = []
        self._failed_calls: List[dict] = []
        self._user_corrections: List[dict] = []
        self._slow_responses: List[dict] = []
        self._unanswered_requests: List[dict] = []

    def record_failed_call(self, tool: str, error: str, context: Optional[dict] = None):
        """Record a failed tool call."""
        self._failed_calls.append({
            "tool": tool, "error": error, "context": context or {},
            "timestamp": datetime.datetime.now().isoformat(),
        })

    def record_user_correction(self, original: str, correction: str):
        """Record a user correction."""
        self._user_corrections.append({
            "original": original, "correction": correction,
            "timestamp": datetime.datetime.now().isoformat(),
        })

    def record_slow_response(self, duration_seconds: float, query: str):
        """Record a slow response."""
        self._slow_responses.append({
            "duration": duration_seconds, "query": query[:200],
            "timestamp": datetime.datetime.now().isoformat(),
        })

    def record_unanswered(self, request: str):
        """Record a request Friday couldn't handle."""
        self._unanswered_requests.append({
            "request": request[:200],
            "timestamp": datetime.datetime.now().isoformat(),
        })

    async def analyze_performance(self, days: int = 7) -> dict:
        """Analyze performance over the last N days."""
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()

        recent_failures = [f for f in self._failed_calls if f["timestamp"] >= cutoff]
        recent_corrections = [c for c in self._user_corrections if c["timestamp"] >= cutoff]
        recent_slow = [s for s in self._slow_responses if s["timestamp"] >= cutoff]
        recent_unanswered = [u for u in self._unanswered_requests if u["timestamp"] >= cutoff]

        return {
            "days_analyzed": days,
            "failed_calls": len(recent_failures),
            "user_corrections": len(recent_corrections),
            "slow_responses": len(recent_slow),
            "unanswered_requests": len(recent_unanswered),
            "most_common_errors": self._top_errors(recent_failures),
            "correction_topics": self._extract_topics(recent_corrections),
        }

    async def propose_improvements(self) -> List[dict]:
        """Generate concrete improvement proposals from performance data."""
        proposals = []

        # Skill gap proposals
        if self._unanswered_requests:
            proposals.append({
                "id": f"prop_{uuid.uuid4().hex[:8]}",
                "type": "new_skill",
                "title": f"Build skill for {len(self._unanswered_requests)} unanswered request(s)",
                "description": f"Friday received {len(self._unanswered_requests)} requests it couldn't handle. "
                               f"Sample: '{self._unanswered_requests[0]['request'][:100]}'",
                "priority": "high" if len(self._unanswered_requests) > 5 else "medium",
            })

        # Correction-based proposals
        if len(self._user_corrections) >= 3:
            proposals.append({
                "id": f"prop_{uuid.uuid4().hex[:8]}",
                "type": "knowledge_update",
                "title": f"Review {len(self._user_corrections)} user corrections",
                "description": "Multiple corrections suggest Friday has knowledge gaps in specific areas. "
                               "Review corrections and update system prompt or add RAG context.",
                "priority": "medium",
            })

        # Performance proposals
        if self._slow_responses:
            avg_slow = sum(s["duration"] for s in self._slow_responses) / len(self._slow_responses)
            proposals.append({
                "id": f"prop_{uuid.uuid4().hex[:8]}",
                "type": "performance",
                "title": f"Optimize slow responses (avg {avg_slow:.1f}s)",
                "description": f"{len(self._slow_responses)} responses took longer than expected. "
                               "Consider caching, pre-loading, or switching to a faster model tier.",
                "priority": "low",
            })

        # Tool failure proposals
        if self._failed_calls:
            proposals.append({
                "id": f"prop_{uuid.uuid4().hex[:8]}",
                "type": "integration_fix",
                "title": f"Fix {len(self._failed_calls)} failed tool call(s)",
                "description": f"Tool failures detected. Most common: {self._top_errors(self._failed_calls)[:1]}",
                "priority": "high",
            })

        self._proposals.extend(proposals)
        return proposals

    def get_proposals(self) -> List[dict]:
        """Return all proposals (pending and approved)."""
        return list(self._proposals)

    def _top_errors(self, failures: List[dict]) -> List[str]:
        from collections import Counter
        errors = [f["error"][:100] for f in failures]
        return [e for e, _ in Counter(errors).most_common(5)]

    def _extract_topics(self, corrections: List[dict]) -> List[str]:
        """Extract common topics from corrections."""
        topics = set()
        for c in corrections:
            words = set(c["original"].lower().split()) & set(c["correction"].lower().split())
            topics.update(w for w in words if len(w) > 4)
        return list(topics)[:10]
