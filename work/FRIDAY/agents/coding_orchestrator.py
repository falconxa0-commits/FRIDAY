import asyncio
import json
import logging
import os
import re
from typing import Dict, Any, List, Optional

logger = logging.getLogger("CodingOrchestrator")


class CodingOrchestrator:
    """Orchestrates multi-file project generation with validation."""
    
    def __init__(self, brain=None):
        self.brain = brain
        self.name = "CodingOrchestrator"
        self.sandbox_dir = os.path.join(os.getcwd(), "friday_workspace", "generated_project")
    
    async def execute(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute a project generation task."""
        return await self.generate_project(task, context)
    
    async def generate_project(self, description: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate a multi-file project from description."""
        if not self.brain:
            return {"status": "error", "message": "No brain available for project generation"}
        
        # Step 1: Generate project plan
        plan = await self._generate_plan(description)
        
        # Step 2: Parse files from plan
        files = self._parse_files(plan)
        
        # Step 3: Validate and write files
        results = await self._write_files(files, description)
        
        return {
            "status": "success",
            "project": description,
            "plan": plan[:1000],
            "files_created": [r["filename"] for r in results if r["success"]],
            "files_failed": [r["filename"] for r in results if not r["success"]],
            "total_files": len(files),
            "output_dir": self.sandbox_dir
        }
    
    async def _generate_plan(self, description: str) -> str:
        """Generate a project plan with file structure."""
        prompt = (
            f"Create a complete project for: {description}\n\n"
            f"For each file, use this exact format:\n"
            f"=== FILE: filename.ext ===\n"
            f"[file contents]\n"
            f"=== END FILE ===\n\n"
            f"Include all necessary files (config, main modules, tests, README). "
            f"Make the code complete and runnable."
        )
        
        full_response = ""
        async for chunk in self.brain.chat_stream(prompt, user_name="CodingOrchestrator"):
            full_response += chunk
        
        return full_response
    
    def _parse_files(self, plan_text: str) -> List[Dict[str, str]]:
        """Parse files from the LLM-generated plan using robust markers."""
        files = []
        
        # Try === FILE: ... === markers first (most reliable)
        pattern = r'===\s*FILE:\s*([^\s=]+(?:\.[a-zA-Z0-9]+))\s*===\s*\n(.*?)===\s*END\s*FILE\s*==='
        matches = re.findall(pattern, plan_text, re.DOTALL)
        
        if matches:
            for filename, content in matches:
                files.append({"filename": filename.strip(), "content": content.strip()})
            return files
        
        # Fallback: try ```filename.ext code blocks
        code_pattern = r'```(\w+\.[a-zA-Z0-9]+)\n(.*?)```'
        matches = re.findall(code_pattern, plan_text, re.DOTALL)
        for filename, content in matches:
            files.append({"filename": filename.strip(), "content": content.strip()})
        
        if files:
            return files
        
        # Last resort: extract ```code blocks and name them generically
        generic_pattern = r'```(?:\w+)?\n(.*?)```'
        matches = re.findall(generic_pattern, plan_text, re.DOTALL)
        for i, content in enumerate(matches):
            if len(content.strip()) > 20:  # Skip tiny fragments
                files.append({"filename": f"file_{i+1}.py", "content": content.strip()})
        
        return files
    
    async def _write_files(self, files: List[Dict[str, str]], description: str) -> List[Dict]:
        """Write generated files to sandbox with validation."""
        os.makedirs(self.sandbox_dir, exist_ok=True)
        results = []
        
        for file_info in files:
            filename = file_info["filename"]
            content = file_info["content"]
            
            # Security: validate filename
            if not self._is_safe_filename(filename):
                results.append({"filename": filename, "success": False, "error": "Unsafe filename"})
                continue
            
            # Security: validate content (no obviously malicious code)
            if self._has_malicious_patterns(content):
                results.append({"filename": filename, "success": False, "error": "Potentially malicious content detected"})
                continue
            
            filepath = os.path.join(self.sandbox_dir, filename)
            
            try:
                # Create subdirectories if needed
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                
                results.append({"filename": filename, "success": True, "path": filepath})
                logger.info(f"Created file: {filepath}")
            except Exception as e:
                results.append({"filename": filename, "success": False, "error": str(e)})
                logger.error(f"Failed to write {filename}: {e}")
        
        return results
    
    def _is_safe_filename(self, filename: str) -> bool:
        """Validate filename for security."""
        # No path traversal
        if '..' in filename or filename.startswith('/'):
            return False
        # No hidden files
        if filename.startswith('.'):
            return False
        # Must have extension
        if '.' not in filename:
            return False
        # Only safe characters
        if not re.match(r'^[a-zA-Z0-9_\-./]+$', filename):
            return False
        return True
    
    def _has_malicious_patterns(self, content: str) -> bool:
        """Check for obviously malicious code patterns."""
        malicious = [
            r'os\.system\s*\(',
            r'subprocess\.call\s*\([^)]*shell\s*=\s*True',
            r'eval\s*\(',
            r'exec\s*\(',
            r'__import__\s*\(',
            r'rm\s+-rf',
        ]
        for pattern in malicious:
            if re.search(pattern, content):
                return True
        return False
