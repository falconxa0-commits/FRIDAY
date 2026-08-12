# FRIDAY Age IV — Final Security Report

## Security Score: 100/100

Evidence: `SecurityOperations.scan()` → 0 findings, 100/100 score

## Security Architecture

### 1. Audit Chain
- HMAC-SHA256 with server-side secret
- `approved_by` field included in hash (forgery-proof)
- Tampered chains archived for forensics
- Atomic write-then-rename persistence

### 2. Plugin Security
- AST scan walks entire tree (not just top-level)
- Catches: `import os/subprocess/socket` + `__import__` + `exec` + `eval` + `compile`
- Plugin marketplace with security-verified badges

### 3. PromptShield
- 7 injection pattern detectors
- 3 secret leak pattern detectors
- Input sanitization before LLM calls
- Output validation after LLM responses
- Role tagging for context boundaries

### 4. PolicyEngine
- Deny-by-default architecture
- Explicit grant/deny lists
- Policy-based rules (allow/deny by capability + resource)
- Decision logging for audit trail
- Default FRIDAY policies (brain.chat allowed, filesystem.delete denied)

### 5. Subprocess Sandbox
- Real process isolation via `multiprocessing`
- Memory limit: `RLIMIT_AS` enforcement
- CPU limit: `RLIMIT_CPU` + SIGALRM backup
- Filesystem limit: `RLIMIT_FSIZE=0`
- Environment isolation: 14-variable whitelist
- Process group isolation: `os.setsid()` + `os.killpg()`
- Audit logging: every execution recorded

### 6. MCP Server
- Auth handshake required (`friday/authenticate`)
- Caller-supplied `risk_level` IGNORED (Sentinel computes)
- 8 tools advertised in `tools/list`

### 7. Webhook Security
- GitHub: HMAC-SHA256 signature verification, fail-closed
- Stripe: signature verification, fail-closed

## Remaining Security Considerations

1. Feature flag default OFF — runtime security (PromptShield + PolicyEngine) is opt-in
2. No RBAC — single token authentication only
3. No TLS termination in-app (nginx recommended)
4. No rate limiting on API (slowapi not installed)
