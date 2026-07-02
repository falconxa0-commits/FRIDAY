import asyncio
import json
import logging
import re
from typing import Dict, Any, Optional

from core.glm_brain import GLMBrain

logger = logging.getLogger("CodingAgent")

# Tool definition for Z.ai Code Interpreter (exposed to the brain router)
ZAI_CODE_INTERPRETER_TOOL = {
    "name": "zai_code_interpreter",
    "description": (
        "Execute code using the Z.ai Code Interpreter tool. "
        "Use for running Python code, performing calculations, "
        "data analysis, or any task requiring code execution."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The source code to execute",
            },
            "language": {
                "type": "string",
                "description": "Programming language (default: python)",
            },
        },
        "required": ["code"],
    },
}


class CodingAgent:
    """AI-powered coding agent for code generation, debugging, and review."""

    def __init__(self, brain=None):
        self.brain = brain
        self.name = "CodingAgent"
        self._glm_brain = GLMBrain()

    async def execute(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute a coding task."""
        ctx = context or {}

        # Determine task type
        task_type = self._classify_task(task)

        if task_type == "debug":
            return await self.debug_code(task, ctx)
        elif task_type == "review":
            return await self.review_code(task, ctx)
        else:
            return await self.write_code(task, ctx)

    def _classify_task(self, task: str) -> str:
        task_lower = task.lower()
        if any(kw in task_lower for kw in ["debug", "fix", "error", "bug", "issue", "not working", "broken"]):
            return "debug"
        if any(kw in task_lower for kw in ["review", "check", "analyze code", "audit", "improve"]):
            return "review"
        return "write"

    async def _try_code_interpreter(self, code: str, language: str = "python") -> Optional[Dict]:
        """Try to execute code using Z.ai Code Interpreter if available."""
        if not self._glm_brain.available():
            return None

        try:
            result = await self._glm_brain.code_interpreter(code, language=language)
            if result.get("status") == "success":
                return result
        except Exception as e:
            logger.debug(f"Z.ai Code Interpreter failed: {e}")

        return None

    async def write_code(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate code based on task description."""
        if not self.brain:
            return {"status": "error", "message": "No brain available for code generation"}

        prompt = (
            f"Write code for the following task. Provide the code in a markdown code block "
            f"with the language specified. Include brief comments.\n\n"
            f"Task: {task}\n\n"
        )

        if context and context.get("plan"):
            prompt += f"Plan: {context['plan']}\n\n"

        if context and context.get("research"):
            prompt += f"Research: {str(context['research'])[:500]}\n\n"

        prompt += "Provide the complete, working code with any necessary imports."

        full_response = ""
        async for chunk in self.brain.chat_stream(prompt, user_name="CodingAgent"):
            full_response += chunk

        # Extract code blocks
        code_blocks = re.findall(r'```(\w+)?\n(.*?)```', full_response, re.DOTALL)

        code = code_blocks[0][1].strip() if code_blocks else full_response
        language = code_blocks[0][0] if code_blocks and code_blocks[0][0] else "python"

        # Try running the code through Z.ai Code Interpreter if available
        interpreter_result = None
        if code and language.lower() == "python":
            interpreter_result = await self._try_code_interpreter(code, language)

        result = {
            "status": "success",
            "task": task,
            "code": code,
            "language": language,
            "full_response": full_response,
            "task_type": "write",
        }

        if interpreter_result:
            result["execution_result"] = interpreter_result

        return result

    async def debug_code(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Debug code by analyzing the error and providing a fix."""
        if not self.brain:
            return {"status": "error", "message": "No brain available for debugging"}

        prompt = (
            f"Debug the following code issue. Analyze the error, identify the root cause, "
            f"and provide a corrected version.\n\n"
            f"Issue: {task}\n\n"
        )

        if context and context.get("code"):
            prompt += f"Code:\n```\n{context['code']}\n```\n\n"

        if context and context.get("error"):
            prompt += f"Error message: {context['error']}\n\n"

        prompt += "Provide: 1) Root cause analysis, 2) The fix, 3) Explanation of why the fix works."

        full_response = ""
        async for chunk in self.brain.chat_stream(prompt, user_name="CodingAgent"):
            full_response += chunk

        code_blocks = re.findall(r'```(\w+)?\n(.*?)```', full_response, re.DOTALL)

        fixed_code = code_blocks[0][1].strip() if code_blocks else None
        language = code_blocks[0][0] if code_blocks and code_blocks[0][0] else "python"

        # Try running the fixed code through Z.ai Code Interpreter if available
        interpreter_result = None
        if fixed_code and language.lower() == "python":
            interpreter_result = await self._try_code_interpreter(fixed_code, language)

        result = {
            "status": "success",
            "task": task,
            "analysis": full_response,
            "fixed_code": fixed_code,
            "language": language,
            "task_type": "debug",
        }

        if interpreter_result:
            result["execution_result"] = interpreter_result

        return result

    async def review_code(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Review code for quality, security, and best practices."""
        if not self.brain:
            return {"status": "error", "message": "No brain available for code review"}

        prompt = (
            f"Review the following code for: 1) Bugs and logic errors, 2) Security vulnerabilities, "
            f"3) Performance issues, 4) Code style and best practices, 5) Suggestions for improvement.\n\n"
            f"Task context: {task}\n\n"
        )

        if context and context.get("code"):
            prompt += f"Code to review:\n```\n{context['code']}\n```\n\n"

        prompt += "Provide a structured review with severity levels (critical/high/medium/low) for each issue."

        full_response = ""
        async for chunk in self.brain.chat_stream(prompt, user_name="CodingAgent"):
            full_response += chunk

        return {
            "status": "success",
            "task": task,
            "review": full_response,
            "task_type": "review"
        }
