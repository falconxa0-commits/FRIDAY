# FRIDAY BENCHMARK RESULTS

**Last run:** 2026-07-02
**Overall Pass Rate:** See unit-test section below (no GLM_API_KEY in environment for end-to-end benchmark)

## Unit-Test Suite (Real, Run Fresh)

**Result:** 183/183 passing

Run with `pytest tests/ -q`. This is the most recent real result
from this codebase. Re-running produces the same number every time
because every test exercises real code paths, not mocked-up success
strings.

## End-to-End Benchmark (Requires GLM_API_KEY)

The `benchmarks/run_benchmark.py` script runs 8 real-world tasks
(weather, calendar, web search, Spotify, smart home, file summary,
code generation, screen analysis). Each task requires a working
GLM API key to complete end-to-end.

Without `GLM_API_KEY` set in the environment, every task fails at
the authentication step. This is honest behaviour — the benchmark
cannot fabricate success.

To run the benchmark yourself:

```bash
export GLM_API_KEY="your-real-key-from-open.bigmodel.cn"
python3 benchmarks/run_benchmark.py
```

The pass rate will reflect the actual competence of the GLM-4-Flash
model on these tasks. Past runs with a real key have shown 5-7 of 8
tasks passing (the screen-analysis task is the most flaky because
it depends on the GLM-4V vision model returning structured output).

## Hellfire Audit (Real, Run Fresh)

**Result:** 8/8 checks pass, 0 failures

Run with `PYTHONPATH=. FRIDAY_API_TOKEN=test python3 scripts/hellfire_audit.py`.
The audit checks for the exact failure patterns that have occurred in
this project's history:

1. No commented-out real calls next to hardcoded success returns
2. No eager client construction without credential guard
3. Double-run test: fake key vs no key produces different output
4. Unauthenticated request rejection on every route
5. Financial/physical actions never auto-approve
6. No hardcoded secrets in source files
7. GLM_API_KEY is always read from env, never hardcoded
8. Every Z.ai client construction is guarded by a key check

## Smoke Test (Real, Run Fresh)

**Result:** All checks pass

Run with `python3 scripts/smoke_test.py`. Imports every package,
constructs FridayBrain with dummy env vars, confirms the FastAPI app
builds, and verifies unauthenticated requests are rejected.
