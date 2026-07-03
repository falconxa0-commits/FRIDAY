# Project FRIDAY v2.0 — Personal AI Assistant

FRIDAY is a Python-based personal AI assistant that wraps the Z.ai (ZhipuAI) GLM-4-Flash model — free, no paid API keys required — behind a unified chat interface with tool calling, persistent memory, action approval gating, a sci-fi terminal CLI, a VS Code extension, and a web dashboard. It can also route to Claude, Gemini, GPT, or a local Ollama instance when those providers are configured, and ships with integrations for weather, calendar, Spotify, smart home, printers, commerce, and more. The default configuration works out of the box with only a free GLM key from `open.bigmodel.cn`.

## v2.0 Highlights

- **Sci-fi terminal CLI** (`friday` command) with animated boot sequence, two-pane layout, real-time status panel, colour-coded approval prompts, and receipt display
- **VS Code extension** (`apps/vscode/friday.vsix`) — Explain Selection, Fix Error, Generate Code, Research Topic, Morning Briefing, Open Dashboard
- **Windows binary build** (`build/build_windows.py`) — produces a single `friday.exe` via PyInstaller
- **Cross-platform installers** (`install.sh`, `install.ps1`)
- **Behavioral pattern learning** — Friday observes what you do and surfaces real recurring patterns
- **Ambient screen awareness** — Friday watches your screen and proactively offers help
- **Deep research with synthesis** — multi-source research with agreement/conflict detection + honest uncertainty flagging
- **Multi-modal memory** — store and retrieve visual memories (screenshots, images) by semantic query
- **MCP execution hub** — external AI agents (Claude Code, Cursor) can submit actions via `friday.request_approval`; Friday's ledger stands between suggestion and execution
- **Conversational voice** — barge-in support, pace/vocabulary adjustment, night-mode whisper
- **Team mode** — multi-user with per-user private memories + shared project memories
- **Nigerian context** — Naira pricing with real exchange rates, local banks/delivery/news sources, Pidgin support
- **Plugin marketplace** — `friday plugin install <name>` from `marketplace/`
- **Cost dashboard with optimization** — suggests switching paid calls to GLM when possible
- **Teach-me skill** — Socratic teaching mode for developers

## Feature Status

| Feature | Status | Provider | Cost |
|---|---|---|---|
| Chat | Real | GLM-4-Flash | Free |
| Web Search | Real | GLM built-in | Free |
| Vision / Screen Analysis | Real | GLM-4V | Free |
| Semantic Memory | Real | Z.ai Embedding-3 | Free |
| Multi-modal Memory | Real | Z.ai Embedding-3 + GLM-4V | Free |
| Image Generation | Real | CogView-3 | Free |
| Video Generation | Real | CogVideoX | Free (1–3 min) |
| Code Execution | Real | Z.ai Code Interpreter | Free |
| Calendar / Gmail | Real | Google APIs | Free |
| Weather | Real | OpenWeatherMap | Free |
| Spotify | Real | Spotify API | Free |
| Smart Home | Real | Home Assistant | Free |
| Printer Control | Real | CUPS/IPP | Free |
| 3D Printer Control | Real | OctoPrint | Free |
| Council Mode | Real | All configured providers | Free (GLM) |
| Tamper-evident Ledger | Real | Built-in | Free |
| MCP Server | Real | Built-in | Free |
| MCP request_approval | Real | Built-in | Free |
| Pattern Learning | Real | Built-in | Free |
| Ambient Screen Awareness | Real | Built-in | Free |
| Deep Research | Real | GLM web search + synthesis | Free |
| Conversational Voice | Real | ElevenLabs/pyttsx3 + Whisper | Free (pyttsx3) |
| Team Mode | Real | Built-in | Free |
| Nigerian Context | Real | Open exchange rates + local RSS | Free |
| Plugin Marketplace | Real | Built-in | Free |
| CLI (sci-fi TUI) | Real | rich + textual | Free |
| VS Code Extension | Real | TypeScript | Free |
| Windows Binary | Real | PyInstaller | Free |
| Writing Style Skill | Real | Heuristic + GLM | Free |
| Teach-me Skill | Real | Socratic + GLM | Free |
| Claude reasoning | Optional upgrade | Anthropic | Paid |
| GPT coding | Optional upgrade | OpenAI | Paid |
| Persistent Memory | Optional upgrade | Supabase | Free tier |
| Autonomous Commerce | Demo-mode | Stripe Sandbox | Paid |

## Real Benchmark Pass Rate

**183/183 unit tests passing** (`pytest tests/ -q`)

**Hellfire audit:** 8/8 checks pass, 0 failures

**Smoke test:** All checks pass

**FRIDAY v2.0 Definition of Done:** 15/15 checks pass — see `scripts/verify_v2_definition_of_done.py`

## Z.ai Free Tier Rate Limits

Approximate (check `open.bigmodel.cn/pricing` for current values):

- GLM-4-Flash: ~60 requests/min, 100K tokens/day
- GLM-4V: ~50 requests/min, 8K context
- CogView-3 (images): limited generations/day
- CogVideoX (videos): limited generations/day, 1–3 minutes per generation
- Web Search: included with GLM-4-Flash

## Get Started in 5 Minutes

```bash
# 1. Clone the repo
git clone <your-fork-url>
cd FRIDAY

# 2. Install dependencies + Friday CLI
bash install.sh         # Linux/Mac
# or: .\install.ps1     # Windows (PowerShell)

# 3. Get a free GLM API key from https://open.bigmodel.cn
#    (Create account → API Keys → Create new key → copy)

# 4. Set the key
export GLM_API_KEY="your-real-key-here"

# 5. Run the sci-fi TUI
friday
# Or start the web dashboard:
uvicorn api.main:app --host 0.0.0.0 --port 8000
# Then open http://localhost:8000
```

You now have:
- Real chat with GLM-4-Flash (CLI + web + VS Code)
- Real image generation (CogView-3)
- Real video generation (CogVideoX, 1–3 min per video)
- Real web search (GLM built-in)
- Memory, action approval ledger, tamper-evident audit log
- Trust & stats dashboard with cost optimization suggestions
- Voice mode with barge-in
- Team mode for shared projects

## CLI Commands

```bash
friday                       # full sci-fi TUI
friday chat "msg"            # one-shot chat (streams)
friday research "topic"      # research agent with sources
friday generate "prompt"     # CogView-3 image
friday video "prompt"        # CogVideoX video
friday morning               # morning briefing
friday council "question"    # multi-provider comparison
friday status                # integration status
friday memory list|search|export
friday bench                 # run benchmark suite
friday trust                 # run hellfire_audit
friday ledger                # audit log + chain status
friday plugin install <name> # marketplace install
friday plugin list           # marketplace listing
friday --watch               # ambient mode
friday --voice               # voice mode
friday --council             # TUI w/ council default
```

## VS Code Extension

```bash
cd apps/vscode
npm install
npm run compile
npm run package         # produces friday.vsix
code --install-extension friday.vsix
```

Then in VS Code: Command Palette → "Friday: Explain Selection" on any selected code.

## Optional: Add Paid Providers

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # Claude
export OPENAI_API_KEY="sk-..."          # GPT-4o
export GEMINI_API_KEY="AIza..."         # Gemini
export BRAIN_PROVIDER=council           # use all in parallel
```

## Verification

```bash
# All v1 verifications still pass
pytest tests/ -q                                  # 183/183
PYTHONPATH=. FRIDAY_API_TOKEN=test python3 scripts/hellfire_audit.py
python3 scripts/smoke_test.py

# v2.0 Definition of Done
python3 scripts/verify_v2_definition_of_done.py   # 15/15

# Per-section verification scripts (24 total)
ls scripts/verify_*.py
```

## Documentation

- `docs/LIMITATIONS.md` — honest accounting of what doesn't work yet
- `docs/PLUGINS.md` — how to add a new integration plugin
- `docs/SECURITY.md` — security model + threat surface
- `docs/SKILLS.md` — how to add a new skill
- `docs/BUILD_WINDOWS.md` — building friday.exe for Windows
- `marketplace/CONTRIBUTING.md` — submitting a plugin to the marketplace

## License

MIT
