"""Behavioral Pattern Engine — discovers real patterns from real interactions.

Friday observes what you actually do over time and surfaces recurring
patterns. Patterns are *discovered*, not programmed — Friday notices
what you actually do, then uses GLM to synthesise the patterns.

Examples it can learn:
  - "You always reschedule Tuesday meetings" → warn proactively
  - "You always ask for music when coding" → suggest without being asked
  - "You check weather before leaving for meetings" → offer automatically
  - "You research topics in batches" → group related queries

The engine never fabricates patterns — it only reports patterns with
real evidence (minimum N occurrences in the interaction history).
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Minimum occurrences for a pattern to be reported
MIN_PATTERN_OCCURRENCES = 3


class PatternEngine:
    """Learns real behavioral patterns from Friday's interaction history."""

    def __init__(self, memory=None):
        # memory is an optional FridayMemory instance — not required,
        # the engine keeps its own interaction log
        self.memory = memory
        self.interactions: List[dict] = []
        self.patterns: List[dict] = []

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    async def observe(self, interaction: dict) -> None:
        """Record a real interaction for pattern analysis.

        Expected fields:
            - timestamp: ISO-8601 (added if missing)
            - action_type: 'chat' | 'tool_call' | 'skill' | 'integration' | 'other'
            - content: str (the user message or action description)
            - context: dict (optional — current mode, time-of-day, etc.)
            - outcome: 'success' | 'error' | 'pending' | 'unknown' (optional)
        """
        if "timestamp" not in interaction:
            interaction["timestamp"] = datetime.datetime.now().isoformat()
        # Required fields with sensible defaults
        interaction.setdefault("action_type", "other")
        interaction.setdefault("content", "")
        interaction.setdefault("context", {})
        interaction.setdefault("outcome", "unknown")
        self.interactions.append(interaction)
        logger.debug(
            "PatternEngine: observed interaction #%d (total=%d)",
            len(self.interactions), len(self.interactions),
        )

    # ------------------------------------------------------------------
    # Pattern discovery — real analysis, not hardcoded
    # ------------------------------------------------------------------

    async def discover_patterns(self) -> List[dict]:
        """Analyze interaction history and surface real recurring patterns.

        Returns a list of pattern dicts with at least:
            - type: 'sequential' | 'keyword' | 'time_of_day' | 'action_pair'
            - description: human-readable pattern description
            - evidence_count: how many times this pattern was observed
            - examples: list of interaction indices that match

        Only patterns with evidence_count >= MIN_PATTERN_OCCURRENCES are returned.
        """
        if len(self.interactions) < MIN_PATTERN_OCCURRENCES:
            return []

        matches = self._collect_pattern_matches()
        scored = self._score_patterns(matches)
        patterns = self._filter_top_patterns(scored)

        # Optional: use GLM to synthesise higher-level patterns
        # (only if GLM is available — fall back gracefully)
        patterns = await self._maybe_add_glm_pattern(patterns)

        self.patterns = patterns
        return patterns

    # ------------------------------------------------------------------
    # discover_patterns() helpers — each has a single responsibility.
    # ------------------------------------------------------------------
    def _collect_pattern_matches(self) -> Dict[str, Dict[Any, Dict[str, Any]]]:
        """Collect raw pattern matches from ``self.interactions``.

        Returns a dict keyed by pattern family (``action_type``,
        ``keyword``, ``action_pair``, ``time_of_day``) whose values are
        themselves dicts mapping each pattern key to
        ``{"count": int, "examples": [interaction_index, ...]}``.

        No filtering by :data:`MIN_PATTERN_OCCURRENCES` happens here —
        callers (``_score_patterns`` / ``_filter_top_patterns``) decide
        which matches survive.
        """
        matches: Dict[str, Dict[Any, Dict[str, Any]]] = {
            "action_type": {},
            "keyword": {},
            "action_pair": {},
            "time_of_day": {},
        }

        # 1. Action-type frequency.
        type_counts = Counter(i["action_type"] for i in self.interactions)
        for action_type, count in type_counts.items():
            matches["action_type"][action_type] = {
                "count": count,
                "examples": [
                    i for i, x in enumerate(self.interactions)
                    if x["action_type"] == action_type
                ],
            }

        # 2. Keyword frequency — what topics come up most?
        word_counts: Dict[str, int] = defaultdict(int)
        for i in self.interactions:
            for word in i["content"].lower().split():
                # Filter very short words and common stopwords
                if len(word) > 4 and word not in {
                    "the", "and", "for", "that", "this", "with", "have",
                    "from", "your", "what", "about", "there", "which",
                }:
                    word_counts[word] += 1
        for word, count in word_counts.items():
            matches["keyword"][word] = {
                "count": count,
                "examples": [
                    i for i, x in enumerate(self.interactions)
                    if word in x["content"].lower()
                ],
            }

        # 3. Action pair sequences — "A then B" patterns.
        pair_counts: Dict[tuple, int] = defaultdict(int)
        pair_examples: Dict[tuple, List[int]] = defaultdict(list)
        for i in range(len(self.interactions) - 1):
            a = self.interactions[i]
            b = self.interactions[i + 1]
            a_key = (a["action_type"], " ".join(a["content"].lower().split()[:3]))
            b_key = (b["action_type"], " ".join(b["content"].lower().split()[:3]))
            pair = (a_key, b_key)
            pair_counts[pair] += 1
            pair_examples[pair].append(i)
        for pair, count in pair_counts.items():
            matches["action_pair"][pair] = {
                "count": count,
                "examples": list(pair_examples[pair]),
            }

        # 4. Time-of-day patterns.
        hour_counts: Dict[int, int] = defaultdict(int)
        hour_examples: Dict[int, List[int]] = defaultdict(list)
        for i, inter in enumerate(self.interactions):
            try:
                ts = datetime.datetime.fromisoformat(inter["timestamp"])
                hour = ts.hour
                hour_counts[hour] += 1
                hour_examples[hour].append(i)
            except (ValueError, KeyError):
                continue
        for hour, count in hour_counts.items():
            matches["time_of_day"][hour] = {
                "count": count,
                "examples": list(hour_examples[hour]),
            }

        return matches

    @staticmethod
    def _score_patterns(
        matches: Dict[str, Dict[Any, Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Convert raw matches into scored pattern descriptors.

        Each scored descriptor carries ``family``, ``key``, ``count``,
        ``examples``, and ``description``. No filtering happens here —
        :meth:`_filter_top_patterns` decides what makes it to the user.
        """
        scored: List[Dict[str, Any]] = []
        # Action-type patterns.
        for action_type, info in matches["action_type"].items():
            scored.append({
                "family": "action_type",
                "key": action_type,
                "count": info["count"],
                "examples": info["examples"],
                "description": (
                    f"You frequently perform '{action_type}' actions "
                    f"({info['count']} times)."
                ),
                "type": "action_type_frequency",
            })
        # Keyword patterns.
        for word, info in matches["keyword"].items():
            scored.append({
                "family": "keyword",
                "key": word,
                "count": info["count"],
                "examples": info["examples"],
                "description": (
                    f"You frequently mention '{word}' ({info['count']} times)."
                ),
                "type": "keyword_frequency",
            })
        # Action-pair patterns.
        for pair, info in matches["action_pair"].items():
            a_desc = f"{pair[0][0]} '{pair[0][1]}'"
            b_desc = f"{pair[1][0]} '{pair[1][1]}'"
            scored.append({
                "family": "action_pair",
                "key": pair,
                "count": info["count"],
                "examples": info["examples"],
                "description": (
                    f"You often do {a_desc} → then {b_desc} "
                    f"({info['count']} times)."
                ),
                "type": "action_pair",
            })
        # Time-of-day patterns.
        for hour, info in matches["time_of_day"].items():
            period = (
                "morning" if 6 <= hour < 12 else
                "afternoon" if 12 <= hour < 18 else
                "evening" if 18 <= hour < 22 else "night"
            )
            scored.append({
                "family": "time_of_day",
                "key": hour,
                "count": info["count"],
                "examples": info["examples"],
                "description": (
                    f"You're most active around {hour:02d}:00 ({period}) — "
                    f"{info['count']} interactions."
                ),
                "type": "time_of_day",
            })
        return scored

    @staticmethod
    def _filter_top_patterns(
        scored: List[Dict[str, Any]],
    ) -> List[dict]:
        """Filter and cap each pattern family before returning.

        Drops any pattern with evidence below :data:`MIN_PATTERN_OCCURRENCES`
        and limits each family to its historical top-N slice (action_type
        has no cap, keyword top-10, action_pair top-5, time_of_day top-3).
        """
        # Family-specific top-N limits (mirrors the original ``sorted(...)[:N]``
        # slices so the public output shape stays identical).
        family_limits = {
            "action_type": None,      # no cap — Counter.most_common was uncapped
            "keyword": 10,
            "action_pair": 5,
            "time_of_day": 3,
        }
        by_family: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in scored:
            if item["count"] >= MIN_PATTERN_OCCURRENCES:
                by_family[item["family"]].append(item)

        patterns: List[dict] = []
        for family, items in by_family.items():
            items.sort(key=lambda x: -x["count"])
            limit = family_limits.get(family)
            if limit is not None:
                items = items[:limit]
            for item in items:
                patterns.append({
                    "type": item["type"],
                    "description": item["description"],
                    "evidence_count": item["count"],
                    "examples": item["examples"][:5],
                })
        return patterns

    async def _maybe_add_glm_pattern(
        self,
        patterns: List[dict],
    ) -> List[dict]:
        """Optionally synthesise a higher-level GLM pattern.

        Skips silently when GLM is unavailable or when there are fewer
        than 2 patterns to synthesise from. Mirrors the original
        best-effort try/except so the engine never breaks because of
        a downstream model failure.
        """
        if len(patterns) < 2:
            return patterns
        try:
            from core.glm_brain import GLMBrain
            glm = GLMBrain()
            if not glm.available():
                return patterns
            glm_pattern = await self._glm_synthesise(patterns)
            if glm_pattern:
                patterns.append(glm_pattern)
        except Exception as exc:  # noqa: BLE001
            logger.debug("GLM synthesis skipped: %s", exc)
        return patterns

    async def _glm_synthesise(self, found_patterns: List[dict]) -> Optional[dict]:
        """Use GLM to synthesise higher-level patterns from the discovered ones.

        Returns a single synthesised pattern or None.
        """
        try:
            from core.glm_brain import GLMBrain
            glm = GLMBrain()
            if not glm.available():
                return None

            prompt = (
                "Based on these discovered behavioral patterns, identify ONE "
                "higher-level insight about the user's workflow:\n\n"
            )
            for p in found_patterns[:8]:
                prompt += f"- {p['description']}\n"
            prompt += (
                "\nRespond with one sentence describing a proactive suggestion "
                "Friday could make based on these patterns."
            )

            response = ""
            async for chunk in glm.chat_stream(prompt):
                response += chunk

            if response.strip():
                return {
                    "type": "glm_synthesised",
                    "description": response.strip(),
                    "evidence_count": sum(p["evidence_count"] for p in found_patterns[:5]),
                    "examples": [],
                }
        except Exception as exc:
            logger.debug("GLM synthesise failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Proactive suggestions
    # ------------------------------------------------------------------

    async def get_suggestions(self, current_context: dict) -> List[dict]:
        """Given current context, return proactive suggestions based on real patterns.

        Only suggests things with real pattern evidence. Never suggests
        things it hasn't actually observed.
        """
        if not self.patterns:
            await self.discover_patterns()

        suggestions: List[dict] = []
        for p in self.patterns:
            # Simple heuristic: if a pattern matches the current context,
            # suggest it. Real implementations would use richer matching.
            ctx_str = " ".join(str(v) for v in current_context.values()).lower()

            # For action_pair patterns, suggest the "B" action if "A" was just done
            if p["type"] == "action_pair":
                a_desc = p["description"].split("→")[0].strip()
                if any(word in ctx_str for word in a_desc.lower().split()[:2]):
                    suggestions.append({
                        "pattern": p["description"],
                        "suggestion": f"Based on your pattern: {p['description']}",
                        "confidence": min(p["evidence_count"] / 10.0, 1.0),
                    })

            # For keyword patterns, suggest if keyword is in current context
            elif p["type"] == "keyword_frequency":
                word = p["description"].split("'")[1] if "'" in p["description"] else ""
                if word and word in ctx_str:
                    suggestions.append({
                        "pattern": p["description"],
                        "suggestion": f"You often talk about '{word}' — want me to pull up related context?",
                        "confidence": min(p["evidence_count"] / 10.0, 1.0),
                    })

        # Sort by confidence descending
        suggestions.sort(key=lambda s: -s["confidence"])
        return suggestions[:5]  # top 5 only

    # ------------------------------------------------------------------
    # Persistence (optional — save/load interaction log)
    # ------------------------------------------------------------------

    def export_interactions(self) -> List[dict]:
        """Return the full interaction log for export."""
        return list(self.interactions)

    def import_interactions(self, interactions: List[dict]) -> None:
        """Load an interaction log from export."""
        self.interactions.extend(interactions)
