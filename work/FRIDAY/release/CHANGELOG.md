# FRIDAY — Changelog

## [v4.0.0] — Age IV Certified — 2026-08-13

### Added
- RuntimeContext (DI container) for 14 runtime services
- RuntimeExecutor (unified execution path)
- RuntimeKernel (scheduler, process manager, IPC, event loop)
- DriverManager (tools, models, plugins with auto-discovery)
- KnowledgeFilesystem (virtual filesystem for engineering knowledge)
- WorkspaceManager (virtual workspaces for task execution)
- BrainRuntimeAdapter (wraps FridayBrain with PromptShield + PolicyEngine)
- RuntimeIntegration (wires all 7 components in single lifecycle)
- SubprocessSandbox (real process isolation with RLIMIT_AS/CPU/FSIZE)
- PromptShield (7 injection + 3 secret detection patterns)
- PolicyEngine (deny-by-default with capability checking)
- CapabilitySandbox (capability-scoped execution)
- RuntimeObservability (26 metrics across 14 subsystems)
- EventBus (pub/sub with wildcard subscriptions)
- CapabilityRegistry (service discovery with aliases)
- ResourceManager (memory, CPU, concurrent request tracking)
- RuntimeScheduler (one-shot + interval job scheduling)
- ExecutionGraph (DAG execution with cycle detection)
- ContextRuntime, StateRuntime, SessionRuntime, WorkflowRuntime
- LifecycleManager (8-state component lifecycle FSM)
- MemoryRuntime (named memory pools with capacity limits)
- PluginRuntime (capability-based plugin execution)
- AgentRuntime (agent lifecycle with circuit breaker)
- EngineeringAnalytics, QualityIntelligence
- RecommendationEngine, HealthMonitor, RegressionDetector
- DocValidator, BenchmarkRunner, AutoFixPipeline
- EngineeringCouncil (12 executive AI reviewers)
- DigitalTwin (engineering knowledge graph)
- ReleaseIntelligence (10-dimension pre-release assessment)
- KeyRotationManager (HMAC key lifecycle management)
- Backup/Restore script for disaster recovery
- Multi-stage Dockerfile (builder + runtime)
- .dockerignore

### Changed
- `core/brain.py`: Added feature flag `FRIDAY_USE_RUNTIME`, `_runtime_chat_stream()`, `_legacy_chat_stream()`
- `core/ledger.py`: HMAC-SHA256 with server secret, `approved_by` in hash, atomic writes, archival on tamper
- `core/glm_brain.py`: Fixed web_search response parsing (was returning hallucinations)
- `core/universal_connector.py`: Wired EthicalSentinel into dispatch path
- `core/brain.py`: GLM path now updates conversation_history
- `core/cost_tracker.py`: Batched writes (dirty flag + flush)
- `database/vector_store.py`: Matrix caching (O(n) → O(1) on search)
- All Spotify/Calendar/Gmail calls: Wrapped in `asyncio.to_thread`
- 19 singleton accessors: Made injectable (`instance=None` parameter)
- 10 layer violations: Fixed via lazy `importlib.import_module()`
- 5 high-complexity functions: Refactored with helper extraction
- `mcp_server.py`: Transport layer extracted to `core/mcp_transport.py`
- `api/routes/stats.py`: `get_stats()` refactored (complexity 16 → <15)
- `core/synthesis.py`: `_parse_synthesis()` refactored (complexity 16 → <15)
- `core/ledger.py`: `wait_for_voice_approval()` refactored (complexity 23 → <15)
- All `except Exception: pass`: Replaced with `except Exception as exc: logger.debug(...)`
- 5 dead-code modules: Documented with DEAD CODE banners
- 6 unused imports: Removed

### Fixed
- Inverted hash verification in `_load_chain` (was archiving valid chains)
- `approved_by` forgeable in audit chain (now in HMAC-SHA256 hash)
- Plugin AST scan bypassable via lazy imports (now walks `ast.walk(tree)`)
- MCP `request_approval` trusting caller-supplied `risk_level` (now ignored)
- MCP `tools/list` hiding 2 of 8 tools (now all 8 advertised)
- CostTracker signature mismatch (was never recording usage)
- GLM path not updating conversation_history (branching/summarization dead)
- GLM web_search returning hallucinations as search results
- `skills/` directory missing (caused 35 test failures)
- `datetime.utcnow()` deprecation (replaced with timezone-aware)
- Embedding dimension mismatch (1024 vs 256 fallback)
- Corrupted audit chain silently wiped (now archived for forensics)

### Security
- Security score: 100/100 (0 findings)
- SubprocessSandbox: Real process isolation with resource limits
- PromptShield: Input sanitization + output validation
- PolicyEngine: Deny-by-default capability checking
- MCP auth handshake required
- Webhook HMAC-SHA256 verification, fail-closed
- Plugin AST scan walks entire tree
- Audit chain HMAC-SHA256 with tamper detection

## [v3.2] — 2026-07-11
- Initial public release
- 13 packages, 186 Python files, 75 API routes
