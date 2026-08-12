"""Trust Report API Route — re-run hellfire audit and return trust status.

Endpoints:
    POST /api/trust/report  — re-run the full hellfire audit and return results
    GET  /api/trust/status   — return the current trust status (last audit result)
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["trust"])

# ---------------------------------------------------------------------------
# In-memory cache of the last audit result
# ---------------------------------------------------------------------------
_last_audit: Optional[Dict[str, Any]] = None


def _run_hellfire_audit() -> Dict[str, Any]:
    """Execute the hellfire audit script and collect results.

    We import and call the check functions directly rather than
    shelling out, so that we can capture structured results.
    """
    results: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "checks": [],
        "total_checks": 0,
        "passed": 0,
        "failed": 0,
        "failures": [],
    }

    try:
        # Ensure the project root is on sys.path so the audit can import modules
        project_root = str(Path(__file__).resolve().parent.parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        # Lazy import via importlib — the API layer should not statically
        # depend on the scripts layer (scripts is a top-level utility layer,
        # not a library). Loaded on demand when an audit is requested.
        import importlib
        _audit_mod = importlib.import_module("scripts.hellfire_audit")
        check_commented_out_calls = _audit_mod.check_commented_out_calls
        check_eager_client_construction = _audit_mod.check_eager_client_construction
        check_double_run = _audit_mod.check_double_run
        check_auth_rejection = _audit_mod.check_auth_rejection
        check_never_auto_approve = _audit_mod.check_never_auto_approve
        check_no_hardcoded_secrets = _audit_mod.check_no_hardcoded_secrets
        check_glm_key_from_env = _audit_mod.check_glm_key_from_env
        check_zhipu_client_guarded = _audit_mod.check_zhipu_client_guarded
        FAILURES = _audit_mod.FAILURES

        # Reset the global FAILURES list before re-running
        FAILURES.clear()

        check_functions = [
            ("commented_out_calls", check_commented_out_calls),
            ("eager_client_construction", check_eager_client_construction),
            ("double_run", check_double_run),
            ("auth_rejection", check_auth_rejection),
            ("never_auto_approve", check_never_auto_approve),
            ("no_hardcoded_secrets", check_no_hardcoded_secrets),
            ("glm_key_from_env", check_glm_key_from_env),
            ("zhipu_client_guarded", check_zhipu_client_guarded),
        ]

        for name, func in check_functions:
            try:
                func()
                passed = True
            except Exception as exc:
                logger.error("Trust audit check '%s' raised: %s", name, exc)
                passed = False
                FAILURES.append((name, str(exc)))

            results["checks"].append({
                "name": name,
                "passed": passed,
            })
            results["total_checks"] += 1

        results["failed"] = len(FAILURES)
        results["passed"] = results["total_checks"] - results["failed"]
        results["failures"] = [
            {"label": label, "detail": detail} for label, detail in FAILURES
        ]

    except ImportError as exc:
        logger.error("Cannot import hellfire_audit: %s", exc)
        results["error"] = f"Audit module import failed: {exc}"
    except Exception as exc:
        logger.error("Hellfire audit error: %s", exc)
        results["error"] = f"Audit execution failed: {exc}"

    return results


@router.post("/trust/report")
async def trust_report():
    """Re-run the hellfire audit and return the full results."""
    global _last_audit
    report = _run_hellfire_audit()
    _last_audit = report
    return report


@router.get("/trust/report")
async def trust_report_get():
    """GET alias for /trust/report — runs the audit fresh on each call."""
    global _last_audit
    report = _run_hellfire_audit()
    _last_audit = report
    return report


@router.get("/trust/status")
async def trust_status():
    """Return the current trust status from the last audit.

    If no audit has been run yet, returns a placeholder.
    """
    if _last_audit is None:
        return {
            "status": "no_audit_run",
            "message": "No audit has been run yet. POST /api/trust/report to run one.",
            "timestamp": None,
        }

    return {
        "status": "pass" if _last_audit.get("failed", 1) == 0 else "fail",
        "total_checks": _last_audit.get("total_checks", 0),
        "passed": _last_audit.get("passed", 0),
        "failed": _last_audit.get("failed", 0),
        "failures": _last_audit.get("failures", []),
        "timestamp": _last_audit.get("timestamp"),
    }
