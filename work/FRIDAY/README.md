# Project FRIDAY — Personal AI Assistant

FRIDAY is a Python-based personal AI assistant that wraps the Z.ai (ZhipuAI) GLM-4-Flash model — free, no paid API keys required — behind a unified chat interface with tool calling, persistent memory, action approval gating, and a web dashboard. It can also route to Claude, Gemini, GPT, or a local Ollama instance when those providers are configured, and ships with integrations for weather, calendar, Spotify, smart home, printers, and more. The default configuration works out of the box with only a free GLM key from `open.bigmodel.cn`.

## Feature Status

| Feature | Status | Provider | Cost |
|---|---|---|---|
| Chat | Real | GLM-4-Flash | Free |
| Web Search | Real | GLM built-in | Free |
| Vision / Screen Analysis | Real | GLM-4V | Free |
| Semantic Memory | Real | Z.ai Embedding-3 | Free |
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
| Claude reasoning | Optional upgrade | Anthropic | Paid |
| GPT coding | Optional upgrade | OpenAI | Paid |
| Persistent Memory | Optional upgrade | Supabase | Free tier |
| Autonomous Commerce | Demo-mode | Stripe Sandbox | Paid |

## Real Benchmark Pass Rate

**183/183 unit tests passing** (`pytest tests/ -q`)

**Hellfire audit:** 8/8 checks pass, 0 failures

**Smoke test:** All checks pass

**End-to-end benchmark:** Requires `GLM_API_KEY` to run — see `benchmarks/results.md` for details.

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

# 2. Install dependencies (no torch, no sentence-transformers — fast install)
pip install -r requirements.txt

# 3. Get a free GLM API key from https://open.bigmodel.cn
#    (Create account → API Keys → Create new key → copy)

# 4. Set the key
export GLM_API_KEY="your-real-key-here"

# 5. Start the API + web dashboard
uvicorn api.main:app --host 0.0.0.0 --port 8000

# 6. Open http://localhost:8000 in your browser
#    (Default FRIDAY_API_TOKEN is empty in dev mode — set it for production)
```

You now have:
- Real chat with GLM-4-Flash
- Real image generation (CogView-3)
- Real video generation (CogVideoX, 1–3 min per video)
- Real web search (GLM built-in)
- Memory, action approval ledger, tamper-evident audit log
- Trust & stats dashboard

## Optional: Add Paid Providers

```bash
# Claude (Anthropic) — better long-form reasoning
export ANTHROPIC_API_KEY="sk-ant-..."

# GPT-4o (OpenAI) — strong code generation
export OPENAI_API_KEY="sk-..."

# Gemini (Google) — cheap multimodal
export GEMINI_API_KEY="AIza..."

# Set BRAIN_PROVIDER=council to use all configured providers in parallel
export BRAIN_PROVIDER=council
```

## Optional: Local LLM (No Network)

```bash
# Install Ollama: https://ollama.com
ollama pull llama3
ollama serve

# In another terminal:
export BRAIN_PROVIDER=ollama
uvicorn api.main:app --port 8000
```

Ollama runs entirely on localhost — no external network calls. See `docs/LIMITATIONS.md` for GPU/CPU requirements.

## Optional: Persistent Memory

```bash
# Create a free Supabase project: https://supabase.com
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_KEY="your-anon-key"
```

Without Supabase, memory is in-process and lost on restart. The export/import endpoints (`GET /api/memory/export`, `POST /api/memory/import`) let you back up manually.

## Docker

```bash
docker build -t friday:latest .
docker run -p 8000:8000 -e GLM_API_KEY="your-key" friday:latest
```

The image builds cleanly because `requirements.txt` has no `torch` or `sentence-transformers` (the historical cause of disk-space failures).

## Project Layout

```
FRIDAY/
├── api/              # FastAPI app + routes (chat, memory, actions, stats, trust)
├── apps/web/static/  # Web dashboard (HTML/JS/CSS, no build step)
├── core/             # Brain, ledger, memory, context, council_mode, etc.
├── integrations/     # Auto-discovered plugins (weather, commerce, printers, etc.)
├── agents/           # Research, coding, writing, task agents
├── skills/           # Skill plugins (morning_briefing, deep_research, etc.)
├── voice/            # Speaker, listener, transcriber (Whisper)
├── vision/           # Screen reader, OCR
├── control/          # Browser (Playwright), PC control, file manager
├── database/         # Supabase client, vector store
├── mcp_server.py     # MCP server (JSON-RPC over stdio)
├── scripts/          # Verification scripts (verify_*.py) + hellfire_audit
├── tests/            # 183 unit tests
├── docs/             # LIMITATIONS.md, PLUGINS.md, SECURITY.md, SKILLS.md
└── benchmarks/       # run_benchmark.py + results.md
```

## Verification

Every claim in this README is verifiable. Run:

```bash
# Full unit-test suite (183 tests)
pytest tests/ -q

# Hellfire audit (8 adversarial checks)
PYTHONPATH=. FRIDAY_API_TOKEN=test python3 scripts/hellfire_audit.py

# Smoke test (imports + app builds)
python3 scripts/smoke_test.py

# Per-section verification scripts
python3 scripts/verify_voice_approval.py       # Section 4a
python3 scripts/verify_wake_on_contact.py      # Section 4b
python3 scripts/verify_printers.py             # Section 4c+4d
python3 scripts/verify_ollama_local.py         # Section 5a
python3 scripts/verify_memory_cycle.py         # Section 5b
python3 scripts/verify_stats.py                # Section 5c
python3 scripts/verify_limitations.py          # Section 5d
python3 scripts/verify_plugin_sdk.py           # Section 6a
python3 scripts/verify_pidgin_transcription.py # Section 6b
python3 scripts/verify_context_modes.py        # Section 6c
python3 scripts/verify_commerce.py             # Section 7
python3 scripts/verify_mcp_server.py           # Section 8
python3 scripts/verify_dashboard.py            # Section 9
python3 scripts/verify_tamper_evident.py       # Section 10a
python3 scripts/verify_council_mode.py         # Section 10b
python3 scripts/verify_trust_report.py         # Section 10c
python3 scripts/verify_creative_routing.py     # Section 10d
python3 scripts/verify_research_backbone.py    # Section 10e
python3 scripts/verify_docker_build.py         # Section 11a
python3 scripts/verify_onboarding.py           # Section 11b
```

## Documentation

- `docs/LIMITATIONS.md` — honest accounting of what doesn't work yet
- `docs/PLUGINS.md` — how to add a new integration plugin
- `docs/SECURITY.md` — security model + threat surface
- `docs/SKILLS.md` — how to add a new skill

## License

MIT
