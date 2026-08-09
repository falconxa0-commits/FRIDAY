"""FRIDAY Startup Benchmark — cold-start breakdown by component.

Measures the time taken by each phase of FRIDAY's startup, broken down
by component:

    1. Python interpreter startup (subprocess ``python -c "pass"``)
    2. Import core modules (config, core.brain, core.ledger, …)
    3. FridayBrain __init__
       - Integration discovery (UniversalConnector)
       - Skills discovery
    4. First chat readiness (time from process start to first chunk
       yielded by ``brain.chat_stream``, using a mocked GLM brain)

Each iteration runs in a fresh subprocess so module caches are cold.
We run 5 iterations and report min / median / p99 / mean for every
phase. For n=5, p99 collapses to max — that's fine for a smoke
benchmark; bump ITERATIONS for tighter percentiles.

Results are written to ``benchmarks/results_startup.json``.

Usage::

    python benchmarks/benchmark_startup.py
    python benchmarks/benchmark_startup.py --iterations=10
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Dict, List

# Ensure benchmarks/ lives on sys.path so the child script can import
# the in-process probe without polluting the parent.
BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
RESULTS_PATH = BENCH_DIR / "results_startup.json"

# Subprocess probe: prints a single JSON line on stdout with per-phase
# timings. Kept as a string so we can pass it via ``python -c`` without
# needing an extra file on disk.
_PROBE_SCRIPT = textwrap.dedent(
    """
    import json
    import os
    import sys
    import time

    # Aggressive log suppression — the audit-chain "broken hash"
    # warning is unrelated to startup perf and would otherwise dominate
    # stderr. We still let unexpected exceptions surface.
    import logging
    logging.disable(logging.CRITICAL)

    os.environ.setdefault("FRIDAY_DEV_MODE", "1")
    # Make sure no real API keys are picked up from the environment so
    # the import path matches the "no providers available" code path
    # that ships by default.
    for k in ("GLM_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
              "TAVILY_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(k, None)

    t_probe_start = time.perf_counter()

    # Phase 2 — import core modules
    t0 = time.perf_counter()
    import config.settings  # noqa: F401
    import core.ledger  # noqa: F401
    import core.glm_brain  # noqa: F401
    import core.brain  # noqa: F401
    from core.brain import FridayBrain  # noqa: F401
    from core.universal_connector import UniversalConnector  # noqa: F401
    t_imports_done = time.perf_counter()

    # Phase 3a — integration discovery only (UniversalConnector)
    from integrations.registry import UniversalRegistry  # noqa: F401
    t_uc_start = time.perf_counter()
    connector = UniversalConnector()
    t_uc_done = time.perf_counter()
    n_integrations = len(connector.integrations)

    # Phase 3b — skills discovery (called inside FridayBrain.__init__).
    # We measure it indirectly by comparing brain-init time with and
    # without skills present. For the cold-start breakdown we just
    # record the brain init time; the skills-only timing is captured
    # in a separate probe below.
    t_brain_start = time.perf_counter()
    brain = FridayBrain()
    t_brain_done = time.perf_counter()
    n_skills = len(brain.skills)

    # Phase 4 — first chat readiness: mock the GLM brain so we don't
    # hit the network, then drain the first chunk from chat_stream.
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    # Build a streaming-shaped mock response — glm_brain.chat_stream
    # iterates the response from client.chat.completions.create(stream=True).
    canned = "Hello from FRIDAY. How can I help?"
    streaming_chunks = [
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=canned),
            finish_reason=None,
        )]),
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=""),
            finish_reason="stop",
        )]),
    ]
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = streaming_chunks
    brain.glm_brain._client = fake_client
    brain.glm_brain._use_api = True  # so available() returns True
    # Patch available() — zhipuai isn't installed in the bench env,
    # so we need to bypass the module-level _ZHIPU_AVAILABLE check
    # to actually exercise the GLM code path.
    brain.glm_brain.available = lambda: True
    brain.glm_brain.api_key = brain.glm_brain.api_key or "test-mock-key"
    brain.local_brain.available = lambda: False
    # Force the tools path off so chat_stream calls glm_brain.chat_stream
    # directly (we'd otherwise need to mock the OpenAI-style tool-call
    # loop). Disabling tools isolates startup cost from tool-routing
    # overhead — that's measured in benchmark_chat_latency.py.
    brain.tools = []

    import asyncio

    async def _first_chunk():
        async for chunk in brain.chat_stream("hi"):
            return chunk
        return ""

    t_first_chunk_start = time.perf_counter()
    first_chunk = asyncio.run(_first_chunk())
    t_first_chunk_done = time.perf_counter()

    payload = {
        "phases_ms": {
            # Phase 1 (Python interpreter) is measured by the parent.
            "import_core_modules": (t_imports_done - t0) * 1000,
            "integration_discovery": (t_uc_done - t_uc_start) * 1000,
            "brain_init_total": (t_brain_done - t_brain_start) * 1000,
            "first_chat_chunk": (t_first_chunk_done - t_first_chunk_start) * 1000,
        },
        "totals_ms": {
            "script_to_first_chunk": (t_first_chunk_done - t_probe_start) * 1000,
            "script_to_brain_ready": (t_brain_done - t_probe_start) * 1000,
        },
        "counts": {
            "integrations": n_integrations,
            "skills": n_skills,
        },
        "first_chunk_preview": (first_chunk or "")[:60],
    }
    print("FRIDAY_STARTUP_BENCH_JSON " + json.dumps(payload))
    """
)


def _measure_python_startup() -> float:
    """Time a no-op Python subprocess in milliseconds."""
    t0 = time.perf_counter()
    subprocess.run(
        [sys.executable, "-c", "pass"],
        cwd=str(REPO_ROOT),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return (time.perf_counter() - t0) * 1000


def _run_one_iteration() -> Dict:
    """Spawn a fresh subprocess, run the probe, parse the JSON line."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Probe subprocess exited {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    line = next(
        (ln for ln in proc.stdout.splitlines()
         if ln.startswith("FRIDAY_STARTUP_BENCH_JSON ")),
        None,
    )
    if line is None:
        raise RuntimeError(
            "Probe did not emit a JSON result line.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return json.loads(line[len("FRIDAY_STARTUP_BENCH_JSON "):])


def _stats(samples: List[float]) -> Dict[str, float]:
    """Compute min / median / p99 / mean / max for a list of floats."""
    if not samples:
        return {"min": 0, "median": 0, "p99": 0, "mean": 0, "max": 0, "n": 0}
    s = sorted(samples)
    n = len(s)
    # p99: nearest-rank method. For n=5, idx = ceil(0.99 * 5) - 1 = 4 → max.
    p99_idx = max(0, min(n - 1, int(round(0.99 * n)) - 1))
    return {
        "min": round(min(s), 3),
        "median": round(statistics.median(s), 3),
        "p99": round(s[p99_idx], 3),
        "mean": round(statistics.mean(s), 3),
        "max": round(max(s), 3),
        "n": n,
    }


def run(iterations: int = 5) -> Dict:
    """Run ``iterations`` cold-start probes and aggregate the results."""
    print(f"[startup] Running {iterations} cold-start probes "
          f"(each in a fresh subprocess)...")

    python_startup_ms = _measure_python_startup()
    print(f"[startup] Python interpreter startup (no-op): "
          f"{python_startup_ms:.1f} ms")

    runs: List[Dict] = []
    for i in range(iterations):
        run = _run_one_iteration()
        runs.append(run)
        total = run["totals_ms"]["script_to_first_chunk"]
        print(f"  iter {i + 1}/{iterations}: script→first-chunk = "
              f"{total:.1f} ms "
              f"(integrations={run['counts']['integrations']}, "
              f"skills={run['counts']['skills']})")

    # Aggregate every numeric phase across runs.
    phase_keys = list(runs[0]["phases_ms"].keys())
    total_keys = list(runs[0]["totals_ms"].keys())
    phases_stats = {
        k: _stats([r["phases_ms"][k] for r in runs]) for k in phase_keys
    }
    totals_stats = {
        k: _stats([r["totals_ms"][k] for r in runs]) for k in total_keys
    }

    python_startup_samples = [
        _measure_python_startup() for _ in range(min(3, iterations))
    ]
    python_startup_stats = _stats(python_startup_samples)

    result = {
        "benchmark": "startup",
        "iterations": iterations,
        "phases_ms": phases_stats,
        "totals_ms": totals_stats,
        "python_interpreter_ms": python_startup_stats,
        "counts": runs[0]["counts"],
        "raw_runs": runs,
        "methodology": {
            "tool": "time.perf_counter (parent) + subprocess probes (child)",
            "python_interpreter": "subprocess python -c 'pass' wall-clock",
            "import_core_modules": "in-probe perf_counter delta around import block",
            "integration_discovery": "UniversalConnector.__init__ duration",
            "brain_init_total": "FridayBrain.__init__ duration (includes skills discovery)",
            "first_chat_chunk": "brain.chat_stream first-chunk latency with mocked GLM brain",
            "note": (
                "Each iteration runs in a fresh subprocess so module "
                "caches are cold. p99 collapses to max for n<=5."
            ),
        },
    }

    RESULTS_PATH.write_text(json.dumps(result, indent=2))
    print(f"[startup] Wrote {RESULTS_PATH}")

    # Pretty-print summary
    print("\n[startup] Summary (median ms across runs):")
    print(f"  Python interpreter startup (no-op):  "
          f"{python_startup_stats['median']:.1f} ms")
    for k, v in phases_stats.items():
        print(f"  {k + ':':37s} "
              f"min={v['min']:7.1f}  p50={v['median']:7.1f}  "
              f"p99={v['p99']:7.1f}  ms")
    print("  " + "-" * 60)
    for k, v in totals_stats.items():
        print(f"  TOTAL {k + ':':29s} "
              f"min={v['min']:7.1f}  p50={v['median']:7.1f}  "
              f"p99={v['p99']:7.1f}  ms")
    return result


def main():
    parser = argparse.ArgumentParser(description="FRIDAY startup benchmark")
    parser.add_argument("--iterations", type=int, default=5,
                        help="Number of cold-start probes (default 5)")
    args = parser.parse_args()
    run(iterations=args.iterations)


if __name__ == "__main__":
    main()
