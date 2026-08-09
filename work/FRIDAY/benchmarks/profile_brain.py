"""FRIDAY Brain Profiling — cProfile of ``FridayBrain.chat_stream``.

Runs 100 chat requests through ``FridayBrain.chat_stream`` with a
mocked GLM client (so we profile FRIDAY's own code, not the network).

Outputs:

  * ``benchmarks/profile_brain.prof``  — binary cProfile data
    (loadable in ``pstats`` or snakeviz).
  * ``benchmarks/profile_brain.txt``   — human-readable top-20
    cumulative-time + top-20 call-count summary, plus hotspot list
    (functions taking >10 ms average per request).

Usage::

    python benchmarks/profile_brain.py
    python benchmarks/profile_brain.py --requests=200
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import json
import os
import pstats
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List
from unittest.mock import MagicMock

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("FRIDAY_DEV_MODE", "1")
for _k in ("GLM_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
           "TAVILY_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_k, None)

import logging  # noqa: E402
logging.disable(logging.WARNING)

PROFILE_PATH = BENCH_DIR / "profile_brain.prof"
SUMMARY_PATH = BENCH_DIR / "profile_brain.txt"
RESULTS_JSON = BENCH_DIR / "results_profile_brain.json"

CANNED_RESPONSE = (
    "Sure — here's my response. I've considered the context and "
    "assembled an answer based on the relevant information. Let me "
    "know if you'd like me to expand on any specific aspect."
)


# ─────────────────────────────────────────────────────────────────────
# Mock GLM brain
# ─────────────────────────────────────────────────────────────────────

def _make_fake_glm_responses(content: str, finish_reason: str = "stop"):
    """Build OpenAI/ZhipuAI-shaped response objects (streaming + non)."""
    fake_choice = SimpleNamespace(
        finish_reason=finish_reason,
        message=SimpleNamespace(content=content, tool_calls=None),
    )
    non_streaming = SimpleNamespace(choices=[fake_choice])

    words = content.split(" ")
    chunk_size = max(1, len(words) // 5)
    streaming_chunks = []
    for i in range(0, len(words), chunk_size):
        piece = " ".join(words[i:i + chunk_size])
        streaming_chunks.append(SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content=piece),
                finish_reason=None,
            )],
        ))
    streaming_chunks.append(SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=""),
            finish_reason=finish_reason,
        )],
    ))
    return streaming_chunks, non_streaming


def build_profiled_brain():
    from core.brain import FridayBrain
    from core.memory import FridayMemory

    # Use real FridayMemory so memory-store code paths show up in the
    # profile (they're part of the per-request cost).
    memory = FridayMemory()
    brain = FridayBrain(memory=memory)

    streaming, non_streaming = _make_fake_glm_responses(CANNED_RESPONSE)
    fake_client = MagicMock()

    def _create(*args, **kwargs):
        if kwargs.get("stream"):
            return streaming
        return non_streaming

    fake_client.chat.completions.create.side_effect = _create

    brain.glm_brain._client = fake_client
    brain.glm_brain._use_api = True
    # Patch available() so the GLM code path runs even without the
    # real zhipuai package installed.
    brain.glm_brain.available = lambda: True
    brain.glm_brain.api_key = brain.glm_brain.api_key or "test-mock-key"
    # Short-circuit the Ollama fallback — we want the GLM path, not
    # the local-brain path that creates a fresh httpx client on every
    # chat_stream call.
    brain.local_brain.available = lambda: False
    brain.tools = []  # isolate brain overhead from tool-loop overhead
    return brain


# ─────────────────────────────────────────────────────────────────────
# Sync wrapper that drains the async generator under cProfile
# ─────────────────────────────────────────────────────────────────────

def _drain_chat_stream_sync(brain, message: str) -> str:
    """Synchronously drain one chat_stream call.

    cProfile tracks the calling thread, so we run the event loop in
    this thread via ``asyncio.run``. The generator's internal awaits
    still release control to the loop, but every function call along
    the way is attributed to the calling frame.
    """
    async def _collect():
        chunks: List[str] = []
        async for chunk in brain.chat_stream(message):
            chunks.append(chunk)
        return "".join(chunks)

    return asyncio.run(_collect())


def run_profile(n_requests: int = 100) -> Dict:
    print(f"[profile] Building brain with mocked GLM client...")
    brain = build_profiled_brain()
    messages = [f"This is benchmark request number {i}. Please respond." for i in range(n_requests)]

    # ── Warm-up (1 request) ───────────────────────────────────────
    print(f"[profile] Warm-up request...")
    _drain_chat_stream_sync(brain, "warm-up message")

    # ── Profiled run ──────────────────────────────────────────────
    print(f"[profile] Profiling {n_requests} chat requests under cProfile...")
    profiler = cProfile.Profile()
    t_start = time.perf_counter()
    profiler.enable()
    for msg in messages:
        _drain_chat_stream_sync(brain, msg)
    profiler.disable()
    t_total = time.perf_counter() - t_start
    print(f"[profile] Total wall-clock: {t_total:.2f} s  "
          f"({t_total * 1000 / n_requests:.2f} ms/request)")

    # ── Save binary .prof ─────────────────────────────────────────
    profiler.dump_stats(str(PROFILE_PATH))
    print(f"[profile] Wrote {PROFILE_PATH}")

    # ── Build human-readable summary ──────────────────────────────
    stats = pstats.Stats(profiler)

    # Top 20 by cumulative time
    buf_cum = io.StringIO()
    stats_cum = pstats.Stats(profiler, stream=buf_cum)
    stats_cum.sort_stats("cumulative").print_stats(20)
    cum_text = buf_cum.getvalue()

    # Top 20 by call count
    buf_cnt = io.StringIO()
    stats_cnt = pstats.Stats(profiler, stream=buf_cnt)
    stats_cnt.sort_stats("tottime").print_stats(20)
    tottime_text = buf_cnt.getvalue()

    buf_callcount = io.StringIO()
    stats_cc = pstats.Stats(profiler, stream=buf_callcount)
    stats_cc.sort_stats("calls").print_stats(20)
    callcount_text = buf_callcount.getvalue()

    # Hotspots: functions where (a) tottime > 10 ms (audit-specified
    # threshold), OR (b) avg/call > 1 ms (single-call cost outliers).
    # We also surface high-call-count functions (>1000 calls) since
    # those often indicate hidden per-request work.
    threshold_total_s = 0.010   # 10 ms total
    threshold_avg_ms = 1.0       # 1 ms per call
    threshold_ncalls = 1000
    hotspots: List[Dict] = []
    try:
        for (file, line, fn), (cc, nc, tt, ct, callers) in stats.stats.items():
            if nc == 0:
                continue
            avg_ms = (tt / nc) * 1000
            is_hot = (
                tt > threshold_total_s
                or avg_ms > threshold_avg_ms
                or nc > threshold_ncalls
            )
            if is_hot:
                hotspots.append({
                    "function": f"{fn} ({Path(file).name}:{line})",
                    "ncalls": nc,
                    "tottime_s": round(tt, 4),
                    "cumtime_s": round(ct, 4),
                    "avg_per_call_ms": round(avg_ms, 3),
                    "tottime_pct": round(tt / t_total * 100, 1),
                    "flags": {
                        "tottime_gt_10ms": tt > threshold_total_s,
                        "avg_gt_1ms": avg_ms > threshold_avg_ms,
                        "ncalls_gt_1000": nc > threshold_ncalls,
                    },
                })
        hotspots.sort(key=lambda x: x["tottime_s"], reverse=True)
    except Exception as exc:
        print(f"[profile] Hotspot extraction failed: {exc}")

    summary = (
        f"# FRIDAY Brain cProfile Summary\n"
        f"\n"
        f"**Requests profiled:** {n_requests}\n"
        f"**Total wall-clock:** {t_total:.2f} s "
        f"({t_total * 1000 / n_requests:.2f} ms/request)\n"
        f"**Mock:** GLM client replaced with MagicMock returning a "
        f"{len(CANNED_RESPONSE)}-char canned response.\n"
        f"**Tools:** disabled (``brain.tools = []``) to isolate brain "
        f"overhead from the tool-routing loop.\n"
        f"**Memory:** real FridayMemory (with hash-based embedding fallback).\n"
        f"\n"
        f"---\n\n"
        f"## Top 20 by cumulative time\n\n"
        f"```\n{cum_text}```\n\n"
        f"## Top 20 by total time (tottime)\n\n"
        f"```\n{tottime_text}```\n\n"
        f"## Top 20 by call count\n\n"
        f"```\n{callcount_text}```\n\n"
        f"## Hotspots (tottime > 10 ms OR avg > 1 ms/call OR ncalls > 1000)\n\n"
    )
    if hotspots:
        summary += "| Function | ncalls | tottime (s) | % | avg/call (ms) | flags |\n"
        summary += "|----------|--------|-------------|---|----------------|-------|\n"
        for h in hotspots:
            flags = []
            if h["flags"]["tottime_gt_10ms"]:
                flags.append("TOT>10ms")
            if h["flags"]["avg_gt_1ms"]:
                flags.append("AVG>1ms")
            if h["flags"]["ncalls_gt_1000"]:
                flags.append(f"NC>1000")
            summary += (
                f"| `{h['function']}` | {h['ncalls']} | "
                f"{h['tottime_s']} | {h['tottime_pct']}% | "
                f"{h['avg_per_call_ms']} | {', '.join(flags) or '-'} |\n"
            )
    else:
        summary += (
            "No function exceeded the 10 ms/call threshold. "
            "FRIDAY's per-request work is well-distributed.\n"
        )
    summary += (
        f"\n---\n\n"
        f"## Binary profile\n\n"
        f"Load `profile_brain.prof` in ``pstats``, ``snakeviz``, or "
        f"``pyprof2calltree`` for interactive exploration:\n\n"
        f"```\n"
        f"python -m pstats benchmarks/profile_brain.prof\n"
        f"snakeviz benchmarks/profile_brain.prof\n"
        f"```\n"
    )
    SUMMARY_PATH.write_text(summary)
    print(f"[profile] Wrote {SUMMARY_PATH}")

    result = {
        "benchmark": "profile_brain",
        "requests": n_requests,
        "total_wall_s": round(t_total, 3),
        "ms_per_request": round(t_total * 1000 / n_requests, 2),
        "canned_response_chars": len(CANNED_RESPONSE),
        "tools_enabled": False,
        "memory_mocked": False,
        "hotspots": hotspots,
        "output_files": {
            "prof_binary": str(PROFILE_PATH),
            "summary_txt": str(SUMMARY_PATH),
        },
        "methodology": {
            "profiler": "cProfile.Profile (deterministic, all calls)",
            "async_strategy": "asyncio.run() per request in calling thread",
            "warmup": "1 unprofiled request before measurement",
            "note": (
                "cProfile adds ~2-5x overhead per call — absolute "
                "timings here are NOT comparable to "
                "benchmark_chat_latency.py. Use this output to find "
                "hotspots and call-count outliers, not for SLA tracking."
            ),
        },
    }
    RESULTS_JSON.write_text(json.dumps(result, indent=2))
    print(f"[profile] Wrote {RESULTS_JSON}")
    print(f"[profile] {len(hotspots)} hotspot(s) > 10 ms/call")
    for h in hotspots[:5]:
        print(f"  - {h['function']}: {h['avg_per_call_ms']} ms/call "
              f"({h['ncalls']} calls)")
    return result


def main():
    p = argparse.ArgumentParser(description="FRIDAY brain cProfile benchmark")
    p.add_argument("--requests", type=int, default=100,
                   help="Number of chat_stream calls to profile (default 100)")
    args = p.parse_args()
    run_profile(n_requests=args.requests)


if __name__ == "__main__":
    main()
