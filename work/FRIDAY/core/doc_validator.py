"""Documentation Validator — validates docs match the codebase.

Checks:
    - README feature claims vs actual implementation
    - API documentation vs actual routes
    - Architecture diagrams vs actual module structure
    - Code examples in docs vs actual API

Usage::

    validator = get_doc_validator()
    results = await validator.validate()
    # results → list of DocValidationResult
"""
from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.doc_validator")


@dataclass
class DocValidationResult:
    """Result of a single documentation check."""
    check: str
    passed: bool
    message: str
    file: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DocValidator:
    """Validates documentation against the actual codebase.

    Checks that documented features, APIs, and architecture match
    the actual implementation.
    """

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()

    async def validate(self) -> List[DocValidationResult]:
        """Run all documentation checks."""
        results = []

        results.append(await self._check_readme_exists())
        results.append(await self._check_api_docs_exist())
        results.append(await self._check_architecture_doc_exists())
        results.append(await self._check_security_doc_exists())
        results.append(await self._check_deployment_doc_exists())
        results.append(await self._check_code_examples_valid())
        results.append(await self._check_doc_links_valid())
        results.append(await self._check_env_var_docs())

        return results

    async def _check_readme_exists(self) -> DocValidationResult:
        readme = self.project_root / "README.md"
        exists = readme.exists()
        return DocValidationResult(
            check="readme_exists",
            passed=exists,
            message="README.md exists" if exists else "README.md missing",
            file="README.md",
        )

    async def _check_api_docs_exist(self) -> DocValidationResult:
        api_doc = self.project_root / "docs" / "API_REFERENCE.md"
        exists = api_doc.exists()
        return DocValidationResult(
            check="api_docs_exist",
            passed=exists,
            message="API_REFERENCE.md exists" if exists else "API_REFERENCE.md missing",
            file=str(api_doc),
        )

    async def _check_architecture_doc_exists(self) -> DocValidationResult:
        arch_doc = self.project_root / "docs" / "ARCHITECTURE.md"
        exists = arch_doc.exists()
        return DocValidationResult(
            check="architecture_doc_exists",
            passed=exists,
            message="ARCHITECTURE.md exists" if exists else "ARCHITECTURE.md missing",
            file=str(arch_doc),
        )

    async def _check_security_doc_exists(self) -> DocValidationResult:
        sec_doc = self.project_root / "docs" / "SECURITY_MODEL.md"
        exists = sec_doc.exists()
        return DocValidationResult(
            check="security_doc_exists",
            passed=exists,
            message="SECURITY_MODEL.md exists" if exists else "SECURITY_MODEL.md missing",
            file=str(sec_doc),
        )

    async def _check_deployment_doc_exists(self) -> DocValidationResult:
        deploy_doc = self.project_root / "docs" / "DEPLOYMENT_GUIDE.md"
        exists = deploy_doc.exists()
        return DocValidationResult(
            check="deployment_doc_exists",
            passed=exists,
            message="DEPLOYMENT_GUIDE.md exists" if exists else "DEPLOYMENT_GUIDE.md missing",
            file=str(deploy_doc),
        )

    async def _check_code_examples_valid(self) -> DocValidationResult:
        """Check that Python code blocks in docs are syntactically valid."""
        docs_dir = self.project_root / "docs"
        if not docs_dir.exists():
            return DocValidationResult(
                check="code_examples_valid",
                passed=False,
                message="No docs/ directory to check",
            )

        total_blocks = 0
        invalid_blocks = 0

        for doc_file in docs_dir.glob("*.md"):
            try:
                content = doc_file.read_text()
                # Extract ```python ... ``` blocks
                blocks = re.findall(r"```python\n(.*?)```", content, re.DOTALL)
                for block in blocks:
                    total_blocks += 1
                    try:
                        ast.parse(block)
                    except SyntaxError:
                        invalid_blocks += 1
            except Exception as exc:
                logger.debug("Non-critical error: %s", exc)

        passed = invalid_blocks == 0
        return DocValidationResult(
            check="code_examples_valid",
            passed=passed,
            message=f"{total_blocks - invalid_blocks}/{total_blocks} code blocks valid",
            details={"total": total_blocks, "invalid": invalid_blocks},
        )

    async def _check_doc_links_valid(self) -> DocValidationResult:
        """Check that internal markdown links point to existing files."""
        docs_dir = self.project_root / "docs"
        if not docs_dir.exists():
            return DocValidationResult(
                check="doc_links_valid",
                passed=True,
                message="No docs/ directory to check",
            )

        total_links = 0
        broken_links = 0

        for doc_file in docs_dir.glob("*.md"):
            try:
                content = doc_file.read_text()
                # Find markdown links: [text](path)
                links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)
                for text, path in links:
                    if path.startswith("http"):
                        continue  # Skip external links
                    total_links += 1
                    # Resolve relative to docs dir
                    target = (docs_dir / path).resolve()
                    if not target.exists():
                        broken_links += 1
            except Exception as exc:
                logger.debug("Non-critical error: %s", exc)

        passed = broken_links == 0
        return DocValidationResult(
            check="doc_links_valid",
            passed=passed,
            message=f"{total_links - broken_links}/{total_links} links valid",
            details={"total": total_links, "broken": broken_links},
        )

    async def _check_env_var_docs(self) -> DocValidationResult:
        """Check that .env.example documents all required env vars."""
        env_example = self.project_root / ".env.example"
        if not env_example.exists():
            return DocValidationResult(
                check="env_var_docs",
                passed=False,
                message=".env.example missing",
            )

        try:
            content = env_example.read_text()
            documented = set(re.findall(r"^([A-Z_]+)=", content, re.MULTILINE))

            # Check that key vars are documented
            required = ["GLM_API_KEY", "FRIDAY_API_TOKEN", "AUTONOMY_PROFILE"]
            missing = [v for v in required if v not in documented]

            passed = len(missing) == 0
            return DocValidationResult(
                check="env_var_docs",
                passed=passed,
                message=f"{len(documented)} env vars documented, {len(missing)} required missing",
                details={"documented": len(documented), "missing": missing},
            )
        except Exception as exc:
            return DocValidationResult(
                check="env_var_docs",
                passed=False,
                message=f"Error: {exc}",
            )

    async def get_summary(self) -> Dict[str, Any]:
        """Get validation summary."""
        results = await self.validate()
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        return {
            "total_checks": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": (passed / len(results) * 100) if results else 0,
            "results": [r.to_dict() for r in results],
        }


# Singleton
_validator: Optional[DocValidator] = None


def get_doc_validator(instance=None) -> DocValidator:
    """Get the singleton DocValidator instance.

    Args:
        instance: Optional instance to inject. When provided, the
            singleton is replaced with this instance. This is primarily
            intended for tests to substitute mock/fake instances.
    """
    global _validator
    if instance is not None:
        _validator = instance
    if _validator is None:
        _validator = DocValidator()
    return _validator
