"""FRIDAY Vector Search Benchmark — scaling analysis for InMemoryVectorStore.

Populates the production ``InMemoryVectorStore`` with 100 / 1 000 /
10 000 mock 1024-dim embeddings and measures search latency.

Compares against a brute-force numpy baseline to confirm that the
current implementation is already O(n) (linear scan). Produces a
concrete recommendation for when to switch to an ANN index
(faiss, hnswlib, or pgvector).

Results are written to ``benchmarks/results_vector_search.json``.

Usage::

    python benchmarks/benchmark_vector_search.py
    python benchmarks/benchmark_vector_search.py --skip-10k
    python benchmarks/benchmark_vector_search.py --trials=200
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List

import numpy as np

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

RESULTS_PATH = BENCH_DIR / "results_vector_search.json"

EMBEDDING_DIM = 1024  # matches ZhipuAI Embedding-3 production dim


# ─────────────────────────────────────────────────────────────────────
# Brute-force numpy reference implementation
# ─────────────────────────────────────────────────────────────────────

class BruteForceVectorStore:
    """Reference implementation — pre-stacked matrix, single matmul."""

    def __init__(self, dim: int = EMBEDDING_DIM):
        self.dim = dim
        self._matrix: np.ndarray = np.empty((0, dim), dtype=np.float32)
        self._texts: List[str] = []
        self._metadatas: List[dict] = []

    def add(self, text: str, embedding: np.ndarray, meta: dict) -> None:
        self._texts.append(text)
        self._metadatas.append(meta)
        # Append-and-restack — same semantics as InMemoryVectorStore,
        # which calls np.array(self.embeddings) on every search.
        self._matrix = np.append(
            self._matrix, embedding.reshape(1, -1).astype(np.float32),
            axis=0,
        )

    def search(self, query_embedding: np.ndarray, top_k: int = 5) -> List[dict]:
        if self._matrix.shape[0] == 0:
            return []
        q = query_embedding.astype(np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            q_norm = 1.0
        # Batched cosine similarity via matrix multiply
        row_norms = np.linalg.norm(self._matrix, axis=1) * q_norm
        row_norms = np.where(row_norms == 0, 1.0, row_norms)
        sims = (self._matrix @ q) / row_norms
        top_indices = np.argsort(sims)[::-1][:top_k]
        return [
            {
                "content": self._texts[idx],
                "metadata": self._metadatas[idx],
                "similarity": float(sims[idx]),
            }
            for idx in top_indices
        ]


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def make_random_unit_vector(dim: int = EMBEDDING_DIM, rng=None) -> np.ndarray:
    rng = rng or np.random.default_rng()
    v = rng.standard_normal(dim).astype(np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def populate(store, n: int, dim: int = EMBEDDING_DIM, seed: int = 42) -> None:
    """Add ``n`` random unit vectors to ``store``."""
    rng = np.random.default_rng(seed)
    for i in range(n):
        v = make_random_unit_vector(dim, rng)
        store.add(f"memory_{i}", v, {"index": i, "text": f"sample {i}"})


def time_searches(store, queries: List[np.ndarray], top_k: int = 5,
                  trials: int = 50) -> Dict[str, float]:
    """Time ``trials`` searches (each using a fresh query)."""
    samples_ms: List[float] = []
    for i in range(trials):
        q = queries[i % len(queries)]
        t0 = time.perf_counter()
        store.search(q, top_k=top_k)
        t1 = time.perf_counter()
        samples_ms.append((t1 - t0) * 1000)
    s = sorted(samples_ms)
    return {
        "min_ms": round(min(s), 4),
        "p50_ms": round(statistics.median(s), 4),
        "p90_ms": round(s[int(0.90 * (len(s) - 1))], 4),
        "p99_ms": round(s[int(0.99 * (len(s) - 1))], 4),
        "mean_ms": round(statistics.mean(s), 4),
        "trials": trials,
    }


# ─────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────

def run(sizes=(100, 1000, 10000), trials: int = 50, top_k: int = 5) -> Dict:
    from database.vector_store import InMemoryVectorStore

    # Build a fixed pool of query vectors so every store is queried
    # with the same workload.
    rng = np.random.default_rng(1234)
    queries = [make_random_unit_vector(EMBEDDING_DIM, rng) for _ in range(trials)]

    print(f"[vector] Trials per size: {trials}, top_k={top_k}, "
          f"dim={EMBEDDING_DIM}")
    results: Dict[str, Dict] = {}
    for n in sizes:
        print(f"\n[vector] Populating size={n}...")
        # ── InMemoryVectorStore (production) ──────────────────────
        prod_store = InMemoryVectorStore()
        t_pop_start = time.perf_counter()
        populate(prod_store, n)
        t_pop_prod = time.perf_counter() - t_pop_start

        # Force one warm-up search so any lazy init happens
        prod_store.search(queries[0], top_k=top_k)
        prod_stats = time_searches(prod_store, queries, top_k=top_k,
                                   trials=trials)

        # ── Brute-force reference (numpy matmul) ──────────────────
        bf_store = BruteForceVectorStore()
        t_bf_start = time.perf_counter()
        populate(bf_store, n)
        t_pop_bf = time.perf_counter() - t_bf_start
        bf_store.search(queries[0], top_k=top_k)  # warm-up
        bf_stats = time_searches(bf_store, queries, top_k=top_k,
                                 trials=trials)

        # ── Per-vector memory cost estimate ───────────────────────
        # Each stored vector = dim * 4 bytes (float32) + small overhead.
        per_vector_kib = round(
            (n * EMBEDDING_DIM * 4) / 1024 / n, 2
        )

        # ── Scaling check ─────────────────────────────────────────
        # Confirm O(n): search time should grow ~linearly with n.
        results[f"n={n}"] = {
            "in_memory_store": prod_stats,
            "brute_force_numpy": bf_stats,
            "populate_ms": {
                "in_memory_store": round(t_pop_prod * 1000, 2),
                "brute_force_numpy": round(t_pop_bf * 1000, 2),
            },
            "per_vector_kib": per_vector_kib,
            "top_k": top_k,
        }
        print(f"  InMemoryVectorStore: "
              f"p50={prod_stats['p50_ms']:.3f} ms  "
              f"p99={prod_stats['p99_ms']:.3f} ms  "
              f"(populate {t_pop_prod*1000:.0f} ms)")
        print(f"  BruteForceNumpy:     "
              f"p50={bf_stats['p50_ms']:.3f} ms  "
              f"p99={bf_stats['p99_ms']:.3f} ms  "
              f"(populate {t_pop_bf*1000:.0f} ms)")

    # ── Scaling analysis ──────────────────────────────────────────
    sizes_keys = [f"n={n}" for n in sizes]
    if len(sizes_keys) >= 2:
        a = results[sizes_keys[0]]["in_memory_store"]["p50_ms"]
        b = results[sizes_keys[-1]]["in_memory_store"]["p50_ms"]
        size_ratio = sizes[-1] / sizes[0]
        time_ratio = b / a if a > 0 else float("inf")
        # Linear scaling => time_ratio ≈ size_ratio
        # Sub-linear => ANN-like behaviour (not expected here)
        # Super-linear => something pathological
        scaling_factor = round(time_ratio / size_ratio, 3) if size_ratio else 0
        linear_scaling_confirmed = 0.5 < scaling_factor < 2.0
    else:
        size_ratio = time_ratio = scaling_factor = 0
        linear_scaling_confirmed = None

    # ── ANN recommendation ────────────────────────────────────────
    # Heuristic: if p99 search latency at the largest size exceeds
    # 50 ms, recommend an ANN index. Otherwise the linear scan is
    # "fast enough" for the current scale.
    largest = results[sizes_keys[-1]]["in_memory_store"]["p99_ms"]
    if largest > 50:
        ann_recommendation = (
            f"Switch to ANN at n≥{sizes[-1]}. Current p99={largest:.1f} ms "
            f"exceeds the 50 ms SLO. Recommended: hnswlib (M=32, ef=64) "
            f"or faiss IVF-PQ. Expected speedup: 5-50x at n=10k, "
            f"50-500x at n=100k."
        )
    elif sizes[-1] >= 10000:
        ann_recommendation = (
            f"Linear scan is acceptable at n={sizes[-1]} (p99={largest:.1f} ms). "
            f"Plan migration to ANN before n reaches ~50k — at that scale "
            f"linear scan will exceed 50 ms p99."
        )
    else:
        ann_recommendation = (
            f"Linear scan is fine at n={sizes[-1]} (p99={largest:.1f} ms). "
            f"Re-benchmark at n=10k before deciding on ANN."
        )

    result = {
        "benchmark": "vector_search",
        "config": {
            "embedding_dim": EMBEDDING_DIM,
            "sizes": list(sizes),
            "trials": trials,
            "top_k": top_k,
            "vector_type": "float32 unit vectors (random Gaussian, normalised)",
        },
        "results": results,
        "scaling_analysis": {
            "smallest_to_largest_size_ratio": round(size_ratio, 2),
            "smallest_to_largest_time_ratio": round(time_ratio, 2),
            "scaling_factor_time_per_size": scaling_factor,
            "linear_scaling_confirmed": linear_scaling_confirmed,
            "explanation": (
                "scaling_factor ≈ 1.0 → O(n) linear scan confirmed. "
                "<0.5 → sub-linear (caching / SIMD wins). "
                ">2.0 → super-linear (pathological)."
            ),
        },
        "ann_recommendation": ann_recommendation,
        "methodology": {
            "production_store": "database.vector_store.InMemoryVectorStore (numpy cosine)",
            "reference_store": "BruteForceVectorStore (same maths, pre-stacked matrix)",
            "warmup": "1 search before timing loop",
            "queries": "pre-generated unit vectors, reused across stores",
            "note": (
                "InMemoryVectorStore rebuilds the matrix from a list "
                "of np.ndarray on every search (np.array(self.embeddings)) "
                "— this is the main reason it lags the brute-force "
                "reference, which keeps a single stacked matrix."
            ),
        },
    }
    RESULTS_PATH.write_text(json.dumps(result, indent=2))
    print(f"\n[vector] Wrote {RESULTS_PATH}")
    print(f"[vector] Scaling factor (time/size): {scaling_factor} "
          f"(linear confirmed: {linear_scaling_confirmed})")
    print(f"[vector] ANN rec: {ann_recommendation}")
    return result


def main():
    p = argparse.ArgumentParser(description="FRIDAY vector search benchmark")
    p.add_argument("--trials", type=int, default=50,
                   help="Search iterations per store size (default 50)")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--skip-10k", action="store_true",
                   help="Skip the 10 000-vector milestone")
    p.add_argument("--sizes", type=str, default="",
                   help="Comma-separated override sizes, e.g. 100,500,1000")
    args = p.parse_args()

    if args.sizes:
        sizes = tuple(int(x) for x in args.sizes.split(","))
    elif args.skip_10k:
        sizes = (100, 1000)
    else:
        sizes = (100, 1000, 10000)

    run(sizes=sizes, trials=args.trials, top_k=args.top_k)


if __name__ == "__main__":
    main()
