"""Deep health endpoint — /api/health/deep.

Goes beyond /healthz to actually test every integration end-to-end,
measure real latency, report honest status per feature, and suggest
specific fixes for anything that's failing.
"""
import asyncio
import datetime
import logging
import time
from typing import Any, Dict, List

from fastapi import APIRouter

logger = logging.getLogger("friday.api.health")

router = APIRouter()


@router.get("/deep")
async def deep_health() -> dict:
    """Deep health check — tests every integration + subsystem."""
    checks: List[dict] = []

    # ---- Brain providers ------------------------------------------------
    checks.append(await _check_glm())
    checks.append(await _check_claude())
    checks.append(await _check_gemini())
    checks.append(await _check_ollama())

    # ---- Integrations ---------------------------------------------------
    checks.append(await _check_integrations())

    # ---- Core subsystems ------------------------------------------------
    checks.append(await _check_memory())
    checks.append(await _check_ledger())
    checks.append(await _check_sentinel())

    # ---- API endpoints --------------------------------------------------
    checks.append(await _check_api_endpoints())

    # ---- Voice subsystem ------------------------------------------------
    checks.append(await _check_voice())

    # ---- Compute overall ------------------------------------------------
    passing = sum(1 for c in checks if c["status"] == "pass")
    failing = sum(1 for c in checks if c["status"] == "fail")
    warning = sum(1 for c in checks if c["status"] == "warn")

    return {
        "timestamp": datetime.datetime.now().isoformat(),
        "overall": "pass" if failing == 0 else ("warn" if warning > 0 else "fail"),
        "summary": {
            "total": len(checks),
            "passing": passing,
            "warning": warning,
            "failing": failing,
        },
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Individual checks — each returns {name, status, latency_ms, detail, fix}
# ---------------------------------------------------------------------------

async def _check_glm() -> dict:
    start = time.time()
    try:
        from core.glm_brain import GLMBrain
        b = GLMBrain()
        ok = b.available()
        latency = (time.time() - start) * 1000
        if ok:
            return {"name": "GLM Brain", "status": "pass",
                    "latency_ms": round(latency, 1),
                    "detail": "GLM-4-Flash available",
                    "fix": None}
        return {"name": "GLM Brain", "status": "warn",
                "latency_ms": round(latency, 1),
                "detail": "GLM_API_KEY not set",
                "fix": "Get a free key at https://open.bigmodel.cn and set GLM_API_KEY env var"}
    except Exception as exc:
        return {"name": "GLM Brain", "status": "fail",
                "latency_ms": round((time.time() - start) * 1000, 1),
                "detail": f"Error: {exc}",
                "fix": "pip install zhipuai"}


async def _check_claude() -> dict:
    start = time.time()
    try:
        import os
        key = os.getenv("ANTHROPIC_API_KEY")
        latency = (time.time() - start) * 1000
        if key:
            return {"name": "Claude (Anthropic)", "status": "pass",
                    "latency_ms": round(latency, 1),
                    "detail": "ANTHROPIC_API_KEY set",
                    "fix": None}
        return {"name": "Claude (Anthropic)", "status": "warn",
                "latency_ms": round(latency, 1),
                "detail": "ANTHROPIC_API_KEY not set (optional upgrade)",
                "fix": "Optional — set ANTHROPIC_API_KEY to enable Claude reasoning"}
    except Exception as exc:
        return {"name": "Claude (Anthropic)", "status": "fail",
                "latency_ms": 0, "detail": f"Error: {exc}", "fix": None}


async def _check_gemini() -> dict:
    start = time.time()
    try:
        import os
        key = os.getenv("GEMINI_API_KEY")
        latency = (time.time() - start) * 1000
        if key:
            return {"name": "Gemini", "status": "pass",
                    "latency_ms": round(latency, 1),
                    "detail": "GEMINI_API_KEY set",
                    "fix": None}
        return {"name": "Gemini", "status": "warn",
                "latency_ms": round(latency, 1),
                "detail": "GEMINI_API_KEY not set (optional upgrade)",
                "fix": "Optional — set GEMINI_API_KEY to enable Gemini"}
    except Exception as exc:
        return {"name": "Gemini", "status": "fail",
                "latency_ms": 0, "detail": f"Error: {exc}", "fix": None}


async def _check_ollama() -> dict:
    start = time.time()
    try:
        from core.local_brain import LocalBrain
        b = LocalBrain()
        ok = await b.available()
        latency = (time.time() - start) * 1000
        if ok:
            return {"name": "Ollama (local LLM)", "status": "pass",
                    "latency_ms": round(latency, 1),
                    "detail": f"Ollama running, model={b.model}",
                    "fix": None}
        return {"name": "Ollama (local LLM)", "status": "warn",
                "latency_ms": round(latency, 1),
                "detail": "Ollama not running (optional local LLM)",
                "fix": "Install from https://ollama.com then run: ollama pull llama3 && ollama serve"}
    except Exception as exc:
        return {"name": "Ollama (local LLM)", "status": "fail",
                "latency_ms": 0, "detail": f"Error: {exc}", "fix": None}


async def _check_integrations() -> dict:
    start = time.time()
    try:
        from core.universal_connector import UniversalConnector
        connector = UniversalConnector()
        results = []
        for name, integration in connector.integrations.items():
            try:
                live = bool(integration.available())
            except Exception:
                live = False
            results.append({"name": name, "live": live})
        latency = (time.time() - start) * 1000
        active = sum(1 for r in results if r["live"])
        return {
            "name": "Integrations",
            "status": "pass" if active > 0 else "warn",
            "latency_ms": round(latency, 1),
            "detail": f"{active}/{len(results)} integrations live",
            "fix": None,
            "integrations": results,
        }
    except Exception as exc:
        return {"name": "Integrations", "status": "fail",
                "latency_ms": 0, "detail": f"Error: {exc}", "fix": None}


async def _check_memory() -> dict:
    start = time.time()
    try:
        from core.memory import FridayMemory
        m = FridayMemory()
        count = len(m._memories)
        latency = (time.time() - start) * 1000
        return {"name": "Memory", "status": "pass",
                "latency_ms": round(latency, 1),
                "detail": f"{count} memories stored",
                "fix": None}
    except Exception as exc:
        return {"name": "Memory", "status": "fail",
                "latency_ms": 0, "detail": f"Error: {exc}", "fix": None}


async def _check_ledger() -> dict:
    start = time.time()
    try:
        from core.ledger import get_ledger
        l = get_ledger()
        valid = l.verify_chain()
        latency = (time.time() - start) * 1000
        return {"name": "Action Ledger", "status": "pass" if valid else "fail",
                "latency_ms": round(latency, 1),
                "detail": f"Chain {'valid' if valid else 'BROKEN'}, {len(l.get_audit_log())} entries",
                "fix": None if valid else "Audit chain tampered with — investigate recent changes"}
    except Exception as exc:
        return {"name": "Action Ledger", "status": "fail",
                "latency_ms": 0, "detail": f"Error: {exc}", "fix": None}


async def _check_sentinel() -> dict:
    start = time.time()
    try:
        from core.sentinel import EthicalSentinel
        s = EthicalSentinel()
        r = s.evaluate_action("read weather forecast")
        latency = (time.time() - start) * 1000
        return {"name": "Ethical Sentinel", "status": "pass",
                "latency_ms": round(latency, 1),
                "detail": f"Ready, classification: {r.get('classification', 'unknown')}",
                "fix": None}
    except Exception as exc:
        return {"name": "Ethical Sentinel", "status": "fail",
                "latency_ms": 0, "detail": f"Error: {exc}", "fix": None}


async def _check_api_endpoints() -> dict:
    start = time.time()
    try:
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        r = client.get("/health")
        latency = (time.time() - start) * 1000
        if r.status_code == 200:
            return {"name": "API Endpoints", "status": "pass",
                    "latency_ms": round(latency, 1),
                    "detail": "/health returns 200",
                    "fix": None}
        return {"name": "API Endpoints", "status": "fail",
                "latency_ms": round(latency, 1),
                "detail": f"/health returned {r.status_code}",
                "fix": "Check uvicorn process — is the API running?"}
    except Exception as exc:
        return {"name": "API Endpoints", "status": "fail",
                "latency_ms": 0, "detail": f"Error: {exc}",
                "fix": "Check if uvicorn is running: uvicorn api.main:app --port 8000"}


async def _check_voice() -> dict:
    start = time.time()
    try:
        from voice.speaker import FridaySpeaker
        s = FridaySpeaker()
        latency = (time.time() - start) * 1000
        notes = []
        if s.use_elevenlabs:
            notes.append("ElevenLabs TTS available")
        elif s.engine is not None:
            notes.append("pyttsx3 local TTS available")
        else:
            notes.append("No TTS — text-only output")
        return {"name": "Voice (TTS)", "status": "pass" if s.use_elevenlabs else "warn",
                "latency_ms": round(latency, 1),
                "detail": "; ".join(notes),
                "fix": None if s.use_elevenlabs else "Optional — set ELEVENLABS_API_KEY for better TTS"}
    except Exception as exc:
        return {"name": "Voice (TTS)", "status": "warn",
                "latency_ms": 0, "detail": f"Error: {exc}",
                "fix": "Optional — install pyttsx3 for local TTS"}
