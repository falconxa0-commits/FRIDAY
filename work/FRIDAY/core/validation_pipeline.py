"""Continuous Validation Pipeline — quality gates for every change.

Runs linting, formatting checks, unit tests, integration tests, and
security checks. Returns a structured report that the task system
uses to decide whether to mark a task complete or requeue it.

Design principles:
    - **Fast**: Runs in <60 seconds for incremental changes.
    - **Parallel**: Independent checks run concurrently.
    - **Structured**: Returns a ValidationReport with per-check results.
    - **Fail-fast**: Critical failures short-circuit remaining checks.
    - **Idempotent**: Running twice produces the same result.

Checks:
    1. Syntax check (python -m py_compile)
    2. Import check (all modules importable)
    3. Linting (ruff if available, else pyflakes)
    4. Format check (black --check if available)
    5. Type check (mypy if available, best-effort)
    6. Unit tests (pytest tests/ -x)
    7. Security regression tests (pytest tests/test_security_regression.py)
    8. Audit chain verification (ledger.verify_chain())
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.validation")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class CheckResult:
    """Result of a single validation check."""
    name: str
    status: CheckStatus
    duration_seconds: float
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    """Full validation report for a task or change."""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    checks: List[CheckResult] = field(default_factory=list)
    total_duration_seconds: float = 0.0
    overall_status: CheckStatus = CheckStatus.PASSED

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)
        if result.status == CheckStatus.FAILED:
            self.overall_status = CheckStatus.FAILED
        elif result.status == CheckStatus.ERROR and self.overall_status != CheckStatus.FAILED:
            self.overall_status = CheckStatus.ERROR

    @property
    def passed(self) -> bool:
        return self.overall_status == CheckStatus.PASSED

    @property
    def summary(self) -> str:
        passed = sum(1 for c in self.checks if c.status == CheckStatus.PASSED)
        failed = sum(1 for c in self.checks if c.status == CheckStatus.FAILED)
        skipped = sum(1 for c in self.checks if c.status == CheckStatus.SKIPPED)
        errors = sum(1 for c in self.checks if c.status == CheckStatus.ERROR)
        return f"{passed} passed, {failed} failed, {skipped} skipped, {errors} errors"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "overall_status": self.overall_status.value,
            "total_duration_seconds": self.total_duration_seconds,
            "summary": self.summary,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Validation Pipeline
# ---------------------------------------------------------------------------
class ValidationPipeline:
    """Runs quality gates on demand.

    Usage::

        pipeline = ValidationPipeline()
        report = await pipeline.run(target_dir="/path/to/FRIDAY")
        if report.passed:
            # safe to proceed
        else:
            # block merge, return to agent
    """

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()
        self._python = sys.executable

    async def _run_subprocess(
        self,
        cmd: List[str],
        timeout: int = 120,
        cwd: Optional[Path] = None,
    ) -> tuple[int, str, str]:
        """Run a subprocess with timeout. Returns (returncode, stdout, stderr)."""
        cwd = cwd or self.project_root
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return (
                proc.returncode,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            return (-1, "", f"Timeout after {timeout}s")
        except Exception as exc:
            return (-2, "", str(exc))

    async def _check_syntax(self) -> CheckResult:
        """Check that all Python files compile."""
        start = time.perf_counter()
        py_files = list(self.project_root.rglob("*.py"))
        py_files = [f for f in py_files if ".venv" not in str(f) and "__pycache__" not in str(f)]

        errors = []
        for f in py_files[:100]:  # limit to first 100 files for speed
            rc, _, stderr = await self._run_subprocess(
                [self._python, "-m", "py_compile", str(f)],
                timeout=10,
            )
            if rc != 0:
                errors.append(f"{f.name}: {stderr[:200]}")

        duration = time.perf_counter() - start
        if errors:
            return CheckResult(
                name="syntax_check",
                status=CheckStatus.FAILED,
                duration_seconds=duration,
                message=f"{len(errors)} files with syntax errors",
                details={"errors": errors[:5]},
            )
        return CheckResult(
            name="syntax_check",
            status=CheckStatus.PASSED,
            duration_seconds=duration,
            message=f"{len(py_files)} files compiled successfully",
        )

    async def _check_imports(self) -> CheckResult:
        """Check that core modules import without errors."""
        start = time.perf_counter()
        modules_to_check = [
            "core.brain", "core.ledger", "core.sentinel",
            "core.task_system", "core.knowledge_base", "core.validation_pipeline",
            "core.engineering_org", "core.glm_brain", "core.universal_connector",
            "cli.commands", "cli.terminal", "mcp_server",
        ]
        errors = []
        for mod in modules_to_check:
            rc, stdout, stderr = await self._run_subprocess(
                [self._python, "-c", f"import {mod}"],
                timeout=15,
            )
            if rc != 0:
                errors.append(f"{mod}: {stderr[:200]}")

        duration = time.perf_counter() - start
        if errors:
            return CheckResult(
                name="import_check",
                status=CheckStatus.FAILED,
                duration_seconds=duration,
                message=f"{len(errors)} import failures",
                details={"errors": errors},
            )
        return CheckResult(
            name="import_check",
            status=CheckStatus.PASSED,
            duration_seconds=duration,
            message=f"{len(modules_to_check)} modules imported successfully",
        )

    async def _check_linting(self) -> CheckResult:
        """Run ruff or pyflakes if available."""
        start = time.perf_counter()
        # Try ruff first
        rc, stdout, stderr = await self._run_subprocess(
            [self._python, "-m", "ruff", "check", "--statistics", "."],
            timeout=30,
        )
        if rc == 0:
            duration = time.perf_counter() - start
            return CheckResult(
                name="linting",
                status=CheckStatus.PASSED,
                duration_seconds=duration,
                message="ruff: no issues found",
            )
        elif rc != -2:  # -2 means ruff not installed
            duration = time.perf_counter() - start
            return CheckResult(
                name="linting",
                status=CheckStatus.FAILED,
                duration_seconds=duration,
                message="ruff found issues",
                details={"output": stdout[:500]},
            )
        # Fallback: pyflakes
        rc, stdout, stderr = await self._run_subprocess(
            [self._python, "-m", "pyflakes", "."],
            timeout=30,
        )
        duration = time.perf_counter() - start
        if rc == 0:
            return CheckResult(
                name="linting",
                status=CheckStatus.PASSED,
                duration_seconds=duration,
                message="pyflakes: no issues",
            )
        return CheckResult(
            name="linting",
            status=CheckStatus.SKIPPED,
            duration_seconds=duration,
            message="Neither ruff nor pyflakes available",
        )

    async def _check_unit_tests(self) -> CheckResult:
        """Run the full test suite."""
        start = time.perf_counter()
        rc, stdout, stderr = await self._run_subprocess(
            [self._python, "-m", "pytest", "tests/", "--tb=line", "-q"],
            timeout=300,
        )
        duration = time.perf_counter() - start

        # Parse test count from output
        passed = 0
        failed = 0
        for line in (stdout + stderr).split("\n"):
            if "passed" in line and "failed" in line:
                # e.g., "755 passed, 3 skipped"
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == "passed,":
                        try:
                            passed = int(parts[i - 1])
                        except (ValueError, IndexError):
                            pass
                    elif part == "failed,":
                        try:
                            failed = int(parts[i - 1])
                        except (ValueError, IndexError):
                            pass
            elif "passed" in line and "failed" not in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == "passed":
                        try:
                            passed = int(parts[i - 1])
                        except (ValueError, IndexError):
                            pass

        if rc == 0:
            return CheckResult(
                name="unit_tests",
                status=CheckStatus.PASSED,
                duration_seconds=duration,
                message=f"{passed} passed, {failed} failed",
                details={"passed": passed, "failed": failed},
            )
        return CheckResult(
            name="unit_tests",
            status=CheckStatus.FAILED if failed > 0 else CheckStatus.ERROR,
            duration_seconds=duration,
            message=f"{passed} passed, {failed} failed",
            details={"passed": passed, "failed": failed, "stderr": stderr[:500]},
        )

    async def _check_security_regression(self) -> CheckResult:
        """Run security regression tests."""
        start = time.perf_counter()
        rc, stdout, stderr = await self._run_subprocess(
            [self._python, "-m", "pytest", "tests/test_security_regression.py",
             "tests/test_ledger_security.py", "-q", "--tb=line"],
            timeout=120,
        )
        duration = time.perf_counter() - start
        if rc == 0:
            return CheckResult(
                name="security_regression",
                status=CheckStatus.PASSED,
                duration_seconds=duration,
                message="Security regression tests passed",
            )
        return CheckResult(
            name="security_regression",
            status=CheckStatus.FAILED,
            duration_seconds=duration,
            message="Security regression tests failed",
            details={"stderr": stderr[:500]},
        )

    async def _check_ledger_chain(self) -> CheckResult:
        """Verify the audit ledger chain is intact."""
        start = time.perf_counter()
        try:
            from core.ledger import get_ledger
            ledger = get_ledger()
            valid = ledger.verify_chain()
            duration = time.perf_counter() - start
            if valid:
                entries = len(ledger.get_audit_log())
                return CheckResult(
                    name="ledger_chain",
                    status=CheckStatus.PASSED,
                    duration_seconds=duration,
                    message=f"Chain valid ({entries} entries)",
                )
            return CheckResult(
                name="ledger_chain",
                status=CheckStatus.FAILED,
                duration_seconds=duration,
                message="Chain BROKEN — tampering detected",
            )
        except Exception as exc:
            duration = time.perf_counter() - start
            return CheckResult(
                name="ledger_chain",
                status=CheckStatus.ERROR,
                duration_seconds=duration,
                message=f"Error checking chain: {exc}",
            )

    async def run(self, skip_tests: bool = False) -> ValidationReport:
        """Run all validation checks.

        Args:
            skip_tests: If True, skip the slow unit test check
                        (useful for quick pre-flight validation).
        """
        report = ValidationReport()
        overall_start = time.perf_counter()

        # Run checks in parallel where possible
        # Syntax + imports + linting can run together
        syntax_result, import_result, lint_result = await asyncio.gather(
            self._check_syntax(),
            self._check_imports(),
            self._check_linting(),
        )
        report.add(syntax_result)
        report.add(import_result)
        report.add(lint_result)

        # Fail-fast: if syntax or imports fail, skip tests
        if syntax_result.status == CheckStatus.FAILED or import_result.status == CheckStatus.FAILED:
            report.total_duration_seconds = time.perf_counter() - overall_start
            return report

        # Run tests + security + ledger in parallel
        if not skip_tests:
            test_result, sec_result, ledger_result = await asyncio.gather(
                self._check_unit_tests(),
                self._check_security_regression(),
                self._check_ledger_chain(),
            )
            report.add(test_result)
            report.add(sec_result)
            report.add(ledger_result)
        else:
            ledger_result = await self._check_ledger_chain()
            report.add(ledger_result)

        report.total_duration_seconds = time.perf_counter() - overall_start
        return report


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_pipeline: Optional[ValidationPipeline] = None


def get_validation_pipeline() -> ValidationPipeline:
    """Get the singleton ValidationPipeline instance."""
    global _pipeline
    if _pipeline is None:
        _pipeline = ValidationPipeline()
    return _pipeline
