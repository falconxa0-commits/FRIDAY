import asyncio
import json
import logging
from typing import Dict, Any, Optional

from core.glm_brain import GLMBrain

logger = logging.getLogger("ResearchAgent")

# Tool definition for Z.ai built-in web search (exposed to the brain router)
ZAI_WEB_SEARCH_TOOL = {
    "name": "zai_web_search",
    "description": (
        "Search the web for real-time information using Z.ai built-in search. "
        "Use this for current events, facts, or anything requiring up-to-date data."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (default 5)",
            },
        },
        "required": ["query"],
    },
}


class ResearchAgent:
    """AI-powered research agent that can search the web and synthesize findings."""

    def __init__(self, brain=None):
        self.brain = brain
        self.name = "ResearchAgent"
        self._glm_brain = GLMBrain()

    async def execute(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute a research task using web search and LLM synthesis."""
        ctx = context or {}

        # Step 1: Web search if available
        search_results = await self._search_web(task)

        # Step 2: LLM-powered analysis
        analysis = await self._analyze_with_llm(task, search_results, ctx)

        # Step 3: Compile findings
        return {
            "task": task,
            "findings": analysis,
            "sources": search_results.get("results", []) if isinstance(search_results, dict) else [],
            "confidence": self._calculate_confidence(search_results, analysis),
            "method": "web_search + llm_synthesis" if search_results else "llm_knowledge"
        }

    async def deep_research(self, topic: str, max_sources: int = 5) -> Dict[str, Any]:
        """Real multi-source research with synthesis, citation, and honest uncertainty.

        Process:
          1. Search for ``max_sources`` real sources via GLM web search
          2. Extract the key claims from each source
          3. Cross-reference: where do sources agree? Where do they conflict?
          4. Synthesize a structured report with inline citations
          5. Flag explicitly where information is uncertain or conflicting
          6. Return sources, full synthesis, and confidence level per claim

        Returns a dict with keys:
            topic, synthesis, sources, agreements, conflicts,
            confidence, limitations
        """
        # 1. Search
        search_results = await self._search_web(topic)
        sources = []
        if search_results and isinstance(search_results, dict):
            for r in search_results.get("results", [])[:max_sources]:
                sources.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", r.get("snippet", ""))[:500],
                })

        # 2. Extract key claims per source (heuristic — split snippet into sentences)
        for s in sources:
            text = s.get("snippet", "")
            sentences = [sent.strip() for sent in text.replace("\n", ". ").split(". ")
                         if len(sent.strip()) > 15]
            s["key_claims"] = sentences[:5]

        # 3. Cross-reference — find agreements (shared keywords) and conflicts
        agreements = self._find_agreements(sources)
        conflicts = self._find_conflicts(sources)

        # 4. Synthesize (use LLM if available, heuristic otherwise)
        synthesis = await self._synthesize(topic, sources, agreements, conflicts)

        # 5. Confidence based on source count + agreement count
        if len(sources) >= 3 and len(agreements) >= 2:
            confidence = "high"
        elif len(sources) >= 2:
            confidence = "medium"
        elif len(sources) >= 1:
            confidence = "low"
        else:
            confidence = "none"

        # 6. Limitations
        limitations = []
        if not sources:
            limitations.append("No web sources were retrieved (GLM web search unavailable).")
        if len(sources) < 3:
            limitations.append(
                f"Only {len(sources)} source(s) found — conclusions should be verified."
            )
        if conflicts:
            limitations.append(
                f"{len(conflicts)} conflict(s) detected between sources — "
                "see the conflicts field for details."
            )
        limitations.append("Web search snippets are summaries — full source content was not fetched.")

        return {
            "topic": topic,
            "synthesis": synthesis,
            "sources": sources,
            "agreements": agreements,
            "conflicts": conflicts,
            "confidence": confidence,
            "limitations": "\n".join(limitations),
        }

    # ------------------------------------------------------------------
    # Deep research helpers
    # ------------------------------------------------------------------

    def _find_agreements(self, sources: list) -> list:
        """Find sentences that appear (approximately) in 2+ sources."""
        agreements = []
        seen_phrases = set()
        for i, s1 in enumerate(sources):
            for claim1 in s1.get("key_claims", []):
                words1 = set(claim1.lower().split())
                if len(words1) < 4:
                    continue
                for j, s2 in enumerate(sources):
                    if i >= j:
                        continue
                    for claim2 in s2.get("key_claims", []):
                        words2 = set(claim2.lower().split())
                        overlap = words1 & words2
                        # At least 50% of the smaller set overlaps
                        if len(overlap) >= 0.5 * min(len(words1), len(words2)):
                            phrase = " ".join(sorted(overlap)[:8])
                            if phrase not in seen_phrases:
                                agreements.append({
                                    "claim": claim1[:200],
                                    "sources": [s1.get("url", ""), s2.get("url", "")],
                                    "shared_terms": sorted(overlap)[:10],
                                })
                                seen_phrases.add(phrase)
        return agreements[:10]

    def _find_conflicts(self, sources: list) -> list:
        """Detect explicit disagreement cues between sources."""
        conflict_cues = [
            "however", "but", "disagree", "incorrect", "wrong",
            "not true", "disputed", "controversy", "debate",
        ]
        conflicts = []
        for s in sources:
            for claim in s.get("key_claims", []):
                lower = claim.lower()
                if any(cue in lower for cue in conflict_cues):
                    conflicts.append({
                        "claim": claim[:200],
                        "source": s.get("url", ""),
                        "cue": next(c for c in conflict_cues if c in lower),
                    })
        return conflicts[:5]

    async def _synthesize(self, topic: str, sources: list,
                          agreements: list, conflicts: list) -> str:
        """Build a synthesis — uses GLM if available, heuristic otherwise."""
        if not sources:
            return f"No sources available for '{topic}'."

        # Try GLM first
        try:
            if self._glm_brain.available():
                prompt = (
                    f"Synthesize a research report on: {topic}\n\n"
                    f"Sources:\n"
                )
                for i, s in enumerate(sources, 1):
                    prompt += f"[{i}] {s.get('title', '')}\n    {s.get('snippet', '')[:300]}\n\n"
                prompt += (
                    f"\nAgreements found: {len(agreements)}\n"
                    f"Conflicts found: {len(conflicts)}\n\n"
                    "Write a 3-paragraph synthesis that:\n"
                    "1. Summarises what the sources agree on\n"
                    "2. Notes any disagreements\n"
                    "3. Gives an honest confidence assessment\n"
                )
                response = ""
                async for chunk in self._glm_brain.chat_stream(prompt):
                    response += chunk
                if response.strip():
                    return response.strip()
        except Exception as exc:
            logger.debug("GLM synthesis failed: %s", exc)

        # Heuristic fallback
        lines = [f"# Synthesis: {topic}\n"]
        lines.append(f"Based on {len(sources)} source(s):\n")
        for i, s in enumerate(sources, 1):
            lines.append(f"[{i}] {s.get('title', 'Untitled')}")
            lines.append(f"    URL: {s.get('url', '')}")
            lines.append(f"    Key claim: {s.get('key_claims', ['(none)'])[0]}")
            lines.append("")
        if agreements:
            lines.append(f"Agreements ({len(agreements)}):")
            for a in agreements:
                lines.append(f"  - {a['claim']}")
            lines.append("")
        if conflicts:
            lines.append(f"Conflicts ({len(conflicts)}):")
            for c in conflicts:
                lines.append(f"  - {c['claim']} (cue: '{c['cue']}')")
            lines.append("")
        lines.append(
            "Note: This is a heuristic synthesis. With GLM_API_KEY configured, "
            "Friday produces a richer natural-language synthesis."
        )
        return "\n".join(lines)

    async def _search_web(self, query: str) -> Optional[Dict]:
        """Search the web — try Z.ai built-in search first, fall back to Tavily."""
        # --- Try Z.ai / GLM web search first ---
        try:
            if self._glm_brain.available():
                results = await self._glm_brain.web_search(query, max_results=5)
                if results:
                    return {"results": results, "provider": "zai"}
        except Exception as e:
            logger.warning(f"Z.ai web search failed: {e}")

        # --- Fallback: Tavily ---
        try:
            from config.settings import TAVILY_API_KEY
            if not TAVILY_API_KEY:
                return None

            from tavily import TavilyClient  # noqa: kept as fallback
            client = TavilyClient(api_key=TAVILY_API_KEY)
            results = client.search(query, max_results=5)
            return results
        except Exception as e:
            logger.warning(f"Web search failed (all providers): {e}")
            return None

    async def _analyze_with_llm(self, task: str, search_results: Optional[Dict], context: Dict) -> str:
        """Use LLM to analyze and synthesize research findings."""
        if not self.brain:
            return self._heuristic_analysis(task, search_results)

        prompt = f"Research task: {task}\n\n"

        if search_results and isinstance(search_results, dict):
            prompt += "Web search results:\n"
            for r in search_results.get("results", []):
                prompt += f"- {r.get('title', '')}: {r.get('content', r.get('snippet', ''))[:200]}\n"
            prompt += "\n"

        if context.get("research"):
            prompt += f"Previous research: {str(context['research'])[:500]}\n\n"

        prompt += "Please provide a comprehensive analysis with key findings, implications, and recommendations."

        # Use brain's chat_stream to get analysis
        full_response = ""
        async for chunk in self.brain.chat_stream(prompt, user_name="ResearchAgent"):
            full_response += chunk

        return full_response or "Research analysis unavailable."

    def _heuristic_analysis(self, task: str, search_results: Optional[Dict]) -> str:
        """Fallback analysis when LLM is unavailable."""
        analysis = f"Research on: {task}\n\n"

        if search_results and isinstance(search_results, dict):
            for r in search_results.get("results", []):
                analysis += f"- {r.get('title', 'Unknown')}: {r.get('content', r.get('snippet', ''))[:200]}\n"
        else:
            analysis += "No web search results available. Analysis based on general knowledge only."

        return analysis

    def _calculate_confidence(self, search_results: Optional[Dict], analysis: str) -> float:
        """Calculate confidence score for the research."""
        score = 0.5  # Base

        if search_results and isinstance(search_results, dict):
            num_results = len(search_results.get("results", []))
            score += min(num_results * 0.1, 0.3)  # More sources = higher confidence

        if analysis and len(analysis) > 200:
            score += 0.1  # Substantial analysis

        return min(score, 1.0)
