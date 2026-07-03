# Contributing to the Friday Plugin Marketplace

Thanks for building a plugin for Friday! This guide walks you through submitting it to the marketplace so other users can install it with `friday plugin install <name>`.

## Plugin structure

Each plugin lives in its own directory under `marketplace/plugins/<your-plugin-name>/`:

```
marketplace/plugins/
  your-plugin-name/
    your_plugin_name.py    # the actual plugin (inherits from BaseIntegration)
    README.md              # what the plugin does + how to configure it
    config.example.json    # optional — example config the user can copy
```

## Plugin requirements

1. **Inherits from `BaseIntegration`** — see `integrations/base.py` for the abstract methods.
2. **Auto-discoverable** — the plugin's class must inherit from `BaseIntegration`. The `UniversalConnector` scans `integrations/` at startup.
3. **No fabrication** — if the backend is unreachable, return `status: "not_implemented"` or `status: "error"`, never a hardcoded success.
4. **Honest credentials** — `available()` must return False if the required env vars aren't set. Never auto-approve dangerous actions.
5. **Respects the ledger** — high-risk actions (financial, physical, code-execution) must be in `NEVER_AUTO_APPROVE_COMPONENTS` in `core/ledger.py`.

## Submitting

1. Fork the [Friday repo](https://github.com/friday-ai/friday)
2. Create your plugin directory under `marketplace/plugins/<your-plugin-name>/`
3. Add an entry to `marketplace/index.json` with name, description, author, homepage, tags
4. Open a pull request
5. A maintainer will review and merge

## Plugin template

```python
"""MyPlugin — does X."""
from typing import Optional
from integrations.base import BaseIntegration


class MyPlugin(BaseIntegration):
    @property
    def name(self) -> str:
        return "MyPlugin"

    def available(self) -> bool:
        # Check env vars + reachability
        import os
        return bool(os.getenv("MY_PLUGIN_API_KEY"))

    def list_actions(self):
        return ["do_thing", "get_status"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}
        if not self.available():
            return self._make_response("not_implemented", "MY_PLUGIN_API_KEY not set")
        # ... real implementation ...
        return self._make_response("success", "Did the thing", receipt_data={...})
```

## Code review checklist

- [ ] Inherits from `BaseIntegration`
- [ ] `available()` returns False when env vars are missing
- [ ] No hardcoded secrets in the source
- [ ] No `# commented-out real call next to a success return` (hellfire audit)
- [ ] No eager client construction in `__init__` without a guard
- [ ] High-risk actions added to `NEVER_AUTO_APPROVE_COMPONENTS`
- [ ] README explains what env vars are required
- [ ] `friday plugin install <name>` works end-to-end

## Tags

Use these standard tags so users can search:

- `weather` — weather integrations
- `finance` — banking, payments, crypto
- `nigeria` — Nigerian-specific integrations
- `productivity` — calendar, email, tasks
- `hardware` — printers, 3D printers, smart home
- `media` — image, video, audio
- `research` — search, news, knowledge bases
