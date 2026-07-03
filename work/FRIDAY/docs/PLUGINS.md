# FRIDAY Plugin SDK

FRIDAY supports automatic plugin discovery. Any Python module in the
`integrations/` or `skills/` directory that follows the conventions below
will be loaded at startup.

## How Plugins Work

### Integration Plugins

An integration plugin is a Python class that inherits from
`BaseIntegration` and implements three required members:

| Member | Type | Description |
|--------|------|-------------|
| `name` | `@property → str` | Human-readable name (e.g. `"MyIntegration"`) |
| `available()` | `method → bool` | Return `True` if the integration is configured and reachable |
| `execute(action, params)` | `async method → dict` | Execute a named action with optional params |

Optional overrides:

| Member | Type | Description |
|--------|------|-------------|
| `health_check()` | `async method → dict` | Lightweight reachability check |
| `list_actions()` | `method → List[str]` | Supported action names |

### Skill Plugins

A skill plugin inherits from `BaseSkill` and implements:

| Member | Type | Description |
|--------|------|-------------|
| `name` | `@property → str` | Skill name |
| `description` | `@property → str` | One-line description |
| `trigger_phrases` | `@property → list` | Phrases that activate the skill |
| `run(brain, params)` | `async method → dict` | Execute the skill's logic |

## Auto-Discovery Mechanism

1. **On startup**, the `UniversalConnector` scans the `integrations/`
   directory for Python modules.
2. It imports each module and looks for classes that subclass
   `BaseIntegration`.
3. Each found class is instantiated and registered under its `.name`.
4. The `UniversalRegistry` also maintains a manual mapping in
   `_INTEGRATION_SPECS` for known integrations.
5. **Skill plugins** follow the same pattern — the `FridayBrain` scans
   the `skills/` directory for `BaseSkill` subclasses.

## Example: Trivial Plugin

Create `integrations/hello_world.py`:

```python
"""Hello World integration — a minimal example plugin."""

import datetime
from typing import Optional

from integrations.base import BaseIntegration


class HelloWorldIntegration(BaseIntegration):
    """A trivial integration that says hello."""

    @property
    def name(self) -> str:
        return "HelloWorld"

    def available(self) -> bool:
        # Always available — no external dependencies
        return True

    def list_actions(self):
        return ["greet", "time"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}

        if action == "greet":
            who = params.get("name", "World")
            return self._make_response(
                "success",
                f"Hello, {who}!",
                receipt_data={"greeting": f"Hello, {who}!"},
            )
        elif action == "time":
            now = datetime.datetime.now().isoformat()
            return self._make_response(
                "success",
                f"Current time: {now}",
                receipt_data={"time": now},
            )
        else:
            return self._make_response(
                "not_implemented",
                f"Action '{action}' is not supported by HelloWorld.",
            )
```

After placing this file in `integrations/`, restart FRIDAY. The
`UniversalConnector` will discover and register it automatically. You can
then call it via:

```
POST /api/integrations/execute
{
  "service": "HelloWorld",
  "action": "greet",
  "params": {"name": "FRIDAY"}
}
```

## Best Practices

1. **Never fake success.** If the external service is unreachable, return
   `status: "not_implemented"` or `status: "error"` — never fabricate
   data.
2. **Use `_make_response()`** from `BaseIntegration` to ensure consistent
   response formatting with receipts.
3. **Handle `available()` honestly.** Check connectivity at call time, not
   just at init time.
4. **Keep actions atomic.** Each action should do one thing. Complex
   workflows should be broken into sequential action calls.
5. **Respect the Ledger.** High-risk actions (financial, physical,
   destructive) are never auto-approved. Don't try to bypass the
   approval gate.
