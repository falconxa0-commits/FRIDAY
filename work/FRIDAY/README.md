# Project FRIDAY v3.2 — Personal AI Assistant

FRIDAY is a Python-based personal AI assistant built around ZhipuAI's free GLM-4-Flash model. It provides chat, web search, image generation, video generation, code execution, voice interaction, screen analysis, multi-agent orchestration, and a tamper-evident action ledger — all on a free Z.ai API key. It runs as a CLI, a REST API, a WebSocket server, an MCP server, and a web dashboard.

## What Makes It Different

1. **MCP `request_approval`** — external AI agents (Claude Code, Cursor) submit actions through Friday's human-in-the-loop gate. No other open-source assistant offers this.
2. **Tamper-evident hash-chained audit log** — every action is provably recorded with SHA-256 hash chaining, persisted to disk, and detectable if tampered with.
3. **Full capability on a free Z.ai key** — chat, search, vision, image gen, video gen, code execution, and embeddings all work on the free tier.
4. **Nigerian-first localization** — Naira pricing with real exchange rates, 15 Nigerian banks, 6 delivery services, 6 news sources, power status checking.
5. **Subconscious mind in RAG** — every response benefits from pattern surfacing from your interaction history, injected into the RAG context.

## Feature Status

| Feature | Status | Provider | Cost |
|---|---|---|---|
| Chat (streaming + tool calling) | Real | GLM-4-Flash | Free |
| Web Search | Real | GLM built-in | Free |
| Vision / Screen Analysis | Real | GLM-4V | Free |
| Image Generation | Real | CogView-3 | Free |
| Video Generation | Real | CogVideoX | Free (1–3 min) |
| Code Execution | Real | Z.ai Code Interpreter | Free |
| Semantic Memory | Real | Z.ai Embedding-3 | Free |
| Multi-modal Memory | Real | Z.ai Embedding-3 + GLM-4V | Free |
| Subconscious Mind (RAG) | Real | Built-in | Free |
| Memory Compression | Real | Built-in | Free |
| Council Mode | Real | All configured providers | Free (GLM) |
| Agent Swarm | Real | Built-in | Free |
| Agent Pipeline | Real | Built-in | Free |
| Tactical Manager | Real | Built-in | Free |
| Coding Orchestrator | Real | Built-in | Free |
| 5 Identity Modes | Real | Built-in | Free |
| Goal Tracking | Real | Built-in | Free |
| Writing Style Adaptation | Real | Built-in | Free |
| Socratic Teaching | Real | Built-in | Free |
| Daily Journal | Real | Built-in | Free |
| Tamper-evident Ledger | Real | Built-in | Free |
| MCP Server (8 tools) | Real | Built-in | Free |
| Plugin Marketplace + Sandbox | Real | Built-in | Free |
| Team Mode | Real | Built-in | Free |
| Push Notifications | Real | Telegram + Desktop | Free |
| Webhook Receiver | Real | Built-in | Free |
| Nigerian Context | Real | Free APIs | Free |
| Weather | Real | OpenWeatherMap | Free |
| Calendar / Gmail | Real | Google APIs | Free |
| Spotify | Real | Spotify API | Free |
| Smart Home | Real | Home Assistant | Free |
| Printer Control | Real | CUPS/IPP | Free |
| 3D Printer Control | Real | OctoPrint | Free |
| Commerce (price comparison) | Real | Playwright | Free |
| Learning System | Real | Built-in | Free |
| Self-Improvement Engine | Real | Built-in | Free |
| Privacy Audit | Real | Built-in | Free |
| Proactive Engine | Real | Built-in | Free |
| Rate Limiting | Real | Built-in | Free |
| CLI (sci-fi TUI) | Real | rich | Free |
| VS Code Extension | Real | TypeScript | Free |
| Friday SDK | Real | Python | Free |
| Claude reasoning | Optional | Anthropic | Paid |
| GPT coding | Optional | OpenAI | Paid |
| Persistent Memory | Optional | Supabase | Free tier |
| Autonomous Commerce | Demo | Stripe Sandbox | Paid |

## Get Started in 5 Minutes

```bash
git clone <repo-url>
cd FRIDAY
pip install -r requirements.txt
echo "GLM_API_KEY=your_free_key_here" >> .env  # get free key at open.bigmodel.cn
friday
```

## Architecture

13 packages, 186 Python files, 75 API routes, 15 CLI commands, 8 MCP tools:

```
core/        29 modules — brain, ledger, memory, AI pipeline, security, scheduling
api/routes/  20 files   — 75 HTTP endpoints + WebSocket
integrations/ 18 plugins — auto-discovered, 36 unique actions
agents/       7 files    — research, coding, writing, task, tactical, orchestrator
skills/       8 files    — auto-discovered, trigger-phrase activated
voice/        5 files    — TTS, STT, wake word, barge-in
vision/       4 files    — screen capture, OCR, GLM-4V analysis
control/      5 files    — PC, browser, file, app, workspace
database/     5 files    — Supabase, vector store, compression, subconscious
config/       3 files    — settings, 5 identity modes, system prompt
cli/          3 files    — terminal TUI + 15 commands
sdk/          1 file     — FridayClient async Python SDK
deploy/       3 files    — nginx, systemd, deploy guide
```

- **Plugin auto-discovery**: drop a `.py` file in `integrations/`, it's auto-loaded
- **5 identity modes**: General, Strategist, Creative, Debugger, Guardian
- **Council mode**: routes to all configured providers in parallel
- **GLM tool calling**: free-tier users get tool-augmented reasoning (5 tools)

## Honest Limitations

See `docs/LIMITATIONS.md` for the complete list. Key points:
- Single-user, single-process architecture
- In-memory state without Supabase (data lost on restart)
- Hardware integrations require real local hardware
- GLM free tier rate limits (~60 RPM, 100K tokens/day)

## License

MIT
