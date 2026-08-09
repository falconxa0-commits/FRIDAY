# FRIDAY Performance Benchmarks — WAVE1-PERF

**Date:** 2026-08-09 (retry)
**Agent:** Performance Engineer
**Codebase:** FRIDAY v3.2 (`/home/z/my-project/work/FRIDAY`)
**Bench files:** `benchmarks/benchmark_*.py`
**Result JSONs:** `benchmarks/results_*.json`

---

## 1. Methodology

### What was measured

| Benchmark | Metric | Mock? | Tool |
|-----------|--------|-------|------|
| `benchmark_startup.py` | Cold-start time per phase (Python interp, imports, brain init, integration discovery, first chat chunk) | GLM client mocked (canned streaming response) | `time.perf_counter()` in fresh subprocess per iteration |
| `benchmark_chat_latency.py` | TTFT (time-to-first-token) and full-response latency under varying message length (10/100/1000/5000 chars) and concurrency (1/5/10/50); ASGI POST `/api/chat` end-to-end | GLM client mocked (streaming + non-streaming shapes) | `asyncio` + `time.perf_counter()`; `httpx.ASGITransport` for the API path |
| `benchmark_memory.py` | Process RSS at each milestone (imports → brain init → 100/1000 memories → 100 chat requests) | GLM client mocked; **real** `FridayMemory` (hash-embed fallback) | `psutil.Process().memory_info().rss` |
| `benchmark_vector_search.py` | Search p50/p99 latency at 100/1000/10000 vectors (1024-dim float32, unit-normalised) | Production `InMemoryVectorStore` + brute-force numpy reference | `time.perf_counter()`, 50 trials per size |

### Environment

- **Python:** 3.12.13 (CPython)
- **OS:** Linux x86_64
- **Packages:** numpy 2.1.3, httpx 0.28.1, fastapi 0.128.0, psutil 7.2.2
- **Not installed:** `zhipuai`, `anthropic`, `google.oauth2` — benchmarks exercise the GLM code path by patching `GLMBrain.available()` to return True and stubbing the SDK client with `MagicMock`.
- **No API keys** — `GLM_API_KEY`, `ANTHROPIC_API_KEY`, etc. are explicitly unset so we measure FRIDAY's own overhead, not the network.
- **Dev mode:** `FRIDAY_DEV_MODE=1` to bypass API token auth for the ASGI benchmark.
- **Module-init noise:** Calendar/Gmail integrations emit `ModuleNotFoundError: No module named 'google'` on import (expected — those integrations are optional). They don't affect timing measurements because integration discovery is one-shot at brain init.

### Time budget

Each benchmark runs in well under the 60-second SLO:

| Benchmark | Wall-clock | Default iterations |
|-----------|-----------:|-------------------:|
| `benchmark_startup.py`         | ~4 s  | 5 cold-start probes |
| `benchmark_chat_latency.py`    | ~2 s  | 10 per (length, concurrency) cell |
| `benchmark_memory.py`          | ~5 s  | 100/1000 memories + 100 chat requests |
| `benchmark_vector_search.py`   | ~33 s | 50 trials per size, 3 sizes × 2 stores |

### Caveats

- The hash-embed fallback (`ZaiEmbedder._hash_embed`) is used because no `GLM_API_KEY` is set. In production with real GLM Embedding-3 API calls, the per-memory cost would be dominated by network latency (~50-200 ms per embed).
- p99 collapses to max for n ≤ 5 (startup benchmark); bump `--iterations=10+` for tighter percentiles.
- All measurements were taken with the host otherwise idle; results will vary on loaded hardware.
- The 50 ms simulated-API latency case (`--simulated-api-ms=50`) is captured separately in §2.2 to model realistic GLM-4-Flash round-trip cost.

---

## 2. Results Table

### 2.1 Startup (`results_startup.json`, n=5)

| Phase | min | p50 | p99 | Status |
|-------|----:|----:|----:|--------|
| Python interpreter (no-op subprocess) | 14.9 | 15.3 | 15.9 ms | ✅ Excellent |
| Import core modules (`config`, `core.brain`, `core.ledger`, `core.glm_brain`, `core.universal_connector`) | 372.5 | 373.8 | 377.2 ms | ⚠️ **Largest single phase — 57% of cold start** |
| Integration discovery (`UniversalConnector.__init__`) | 57.4 | 58.3 | 59.7 ms | ⚠️ 16 integrations scanned |
| `FridayBrain.__init__` (skills + tool defs + subsystems) | 2.3 | 2.4 | 2.5 ms | ✅ Negligible (imports already warm) |
| First chat chunk (mocked GLM, cold path through `_inject_rag_context`) | 159.9 | 170.2 | 182.9 ms | ⚠️ Cold RAG/Subconscious/Learning imports |
| **TOTAL script → first chat chunk** | **636.1** | **647.9** | **664.2 ms** | ⚠️ Above the audit's 300 ms "good" target |
| TOTAL script → brain ready | 432.2 | 435.8 | 438.2 ms | — |

Counts at startup: **16 integrations** discovered, **2 skills** (`daily_journal`, `morning_briefing`).

### 2.2 Chat Latency (`results_chat_latency.json`, n=10, simulated_api_ms=0)

**Message-length sweep (concurrency=1):**

| Message length | TTFT p50 | TTFT p99 | Full p50 | Full p99 |
|----------------|---------:|---------:|---------:|---------:|
| 10 chars       | 0.10 ms  | 0.45 ms  | 0.11 ms  | 0.46 ms  |
| 100 chars      | 0.10 ms  | 0.11 ms  | 0.11 ms  | 0.12 ms  |
| 1 000 chars    | 0.15 ms  | 0.16 ms  | 0.15 ms  | 0.17 ms  |
| 5 000 chars    | 0.38 ms  | 0.39 ms  | 0.38 ms  | 0.40 ms  |

TTFT is **sub-millisecond** with a mocked GLM client — the brain's own overhead is tiny. Real-world TTFT will be dominated by GLM API latency (~200-500 ms for `glm-4-flash`).

**Concurrency sweep (100-char message, max latency across batch):**

| Concurrency | TTFT max p50 | TTFT max p99 | Full max p50 | Full max p99 |
|-------------|-------------:|-------------:|-------------:|-------------:|
| c=1         | 0.10 ms      | 0.20 ms      | 0.11 ms      | 0.21 ms      |
| c=5         | 0.44 ms      | 0.58 ms      | 0.45 ms      | 0.58 ms      |
| c=10        | 0.81 ms      | 0.97 ms      | 0.81 ms      | 0.98 ms      |
| c=50        | 3.42 ms      | 3.80 ms      | 3.44 ms      | 3.82 ms      |

With mocked GLM (0 ms API), c=50 adds only ~3 ms of overhead — well within SLO. This is **not** a realistic picture of production concurrency because the GLM call itself blocks the event loop's thread pool.

**With 50 ms simulated GLM API latency** (`--simulated-api-ms=50`, captured in a separate run):

| Concurrency | TTFT max p50 | Full max p50 | Serialization factor |
|-------------|-------------:|-------------:|---------------------:|
| c=1         | 50.55 ms     | 50.57 ms     | 1.0× (baseline)      |
| c=5         | 50.93 ms     | 50.95 ms     | 1.0× (parallel)      |
| c=10        | 101.26 ms    | 101.28 ms    | 2.0× (partial serial) |
| c=50        | 452.93 ms    | 452.93 ms    | 8.9× (heavy serial)  |

At c=50 with realistic API latency, requests are serialized ~9× — caused by `asyncio.to_thread(_call_stream)` saturating the default `ThreadPoolExecutor` (8 workers on a 4-core machine: `min(32, cpu_count+4)`).

**ASGI POST `/api/chat`** (10 iterations, mocked brain swapped into singleton):

| Endpoint | p50 | p90 | p99 | Notes |
|----------|----:|----:|----:|-------|
| `POST /api/chat` | 1.06 ms | 1.55 ms | 1.55 ms | 172-byte JSON response |

API overhead (FastAPI + auth + rate limiting) is ~1 ms — negligible.

### 2.3 Memory (`results_memory.json`)

| Milestone | RSS (MiB) | Delta (MiB) | Notes |
|-----------|----------:|------------:|-------|
| After imports | 27.4 | — | Python + numpy + fastapi + psutil |
| After `FridayBrain()` init | 77.4 | +49.9 | 16 integrations + ledger + skills + tool defs |
| After 100 memories | 77.4 | +0.0 | Within psutil granularity (populate 137 ms) |
| After 1 000 memories | 77.7 | +0.3 | ~0.16 KiB per memory effective (populate 1 321 ms) |
| After 100 chat requests | 86.6 | +8.9 | 90 KiB/request — RAG growth + SubconsciousMind/LearningSystem instances per request |
| **PEAK** | **86.6** | **+59.2** | — |

Marginal memory cost per stored memory: **~0.16 KiB** (with the hash-embed 256-dim fallback). Real 1024-dim GLM embeddings would push this to ~5 KiB/memory.

*Note:* the previous WAVE1-PERF run included a 10 000-memory milestone and a 1 000-ledger-action milestone. Both were removed from the default config in this retry because:
1. The 10k memory milestone added ~20-30 s of populate time (dominated by hash-embed cost) — available via `--include-10k` if needed.
2. The 1 000-ledger-action milestone took ~30 s on its own because each `approve_action` re-hashes the entire chain at ~25 ms/action — available via `--ledger-actions N` if needed. This is a **real production hazard**: a long-running FRIDAY with 1 000+ approved actions will see noticeable CPU on every new approval. (See Bottleneck #3 below.)

### 2.4 Vector Search (`results_vector_search.json`, 50 trials per size)

| Size | InMemoryStore p50 | p99 | BruteForce-numpy p50 | p99 | Ratio (IMS/BF) |
|------|------------------:|----:|---------------------:|----:|---------------:|
| 100   | 0.094 ms | 0.133 ms | 0.061 ms | 0.076 ms | 1.54× |
| 1 000 | 2.892 ms | 22.454 ms | 0.812 ms | 0.888 ms | 3.56× |
| 10 000 | 23.752 ms | 52.378 ms | 10.454 ms | 38.877 ms | 2.27× |

**Scaling factor (n=100 → n=10 000, time ratio / size ratio):** **2.53** (super-linear; expected ~1.0 for true O(n))

**Linear scan confirmed?** No — `InMemoryVectorStore.search()` rebuilds the entire embedding matrix from a Python list of `np.ndarray` on **every** search call (`np.array(self.embeddings)` at `database/vector_store.py:31`). This adds O(n) copy overhead on top of the O(n) similarity computation. The p99 outliers at n=1000 (22 ms vs p50 of 2.9 ms) and n=10000 (52 ms vs p50 of 24 ms) are GC pauses triggered by the repeated large-array allocations.

**ANN recommendation:** Switch to **hnswlib (M=32, ef=64)** or **faiss IVF-PQ** at n≥10 000. Current p99 (52 ms) exceeds the 50 ms SLO. Expected speedup: 5-50× at n=10k, 50-500× at n=100k.

---

## 3. Bottleneck Analysis — Top 3

### Bottleneck #1: `InMemoryVectorStore.search` rebuilds the matrix on every call (3-4× slower than brute-force numpy, super-linear scaling)

**Where:** `database/vector_store.py:28-45`

```python
def search(self, query_embedding, top_k=5):
    if not self.embeddings:
        return []
    matrix = np.array(self.embeddings)   # ← O(n) copy on EVERY search
    ...
```

The production store is 2-4× slower than the brute-force numpy reference (2.9 ms vs 0.8 ms at n=1 000; 24 ms vs 10 ms at n=10 000) because `np.array(self.embeddings)` rebuilds the matrix from a Python list on every search. The reference implementation (in `benchmarks/benchmark_vector_search.py:BruteForceVectorStore`) keeps a single stacked matrix and gets 4× better performance with the same maths. Worse, the repeated allocation triggers GC pauses that produce 7× p99/p50 outliers at n=1000.

**Fix:** Cache the matrix as `self._matrix` and rebuild it only when `add()` is called (mark dirty). Expected impact: 3-4× speedup for vector search at all sizes, plus the p99 outliers disappear. At n=10 000, p99 drops from 52 ms → ~13 ms — well under the 50 ms SLO without needing ANN.

### Bottleneck #2: Concurrency collapses under realistic API latency (8.9× serialization at c=50)

**Where:** `core/glm_brain.py:171` (`asyncio.to_thread(_call_stream)`)

Each chat request calls `glm_brain.chat_stream`, which wraps the synchronous ZhipuAI SDK call in `asyncio.to_thread()`. The default `ThreadPoolExecutor` has `min(32, cpu_count + 4)` workers — on a 4-core machine, that's 8 workers. 50 concurrent requests therefore serialize into ~6 batches × 50 ms = 300 ms, plus brain overhead = 453 ms total (vs 51 ms at c=5).

The audit flagged "blocking sync calls in async defs" — this is the manifestation. The ZhipuAI SDK is synchronous, so `to_thread` is the only option without rewriting the GLM client on top of `httpx.AsyncClient`.

**Fix options (in order of effort):**
1. **Quick:** Bump the executor size — `loop.set_default_executor(ThreadPoolExecutor(max_workers=64))`. Expected impact: c=50 drops from 453 ms → ~100 ms.
2. **Medium:** Replace `glm_brain._call_stream`'s `to_thread` with a native async `httpx.AsyncClient` call to the OpenAI-compatible ZhipuAI endpoint. Expected impact: c=50 drops to ~55 ms (true parallelism).
3. **Strategic:** Add a request queue with bounded concurrency (e.g. `asyncio.Semaphore(20)`) so the API isn't flooded. Expected impact: protects the GLM API rate limit, modest latency regression at low concurrency.

### Bottleneck #3: Cold start spends 57% of its time in module imports (374 ms p50)

**Where:** `core/brain.py` top-of-file imports + transitive imports of `integrations.registry` → 16 integration modules → optional deps (anthropic, gemini, google).

The cold-start breakdown:

```
Python interp            15 ms   ( 2%)
import_core_modules     374 ms   (57%)  ← dominant
integration_discovery    58 ms   ( 9%)  ← scans 16 integration dirs
brain_init_total          2 ms   ( 0%)
first_chat_chunk        170 ms   (26%)  ← lazy imports of SubconsciousMind + FridayLearningSystem
TOTAL                  648 ms
```

The `first_chat_chunk` phase is slow because `_inject_rag_context` does `from database.subconscious import SubconsciousMind; SubconsciousMind()` and `from core.learning import FridayLearningSystem; FridayLearningSystem()` **on every chat request**. These are cheap once the modules are imported, but the first call pays the import cost (~170 ms combined).

**Fix:**
1. Move `SubconsciousMind()` and `FridayLearningSystem()` instantiation into `FridayBrain.__init__` as brain-level singletons. Eliminates the 170 ms first-chat penalty AND the per-chat object allocations (memory growth at 100 chat requests was +9 MiB).
2. Defer heavy imports in `core.brain` (anthropic, gemini, google) — they're already lazy but the module-level `from config.friday_identity import get_system_prompt` and `from integrations.registry import UniversalRegistry` could be moved into `__init__`. Expected impact: `import_core_modules` drops from 374 ms → ~250 ms.

---

## 4. Recommendations (Priority Order)

| # | Action | Effort | Expected Impact | Files to touch |
|---|--------|--------|-----------------|----------------|
| 1 | Cache the matrix in `InMemoryVectorStore` (rebuild on `add()` only) | 15 min | 3-4× faster vector search at all scales; p99 at 10k drops from 52 ms → ~13 ms | `database/vector_store.py` |
| 2 | Increase default executor size OR rewrite GLM client with `httpx.AsyncClient` | 1 h / 1 day | c=50 latency drops from 453 ms → ~100 ms (executor) or ~55 ms (native async) | `core/glm_brain.py`, optionally `api/main.py` for executor config |
| 3 | Move `SubconsciousMind()` and `FridayLearningSystem()` instantiation out of `_inject_rag_context` (make them brain-level singletons) | 30 min | Saves ~170 ms on first chat + eliminates per-request object allocations (memory growth at 100 chat requests drops from +9 MiB → +1-2 MiB) | `core/brain.py:549` |
| 4 | Cache `ZaiEmbedder.embed()` results by text hash | 30 min | In production with real `GLM_API_KEY`, eliminates 4 network calls per chat request → ~200-800 ms latency win | `core/embeddings.py` |
| 5 | Defer heavy imports in `core.brain` (move `friday_identity` and `UniversalRegistry` imports into `__init__`) | 1 h | Cold-start `import_core_modules` phase drops from 374 ms → ~250 ms | `core/brain.py` |
| 6 | Investigate the `ActionLedger.approve_action` per-action cost (~25 ms/action) — likely full-chain re-hash on every approval | 2 h | Long-running FRIDAY processes won't see CPU spikes after 1 000+ approved actions | `core/ledger.py` |
| 7 | Switch to ANN (hnswlib or faiss) once memory count exceeds 50 k | 4 h | 5-50× faster search at scale; needed before n=50k | `database/vector_store.py` (add `HnswlibVectorStore` and switch on size threshold) |
| 8 | Re-run these benchmarks after each fix to confirm impact | — | Regression detection | — |

**Expected total impact** (items 1-4 combined): Realistic chat latency at c=50 with 50 ms GLM API drops from **453 ms → ~80-100 ms p99** (5× win). Cold start drops from 648 ms → ~500 ms. Memory growth at 100 chat requests drops from +9 MiB → +1-2 MiB.

---

## 5. Reproduction Instructions

### Prerequisites

```bash
cd /home/z/my-project/work/FRIDAY
pip install psutil httpx numpy fastapi  # if missing
# GLM_API_KEY not required — benchmarks mock the GLM client.
# zhipuai / anthropic packages not required either.
```

### Run each benchmark (all complete in <60 s)

```bash
python benchmarks/benchmark_startup.py --iterations=5
python benchmarks/benchmark_chat_latency.py --iterations=10
python benchmarks/benchmark_chat_latency.py --iterations=10 --simulated-api-ms=50   # API-latency scenario
python benchmarks/benchmark_memory.py                              # default: 100/1000 memories + 100 chats
python benchmarks/benchmark_memory.py --include-10k                # also populate 10 000 memories (~30 s extra)
python benchmarks/benchmark_memory.py --ledger-actions 100         # add 100 ledger actions (~2.5 s)
python benchmarks/benchmark_vector_search.py --trials=50
python benchmarks/benchmark_vector_search.py --skip-10k            # faster: only 100/1000
```

### Output files

| File | Source | Description |
|------|--------|-------------|
| `benchmarks/results_startup.json`        | `benchmark_startup.py`        | Cold-start phase timings (min/p50/p99/max) |
| `benchmarks/results_chat_latency.json`   | `benchmark_chat_latency.py`   | TTFT/full-response stats per length & concurrency + ASGI POST /api/chat |
| `benchmarks/results_memory.json`         | `benchmark_memory.py`         | RSS snapshots per milestone + per-memory marginal cost |
| `benchmarks/results_vector_search.json`  | `benchmark_vector_search.py`  | Search latency per size + scaling analysis + ANN recommendation |

*Note:* `benchmarks/profile_brain.py` (cProfile of `chat_stream`) is also present from a prior WAVE1-PERF run — it is **out of scope** for this retry (the 4 benchmark_*.py + RESULTS.md are the owned deliverables) but remains available for hot-spot analysis. Its outputs (`profile_brain.prof`, `profile_brain.txt`, `results_profile_brain.json`) reflect a prior code state and may be regenerated by running `python benchmarks/profile_brain.py --requests=100`.
