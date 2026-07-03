#!/usr/bin/env python3
"""End-to-end Chat Test for FRIDAY AI Assistant.

Tests the full pipeline:
  - Brain init with different BRAIN_PROVIDER values (glm, claude, local)
  - Chat streaming produces real (non-empty) responses
  - Memory storage after chat
  - Receipt generation via the action ledger

Run:  python scripts/e2e_chat_test.py
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("E2E_Chat_Test")

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results: dict = {}


def record(name: str, status: str, detail: str = ""):
    results[name] = {"status": status, "detail": detail}
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}[status]
    logger.info(f"  {icon} {name}: {status}" + (f" — {detail}" if detail else ""))


# ──────────────────────────────────────────────────────────────────────────────
# Test 1: Brain provider switching (glm / claude / local)
# ──────────────────────────────────────────────────────────────────────────────

async def test_provider_switching():
    """Verify FridayBrain can be constructed with each provider setting."""
    logger.info("=== Test 1: BRAIN_PROVIDER Switching ===")

    original = os.environ.get("BRAIN_PROVIDER", "")

    for provider in ("glm", "claude", "local"):
        os.environ["BRAIN_PROVIDER"] = provider
        try:
            from core.brain import FridayBrain
            brain = FridayBrain()
            stats = brain.get_stats()
            reported = stats.get("provider", "unknown")
            logger.info(f"  Provider={provider} → brain reports provider={reported}")
            record(f"provider_{provider}", PASS, f"brain provider={reported}")
        except Exception as e:
            record(f"provider_{provider}", FAIL, str(e))

    # Restore original
    if original:
        os.environ["BRAIN_PROVIDER"] = original
    else:
        os.environ.pop("BRAIN_PROVIDER", None)


# ──────────────────────────────────────────────────────────────────────────────
# Test 2: GLM streaming chat
# ──────────────────────────────────────────────────────────────────────────────

async def test_glm_chat():
    """Attempt a real GLM chat and verify a non-trivial response."""
    logger.info("=== Test 2: GLM Native Streaming Chat ===")

    try:
        from core.glm_brain import GLMBrain
        glm = GLMBrain()
    except Exception as e:
        record("glm_chat", SKIP, f"Cannot import GLMBrain: {e}")
        return

    if not glm.available():
        record("glm_chat", SKIP, "GLMBrain not available (no API key or package)")
        return

    full_response = ""
    try:
        async for chunk in glm.chat_stream("Say hello in one sentence."):
            full_response += chunk
    except Exception as e:
        record("glm_chat", FAIL, f"Stream error: {e}")
        return

    if len(full_response.strip()) < 5:
        record("glm_chat", FAIL, f"Response too short or empty: '{full_response[:200]}'")
    else:
        record("glm_chat", PASS, f"Response length={len(full_response)} chars")


# ──────────────────────────────────────────────────────────────────────────────
# Test 3: Local (Ollama) chat
# ──────────────────────────────────────────────────────────────────────────────

async def test_local_chat():
    """Attempt a local Ollama chat and verify a non-trivial response."""
    logger.info("=== Test 3: Local (Ollama) Chat ===")

    try:
        from core.local_brain import LocalBrain
        local = LocalBrain()
    except Exception as e:
        record("local_chat", SKIP, f"Cannot import LocalBrain: {e}")
        return

    is_available = await local.available()
    if not is_available:
        record("local_chat", SKIP, "Ollama not running or model not pulled")
        return

    full_response = ""
    try:
        async for chunk in local.chat_stream("Say hello in one sentence."):
            full_response += chunk
    except Exception as e:
        record("local_chat", FAIL, f"Stream error: {e}")
        return

    if len(full_response.strip()) < 5:
        record("local_chat", FAIL, f"Response too short or empty: '{full_response[:200]}'")
    else:
        record("local_chat", PASS, f"Response length={len(full_response)} chars")


# ──────────────────────────────────────────────────────────────────────────────
# Test 4: Memory storage after chat
# ──────────────────────────────────────────────────────────────────────────────

async def test_memory_storage():
    """Store a conversation and verify memory retrieval."""
    logger.info("=== Test 4: Memory Storage After Chat ===")

    try:
        from core.memory import FridayMemory
        mem = FridayMemory()
    except Exception as e:
        record("memory_storage", FAIL, f"Cannot create FridayMemory: {e}")
        return

    # Store a unique conversation
    uid = uuid.uuid4().hex[:8]
    user_msg = f"My name is TestUser-{uid} and I live in TestCity."
    assistant_msg = f"Hello TestUser-{uid}, TestCity sounds great!"

    try:
        mem.store_conversation("user", user_msg)
        mem.store_conversation("assistant", assistant_msg)
    except Exception as e:
        record("memory_storage", FAIL, f"store_conversation error: {e}")
        return

    # Retrieve memories
    try:
        results_list = mem.retrieve_relevant_memories(f"TestUser-{uid}")
    except Exception as e:
        record("memory_storage", FAIL, f"retrieve_relevant_memories error: {e}")
        return

    if not results_list:
        record("memory_storage", FAIL, "No memories retrieved for known conversation")
        return

    # Verify the stored content appears in results
    found = any(uid in str(m) for m in results_list)
    if found:
        record("memory_storage", PASS, f"Found stored memory for uid={uid}")
    else:
        record("memory_storage", FAIL, f"Memory stored but not retrieved for uid={uid}")


# ──────────────────────────────────────────────────────────────────────────────
# Test 5: Receipt generation
# ──────────────────────────────────────────────────────────────────────────────

async def test_receipt_generation():
    """Queue an action through the ledger and verify receipt data."""
    logger.info("=== Test 5: Receipt Generation ===")

    try:
        from core.ledger import ActionLedger
        ledger = ActionLedger()
    except Exception as e:
        record("receipt_generation", FAIL, f"Cannot create ActionLedger: {e}")
        return

    # Queue a low-risk action that should auto-approve under STANDARD
    original_profile = os.environ.get("AUTONOMY_PROFILE", "")
    os.environ["AUTONOMY_PROFILE"] = "STANDARD"
    ledger.profile = "STANDARD"

    action_id = ledger.queue_action(
        component="Weather",
        action="get_weather",
        params={"location": "Lagos"},
        risk_level="low",
    )

    if not action_id:
        record("receipt_generation", FAIL, "queue_action returned no action_id")
        os.environ["AUTONOMY_PROFILE"] = original_profile
        return

    # Check the action exists in pending_actions
    action_data = ledger.pending_actions.get(action_id)
    if not action_data:
        record("receipt_generation", FAIL, f"Action {action_id} not found in ledger")
        os.environ["AUTONOMY_PROFILE"] = original_profile
        return

    # Verify receipt fields
    has_id = bool(action_data.get("id"))
    has_component = action_data.get("component") == "Weather"
    has_action = action_data.get("action") == "get_weather"
    has_timestamp = bool(action_data.get("timestamp"))
    status_val = action_data.get("status")

    if has_id and has_component and has_action and has_timestamp:
        record(
            "receipt_generation",
            PASS,
            f"action_id={action_id[:8]}… status={status_val}",
        )
    else:
        record(
            "receipt_generation",
            FAIL,
            f"Missing fields: id={has_id} comp={has_component} "
            f"action={has_action} ts={has_timestamp}",
        )

    os.environ["AUTONOMY_PROFILE"] = original_profile


# ──────────────────────────────────────────────────────────────────────────────
# Test 6: GLM Web Search
# ──────────────────────────────────────────────────────────────────────────────

async def test_glm_web_search():
    """Test GLM web search returns real results."""
    logger.info("=== Test 6: GLM Web Search ===")

    try:
        from core.glm_brain import GLMBrain
        glm = GLMBrain()
    except Exception as e:
        record("glm_web_search", SKIP, f"Cannot import GLMBrain: {e}")
        return

    if not glm.available():
        record("glm_web_search", SKIP, "GLMBrain not available")
        return

    try:
        search_results = await glm.web_search("current weather in Lagos Nigeria")
    except Exception as e:
        record("glm_web_search", FAIL, f"web_search error: {e}")
        return

    if not search_results:
        record("glm_web_search", FAIL, "web_search returned empty results")
    else:
        record("glm_web_search", PASS, f"Got {len(search_results)} results")


# ──────────────────────────────────────────────────────────────────────────────
# Test 7: GLM Code Interpreter
# ──────────────────────────────────────────────────────────────────────────────

async def test_glm_code_interpreter():
    """Test GLM code interpreter."""
    logger.info("=== Test 7: GLM Code Interpreter ===")

    try:
        from core.glm_brain import GLMBrain
        glm = GLMBrain()
    except Exception as e:
        record("glm_code_interpreter", SKIP, f"Cannot import GLMBrain: {e}")
        return

    if not glm.available():
        record("glm_code_interpreter", SKIP, "GLMBrain not available")
        return

    try:
        result = await glm.code_interpreter("print(2 + 2)")
    except Exception as e:
        record("glm_code_interpreter", FAIL, f"code_interpreter error: {e}")
        return

    if result.get("status") == "success":
        record("glm_code_interpreter", PASS, f"output: {str(result.get('output', ''))[:80]}")
    else:
        record("glm_code_interpreter", FAIL, f"status={result.get('status')} err={result.get('error')}")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

async def main():
    logger.info("=" * 60)
    logger.info("FRIDAY — End-to-End Chat Test (Z.ai Native Streaming)")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    await test_provider_switching()
    await test_glm_chat()
    await test_local_chat()
    await test_memory_storage()
    await test_receipt_generation()
    await test_glm_web_search()
    await test_glm_code_interpreter()

    # ── Summary ──────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)

    pass_count = sum(1 for v in results.values() if v["status"] == PASS)
    skip_count = sum(1 for v in results.values() if v["status"] == SKIP)
    fail_count = sum(1 for v in results.values() if v["status"] == FAIL)
    total = len(results)

    for name, r in results.items():
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}[r["status"]]
        logger.info(f"  {icon} {name}: {r['status']}" + (f" — {r['detail']}" if r["detail"] else ""))

    logger.info(f"\n  {pass_count}/{total} passed, {skip_count} skipped, {fail_count} failed")

    return results


if __name__ == "__main__":
    asyncio.run(main())
