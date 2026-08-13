# FRIDAY — Release Notes

**Version:** Age IV (v4.0.0)  
**Date:** 2026-08-13  
**Status:** Production Certified

## Major Changes

### Runtime Platform
- Built 33-module runtime layer across 5 packages (kernel, drivers, fs, security, core)
- RuntimeContext provides dependency injection for 14 services
- RuntimeExecutor provides unified execution path (execute, workflow, graph, schedule)
- RuntimeKernel provides fair priority scheduling, process management, IPC, event loop
- RuntimeIntegration wires all subsystems in a single start/stop lifecycle

### Security
- SubprocessSandbox with real process isolation (RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE)
- PromptShield with 7 injection patterns + 3 secret leak patterns
- PolicyEngine with deny-by-default capability checking
- HMAC-SHA256 audit chain with tamper detection and forensic archival
- MCP server auth handshake required
- Webhook HMAC-SHA256 verification, fail-closed

### Brain Integration
- BrainRuntimeAdapter wraps FridayBrain with full security pipeline
- Feature flag `FRIDAY_USE_RUNTIME=1` enables runtime-mediated execution
- Backward compatible — legacy path preserved when flag is OFF

### Engineering
- 0 layer violations (was 10)
- 0 production functions with complexity >15 (was 8)
- 0 bare `except: pass` in production code
- 19 singletons made injectable
- 14/14 runtime services healthy
- Architecture health: 72/100 (was 14/100)

### Testing
- 2,246 tests (was ~270 at start of Age IV)
- 20 chaos engineering tests
- 28 sandbox production tests
- 128 singleton injection tests
- 24 layer integrity tests
- 101 complexity reduction tests
- 226 code quality tests

## Performance

| Metric | Value |
|--------|-------|
| Cold start | 442ms |
| RuntimeContext init | 64ms |
| EventBus throughput | 352,720 events/sec |
| KernelScheduler | 96,856 tasks/sec |
| RuntimeExecutor | 88,437 exec/sec |
| PromptShield | 85,731 sanitizations/sec |

## Known Limitations

1. Feature flag default OFF — runtime security is opt-in
2. 10 god classes accepted with documented justification
3. Single-process only — no distributed execution
4. Single-user only — no multi-tenancy
5. 13 stub modules (experimental/dead code, documented)
