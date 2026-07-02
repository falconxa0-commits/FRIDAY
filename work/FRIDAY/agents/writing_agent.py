import asyncio
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("WritingAgent")


class WritingAgent:
    """AI-powered writing agent for content creation, editing, and formatting."""
    
    def __init__(self, brain=None):
        self.brain = brain
        self.name = "WritingAgent"
    
    async def execute(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute a writing task."""
        ctx = context or {}
        
        # Determine writing type
        task_lower = task.lower()
        if any(kw in task_lower for kw in ["proofread", "edit", "check grammar", "fix"]):
            return await self.proofread(task, ctx)
        elif any(kw in task_lower for kw in ["report", "summary", "analysis"]):
            return await self.write_report(task, ctx)
        else:
            return await self.write_document(task, ctx)
    
    async def write_document(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate a document based on task description."""
        if not self.brain:
            return {"status": "error", "message": "No brain available for writing"}
        
        ctx = context or {}
        prompt = f"Write a comprehensive document on the following topic:\n\n{task}\n\n"
        
        if ctx.get("research"):
            prompt += f"Research findings:\n{str(ctx['research'])[:1000]}\n\n"
        if ctx.get("plan"):
            prompt += f"Outline/Plan:\n{str(ctx['plan'])[:500]}\n\n"
        
        prompt += "Write in a clear, professional style with proper structure (headings, paragraphs, bullet points where appropriate)."
        
        full_response = ""
        async for chunk in self.brain.chat_stream(prompt, user_name="WritingAgent"):
            full_response += chunk
        
        return {
            "status": "success",
            "task": task,
            "content": full_response,
            "word_count": len(full_response.split()),
            "task_type": "write"
        }
    
    async def proofread(self, text: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Proofread and improve text."""
        if not self.brain:
            return {"status": "error", "message": "No brain available for proofreading"}
        
        prompt = (
            f"Proofread and improve the following text. Fix grammar, spelling, punctuation, "
            f"and style issues. Also suggest improvements for clarity and impact.\n\n"
            f"Text:\n{text}\n\n"
            f"Provide: 1) The corrected text, 2) A list of changes made, 3) Style suggestions."
        )
        
        full_response = ""
        async for chunk in self.brain.chat_stream(prompt, user_name="WritingAgent"):
            full_response += chunk
        
        return {
            "status": "success",
            "task": text[:100],
            "corrected": full_response,
            "task_type": "proofread"
        }
    
    async def write_report(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate a structured report."""
        if not self.brain:
            return {"status": "error", "message": "No brain available for report writing"}
        
        ctx = context or {}
        prompt = (
            f"Write a structured report on the following topic. Include: Executive Summary, "
            f"Background, Findings, Analysis, Recommendations, and Conclusion.\n\n"
            f"Topic: {task}\n\n"
        )
        
        if ctx.get("research"):
            prompt += f"Research data:\n{str(ctx['research'])[:1000]}\n\n"
        if ctx.get("execution"):
            prompt += f"Implementation results:\n{str(ctx['execution'])[:500]}\n\n"
        
        full_response = ""
        async for chunk in self.brain.chat_stream(prompt, user_name="WritingAgent"):
            full_response += chunk
        
        return {
            "status": "success",
            "task": task,
            "content": full_response,
            "word_count": len(full_response.split()),
            "task_type": "report"
        }
    
    def format_research_report(self, topic: str, findings: list, sources: list = None) -> Dict[str, Any]:
        """Format pre-collected research into a report."""
        report = f"# Research Report: {topic}\n\n"
        report += "## Key Findings\n\n"
        for i, finding in enumerate(findings, 1):
            report += f"{i}. {finding}\n"
        
        if sources:
            report += "\n## Sources\n\n"
            for i, source in enumerate(sources, 1):
                if isinstance(source, dict):
                    report += f"{i}. [{source.get('title', 'Untitled')}]({source.get('url', '#')})\n"
                else:
                    report += f"{i}. {source}\n"
        
        return {
            "status": "success",
            "task": topic,
            "content": report,
            "word_count": len(report.split()),
            "task_type": "research_report"
        }
