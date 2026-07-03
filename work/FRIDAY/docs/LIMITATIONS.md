# FRIDAY — Known Limitations

This document is an honest accounting of FRIDAY's current limitations.
We believe in transparency over marketing.

## Architecture

- **Single-user, single-process application.** FRIDAY is designed for one
  user at a time. There is no multi-tenancy, session isolation, or
  concurrent-user support.
- **Conversation state is in-memory** unless Supabase is configured.
  Restarting the process loses in-memory conversation history and session
  facts. Supabase-backed storage survives restarts but requires setup.
- **No horizontal scaling.** The action ledger, brain state, and
  integration connections are all per-process. Running multiple instances
  will cause conflicts.

## Brain / LLM

- **GLM free tier has rate limits.** Approximately 60 requests per minute
  and 100K tokens per day. Heavy usage will hit these limits. Check
  https://open.bigmodel.cn/pricing for the current official limits —
  Z.ai has updated these in the past and may again.
- **Claude/Gemini require paid API keys.** These providers are not free
  and costs scale with usage.
- **Ollama requires local installation.** You must install Ollama
  separately (https://ollama.com) and pull a model (e.g.,
  `ollama pull llama3`). Performance depends on your hardware:
  - GPU (NVIDIA with CUDA, or Apple Silicon) gives 10-50 tokens/sec on
    mid-size models like llama3 (8B).
  - CPU-only inference is much slower (1-5 tokens/sec on the same model)
    and may be unusable for chat.
  - Smaller models (e.g., `qwen2.5:0.5b`, `phi3:mini`) run acceptably
    on CPU but produce lower-quality responses.

## Integrations

- **Hardware integrations require real local hardware.**
  - Printer integration needs CUPS installed and a configured printer.
  - 3D printer integration needs OctoPrint or Moonraker running and
    reachable.
  - Smart Home integration needs a Home Assistant instance.
  - Spotify integration requires a Spotify Developer app and premium
    account for some features.
- **Price comparison uses web scraping** which can break if site layouts
  change. It is not guaranteed to work on all sites at all times.
- **Checkout integration is not configured by default.** Real payment
  processing requires a Stripe or equivalent gateway. Without it, the
  integration returns `not_implemented`.

## Content Generation

- **Image generation** (CogView-3 via Z.ai) takes 5-30 seconds per image.
  The free tier may have daily generation limits.
- **Video generation** (CogVideoX via Z.ai) takes 1-3 minutes per video.
  It is asynchronous — the API returns a task ID that must be polled.
- **Generated content quality varies.** LLM outputs are non-deterministic.
  The same prompt may produce different results each time.

## Voice

- **Wake word detection** requires a Picovoice access key and a
  microphone. The built-in keyword ("bumblebee") works without a custom
  .ppn file.
- **ElevenLabs TTS** requires an API key. Without it, FRIDAY falls back
  to pyttsx3 (lower quality) or console-only output.
- **Voice approval** (barge-in confirmation) requires both a microphone
  and speaker. It does not work in headless/text-only mode.

## Memory & Privacy

- **Without Supabase**, all data lives in-process memory and is lost on
  restart.
- **Memory export/import** works for in-memory data but does not
  currently export vector embeddings (only the raw text/facts).
- **Ollama is network-free** when configured correctly, but this must be
  verified per installation. The `scripts/ollama_network_test.py` script
  can confirm this.

## Security

- **The Ethical Sentinel uses keyword-based heuristics.** It is not a
  substitute for proper access controls or security audits. Sophisticated
  prompt injection may bypass pattern matching.
- **Action Ledger persistence** uses a local JSON file. It is not
  tamper-proof or encrypted.
- **API authentication** uses a single bearer token. There is no
  role-based access control or OAuth integration.

## MCP Server

- **The MCP server** exposes FRIDAY's action layer over a standard
  protocol, but it inherits all the limitations above. It does not add
  multi-user support or additional security layers.

---

*We prefer honest limitations over broken promises. If you find something
that works worse than documented, please open an issue.*
