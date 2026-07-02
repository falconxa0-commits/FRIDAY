import asyncio
import logging
from typing import Dict, List, Any, Optional
from enum import Enum

logger = logging.getLogger("AgentManager")


class AgentType(Enum):
    RESEARCH = "research"
    CODING = "coding"
    WRITING = "writing"
    TASK = "task"


class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentResult:
    def __init__(self, agent_type: AgentType, status: AgentStatus, output: Any, confidence: float = 0.0, duration: float = 0.0):
        self.agent_type = agent_type
        self.status = status
        self.output = output
        self.confidence = confidence
        self.duration = duration
    
    def to_dict(self):
        return {
            "agent_type": self.agent_type.value,
            "status": self.status.value,
            "output": str(self.output)[:2000],
            "confidence": self.confidence,
            "duration": self.duration
        }


class AgentManager:
    """Manages and orchestrates AI agents for complex multi-step tasks."""
    
    def __init__(self, brain=None):
        self.brain = brain
        self.agents = {}
        self.active_tasks: Dict[str, AgentResult] = {}
        self._init_agents()
    
    def _init_agents(self):
        from agents.research_agent import ResearchAgent
        from agents.coding_agent import CodingAgent
        from agents.writing_agent import WritingAgent
        from agents.task_agent import TaskAgent
        
        self.agents = {
            AgentType.RESEARCH: ResearchAgent(self.brain),
            AgentType.CODING: CodingAgent(self.brain),
            AgentType.WRITING: WritingAgent(self.brain),
            AgentType.TASK: TaskAgent(self.brain),
        }
    
    async def run_agent(self, agent_type: str, task: str, context: Optional[Dict] = None) -> AgentResult:
        """Run a single agent on a task."""
        at = AgentType(agent_type)
        agent = self.agents.get(at)
        if not agent:
            return AgentResult(at, AgentStatus.FAILED, f"Unknown agent type: {agent_type}")
        
        import time
        start = time.time()
        try:
            result = await agent.execute(task, context or {})
            duration = time.time() - start
            agent_result = AgentResult(at, AgentStatus.COMPLETED, result, confidence=0.8, duration=duration)
        except Exception as e:
            duration = time.time() - start
            agent_result = AgentResult(at, AgentStatus.FAILED, str(e), confidence=0.0, duration=duration)
            logger.error(f"Agent {agent_type} failed: {e}")
        
        self.active_tasks[f"{agent_type}_{int(start)}"] = agent_result
        return agent_result
    
    async def run_swarm(self, task: str, agent_types: Optional[List[str]] = None, context: Optional[Dict] = None) -> List[AgentResult]:
        """Run multiple agents in parallel on the same task and synthesize results."""
        if agent_types is None:
            agent_types = [at.value for at in AgentType]
        
        tasks = [
            self.run_agent(at, task, context)
            for at in agent_types
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        agent_results = []
        for r in results:
            if isinstance(r, Exception):
                agent_results.append(AgentResult(AgentType.TASK, AgentStatus.FAILED, str(r)))
            else:
                agent_results.append(r)
        
        return agent_results
    
    async def run_pipeline(self, task: str, context: Optional[Dict] = None) -> AgentResult:
        """Run a sequential pipeline: Research → Plan → Execute → Write."""
        ctx = context or {}
        
        # Step 1: Research
        research = await self.run_agent("research", task, ctx)
        if research.status == AgentStatus.FAILED:
            return research
        ctx["research"] = research.output
        
        # Step 2: Plan (Task Agent)
        plan = await self.run_agent("task", f"Break down this task based on research: {task}\nResearch: {research.output}", ctx)
        if plan.status == AgentStatus.FAILED:
            return plan
        ctx["plan"] = plan.output
        
        # Step 3: Execute (Coding Agent if code-related, otherwise skip)
        if self._is_code_task(task):
            execution = await self.run_agent("coding", f"Implement: {task}\nPlan: {plan.output}", ctx)
            if execution.status == AgentStatus.COMPLETED:
                ctx["execution"] = execution.output
        
        # Step 4: Write report
        report = await self.run_agent("writing", f"Write a comprehensive report on: {task}", ctx)
        return report
    
    def _is_code_task(self, task: str) -> bool:
        code_keywords = ["code", "build", "implement", "develop", "program", "script", "function", "api", "app"]
        return any(kw in task.lower() for kw in code_keywords)
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "available_agents": [at.value for at in AgentType],
            "active_tasks": {k: v.to_dict() for k, v in self.active_tasks.items()},
            "total_completed": sum(1 for v in self.active_tasks.values() if v.status == AgentStatus.COMPLETED)
        }
