import asyncio
import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("TacticalManager")


class TacticalManager:
    """Coordinates parallel agent execution with strategic oversight."""
    
    def __init__(self, brain=None, agent_manager=None):
        self.brain = brain
        self.agent_manager = agent_manager
        self.name = "TacticalManager"
        self.tactical_history: List[Dict] = []
    
    async def execute(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute tactical coordination of agents."""
        return await self.coordinate(task, context)
    
    async def coordinate(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Coordinate multiple agents for a complex task."""
        ctx = context or {}
        
        # Determine which agents to deploy
        agent_plan = self._plan_agents(task)
        
        # Execute agents in parallel
        if self.agent_manager:
            results = await self.agent_manager.run_swarm(task, agent_plan, ctx)
        else:
            results = []
            for agent_type in agent_plan:
                results.append({
                    "agent_type": agent_type,
                    "status": "skipped",
                    "output": "No agent manager available",
                    "confidence": 0.0
                })
        
        # Synthesize results
        synthesis = await self._synthesize_results(task, results)
        
        # Record
        entry = {
            "task": task,
            "agents_used": agent_plan,
            "results_count": len(results),
            "successful": sum(1 for r in results if isinstance(r, dict) and r.get("status") == "completed"),
            "synthesis_length": len(synthesis)
        }
        self.tactical_history.append(entry)
        
        return {
            "task": task,
            "agent_results": [r.to_dict() if hasattr(r, 'to_dict') else r for r in results],
            "synthesis": synthesis,
            "agents_deployed": agent_plan,
            "coordination_status": "success"
        }
    
    def _plan_agents(self, task: str) -> List[str]:
        """Determine which agents to deploy based on task."""
        task_lower = task.lower()
        agents = []
        
        if any(kw in task_lower for kw in ["research", "find", "search", "investigate", "analyze"]):
            agents.append("research")
        if any(kw in task_lower for kw in ["code", "build", "implement", "develop", "debug", "fix"]):
            agents.append("coding")
        if any(kw in task_lower for kw in ["write", "document", "report", "draft", "compose"]):
            agents.append("writing")
        if any(kw in task_lower for kw in ["plan", "organize", "break down", "schedule"]):
            agents.append("task")
        
        # Default: deploy research + task
        if not agents:
            agents = ["research", "task"]
        
        return agents
    
    async def _synthesize_results(self, task: str, results: List) -> str:
        """Synthesize agent results into a cohesive response."""
        if not self.brain or not results:
            return self._heuristic_synthesis(task, results)
        
        results_text = ""
        for r in results:
            if isinstance(r, dict):
                results_text += f"\n[{r.get('agent_type', 'unknown')}]: {str(r.get('output', ''))[:500]}\n"
            elif hasattr(r, 'output'):
                results_text += f"\n[{r.agent_type.value if hasattr(r, 'agent_type') else 'unknown'}]: {str(r.output)[:500]}\n"
        
        prompt = (
            f"Synthesize the following agent results into a cohesive response for the task: {task}\n\n"
            f"Agent Results:{results_text}\n\n"
            f"Provide a unified, well-structured response that combines the best insights from each agent."
        )
        
        full_response = ""
        async for chunk in self.brain.chat_stream(prompt, user_name="TacticalManager"):
            full_response += chunk
        
        return full_response or "Synthesis unavailable."
    
    def _heuristic_synthesis(self, task: str, results: List) -> str:
        """Fallback synthesis."""
        synthesis = f"Tactical analysis for: {task}\n\n"
        for r in results:
            if isinstance(r, dict):
                synthesis += f"- {r.get('agent_type', 'Agent')}: {str(r.get('output', 'No output'))[:200]}\n"
        return synthesis
    
    def get_tactical_report(self) -> Dict[str, Any]:
        """Generate a report on tactical operations."""
        if not self.tactical_history:
            return {"total_operations": 0}
        
        return {
            "total_operations": len(self.tactical_history),
            "avg_agents_per_op": sum(h.get("results_count", 0) for h in self.tactical_history) / len(self.tactical_history),
            "success_rate": sum(h.get("successful", 0) for h in self.tactical_history) / max(sum(h.get("results_count", 1) for h in self.tactical_history), 1),
            "recent_operations": self.tactical_history[-5:]
        }
