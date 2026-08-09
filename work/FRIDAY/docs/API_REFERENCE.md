# FRIDAY API Reference

Complete reference for every HTTP endpoint exposed by the FRIDAY FastAPI server.
The server is defined in [`api/main.py`](../api/main.py) and routes are organised
into 25 files under [`api/routes/`](../api/routes/).

- **Base URL:** `http://localhost:8000`
- **Default port:** `8000` (configurable via `uvicorn --port`)
- **Spec format:** OpenAPI 3.x — interactive docs at `http://localhost:8000/docs`
- **WebSocket:** `ws://localhost:8000/api/stream` (token-authenticated handshake)

---

## Authentication

FRIDAY uses a single bearer-token model with timing-safe comparison
(`hmac.compare_digest`). The auth flow is implemented in
[`api/main.py::verify_token`](../api/main.py).

| Header / Query            | Required | Notes |
|--------------------------|----------|-------|
| `Authorization: Bearer <FRIDAY_API_TOKEN>` | Yes (most routes) | Compared with `FRIDAY_API_TOKEN` env var |
| `?token=<FRIDAY_API_TOKEN>` | Fallback for SSE / EventSource | Same secret, query-param delivery |
| `Authorization: Bearer <FRIDAY_USER_TOKEN>` | Team-mode routes only | Per-user token issued via `core/team_mode.py` |

### Auth modes

| Mode | Trigger | Behaviour |
|------|---------|-----------|
| **Production** (default) | `FRIDAY_API_TOKEN` is set in `.env` | All `/api/*` routes require the bearer token; missing/wrong → `403 Forbidden`. |
| **Dev mode** | `FRIDAY_DEV_MODE=1` AND `FRIDAY_API_TOKEN` is empty | No auth required. **Never use in production.** |
| **Auto-generated** | First start with no token and no dev-mode flag | A 32-byte `secrets.token_urlsafe` token is generated, written to `.env`, and used thereafter. |

### Routes that bypass the global token

These routes implement their own authentication and are intentionally public
(or signature-verified):

| Route | Auth | Reason |
|------|------|--------|
| `GET /` | none | Static web UI (index.html) |
| `GET /health` | none | Container / load-balancer probe |
| `GET /api/ping` | none (rate-limited 30/min) | Liveness probe |
| `GET /api/health/*` | none | Deep health check (see Security Model for caveats) |
| `POST /api/webhooks/{source}` | signature | GitHub HMAC, Stripe signature, or unverified `custom` |
| `/api/team/*` | per-user `FRIDAY_USER_TOKEN` | Team-mode multi-user endpoints |

---

## Endpoint Inventory

The full list of endpoints (75+ across 25 route files) is shown below. Routes
are grouped by the file that defines them.

### Chat (`api/routes/chat.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/chat` | Bearer | Non-streaming chat — returns full response JSON |
| GET | `/api/chat/stream` | Bearer (query-param) | SSE streaming chat |
| GET | `/api/chat/history` | Bearer | Conversation history |
| POST | `/api/chat/clear` | Bearer | Clear conversation context |
| GET | `/api/chat/stats` | Bearer | Brain stats (provider, model, history length) |

### Memory (`api/routes/memory.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/memory/all` | Bearer | List all stored memories |
| POST | `/api/memory/` | Bearer | Add a memory (raw text + metadata) |
| GET | `/api/memory/?query=…` | Bearer | Semantic memory search |
| DELETE | `/api/memory/{memory_id}` | Bearer | Delete a memory |
| GET | `/api/memory/export` | Bearer | Export all memories as JSON |
| POST | `/api/memory/import` | Bearer | Import memories from JSON |
| GET | `/api/memory/wisdom` | Bearer | Compressed wisdom tokens |
| POST | `/api/memory/compress` | Bearer | Trigger memory compression |

### Agents (`api/routes/agents.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/task` | Bearer | Run a single agent |
| POST | `/api/swarm` | Bearer | Run multiple agents in parallel |
| POST | `/api/pipeline` | Bearer | Sequential pipeline (research → plan → execute → write) |
| GET | `/api/status` | Bearer | Agent manager status |
| GET | `/api/types` | Bearer | List available agent types |
| POST | `/api/tactical` | Bearer | Run tactical coordination |
| POST | `/api/generate-project` | Bearer | Generate a multi-file project |

### Integrations (`api/routes/integrations.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/integrations/` | Bearer | List all registered services |
| GET | `/api/integrations/available` | Bearer | List services with `available()==True` |
| GET | `/api/integrations/categories` | Bearer | Services grouped by category |
| POST | `/api/integrations/execute` | Bearer (rate-limited 20/min) | Execute an integration action |

### Actions (`api/routes/actions.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/actions/` | Bearer | List pending actions |
| POST | `/api/actions/{action_id}/approve` | Bearer | Approve a pending action |
| POST | `/api/actions/{action_id}/reject` | Bearer | Reject a pending action |
| GET | `/api/actions/verify` | Bearer | Verify hash-chain integrity |
| GET | `/api/actions/audit` | Bearer | Full hash-chained audit log |

### Scheduler (`api/routes/scheduler.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/scheduler/` | Bearer | List scheduled tasks |
| POST | `/api/scheduler/` | Bearer | Create a scheduled task |
| DELETE | `/api/scheduler/{task_id}` | Bearer | Remove a scheduled task |

### Stats (`api/routes/stats.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/stats` | Bearer | Full usage statistics (provider breakdown, cost, rate-limit warnings) |
| GET | `/api/stats/predictor/cache` | Bearer | Predictor preloaded cache contents |

### Trust (`api/routes/trust.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/trust/report` | Bearer | Re-run hellfire audit |
| GET | `/api/trust/report` | Bearer | Alias — runs audit fresh |
| GET | `/api/trust/status` | Bearer | Last audit result |

### Team (`api/routes/team.py`) — per-user token

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/team/members` | `FRIDAY_USER_TOKEN` | List team members |
| POST | `/api/team/invite` | `FRIDAY_USER_TOKEN` | Create an invitation |
| GET | `/api/team/context` | `FRIDAY_USER_TOKEN` | Shared + private context for caller |
| POST | `/api/team/memory/private` | `FRIDAY_USER_TOKEN` | Store a private memory |
| POST | `/api/team/memory/shared` | `FRIDAY_USER_TOKEN` | Store a shared memory |
| GET | `/api/team/memories` | `FRIDAY_USER_TOKEN` | All memories visible to caller |

### Health (`api/routes/health.py`) — public

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/health/deep` | none | Per-subsystem status + latency (10 checks) |

### Patterns (`api/routes/patterns.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/patterns` | Bearer | List discovered behavioral patterns |
| POST | `/api/patterns/observe` | Bearer | Record an interaction |
| POST | `/api/patterns/suggest` | Bearer | Get proactive suggestions |

### Visual Memory (`api/routes/visual_memory.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/visual-memory/store` | Bearer | Store image + description |
| GET | `/api/visual-memory/search?query=…` | Bearer | Semantic search across visual memories |
| GET | `/api/visual-memory/list` | Bearer | List all visual memories |
| DELETE | `/api/visual-memory/{memory_id}` | Bearer | Delete a visual memory |

### Nigeria (`api/routes/nigeria.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/nigeria/naira?usd=…` | Bearer | Convert USD → NGN at live rate |
| GET | `/api/nigeria/banks` | Bearer | List Nigerian banks |
| GET | `/api/nigeria/delivery` | Bearer | List Nigerian delivery services |
| GET | `/api/nigeria/news?max_per_source=…` | Bearer | Nigerian news via RSS |
| GET | `/api/nigeria/power` | Bearer | Power/network status heuristic |

### Identity (`api/routes/identity.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/identity` | Bearer | Current mode + all available modes |
| POST | `/api/identity/{mode}` | Bearer | Switch identity mode |

### Subconscious (`api/routes/subconscious.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/subconscious/patterns?context=…` | Bearer | Surface patterns from subconscious |
| GET | `/api/subconscious/intuition` | Bearer | Current intuition string |

### Persona (`api/routes/persona.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/persona/export` | Bearer | Export full persona as JSON |
| POST | `/api/persona/import` | Bearer | Import a previously exported persona |

### Goals (`api/routes/goals.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/goals` | Bearer | Create a new goal |
| GET | `/api/goals` | Bearer | List all active goals |
| PATCH | `/api/goals/{goal_id}/progress` | Bearer | Update progress |
| GET | `/api/goals/{goal_id}/nudge` | Bearer | Get a Friday nudge for next action |
| DELETE | `/api/goals/{goal_id}` | Bearer | Remove a goal |

### Notify (`api/routes/notify.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/notify` | Bearer | Send notification via Telegram + desktop |

### Webhooks (`api/routes/webhooks.py`) — signature-verified

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/webhooks/github` | GitHub HMAC-SHA256 | GitHub event (PR, issue, push) |
| POST | `/api/webhooks/stripe` | Stripe signature (header required) | Stripe event |
| POST | `/api/webhooks/custom` | none | Generic webhook receiver |

### Learning (`api/routes/learning.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/learning/corrections` | Bearer | List recorded corrections |
| POST | `/api/learning/corrections` | Bearer | Manually record a correction |
| DELETE | `/api/learning/corrections/{correction_id}` | Bearer | Remove a correction |

### Self-Improvement (`api/routes/self_improvement.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/self-improvement/proposals` | Bearer | List improvement proposals |
| POST | `/api/self-improvement/analyze?days=…` | Bearer | Trigger performance analysis |
| POST | `/api/self-improvement/approve/{proposal_id}` | Bearer | Queue a proposal through the ledger |

### Privacy (`api/routes/privacy.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/privacy/report` | Bearer | Full privacy report |
| GET | `/api/privacy/data/{provider}` | Bearer | Data sent to a specific provider |
| DELETE | `/api/privacy/purge/{provider}` | Bearer | Purge provider history (ledger-gated) |

### Proactive (`api/routes/proactive.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/proactive/briefing` | Bearer | Trigger proactive daily briefing |
| GET | `/api/proactive/status` | Bearer | Current mode + proactive engine status |

### Branching (`api/routes/branching.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/chat/branch` | Bearer | Create a fork at a message index |
| GET | `/api/chat/branches` | Bearer | List all branches |
| POST | `/api/chat/branch/{branch_id}/switch` | Bearer | Switch active branch |
| POST | `/api/chat/branch/{branch_id}/merge-insight` | Bearer | Bring branch learning back |
| DELETE | `/api/chat/branch/{branch_id}` | Bearer | Close a branch |

### Root-level routes (`api/main.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | none | Serve `index.html` (web UI) |
| GET | `/health` | none | Lightweight health probe |
| GET | `/api/ping` | none (rate-limited 30/min) | Liveness ping |
| GET | `/metrics` | localhost only (or `METRICS_ALLOW_EXTERNAL=1`) | Prometheus text exposition |
| WS | `/api/stream` | token in first JSON message | WebSocket chat with heartbeat |

### MCP server (`mcp_server.py`) — JSON-RPC over stdio

The MCP server is **not HTTP** — it speaks JSON-RPC 2.0 over stdio and is
launched via `python mcp_server.py`. External clients (Claude Code, Cursor,
etc.) launch it as a subprocess.

| Method | Description |
|--------|-------------|
| `initialize` | JSON-RPC handshake (returns server info + capabilities) |
| `tools/list` | Returns 8 tool definitions |
| `tools/call` | Dispatches a tool by name with arguments |
| `friday.<tool>` | Namespaced direct call (e.g. `friday.chat`) |
| `notifications/initialized` | Client init notification (no response) |

The 8 advertised tools: `chat`, `vision`, `web_search`, `image_generation`,
`video_generation`, `code_execution`, `execute_action`, `request_approval`.

---

## Common Error Codes

| Code | Meaning | When |
|------|---------|------|
| `200 OK` | Request succeeded | Most successful calls |
| `403 Forbidden` | Invalid or missing bearer token | All authenticated routes when token absent/wrong |
| `404 Not Found` | Resource (memory / goal / branch / action) not found | By-id lookups |
| `422 Unprocessable Entity` | Body failed Pydantic validation | All POST/PATCH with body |
| `429 Too Many Requests` | Rate limit exceeded | `/api/chat` (60/min), `/api/chat/stream` (30/min), `/api/integrations/execute` (20/min), `/api/ping` (30/min) |
| `500 Internal Server Error` | Unhandled exception | Brain unavailable, integration crashed |
| `503 Service Unavailable` | Required subsystem not initialised | Memory / connector / registry unavailable |

WebSocket close codes: `1008 Policy Violation` (failed auth handshake),
`1011 Internal Error` (server crash).

---

## Rate Limits

Rate limiting is provided by [`slowapi`](https://slowapi.readthedocs.io/) when
installed (falls back to no-op if `slowapi` is missing). Limits are applied
per-remote-IP.

| Endpoint | Limit | Notes |
|----------|-------|-------|
| `POST /api/chat` | 60 / minute | Per-IP |
| `GET /api/chat/stream` | 30 / minute | SSE |
| `POST /api/integrations/execute` | 20 / minute | Protects integration backends |
| `GET /api/ping` | 30 / minute | Liveness probe |

A separate in-process rate limiter (`core/rate_limiter.py::check_rate_limit`)
is also called by the chat routes — it raises a `429` with a `Retry-After`
header when exceeded. Nginx-level rate limiting can be added via
`limit_req_zone` in the `http {}` block (see
[Deployment Guide §4](./DEPLOYMENT_GUIDE.md#4-production-deployment) for the
correct placement — the shipped `deploy/nginx.conf` has it in the wrong
context).

---

## Detailed Endpoint Reference

The rest of this document gives request/response examples for the most
commonly used endpoints. For endpoints not shown here, use the interactive
OpenAPI docs at `/docs`.

### POST `/api/chat`

Non-streaming chat. Returns the full response as JSON.

**Request body**
```json
{
  "message": "What's the weather in Lagos?",
  "user_name": "User"
}
```

**Response (200)**
```json
{
  "response": "The weather in Lagos is currently 29°C with light rain..."
}
```

**Errors**

| Status | Cause |
|--------|-------|
| 422 | Body missing `message` |
| 429 | Rate limit exceeded |
| 500 | Brain raised an exception |
| 503 | Brain singleton unavailable |

---

### GET `/api/chat/stream`

SSE streaming chat. Use `?token=` for EventSource auth (EventSource cannot
send custom headers).

**Query params**
| Name | Required | Default | Description |
|------|----------|---------|-------------|
| `message` | Yes | — | User message |
| `user_name` | No | `User` | Display name |
| `token` | Yes (for SSE) | — | Bearer token |

**Response** — `text/event-stream` with three event types:

```
data: {"type": "text", "content": "The weather in "}

data: {"type": "text", "content": "Lagos is "}

data: {"type": "tool_call", "raw": "\n[System: Weather.get_weather(city=Lagos)]\n"}

data: {"type": "text", "content": "29°C with light rain."}

data: [DONE]
```

On error: `data: {"type": "error", "message": "..."}`

---

### POST `/api/memory/`

Add a memory. Stores the text as a conversation turn and pattern-matches
facts (name, location, job, likes, dislikes, etc.).

**Request body**
```json
{
  "text": "My name is Ada and I live in Abuja.",
  "metadata": {"source": "manual"}
}
```

**Response (200)**
```json
{
  "status": "success",
  "message": "Memory stored."
}
```

---

### GET `/api/memory/?query=…`

Semantic search across stored memories.

**Response (200)**
```json
{
  "results": [
    {
      "content": "User's name is Ada",
      "score": 0.91,
      "timestamp": "2026-07-17T12:34:56.789",
      "metadata": {}
    }
  ]
}
```

---

### GET `/api/memory/export`

Export all in-memory data plus session facts. Round-trips through
`/api/memory/import` losslessly.

**Response (200)**
```json
{
  "exported_at": "2026-07-17T12:34:56.789",
  "memory_count": 42,
  "memories": [
    {"role": "user", "content": "...", "timestamp": "...", "metadata": {}}
  ],
  "session_facts": {"name": "Ada", "location": "Abuja"}
}
```

---

### POST `/api/integrations/execute`

Execute an integration action. Goes through the
`UniversalConnector` → ledger approval gate → integration backend.

**Request body**
```json
{
  "service": "Weather",
  "action": "get_weather",
  "params": {"city": "Lagos"}
}
```

**Response (200)** — note the `receipt` always contains real backend data
(never echoes of input params):
```json
{
  "status": "success",
  "message": "Weather retrieved",
  "result": {
    "status": "success",
    "message": "Weather retrieved",
    "receipt": {
      "type": "api_response",
      "data": {"temp": 29, "condition": "Light rain"},
      "timestamp": "2026-07-17T12:34:56.789"
    }
  },
  "receipt": {
    "service": "Weather",
    "action": "get_weather",
    "integration_status": "success",
    "receipt": {
      "type": "api_response",
      "data": {"temp": 29, "condition": "Light rain"},
      "timestamp": "2026-07-17T12:34:56.789"
    },
    "timestamp": "2026-07-17T12:34:56.789"
  }
}
```

**Errors**

| Status | Cause |
|--------|-------|
| 503 | Connector or registry unavailable |
| 500 | Integration raised an exception |

---

### POST `/api/actions/{action_id}/approve`

Approve a pending action. Unblocks `ledger.wait_for_approval()` callers.

**Response (200)**
```json
{"status": "success", "message": "Action approved."}
```

**Response (404)** — action_id not in pending_actions
```json
{"detail": "Action not found."}
```

---

### GET `/api/actions/verify`

Verify the integrity of the HMAC-SHA256 audit chain.

**Response (200)**
```json
{
  "valid": true,
  "entry_count": 142,
  "message": "Chain intact"
}
```

If the chain has been tampered with: `valid: false`, message
`"Chain BROKEN — tampering detected"`. The tampered file is archived to
`action_ledger_chain.json.tampered.<timestamp>.json` and a fresh chain is
started.

---

### POST `/api/webhooks/github`

Receive a GitHub webhook. Verifies the `X-Hub-Signature-256` header against
`GITHUB_WEBHOOK_SECRET` using HMAC-SHA256.

**Headers**
| Header | Required | Description |
|--------|----------|-------------|
| `X-Hub-Signature-256` | Yes (if secret set) | `sha256=<hex>` |
| `X-GitHub-Event` | Yes | Event type (`pull_request`, `issues`, `push`, …) |

**Response (200)** — for `pull_request` events:
```json
{
  "status": "received",
  "event": "pull_request",
  "action": "opened",
  "pr_title": "Add new feature",
  "pr_url": "https://github.com/owner/repo/pull/42",
  "message": "GitHub PR opened: Add new feature"
}
```

**Errors**

| Status | Cause |
|--------|-------|
| 403 | Signature present but doesn't match (fail-closed) |

> **Note:** If `GITHUB_WEBHOOK_SECRET` is **not set**, the signature check is
> **skipped** and the webhook is accepted without verification. This is a
> known limitation — set the secret in production.

---

### POST `/api/webhooks/stripe`

Stripe webhook receiver. Currently returns the parsed event type but does
**not** verify the Stripe signature (planned for the Security Agent's
hardening pass).

**Response (200)**
```json
{
  "status": "received",
  "source": "stripe",
  "event_type": "checkout.session.completed",
  "message": "Stripe event: checkout.session.completed"
}
```

**Errors**

| Status | Cause |
|--------|-------|
| 403 | Missing `Stripe-Signature` header |

---

### WS `/api/stream`

WebSocket chat with heartbeat.

**Handshake**
1. Client connects to `ws://localhost:8000/api/stream`
2. Client sends first message: `{"token": "<FRIDAY_API_TOKEN>"}`
3. Server validates token (timing-safe); on failure: `{"error": "Unauthorized"}`
   + close `1008 Policy Violation`

**Message loop**
- Client sends a text message → server streams `brain.chat_stream(message)` back
  chunk-by-chunk.
- Server sends `{"type": "ping"}` every 30s. Client must respond with `__pong__`
  within 40s or the connection is closed.
- After each response, server pushes
  `{"pending_actions": [...]}` so the UI can surface approval requests.

---

### MCP: `tools/call` with `request_approval`

The MCP killer feature. An external AI agent (Claude Code, Cursor) submits
an action for human approval through Friday's ledger.

**Request (JSON-RPC over stdio)**
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "request_approval",
    "arguments": {
      "component": "Finance",
      "action": "transfer",
      "params": {"amount": 50000, "to": "0123456789"},
      "description": "Transfer ₦50,000 to savings",
      "timeout": 60,
      "use_voice": false
    }
  }
}
```

> **Security:** The `risk_level` field is **ignored** if supplied by the
> caller. Risk is computed by `EthicalSentinel` (or the keyword fallback)
> using component + action + params. This prevents self-approval attacks
> where an agent labels a destructive action `"low"` to bypass the gate.

**Response**
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"status\":\"approved\",\"action_id\":\"abc-123\",\"component\":\"Finance\",\"action\":\"transfer\",\"message\":\"Action abc-123 approved by human\",\"receipt\":{...}}"
      }
    ]
  }
}
```

`status` is one of: `"approved"`, `"rejected"`, `"timeout"`, `"error"`.

---

## Versioning & Stability

- FRIDAY does **not** use URL-based versioning. All routes are under `/api/`.
- Breaking changes will be flagged in [`CHANGELOG_FIXES.md`](../CHANGELOG_FIXES.md)
  and [README](../README.md) at least one minor version ahead of removal.
- The OpenAPI schema at `/openapi.json` is the canonical machine-readable
  source for client generation.

---

## See Also

- [Deployment Guide](./DEPLOYMENT_GUIDE.md) — install, configure, harden
- [Developer Guide](./DEVELOPER_GUIDE.md) — extending FRIDAY with new integrations, skills, agents
- [Security Model](./SECURITY_MODEL.md) — auth, authorization, audit chain, plugin sandbox
- [Threat Model](./THREAT_MODEL.md) — attack surface and mitigations
- [Architecture](./ARCHITECTURE.md) — system diagrams and data flow
