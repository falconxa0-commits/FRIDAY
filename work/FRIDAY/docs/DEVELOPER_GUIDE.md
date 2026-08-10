# FRIDAY Developer Guide

This guide is for engineers extending FRIDAY v3.2 — adding integrations,
skills, agents, plugins, tests, or contributing back to the core.

> **Before reading:** skim [Architecture](./ARCHITECTURE.md) for the system
> diagram, and [API Reference](./API_REFERENCE.md) for the HTTP surface
> you'll be extending.

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Adding a New Integration](#2-adding-a-new-integration)
3. [Adding a New Skill](#3-adding-a-new-skill)
4. [Adding a New Agent](#4-adding-a-new-agent)
5. [Plugin Marketplace](#5-plugin-marketplace)
6. [Testing](#6-testing)
7. [Code Style](#7-code-style)
8. [Contributing](#8-contributing)

---

## 1. Project Structure

FRIDAY is organised into 13 top-level Python packages. Each package has a
single responsibility and a clear public surface.

```
FRIDAY/
├── core/           29 modules — brain, ledger, memory, sentinel, scheduler, …
├── api/
│   ├── main.py     FastAPI app, auth, WebSocket, route registration
│   └── routes/     25 files — one per resource area (chat, memory, …)
├── agents/         7 files — ResearchAgent, CodingAgent, WritingAgent, TaskAgent,
│                   TacticalManager, CodingOrchestrator, AgentManager
├── integrations/   18 plugins — Weather, Finance, Spotify, … (auto-discovered)
├── skills/         4 files — BaseSkill ABC + MorningBriefing + DailyJournal
├── voice/          5 files — listener, speaker, transcriber, wake_word, conversational
├── vision/         4 files — screen_reader, screen_analyzer, ocr, presence
├── control/        5 files — pc_control, browser_control, file_manager, app_launcher, workspace
├── database/       5 files — supabase_client, vector_store, compression, subconscious
├── config/         3 files — settings.py (env loader), identities.py, friday_identity.py
├── cli/            3 files — __main__.py, commands.py, terminal.py (TUI)
├── sdk/            1 file — friday_sdk.py (FridayClient async Python SDK)
├── mcp_server.py   MCP JSON-RPC server (stdio) — 8 tools
├── marketplace/    Plugin marketplace index + plugin source dirs
├── deploy/         nginx.conf, systemd/friday.service
├── docs/           This guide + ARCHITECTURE, API_REFERENCE, …
├── tests/          pytest suite (307 tests as of v3.2)
├── scripts/        38 verify_*.py + hellfire_audit.py + smoke_test.py
├── benchmarks/     run_benchmark.py + tasks.json + results.md
└── apps/           web/, desktop/, vscode/, mobile/, browser_extension/
```

### Package ownership

| Package | Owner | Touch policy |
|---------|-------|--------------|
| `core/` | Core maintainer | Public API only — coordinate changes via PR |
| `api/routes/` | API maintainer | Add new files for new resource areas; don't touch existing routes without coordination |
| `agents/` | Agent maintainer | Add new agent classes via the `AgentType` enum |
| `integrations/` | Plugin ecosystem | **Anyone can add new files here** — auto-discovered |
| `skills/` | Plugin ecosystem | **Anyone can add new files here** — auto-discovered |
| `voice/`, `vision/`, `control/` | Subsystem maintainers | Coordinate before changing public APIs |
| `database/` | Database maintainer | All schema changes need a migration file |
| `config/` | Core maintainer | Add new env vars to `settings.py` AND `.env.example` |
| `cli/` | CLI maintainer | Add new subcommands to `commands.py::COMMANDS` dict |
| `mcp_server.py` | Core maintainer | Adding a new tool requires `TOOLS` list + dispatch handler |

### Public vs private API

- **Public API:** `BaseIntegration`, `BaseSkill`, `AgentManager`, `ActionLedger`,
  `EthicalSentinel`, `UniversalConnector`, `UniversalRegistry`,
  `FridayBrain` (its public methods), `FridayMemory`, `FridayClient`.
- **Private API:** Everything else. We don't break private APIs gratuitously,
  but we don't promise stability either.

---

## 2. Adding a New Integration

Integrations are the primary extension point. Drop a Python file in
`integrations/` and it's auto-loaded on next startup by
`UniversalConnector._discover_plugins`.

### 2.1 The `BaseIntegration` contract

Defined in [`integrations/base.py`](../integrations/base.py). Every
integration must implement:

```python
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import datetime


class BaseIntegration(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable integration name (e.g. 'Weather', 'Gmail')."""
        pass

    @abstractmethod
    def available(self) -> bool:
        """Returns True if configured + reachable."""
        pass

    @abstractmethod
    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        """Execute the specified action with params.

        Returns:
            {
                "status": "success" | "error" | "not_implemented" | "demo_mode",
                "message": "...",
                "receipt": {"type": "...", "data": "...", "timestamp": "..."}
            }
        """
        pass

    # Optional overrides
    async def health_check(self) -> dict: ...
    def list_actions(self) -> List[str]: ...

    # Helper
    def _make_response(self, status, message, receipt_data=None) -> dict: ...
```

### 2.2 Step-by-step example: Joke integration

Create `integrations/joke.py`:

```python
"""Joke integration — fetches a random joke from JokeAPI."""

import logging
from typing import Optional

import httpx

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)

JOKE_API_URL = "https://v2.jokeapi.dev/joke/Any?safe-mode"


class JokeIntegration(BaseIntegration):
    """Fetches jokes from the public JokeAPI (no key required)."""

    @property
    def name(self) -> str:
        return "Joke"

    def available(self) -> bool:
        # No credentials needed — always available if the network is up.
        # We don't ping the API here (that's health_check's job).
        return True

    def list_actions(self) -> list[str]:
        return ["get_random", "get_by_category"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}

        if action == "get_random":
            return await self._fetch_joke()
        elif action == "get_by_category":
            category = params.get("category", "Any")
            return await self._fetch_joke(category)
        else:
            return self._make_response(
                "not_implemented",
                f"Action '{action}' is not supported by Joke.",
            )

    async def _fetch_joke(self, category: str = "Any") -> dict:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                url = f"https://v2.jokeapi.dev/joke/{category}?safe-mode"
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()

            if data.get("type") == "single":
                joke = data.get("joke", "")
            else:
                joke = f"{data.get('setup', '')} ... {data.get('delivery', '')}"

            return self._make_response(
                "success",
                f"Joke retrieved: {joke}",
                receipt_data={
                    "joke": joke,
                    "category": data.get("category", "Any"),
                    "id": data.get("id"),
                },
            )
        except Exception as exc:
            logger.exception("Joke fetch failed")
            return self._make_response("error", f"Joke API error: {exc}")
```

### 2.3 Test it

Restart FRIDAY. The integration is auto-discovered:

```bash
friday status
# Should list "Joke" under discovered integrations

curl -X POST http://localhost:8000/api/integrations/execute \
  -H "Authorization: Bearer $FRIDAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service": "Joke", "action": "get_random", "params": {}}'
```

### 2.4 Auto-discovery mechanism

`UniversalConnector._discover_plugins` (`core/universal_connector.py:37-55`)
walks `integrations/` via `pkgutil.iter_modules`, imports each module, and
registers any `BaseIntegration` subclass under its `.name` property.

The `UniversalRegistry` (`integrations/registry.py`) maintains a parallel
manual mapping in `_INTEGRATION_SPECS` for known integrations — this is for
category metadata (Core, Finance, Information) and is optional. New
integrations are auto-discovered even without a registry entry.

### 2.5 Rules

1. **Never fake success.** If the backend is unreachable, return
   `status: "not_implemented"` or `status: "error"` — never fabricate data.
2. **Honest `available()`.** Return `False` if required env vars are
   missing. Check at call time, not just at init.
3. **Atomic actions.** Each `execute(action, …)` call does one thing.
   Multi-step workflows belong in a Skill.
4. **Respect the ledger.** Don't try to bypass the approval gate. If your
   integration is destructive, it'll be in `NEVER_AUTO_APPROVE_COMPONENTS`
   — that's correct behaviour.
5. **Use `_make_response()`** so receipts are formatted consistently.
6. **Add high-risk integrations to `NEVER_AUTO_APPROVE_COMPONENTS`** in
   `core/ledger.py` if they handle money, physical hardware, or code
   execution.

---

## 3. Adding a New Skill

Skills are multi-step, brain-augmented workflows. They're auto-discovered
by `FridayBrain._discover_skills` at startup.

### 3.1 The `BaseSkill` ABC

Defined in [`skills/base.py`](../skills/base.py):

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseSkill(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique snake_case identifier."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line human-readable description."""

    @property
    def triggers(self) -> list[str]:
        """Optional activation phrases. Default: empty (explicit-only)."""
        return []

    @property
    def enabled(self) -> bool:
        """Whether to load at startup. Default: True."""
        return True

    @abstractmethod
    async def run(self, brain: Any, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute the skill. Returns at least:
            {"status": "success" | "error" | "not_implemented",
             "message": "..."}
        """
```

### 3.2 Step-by-step example: Weather Report skill

Create `skills/weather_report.py`:

```python
"""WeatherReport skill — fetches weather + adds a friendly summary."""

from typing import Any, Dict, Optional

from skills.base import BaseSkill


class WeatherReport(BaseSkill):
    """Generates a friendly weather report for a city."""

    @property
    def name(self) -> str:
        return "weather_report"

    @property
    def description(self) -> str:
        return "Fetches weather for a city and adds a friendly summary."

    @property
    def triggers(self) -> list[str]:
        # These phrases (matched case-insensitive as substrings) activate
        # the skill from free-text chat.
        return ["weather report", "what's the weather", "weather in"]

    async def run(self, brain: Any, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = params or {}
        city = params.get("city", "Lagos")

        # Use the brain's universal_connector to fetch weather
        connector = getattr(brain, "universal_connector", None)
        if not connector or "Weather" not in connector.integrations:
            return {
                "status": "error",
                "message": "Weather integration not available.",
            }

        try:
            result = await connector.execute_action(
                "Weather", "get_weather",
                {"city": city}, approval_timeout=5,
            )
            if result.get("status") != "success":
                return {
                    "status": "error",
                    "message": result.get("message", "Weather fetch failed"),
                }

            data = result.get("receipt", {}).get("data", {})
            summary = await self._brain_summary(brain, city, data)
            return {
                "status": "success",
                "message": summary,
                "data": data,
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    async def _brain_summary(self, brain: Any, city: str, data: dict) -> str:
        if not hasattr(brain, "chat_stream"):
            return f"Weather in {city}: {data}"
        prompt = (
            f"Summarise this weather data in one friendly sentence: "
            f"city={city}, data={data}"
        )
        chunks = []
        async for chunk in brain.chat_stream(prompt):
            chunks.append(chunk)
        return "".join(chunks)
```

### 3.3 Trigger routing

When `triggers` is non-empty, `FridayBrain.chat_stream` checks each
incoming user message against the trigger phrases (case-insensitive
substring match). The first matching skill runs, and its result is
prepended to the brain's response.

To invoke a skill explicitly (regardless of triggers), use the `run_skill`
tool exposed by the brain's tool router.

### 3.4 Skills vs integrations — when to use which

| Use a Skill when… | Use an Integration when… |
|-------------------|--------------------------|
| Multiple steps are required | One HTTP call / one API action |
| You need the brain's LLM to synthesise output | You just need to fetch data |
| You want free-text activation | You want explicit programmatic dispatch |
| The workflow is user-facing | The workflow is infrastructure |

---

## 4. Adding a New Agent

Agents are higher-level orchestration primitives. Each agent type is a
class with an `execute(task, context)` async method, registered in
`AgentManager`.

### 4.1 Current limitations

> ⚠️ The agent system in v3.2 does **not** have a true tool-use loop.
> Agents can call the brain's `chat_stream` and access the universal
> connector, but they cannot autonomously iterate on tool calls (e.g.
> "search → read → search again → synthesise"). That loop is hard-coded
> per-agent. A general-purpose tool-use loop is planned for v4.0.

### 4.2 Step-by-step: add a Summary agent

1. **Define the agent class** in `agents/summary_agent.py`:

```python
import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("SummaryAgent")


class SummaryAgent:
    """Condenses a long text into a bullet-point summary."""

    def __init__(self, brain=None):
        self.brain = brain
        self.name = "SummaryAgent"

    async def execute(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        ctx = context or {}
        text = ctx.get("text", task)

        if not self.brain:
            return {"error": "Brain not available for SummaryAgent"}

        prompt = (
            f"Summarise the following text in 5 bullet points. "
            f"Be concise and factual.\n\nTEXT:\n{text[:4000]}"
        )
        chunks = []
        async for chunk in self.brain.chat_stream(prompt):
            chunks.append(chunk)
        summary = "".join(chunks)

        return {
            "summary": summary,
            "input_length": len(text),
            "output_length": len(summary),
            "method": "llm_summarisation",
        }
```

2. **Register the agent type** in `agents/agent_manager.py`:

```python
class AgentType(Enum):
    RESEARCH = "research"
    CODING = "coding"
    WRITING = "writing"
    TASK = "task"
    SUMMARY = "summary"   # ← new


class AgentManager:
    def _init_agents(self):
        from agents.research_agent import ResearchAgent
        from agents.coding_agent import CodingAgent
        from agents.writing_agent import WritingAgent
        from agents.task_agent import TaskAgent
        from agents.summary_agent import SummaryAgent    # ← new

        self.agents = {
            AgentType.RESEARCH: ResearchAgent(self.brain),
            AgentType.CODING: CodingAgent(self.brain),
            AgentType.WRITING: WritingAgent(self.brain),
            AgentType.TASK: TaskAgent(self.brain),
            AgentType.SUMMARY: SummaryAgent(self.brain),  # ← new
        }
```

3. **Use it**:

```bash
curl -X POST http://localhost:8000/api/task \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"agent_type": "summary", "prompt": "Summarise this", "context": {"text": "..."}}'
```

### 4.3 Swarm and pipeline

`AgentManager.run_swarm(task, agent_types)` runs multiple agents in
parallel via `asyncio.gather` and returns all results. Useful for getting
multiple perspectives on the same task.

`AgentManager.run_pipeline(task)` runs a fixed sequence:
Research → Plan (Task) → Execute (Coding, if code-related) → Write (report).
To add your agent to the pipeline, edit `run_pipeline` in
`agent_manager.py`.

### 4.4 Tactical Manager and Coding Orchestrator

For more sophisticated coordination:
- **`TacticalManager`** (`agents/tactical_manager.py`) — plans which agents
  to dispatch, then synthesises results. Exposed via `POST /api/tactical`.
- **`CodingOrchestrator`** (`agents/coding_orchestrator.py`) — generates
  multi-file projects. Exposed via `POST /api/generate-project`.

Both consume the singleton `FridayBrain` from `api.main._get_brain()`.

---

## 5. Plugin Marketplace

The marketplace lives in [`marketplace/`](../marketplace/). Plugins are
catalogued in `marketplace/index.json` and their source lives in
`marketplace/plugins/<name>/`.

### 5.1 Publishing a plugin

1. **Build the plugin** as a `BaseIntegration` subclass (see
   [§2](#2-adding-a-new-integration)).
2. **Create the directory:**
   ```
   marketplace/plugins/
     my_plugin/
       my_plugin.py
       README.md
       config.example.json   (optional)
   ```
3. **Add an entry** to [`marketplace/index.json`](../marketplace/index.json):
   ```json
   {
     "name": "my_plugin",
     "description": "One-line description",
     "author": "Your Name",
     "homepage": "https://github.com/you/my_plugin",
     "tags": ["productivity"]
   }
   ```
4. **Open a PR.** A maintainer will review and merge.

### 5.2 The AST security scan

When a user runs `friday plugin install <name>`, the CLI copies the plugin
into `integrations/` only after a static AST scan passes. The scan is in
`cli/commands.py::_plugin_install` and walks the **entire** AST (not just
top-level nodes) to catch lazy imports inside functions, classes, and
conditionals.

**Blocked imports** (anywhere in the AST):

```
os, subprocess, socket, shlex, ctypes, sys,
importlib, builtins, pty, multiprocessing
```

**Blocked calls** (dynamic code execution):

```
__import__(...), exec(...), eval(...), compile(...)
```

If any blocked pattern is found, the install is refused and the user is
shown how to copy the file manually if they have personally audited it.

### 5.3 Manual override (when you trust a plugin)

If the AST scan refuses your plugin but you've personally reviewed the
source:

```bash
cp marketplace/plugins/<name>/<name>.py integrations/
```

This bypasses the scan. Document the reason in your ops runbook — the
audit chain will record the integration's actions but not the fact that
you bypassed the scan.

### 5.4 Limitations of the scan

The scan is **defence-in-depth, not a sandbox**. A determined attacker
can still bypass it via:
- Computed attribute access (`getattr(obj, "sub" + "process")`)
- Reading the module file at runtime and `exec`ing it
- Importing a non-blocked module that imports a blocked one (transitive)

For true isolation, run plugins in a subprocess with `seccomp`/`bubblewrap`
(planned for v4.0). Until then, only install plugins from trusted authors.

---

## 6. Testing

### 6.1 Running the test suite

```bash
# Full suite (304 passing tests as of v3.2)
cd /home/z/my-project/work/FRIDAY
python -m pytest tests/ --tb=short -q

# Verbose, single file
python -m pytest tests/test_api.py -v

# Stop on first failure, drop into pdb
python -m pytest tests/test_ledger_security.py -x --pdb

# With coverage (configure .coveragerc first)
pip install pytest-cov
python -m pytest tests/ --cov=core --cov=api --cov-report=term-missing
```

### 6.2 Test layout

```
tests/
├── conftest.py              Sets FRIDAY_DEV_MODE=1, FRIDAY_API_TOKEN=""
├── test_api.py              API endpoint tests (TestClient + mocked brain)
├── test_ledger_security.py  HMAC chain, tamper detection, NEVER_AUTO_APPROVE
├── test_mcp_security.py     MCP fail-closed, dispatch routing
├── test_plugin_sandbox.py   AST scan catches lazy imports
├── test_sentinel.py         23 risk-classification tests
├── test_memory.py           27 fact-extraction patterns
├── test_emotions.py         36 VAD-engine tests
├── test_integrations.py     BaseIntegration contract + registry
├── integration/
│   └── test_integration.py  Real GLM/MCP/webhook tests (gated on RUN_INTEGRATION_TESTS=1)
└── ... (24 more files)
```

### 6.3 Writing a test

Use the existing fixtures in `tests/test_api.py` as a template. The
canonical pattern:

```python
"""Tests for MyFeature."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """TestClient with all external deps mocked."""
    mock_brain = MagicMock()
    mock_brain.chat_stream = AsyncMock()

    async def fake_stream(msg, user_name="User"):
        yield "Hello from Friday"

    mock_brain.chat_stream = fake_stream

    with patch("api.main._get_brain", new_callable=AsyncMock, return_value=mock_brain), \
         patch("api.main.FRIDAY_API_TOKEN", "test-token"):
        from api.main import app
        with TestClient(app) as c:
            yield c


@pytest.fixture()
def auth_headers():
    return {"Authorization": "Bearer test-token"}


class TestMyFeature:

    def test_basic_call(self, client, auth_headers):
        resp = client.post("/api/my-feature", json={"x": 1}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
```

### 6.4 Pytest fixtures and mocking patterns

| Pattern | When to use | Example |
|---------|-------------|---------|
| `pytest.fixture()` | Setup shared across tests | `client`, `auth_headers` |
| `AsyncMock` / `MagicMock` | Replace async/sync deps | Brain, integrations |
| `patch("module.attr", …)` | Replace a single import | `patch("api.main._get_brain")` |
| `patch.dict(os.environ, …)` | Temp env var changes | For testing config branches |
| `tmp_path` (builtin) | Temp file paths | Audit chain files |
| `pytest.mark.asyncio` | Async tests (with `asyncio_mode=strict`) | All `async def test_…` |
| `@pytest.mark.skip(reason="…")` | Skip a flaky test | Document why |

### 6.5 Integration tests

`tests/integration/test_integration.py` contains 3 tests that hit real
external services (real GLM API, real MCP subprocess, real GitHub webhook).
They're skipped by default — set `RUN_INTEGRATION_TESTS=1` to run them:

```bash
RUN_INTEGRATION_TESTS=1 GLM_API_KEY=real-key python -m pytest tests/integration/ -v
```

### 6.6 Hellfire audit

`scripts/hellfire_audit.py` is an adversarial check suite with 8 tests:

1. No commented-out real calls next to hardcoded returns
2. No eager client construction without guards
3. No double-run of expensive setup
4. Auth rejection works (no token → 403)
5. `NEVER_AUTO_APPROVE_COMPONENTS` is intact
6. No hardcoded secrets
7. GLM key sourced from env, not hardcoded
8. ZhipuAI client construction is guarded

Run with:

```bash
PYTHONPATH=. FRIDAY_API_TOKEN=test python3 scripts/hellfire_audit.py
```

This runs in CI on every push. Failing any check breaks the build.

---

## 7. Code Style

### 7.1 Naming conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Module / file | `snake_case.py` | `agent_manager.py` |
| Class | `PascalCase` | `AgentManager`, `FridayBrain` |
| Function / method | `snake_case` | `run_agent`, `get_status` |
| Constant | `UPPER_SNAKE` | `NEVER_AUTO_APPROVE_COMPONENTS`, `GENESIS_HASH` |
| Private member | `_leading_underscore` | `_audit_chain`, `_events` |
| Env var | `UPPER_SNAKE` | `FRIDAY_API_TOKEN`, `GLM_API_KEY` |
| Pydantic model | `PascalCaseRequest` / `PascalCaseResponse` | `ChatRequest`, `GoalRequest` |

### 7.2 Type hints

- **Required** on all public functions and methods.
- Use `Optional[X]` (not `X | None`) for Python 3.10 compatibility.
- Use `dict` / `list` / `tuple` directly (not `Dict` / `List` / `Tuple`)
  for new code — `from __future__ import annotations` makes them
  equivalent.
- Async functions must be `async def` with `AsyncGenerator` / `Awaitable`
  return types where appropriate.

```python
async def execute_action(
    self,
    service_name: str,
    action: str,
    params: Optional[dict] = None,
    approval_timeout: int = 300,
) -> dict:
    ...
```

### 7.3 Docstring style — Google

```python
def queue_action(self, component, action, params, risk_level="high"):
    """Queue an action for approval.

    Brief description on first line.

    Args:
        component: The integration component name (e.g. "Finance").
        action: The action name (e.g. "transfer").
        params: Action parameters as a dict.
        risk_level: One of "low", "medium", "high", "critical".

    Returns:
        The new action_id (UUID string).

    Raises:
        ValueError: If component is empty.
    """
```

### 7.4 Error handling patterns

| Pattern | When | Example |
|---------|------|---------|
| `try/except Exception` + `logger.exception` | Top-level handlers (API routes, MCP handlers) | `api/routes/chat.py` |
| Re-raise specific exceptions | Library code | `raise ValueError(…)` from `except` |
| `logger.debug("Non-critical error: %s", e)` | Best-effort cleanup | Heartbeat tasks |
| `HTTPException(status_code=503, detail="…")` | Service unavailable | When singleton init fails |
| `HTTPException(status_code=404, detail="…")` | By-id lookups | `goals`, `branches`, `actions` |

Never swallow exceptions silently (`except: pass`). Always at least
`logger.debug` so the trace is recoverable.

### 7.5 Imports

- Standard library first, third-party second, local last.
- Use absolute imports (`from core.ledger import get_ledger`), not
  relative (`from ..core.ledger import …`).
- Lazy imports inside functions are OK for optional dependencies (e.g.
  `pyautogui`, `whisper`) — they let the rest of the app boot when the
  dep is missing.

### 7.6 Logging

- One logger per module: `logger = logging.getLogger(__name__)`.
- Levels: `DEBUG` (verbose), `INFO` (lifecycle), `WARNING` (degraded),
  `ERROR` (failed operation), `CRITICAL` (system down).
- Never `print()` in library code. CLI is the only place `print()` is OK
  (and even there, prefer `rich.console.print`).

---

## 8. Contributing

### 8.1 Branch naming

```
feature/<short-description>      # New feature
fix/<short-description>          # Bug fix
docs/<short-description>         # Documentation only
security/<short-description>     # Security fix (request review from security maintainer)
chore/<short-description>        # Refactor / dep upgrade / CI
```

Examples: `feature/joke-integration`, `fix/ledger-hmac-secret`,
`docs/api-reference`.

### 8.2 PR checklist

Before opening a PR:

- [ ] Branch is up to date with `main`
- [ ] `python -m pytest tests/ --tb=short -q` passes locally
- [ ] `PYTHONPATH=. FRIDAY_API_TOKEN=test python3 scripts/hellfire_audit.py` passes
- [ ] New code has type hints on all public functions
- [ ] New code has Google-style docstrings
- [ ] New integrations inherit from `BaseIntegration` and use `_make_response()`
- [ ] New skills inherit from `BaseSkill`
- [ ] New env vars are added to `.env.example` AND `config/settings.py`
- [ ] New API routes are added to `api/main.py` with appropriate auth
- [ ] New tests added (target: 90%+ coverage on new code)
- [ ] No `# commented-out real call next to a success return` (hellfire #1)
- [ ] No eager client construction in `__init__` without guard (hellfire #2)
- [ ] No theatrical naming (`Singularity`, `God-Mode`, `Apex`, etc.)
- [ ] CHANGELOG entry added (if user-visible)
- [ ] Documentation updated (if API surface changed)

### 8.3 CI requirements

CI (`.github/workflows/ci.yml`) runs on every push and PR:

1. **Boot smoke test** — `FridayBrain` constructs with `BRAIN_PROVIDER=glm`
2. **MockBrain regression guard** — ensures benchmarks use real brain, not mock
3. **Full pytest suite** — `PYTHONPATH=. python -m pytest tests/ -q --tb=short`
4. **Hellfire audit** — `PYTHONPATH=. FRIDAY_API_TOKEN=test python3 scripts/hellfire_audit.py`
5. **Theatrical naming grep** — refuses `Singularity`, `God-Mode`, `FridayApex`,
   `Ghost-Mode`, `SWAT`, `4D timeline`, `Nexus Cinema`, `peer not a tool`,
   `Recursive Modification`

A PR is mergeable only when all 5 steps pass. The maintainers may also
request manual review from the security maintainer for any change touching
`core/ledger.py`, `core/sentinel.py`, `cli/commands.py::_plugin_install`,
or `mcp_server.py`.

### 8.4 Commit message conventions

Use the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
feat(integrations): add Joke integration
fix(ledger): include approved_by in HMAC hash content
docs(api): add response examples for /api/chat
security(mcp): ignore caller-supplied risk_level
chore(deps): bump fastapi to 0.110.0
```

### 8.5 Release process

1. Update `version` in `pyproject.toml` and `README.md`
2. Update `CHANGELOG_FIXES.md` with all user-visible changes
3. Tag: `git tag v3.3.0 && git push --tags`
4. CI publishes a GitHub Release with the changelog as the body
5. Docker image is built and pushed to GHCR (planned — currently manual)

---

## See Also

- [API Reference](./API_REFERENCE.md)
- [Deployment Guide](./DEPLOYMENT_GUIDE.md)
- [Security Model](./SECURITY_MODEL.md)
- [Architecture](./ARCHITECTURE.md)
- [Plugin SDK](./PLUGINS.md)
- [Skills Framework](./SKILLS.md)
