# Project FRIDAY - Remediation & Build Changelog

## Phase 4 — Z.ai Native SDK Integration (v5.1)

### Headline Changes
- **Z.ai Native Integration**: GLM-4 series (free-tier default), CogView-3 image generation, CogVideoX video generation, and Z.ai web search replace Tavily.
- **Free Out-of-the-Box**: FRIDAY now works with zero paid API keys via the Z.ai free tier.
- **Embedding-3 API**: Z.ai Embedding-3 replaces sentence-transformers for vector embeddings, with hash-based fallback for offline use.
- **Safety Improvements**: Never-auto-approve set for ImageGen, VideoGen, CodeExecution, Printer, Printer3D components.
- **Deprecation Fixes**: Replaced all `datetime.utcnow()` with `datetime.now(timezone.utc)`.
- **Theatrical Naming Purge**: Removed "Singularity", "SWAT", "Ghost-Mode Stealth Uplink", "Neural Cross-Pollination", "Recursive Modification Protocol", "4D timelines", "Nexus Cinema HUD", "FridayApexOrchestrator" from all files.

### Regressions & Adversarial Fixes (Round 4)
- **Smoke Test**: Fixed route introspection bug — now uses `getattr(route, 'path', None)` and handles included routers.
- **Hellfire Audit**: Added Check 7 (GLM_API_KEY never hardcoded) and Check 8 (all Z.ai clients guarded by key check).

## Phase 3 - Build Changelog

### Headline Features
- **Feature 1: Action Receipts**: Every system action now returns a structured 'receipt' with real API payloads or screenshot paths.
- **Feature 2: Human-in-the-Loop Ledger**: Sensitive actions are queued for manual approval via '/api/actions/approve'.
- **Feature 3: One-Command Docker Install**: setup.sh + Docker Compose for 2-minute deployment.
- **Feature 4: Local Fallback (Ollama)**: Automatic redirection to local Llama3 if cloud providers are offline.
- **Feature 5: Honest Benchmarks**: Comprehensive task suite (tasks.json) with real brain-execution logging.
- **Feature 6: Memory Inspector**: CRUD access to vector memory via authenticated API and Web UI.
- **Feature 7: Plugin SDK**: Standardized BaseIntegration interface with automatic discovery.
- **Feature 8: Barge-in Voice**: Interruptible speech synthesis via signal latching.

### Regressions & Adversarial Fixes (Round 3)
- **Spotify Integration**: Restored real Spotipy search/playback calls.
- **Benchmark Runner**: Now correctly invokes FridayBrain; results reflect real API attempts.
- **.env.example**: Restored all core variables; verified survival through setup.sh prompt flow.
