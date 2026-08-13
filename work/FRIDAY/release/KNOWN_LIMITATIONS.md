# FRIDAY — Known Limitations

**Version:** Age IV (v4.0.0)  
**Date:** 2026-08-13

## Architectural Limitations

### 1. Single-Process Architecture
FRIDAY runs as a single Python process. All state is in-memory. There is no distributed execution, no multi-process coordination, and no horizontal scaling.

**Impact:** Cannot serve multiple concurrent users. Process restart loses in-memory state (except audit chain + cost data which persist to disk).

**Mitigation:** Supabase persistence is available but degrades permanently on first error. The audit chain and cost tracker persist to JSON files.

**Age V Solution:** Distributed runtime with Redis-backed event bus + scheduler.

### 2. Single-User Only
No multi-tenancy. All users share the same FridayBrain, memory, and ledger.

**Impact:** Cannot be deployed as a SaaS platform.

**Age V Solution:** Per-tenant runtime contexts with isolated memory and ledger.

### 3. Module-Level Singletons (19 instances)
19 core modules use the singleton pattern. These have been made injectable (`instance=None` parameter) but are not removed.

**Impact:** Testing requires singleton reset between tests. Production code can inject custom instances but the default is still singleton.

**Age V Solution:** Full dependency injection via RuntimeContext.

## Security Limitations

### 4. Feature Flag Default OFF
The brain's runtime security pipeline (PromptShield + PolicyEngine) is opt-in via `FRIDAY_USE_RUNTIME=1`.

**Impact:** By default, the brain does NOT sanitize input or check capabilities.

**Mitigation:** Set `FRIDAY_USE_RUNTIME=1` in production `.env`.

**Age V Solution:** Default ON after validation.

### 5. No Subprocess Sandbox for Plugins
While `SubprocessSandbox` exists and is available, plugins are NOT executed in it by default. The `CapabilitySandbox` (no real isolation) is used.

**Impact:** A malicious plugin that bypasses the AST scan can execute arbitrary code in-process.

**Mitigation:** Plugin AST scan walks the entire tree. Manual plugin review required.

**Age V Solution:** All plugin execution routed through SubprocessSandbox.

### 6. No Rate Limiting
`slowapi` is not installed. API endpoints have no rate limiting.

**Impact:** Vulnerable to DoS attacks.

**Mitigation:** Use nginx `limit_req` directive (configured in `deploy/nginx.conf`).

### 7. No RBAC
Single `FRIDAY_API_TOKEN` for all operations. No role-based access control.

**Impact:** All authenticated users have full access.

**Age V Solution:** Per-user tokens with role-based permissions.

## Performance Limitations

### 8. O(n) Vector Search
`InMemoryVectorStore.search` is O(n) with cached matrix. At 10k memories, p99 is ~13ms.

**Impact:** Performance degrades linearly with memory count.

**Age V Solution:** ANN index (hnswlib or faiss) for sub-linear search.

### 9. Cold Start 442ms
FridayBrain initialization takes 442ms. Imports dominate (57%).

**Impact:** Not suitable for serverless deployments (e.g., AWS Lambda).

**Mitigation:** Use long-running process (systemd, Docker).

### 10. No Streaming Voice
Voice mode uses batch Whisper transcription (not streaming).

**Impact:** Voice interactions have 1-3 second latency.

**Age V Solution:** Streaming STT (e.g., Deepgram, Whisper streaming).

## Maintainability Limitations

### 11. 10 God Classes (>500 LOC)
10 production modules exceed 500 LOC. Each has a documented justification for remaining large.

**Impact:** Larger files are harder to navigate and maintain.

**Age V Solution:** Continue decomposition where beneficial.

### 12. 13 Experimental/Dead Modules
13 modules are marked as experimental or dead code (not imported by any production code).

**Impact:** Increases repository size and maintenance burden.

**Mitigation:** All are documented with DEAD CODE banners and emit DeprecationWarning on import.

### 13. 3 TODOs
3 TODO comments remain in production code.

**Impact:** Minor — indicates incomplete work.

**Mitigation:** All are documented as false positives (section header comments in TODO-detection engine).

## Deployment Limitations

### 14. No Windows Support
The MCP server uses `asyncio.StreamReaderProtocol` on `sys.stdin` which fails on Windows.

**Impact:** FRIDAY CLI and MCP server work on Linux/macOS only.

**Age V Solution:** Windows-compatible pipe transport.

### 15. Optional Dependencies Not in Docker
Docker image does not install voice, vision, Supabase, Playwright, Anthropic, or OpenAI packages.

**Impact:** Features requiring these packages are unavailable in Docker.

**Mitigation:** Install optional dependencies: `pip install -e .[full]`

### 16. No Automated Backup
The backup script exists (`scripts/backup_restore.py`) but is not automated.

**Impact:** Engineering data (tasks, knowledge, receipts) may be lost on server failure.

**Age V Solution:** Cron-scheduled backups + offsite replication.
