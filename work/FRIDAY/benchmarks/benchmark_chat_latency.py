"""FRIDAY Chat Latency Benchmark — end-to-end chat latency.

Measures ``FridayBrain.chat_stream`` latency under varying:

  * Message lengths: 10 / 100 / 1 000 / 5 000 chars
  * Concurrency: 1 / 5 / 10 / 50 concurrent requests

The GLM brain is mocked so we don't hit the network — we measure the
brain's own overhead (memory store, RAG retrieval, conversation
history, tool routing, async scheduling) on top of an optional
simulated per-request API latency (default 0 ms).

Two sub-benchmarks are produced:

  1. ``brain_direct`` — call ``brain.chat_stream`` directly and
     measure time-to-first-token (TTFT) and time-to-full-response.
  2. ``api_asgi`` — drive the FastAPI ``POST /api/chat`` endpoint via
     ``httpx.AsyncClient`` with ``httpx.ASGITransport`` to include
     the API layer overhead (auth, rate limiting, SSE encoding).

Results are written to ``benchmarks/results_chat_latency.json``.

Usage::

    python benchmarks/benchmark_chat_latency.py
    python benchmarks/benchmark_chat_latency.py --iterations=20
    python benchmarks/benchmark_chat_latency.py --simulated-api-ms=50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Tuple
from unittest.mock import MagicMock

# Make sure the repo root is importable.
BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

# Keep the test environment deterministic.
os.environ.setdefault("FRIDAY_DEV_MODE", "1")
for _k in ("GLM_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
           "TAVILY_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_k, None)

# Silence noisy module-init logs (audit-chain warnings etc.).
import logging  # noqa: E402
logging.disable(logging.WARNING)

RESULTS_PATH = BENCH_DIR / "results_chat_latency.json"


# ─────────────────────────────────────────────────────────────────────
# Mock GLM brain builder
# ─────────────────────────────────────────────────────────────────────

CANNED_RESPONSE = (
    "Hello from FRIDAY. I've processed your request and here's my "
    "response: the analysis is complete. Let me know if you'd like to "
    "explore any sub-topic in more depth."
)


def _make_fake_glm_responses(content: str, finish_reason: str = "stop"):
    """Build OpenAI/ZhipuAI-shaped response objects (streaming + non).

    Returns a tuple ``(streaming_list, non_streaming_obj)``:

      * ``streaming_list`` — a list of chunk objects, each with
        ``choices[0].delta.content``. Used by ``glm_brain.chat_stream``
        which iterates the response.
      * ``non_streaming_obj`` — a single response object with
        ``choices[0].message.content``. Used by
        ``_glm_stream_with_tools`` which accesses
        ``response.choices[0].message``.
    """
    fake_choice = SimpleNamespace(
        finish_reason=finish_reason,
        message=SimpleNamespace(content=content, tool_calls=None),
    )
    non_streaming = SimpleNamespace(choices=[fake_choice])

    # Split the canned text into ~5 "tokens" so the streaming path
    # exercises the yield-per-chunk loop in glm_brain.chat_stream.
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
    # Final chunk carries the finish_reason
    streaming_chunks.append(SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=""),
            finish_reason=finish_reason,
        )],
    ))

    return streaming_chunks, non_streaming


def build_brain_with_mock_glm(
    simulated_api_ms: int = 0,
    tools_enabled: bool = False,
):
    """Return a FridayBrain whose GLM client is mocked.

    Args:
        simulated_api_ms: Simulated network/model latency injected
            before the canned response is returned. Default 0 — we
            measure pure brain overhead.
        tools_enabled: If True, leave ``brain.tools`` populated so
            ``_glm_stream_with_tools`` runs (the path real production
            traffic takes). If False (default), clear tools so the
            simpler ``glm_brain.chat_stream`` path is exercised —
            isolating brain overhead from tool-loop overhead.
    """
    from core.brain import FridayBrain

    brain = FridayBrain()

    fake_client = MagicMock()
    streaming_response, non_streaming_response = _make_fake_glm_responses(CANNED_RESPONSE)

    # The GLM brain calls client.chat.completions.create(stream=True)
    # from glm_brain.chat_stream (streaming path) and create(stream=False)
    # from _glm_stream_with_tools (non-streaming path). The side_effect
    # inspects the stream kwarg and returns the right shape.
    def _create(*args, **kwargs):
        if simulated_api_ms > 0:
            time.sleep(simulated_api_ms / 1000.0)
        if kwargs.get("stream"):
            return streaming_response
        return non_streaming_response

    fake_client.chat.completions.create.side_effect = _create

    # Wire the mock into the GLM brain. ``available()`` checks
    # ``_ZHIPU_AVAILABLE`` (module-level) and ``_use_api`` first; we
    # patch ``available`` directly so the GLM code path is exercised
    # even when the real ``zhipuai`` package isn't installed.
    brain.glm_brain._client = fake_client
    brain.glm_brain._use_api = True
    brain.glm_brain.available = lambda: True
    brain.glm_brain.api_key = brain.glm_brain.api_key or "test-mock-key"

    # Also short-circuit the no-API-key fallback path that would
    # otherwise try to reach Ollama via httpx on every chat_stream
    # call (audit's "blocking sync I/O" hotspot).
    brain.local_brain.available = lambda: False

    if not tools_enabled:
        brain.tools = []

    return brain


# ─────────────────────────────────────────────────────────────────────
# Direct brain.chat_stream benchmark
# ─────────────────────────────────────────────────────────────────────

async def _measure_one_request(
    brain,
    message: str,
) -> Dict[str, float]:
    """Measure TTFT and full-response time for a single chat_stream call."""
    t_start = time.perf_counter()
    t_first = None
    full = []
    async for chunk in brain.chat_stream(message):
        if t_first is None:
            t_first = time.perf_counter()
        full.append(chunk)
    t_end = time.perf_counter()
    return {
        "ttft_ms": (t_first - t_start) * 1000 if t_first else 0.0,
        "full_ms": (t_end - t_start) * 1000,
        "response_chars": len("".join(full)),
    }


def _stats(samples: List[float]) -> Dict[str, float]:
    if not samples:
        return {"min": 0, "p50": 0, "p90": 0, "p99": 0, "mean": 0, "n": 0}
    s = sorted(samples)
    n = len(s)
    p50 = s[int(0.50 * (n - 1))]
    p90 = s[int(0.90 * (n - 1))]
    p99 = s[int(0.99 * (n - 1))]
    return {
        "min": round(min(s), 3),
        "p50": round(p50, 3),
        "p90": round(p90, 3),
        "p99": round(p99, 3),
        "mean": round(statistics.mean(s), 3),
        "n": n,
    }


async def bench_message_lengths(
    iterations: int,
    simulated_api_ms: int,
) -> Dict:
    """Vary message length, hold concurrency at 1."""
    print(f"[chat] Message-length sweep (iterations={iterations}, "
          f"simulated_api_ms={simulated_api_ms})...")
    brain = build_brain_with_mock_glm(simulated_api_ms=simulated_api_ms)
    results: Dict[str, Dict] = {}

    for n_chars in (10, 100, 1000, 5000):
        msg = "x" * n_chars  # repetitive but stable; brain doesn't parse content
        samples_ttft: List[float] = []
        samples_full: List[float] = []
        for _ in range(iterations):
            r = await _measure_one_request(brain, msg)
            samples_ttft.append(r["ttft_ms"])
            samples_full.append(r["full_ms"])
        results[f"{n_chars}_chars"] = {
            "ttft_ms": _stats(samples_ttft),
            "full_ms": _stats(samples_full),
        }
        s = results[f"{n_chars}_chars"]
        print(f"  {n_chars:>5d} chars: "
              f"TTFT p50={s['ttft_ms']['p50']:7.2f} ms  "
              f"p99={s['ttft_ms']['p99']:7.2f} ms  |  "
              f"full p50={s['full_ms']['p50']:7.2f} ms  "
              f"p99={s['full_ms']['p99']:7.2f} ms")

    return results


async def bench_concurrency(
    iterations: int,
    simulated_api_ms: int,
) -> Dict:
    """Vary concurrency, hold message length at ~100 chars."""
    print(f"[chat] Concurrency sweep (iterations={iterations}, "
          f"simulated_api_ms={simulated_api_ms})...")
    brain = build_brain_with_mock_glm(simulated_api_ms=simulated_api_ms)
    msg = "y" * 100
    results: Dict[str, Dict] = {}

    for c in (1, 5, 10, 50):
        # We collect ``iterations`` samples per concurrency level.
        # Each sample is one batch of ``c`` concurrent requests; we
        # record the *max* full-response time in each batch as the
        # batch latency (worst-case user experience).
        batch_full: List[float] = []
        batch_ttft: List[float] = []
        for _ in range(iterations):
            coros = [_measure_one_request(brain, msg) for _ in range(c)]
            done = await asyncio.gather(*coros)
            batch_full.append(max(d["full_ms"] for d in done))
            batch_ttft.append(max(d["ttft_ms"] for d in done))
        results[f"c={c}"] = {
            "ttft_max_ms": _stats(batch_ttft),
            "full_max_ms": _stats(batch_full),
        }
        s = results[f"c={c}"]
        print(f"  c={c:>2d}: "
              f"TTFT max p50={s['ttft_max_ms']['p50']:7.2f} ms  "
              f"p99={s['ttft_max_ms']['p99']:7.2f} ms  |  "
              f"full max p50={s['full_max_ms']['p50']:7.2f} ms  "
              f"p99={s['full_max_ms']['p99']:7.2f} ms")

    return results


# ─────────────────────────────────────────────────────────────────────
# ASGI / API-level benchmark (httpx + FastAPI app)
# ─────────────────────────────────────────────────────────────────────

async def bench_api_asgi(iterations: int) -> Dict:
    """Drive ``POST /api/chat`` through httpx ASGI transport.

    Reuses the singleton brain from ``api.main._brain_instance`` so we
    can pre-mock it before any HTTP request arrives.
    """
    print(f"[chat] ASGI API sweep (iterations={iterations})...")

    # Import API modules (heavyweight — done once).
    import api.main as api_main
    from httpx import AsyncClient, ASGITransport

    # Pre-build a mocked brain and swap it into the singleton slot
    # so the API doesn't construct a real one.
    brain = build_brain_with_mock_glm(simulated_api_ms=0)
    api_main._brain_instance = brain

    api_token = os.environ.get("FRIDAY_API_TOKEN", "")
    headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}

    samples_full: List[float] = []
    samples_bytes: List[float] = []

    transport = ASGITransport(app=api_main.app)
    async with AsyncClient(transport=transport, base_url="http://bench") as client:
        for _ in range(iterations):
            t0 = time.perf_counter()
            r = await client.post(
                "/api/chat",
                json={"message": "hello", "user_name": "Bench"},
                headers=headers,
            )
            t1 = time.perf_counter()
            if r.status_code != 200:
                raise RuntimeError(
                    f"API returned {r.status_code}: {r.text[:200]}"
                )
            samples_full.append((t1 - t0) * 1000)
            samples_bytes.append(len(r.content))

    stats_full = _stats(samples_full)
    print(f"  POST /api/chat: "
          f"full p50={stats_full['p50']:7.2f} ms  "
          f"p90={stats_full['p90']:7.2f} ms  "
          f"p99={stats_full['p99']:7.2f} ms  "
          f"(response ~{statistics.mean(samples_bytes):.0f} bytes)")
    return {
        "post_chat_full_ms": stats_full,
        "response_bytes_mean": round(statistics.mean(samples_bytes), 1),
        "iterations": iterations,
    }


# ─────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────

async def run_async(iterations: int, simulated_api_ms: int,
                    skip_api: bool) -> Dict:
    msg_lens = await bench_message_lengths(iterations, simulated_api_ms)
    conc = await bench_concurrency(iterations, simulated_api_ms)
    api = None
    if not skip_api:
        try:
            api = await bench_api_asgi(iterations)
        except Exception as exc:
            print(f"[chat] ASGI API bench skipped: {exc}")
            api = {"error": str(exc)}
    return {
        "benchmark": "chat_latency",
        "iterations": iterations,
        "simulated_api_ms": simulated_api_ms,
        "brain_direct": {
            "message_lengths": msg_lens,
            "concurrency": conc,
        },
        "api_asgi": api,
        "methodology": {
            "mock": "GLM client replaced with MagicMock returning canned response",
            "ttft": "time from chat_stream call to first chunk yielded",
            "full": "time from chat_stream call to generator exhaustion",
            "concurrency_metric": "max(full_ms) across the batch — worst-case user latency",
            "canned_response_chars": len(CANNED_RESPONSE),
            "note": (
                "simulated_api_ms=0 isolates brain overhead. Set to "
                "~50 to model realistic GLM-4-Flash round-trip."
            ),
        },
    }


def run(iterations: int = 10, simulated_api_ms: int = 0,
        skip_api: bool = False) -> Dict:
    result = asyncio.run(run_async(iterations, simulated_api_ms, skip_api))
    RESULTS_PATH.write_text(json.dumps(result, indent=2))
    print(f"[chat] Wrote {RESULTS_PATH}")
    return result


def main():
    p = argparse.ArgumentParser(description="FRIDAY chat latency benchmark")
    p.add_argument("--iterations", type=int, default=10,
                   help="Samples per (length, concurrency) cell (default 10)")
    p.add_argument("--simulated-api-ms", type=int, default=0,
                   help="Per-request simulated GLM API latency in ms (default 0)")
    p.add_argument("--skip-api", action="store_true",
                   help="Skip the ASGI /api/chat benchmark")
    args = p.parse_args()
    run(iterations=args.iterations,
        simulated_api_ms=args.simulated_api_ms,
        skip_api=args.skip_api)


if __name__ == "__main__":
    main()
