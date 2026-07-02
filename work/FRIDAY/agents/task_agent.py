import asyncio
import json
import logging
import re
from typing import Dict, Any, List, Optional

logger = logging.getLogger("TaskAgent")


class TaskAgent:
    """AI-powered task decomposition and planning agent."""
    
    def __init__(self, brain=None):
        self.brain = brain
        self.name = "TaskAgent"
    
    async def execute(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute task decomposition."""
        return await self.break_down_task(task, context)
    
    async def break_down_task(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Break down a complex task into actionable steps."""
        if not self.brain:
            return self._heuristic_breakdown(task)
        
        ctx = context or {}
        prompt = (
            f"Break down the following task into clear, actionable steps. "
            f"For each step, provide: a title, description, estimated complexity (low/medium/high), "
            f"and any dependencies on previous steps.\n\n"
            f"Task: {task}\n\n"
        )
        
        if ctx.get("research"):
            prompt += f"Research context: {str(ctx['research'])[:500]}\n\n"
        
        prompt += "Format as a numbered list with clear steps."
        
        full_response = ""
        async for chunk in self.brain.chat_stream(prompt, user_name="TaskAgent"):
            full_response += chunk
        
        # Parse into structured steps
        steps = self._parse_steps(full_response)
        
        return {
            "task": task,
            "steps": steps,
            "total_steps": len(steps),
            "estimated_complexity": self._estimate_overall_complexity(steps),
            "full_plan": full_response
        }
    
    def _parse_steps(self, response: str) -> List[Dict[str, Any]]:
        """Parse LLM response into structured steps."""
        steps = []
        lines = response.strip().split('\n')
        current_step = None
        
        for line in lines:
            # Match numbered steps like "1." or "Step 1:" etc.
            match = re.match(r'(?:\d+[\.\)]\s*|Step\s+\d+\s*[:\-]\s*)(.+)', line.strip())
            if match:
                if current_step:
                    steps.append(current_step)
                current_step = {
                    "title": match.group(1).strip(),
                    "description": "",
                    "complexity": "medium",
                    "dependencies": []
                }
            elif current_step and line.strip():
                current_step["description"] += line.strip() + " "
                # Detect complexity keywords
                if any(kw in line.lower() for kw in ["complex", "hard", "difficult"]):
                    current_step["complexity"] = "high"
                elif any(kw in line.lower() for kw in ["simple", "easy", "straightforward"]):
                    current_step["complexity"] = "low"
        
        if current_step:
            steps.append(current_step)
        
        # If parsing failed, create a single step from the whole response
        if not steps:
            steps = [{"title": "Complete the task", "description": response, "complexity": "medium", "dependencies": []}]
        
        # Add step numbers and dependencies
        for i, step in enumerate(steps):
            step["step_number"] = i + 1
            if i > 0:
                step["dependencies"] = [i]
        
        return steps
    
    def _estimate_overall_complexity(self, steps: List[Dict]) -> str:
        complexity_map = {"low": 1, "medium": 2, "high": 3}
        avg = sum(complexity_map.get(s.get("complexity", "medium"), 2) for s in steps) / max(len(steps), 1)
        if avg < 1.5:
            return "low"
        elif avg < 2.5:
            return "medium"
        return "high"
    
    def _heuristic_breakdown(self, task: str) -> Dict[str, Any]:
        """Fallback breakdown when LLM unavailable."""
        return {
            "task": task,
            "steps": [
                {"step_number": 1, "title": "Analyze requirements", "description": f"Understand the scope and requirements of: {task}", "complexity": "medium", "dependencies": []},
                {"step_number": 2, "title": "Plan approach", "description": "Determine the best approach and identify needed resources", "complexity": "medium", "dependencies": [0]},
                {"step_number": 3, "title": "Execute", "description": "Carry out the planned approach step by step", "complexity": "high", "dependencies": [1]},
                {"step_number": 4, "title": "Verify and refine", "description": "Check results and make adjustments as needed", "complexity": "low", "dependencies": [2]},
            ],
            "total_steps": 4,
            "estimated_complexity": "medium",
            "full_plan": f"Heuristic plan for: {task}"
        }
