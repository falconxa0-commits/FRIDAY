"""Benchmark Runner — executes benchmarks and tracks results over time.

Runs all benchmark scripts and stores results for release-over-release
comparison. Detects performance regressions automatically.

Usage::

    runner = get_benchmark_runner()
    results = await runner.run_all()
    # results → list of BenchmarkResult
    comparison = await runner.compare_to_previous()
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.benchmark_runner")

_BENCH_DIR = Path(os.environ.get(
    "FRIDAY_ENGINEERING_DIR",
    str(Path.cwd() / ".friday"),
)) / "benchmarks"
_BENCH_DIR.mkdir(parents=True, exist_ok=True)
_HISTORY_PATH = _BENCH_DIR / "history.json"


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run."""
    name: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_seconds: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    status: str = "pass"  # pass, fail, skip
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BenchmarkRunner:
    """Runs benchmarks and tracks results over time.

    Executes benchmark scripts in the benchmarks/ directory and
    stores results for historical comparison.
    """

    # Benchmark scripts to run (relative to project root)
    BENCHMARK_SCRIPTS = [
        "benchmarks/benchmark_startup.py",
        "benchmarks/benchmark_vector_search.py",
        "benchmarks/benchmark_memory.py",
    ]

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()
        self._history: List[BenchmarkResult] = []
        self._load()

    def _load(self) -> None:
        if not _HISTORY_PATH.exists():
            return
        try:
            with open(_HISTORY_PATH) as f:
                data = json.load(f)
            self._history = [BenchmarkResult(**r) for r in data]
            logger.info(f"Loaded {len(self._history)} benchmark results")
        except Exception as exc:
            logger.error(f"Failed to load benchmark history: {exc}")

    def _persist(self) -> None:
        data = [r.to_dict() for r in self._history]
        with open(_HISTORY_PATH, "w") as f:
            json.dump(data, f, indent=2, default=str)

    async def run_benchmark(self, script_path: str) -> BenchmarkResult:
        """Run a single benchmark script."""
        full_path = self.project_root / script_path
        if not full_path.exists():
            return BenchmarkResult(
                name=script_path,
                status="skip",
                error=f"Script not found: {script_path}",
            )

        import time
        start = time.perf_counter()

        try:
            result = await asyncio.create_subprocess_exec(
                sys.executable, str(full_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.project_root),
            )
            stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=120)

            duration = time.perf_counter() - start

            if result.returncode == 0:
                # Try to parse metrics from stdout
                metrics = {}
                try:
                    # Look for JSON output
                    for line in stdout.decode().split("\n"):
                        if line.strip().startswith("{"):
                            metrics = json.loads(line)
                            break
                except Exception:
                    pass

                return BenchmarkResult(
                    name=script_path,
                    duration_seconds=duration,
                    metrics=metrics,
                    status="pass",
                )
            else:
                return BenchmarkResult(
                    name=script_path,
                    duration_seconds=duration,
                    status="fail",
                    error=stderr.decode()[:200],
                )

        except asyncio.TimeoutError:
            return BenchmarkResult(
                name=script_path,
                duration_seconds=120.0,
                status="fail",
                error="Timeout after 120s",
            )
        except Exception as exc:
            return BenchmarkResult(
                name=script_path,
                status="fail",
                error=str(exc),
            )

    async def run_all(self) -> List[BenchmarkResult]:
        """Run all benchmarks and store results."""
        results = []
        for script in self.BENCHMARK_SCRIPTS:
            result = await self.run_benchmark(script)
            results.append(result)
            self._history.append(result)

        self._persist()
        return results

    async def get_history(self, name: Optional[str] = None, limit: int = 10) -> List[BenchmarkResult]:
        """Get benchmark history, optionally filtered by name."""
        filtered = self._history
        if name:
            filtered = [r for r in filtered if r.name == name]
        return filtered[-limit:]

    async def compare_to_previous(self) -> Dict[str, Any]:
        """Compare latest results to previous run."""
        if len(self._history) < 2:
            return {"message": "Insufficient history for comparison"}

        # Group by benchmark name
        by_name: Dict[str, List[BenchmarkResult]] = {}
        for r in self._history:
            by_name.setdefault(r.name, []).append(r)

        comparisons = {}
        for name, results in by_name.items():
            if len(results) < 2:
                continue
            latest = results[-1]
            previous = results[-2]

            duration_change = latest.duration_seconds - previous.duration_seconds
            duration_pct = (duration_change / previous.duration_seconds * 100) if previous.duration_seconds else 0

            comparisons[name] = {
                "latest_duration": latest.duration_seconds,
                "previous_duration": previous.duration_seconds,
                "change_seconds": duration_change,
                "change_pct": round(duration_pct, 1),
                "regression": duration_pct > 10,  # >10% slower = regression
                "improvement": duration_pct < -10,  # >10% faster = improvement
            }

        return comparisons

    async def get_stats(self) -> Dict[str, Any]:
        """Get benchmark statistics."""
        return {
            "total_runs": len(self._history),
            "benchmarks": list(set(r.name for r in self._history)),
            "pass_rate": sum(1 for r in self._history if r.status == "pass") / max(len(self._history), 1) * 100,
        }


# Singleton
_runner: Optional[BenchmarkRunner] = None


def get_benchmark_runner() -> BenchmarkRunner:
    global _runner
    if _runner is None:
        _runner = BenchmarkRunner()
    return _runner
