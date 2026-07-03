#!/usr/bin/env python3
"""Smoke test — fast, runs every time.
Imports every package, constructs FridayBrain with dummy env vars,
confirms the FastAPI app builds, exits non-zero on first failure.
"""
import os
import sys

# Set dummy env vars BEFORE any imports
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("BRAIN_PROVIDER", "ollama")
os.environ.setdefault("FRIDAY_API_TOKEN", "smoke_test_token")
os.environ.setdefault("AUTONOMY_PROFILE", "GUEST")
os.environ.setdefault("GLM_API_KEY", "")

FAILURES = []

def check(label, fn):
    try:
        fn()
        print(f"  PASS  {label}")
    except Exception as e:
        FAILURES.append((label, str(e)))
        print(f"  FAIL  {label}: {e}")

def main():
    print("=" * 60)
    print("FRIDAY SMOKE TEST")
    print("=" * 60)

    # --- 1. Package imports ---
    print("\n[1] Package imports")
    for pkg in [
        "core", "core.brain", "core.memory", "core.emotions",
        "core.personality", "core.sentinel", "core.ledger",
        "core.scheduler", "core.context",
        "core.proactive", "core.onboarding",
        "core.glm_brain", "core.embeddings",
        "core.pattern_engine", "core.ambient", "core.team_mode",
        "core.multimodal_memory", "core.predictor", "core.persona",
        "agents", "agents.agent_manager", "agents.research_agent",
        "agents.coding_agent", "agents.writing_agent", "agents.task_agent",
        "agents.coding_orchestrator", "agents.tactical_manager",
        "config", "config.settings", "config.friday_identity", "config.identities",
        "integrations", "integrations.base", "integrations.registry",
        "integrations.weather", "integrations.crypto_tracker",
        "integrations.finance", "integrations.global_pulse",
        "integrations.smart_home", "integrations.spotify_integration",
        "integrations.gmail_integration", "integrations.calendar_integration",
        "integrations.image_gen", "integrations.video_gen",
        "database", "database.supabase_client", "database.vector_store",
        "database.compression", "database.subconscious",
        "skills", "skills.base",
        "voice", "voice.speaker", "voice.listener",
        "vision", "vision.screen_reader", "vision.ocr",
        "control", "control.pc_control", "control.browser_control",
        "control.file_manager", "control.workspace", "control.app_launcher",
        "api", "api.main",
        "api.routes", "api.routes.chat", "api.routes.memory",
        "api.routes.agents", "api.routes.integrations",
        "api.routes.actions", "api.routes.scheduler",
    ]:
        check(pkg, lambda p=pkg: __import__(p))

    # --- 2. Core object construction ---
    print("\n[2] Core object construction (no external services)")
    def make_memory():
        from core.memory import FridayMemory
        m = FridayMemory()
        m.store_conversation("user", "smoke test message")
        results = m.retrieve_relevant_memories("smoke test")
        assert len(results) >= 1, "memory retrieval returned nothing"

    def make_emotions():
        from core.emotions import EmotionsEngine
        e = EmotionsEngine()
        r1 = e.detect_emotion("I'm happy!")
        r2 = e.detect_emotion("I'm furious!")
        assert r1 != r2, f"emotions returned same result for happy vs furious: {r1}"

    def make_personality():
        from core.personality import FridayPersonality
        p = FridayPersonality()
        g = p.get_greeting("User")
        assert len(g) > 0, "empty greeting"

    def make_sentinel():
        from core.sentinel import EthicalSentinel
        s = EthicalSentinel()
        r = s.evaluate_action("delete all files")
        assert r["classification"] != "safe", "delete all files classified as safe!"

    def make_ledger():
        from core.ledger import ActionLedger
        l = ActionLedger()
        assert l is not None

    def make_brain():
        from core.brain import FridayBrain
        b = FridayBrain(provider="ollama")
        assert b.provider == "ollama"

    check("FridayMemory", make_memory)
    check("EmotionsEngine", make_emotions)
    check("FridayPersonality", make_personality)
    check("EthicalSentinel", make_sentinel)
    check("ActionLedger", make_ledger)
    check("FridayBrain (ollama)", make_brain)

    # --- 3. FastAPI app builds ---
    print("\n[3] FastAPI app builds")
    def make_app():
        from api.main import app
        from fastapi.routing import APIRoute, APIWebSocketRoute
        routes = []

        def collect_routes(router, prefix=""):
            """Recursively collect route paths, applying router prefixes.

            FastAPI's include_router() flattens sub-routes into app.routes
            already (the prefix is baked into each route's .path), but some
            older patterns keep them nested. We handle BOTH shapes:
            1. Flat: route.path is already '/api/chat'  (current FastAPI)
            2. Nested: route.routes contains APIRoutes  (older FastAPI)
            """
            for route in router.routes:
                # Sub-router case
                if hasattr(route, "routes") and not isinstance(route, (APIRoute, APIWebSocketRoute)):
                    sub_prefix = prefix + getattr(route, "prefix", "")
                    collect_routes(route, sub_prefix)
                    continue
                # Direct route — its .path already includes the prefix
                # that was passed to include_router(), so we use it as-is.
                path = getattr(route, "path", None)
                if path is None:
                    continue
                # If the path doesn't already start with the prefix (older
                # FastAPI versions), prepend it.
                full_path = path if path.startswith(prefix) else prefix + path
                routes.append(full_path)

        collect_routes(app)
        assert "/health" in routes, f"missing /health, found: {sorted(set(routes))}"
        assert any(r == "/api/chat" or r.startswith("/api/chat") for r in routes), \
            f"missing /api/chat, found: {sorted(set(routes))}"

    check("FastAPI app", make_app)

    # --- 4. Auth rejects unauthenticated ---
    print("\n[4] Auth rejection")
    def test_auth_reject():
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        r = client.post("/api/chat", json={"message": "test"})
        assert r.status_code == 403, f"expected 403, got {r.status_code}"

    check("Unauthenticated → 403", test_auth_reject)

    # --- Summary ---
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} / {len(FAILURES) + 4 + 6 + 1 + 1} checks")
        for label, err in FAILURES:
            print(f"  - {label}: {err}")
        sys.exit(1)
    else:
        print("ALL SMOKE CHECKS PASSED")
        sys.exit(0)

if __name__ == "__main__":
    main()
