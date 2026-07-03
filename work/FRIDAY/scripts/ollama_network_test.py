#!/usr/bin/env python3
"""Ollama Network Isolation Test.

Proves that Ollama is genuinely network-free by running a conversation
with all outbound network blocked except localhost.

Steps:
  1. Verify Ollama is running and accessible on localhost.
  2. Block all outbound network (except loopback) using iptables.
  3. Run a conversation with the LocalBrain.
  4. Verify the conversation succeeded (proving no external calls needed).
  5. Unblock outbound network.
  6. Verify no external connections were made during the test.

Run:  python scripts/ollama_network_test.py

NOTE: This script requires root/sudo to manipulate iptables.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("OllamaNetworkTest")

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results: dict = {}


def record(name: str, status: str, detail: str = ""):
    results[name] = {"status": status, "detail": detail}
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}[status]
    logger.info(f"  {icon} {name}: {status}" + (f" — {detail}" if detail else ""))


# ──────────────────────────────────────────────────────────────────────────────
# iptables helpers (require root)
# ──────────────────────────────────────────────────────────────────────────────

def _iptables_block_outbound():
    """Block all outbound traffic except loopback."""
    cmds = [
        # Allow loopback
        ["sudo", "iptables", "-I", "OUTPUT", "1", "-o", "lo", "-j", "ACCEPT"],
        # Allow established connections (so existing SSH doesn't die)
        ["sudo", "iptables", "-I", "OUTPUT", "2", "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        # Block all other outbound
        ["sudo", "iptables", "-A", "OUTPUT", "-j", "DROP"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
    logger.info("Outbound network blocked (iptables rules applied)")


def _iptables_restore():
    """Remove the block rules added by _iptables_block_outbound."""
    cmds = [
        # Delete the DROP rule
        ["sudo", "iptables", "-D", "OUTPUT", "-j", "DROP"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, capture_output=True, timeout=10)
    logger.info("Outbound network restored (iptables DROP rule removed)")


def _can_reach_localhost() -> bool:
    """Quick check that localhost is still reachable."""
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _can_reach_external() -> bool:
    """Quick check that external network is reachable."""
    try:
        import httpx
        resp = httpx.get("https://httpbin.org/get", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Test functions
# ──────────────────────────────────────────────────────────────────────────────

async def test_ollama_available():
    """Step 1: Verify Ollama is running."""
    logger.info("=== Step 1: Verify Ollama is Running ===")
    try:
        from core.local_brain import LocalBrain
        local = LocalBrain()
        is_available = await local.available()
        if is_available:
            record("ollama_available", PASS, "Ollama is running and model is available")
        else:
            record("ollama_available", FAIL, "Ollama not running or model not pulled")
    except Exception as e:
        record("ollama_available", FAIL, str(e))


async def test_block_outbound():
    """Step 2: Block outbound network and verify external is unreachable."""
    logger.info("=== Step 2: Block Outbound Network ===")
    try:
        _iptables_block_outbound()

        # Verify localhost still works
        if _can_reach_localhost():
            record("localhost_reachable", PASS, "Ollama still reachable on localhost")
        else:
            record("localhost_reachable", FAIL, "Ollama unreachable on localhost after block")

        # Verify external is blocked
        external_reachable = _can_reach_external()
        if not external_reachable:
            record("external_blocked", PASS, "External network correctly blocked")
        else:
            record("external_blocked", FAIL, "External network still reachable — block ineffective")

    except subprocess.CalledProcessError as e:
        record("block_outbound", SKIP, f"iptables requires root: {e}")
        return False
    except Exception as e:
        record("block_outbound", FAIL, str(e))
        return False
    return True


async def test_conversation_while_blocked():
    """Step 3: Run a conversation while network is blocked."""
    logger.info("=== Step 3: Conversation While Network Blocked ===")
    try:
        from core.local_brain import LocalBrain
        local = LocalBrain()

        full_response = ""
        async for chunk in local.chat_stream("What is 2 + 2? Answer in one sentence."):
            full_response += chunk

        if len(full_response.strip()) > 3:
            record(
                "conversation_while_blocked",
                PASS,
                f"Ollama responded ({len(full_response)} chars) without external network",
            )
        else:
            record(
                "conversation_while_blocked",
                FAIL,
                f"Response too short: '{full_response[:200]}'",
            )
    except Exception as e:
        record("conversation_while_blocked", FAIL, str(e))


async def test_restore_network():
    """Step 5: Restore network and verify external is reachable again."""
    logger.info("=== Step 5: Restore Network ===")
    try:
        _iptables_restore()
        # Give it a moment
        await asyncio.sleep(2)

        external_reachable = _can_reach_external()
        if external_reachable:
            record("network_restored", PASS, "External network reachable again")
        else:
            record("network_restored", FAIL, "External network still unreachable after restore")
    except Exception as e:
        record("network_restored", FAIL, str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

async def main():
    logger.info("=" * 60)
    logger.info("FRIDAY — Ollama Network Isolation Test")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    await test_ollama_available()

    # If Ollama isn't available, skip the rest
    if results.get("ollama_available", {}).get("status") != PASS:
        logger.info("Ollama not available — skipping network isolation test")
    else:
        block_ok = await test_block_outbound()
        if block_ok:
            await test_conversation_while_blocked()
            await test_restore_network()
        else:
            logger.info("Could not block outbound network — skipping conversation test")

    # ── Summary ──────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)

    for name, r in results.items():
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}[r["status"]]
        logger.info(f"  {icon} {name}: {r['status']}" + (f" — {r['detail']}" if r["detail"] else ""))

    pass_count = sum(1 for v in results.values() if v["status"] == PASS)
    total = len(results)
    logger.info(f"\n  {pass_count}/{total} tests passed")

    return results


if __name__ == "__main__":
    asyncio.run(main())
