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
