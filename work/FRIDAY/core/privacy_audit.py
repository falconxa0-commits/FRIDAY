"""Privacy Audit Engine — what does Friday know about you, where did your data go?

Reports:
- All facts extracted from conversations
- Every API call made and what data was sent
- Which providers received your messages
- What's stored locally vs. in Supabase
- Option to purge provider history
"""
import datetime
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class PrivacyAuditEngine:
    """Answers honestly: what does Friday know, and where did your data go?"""

    def __init__(self):
        pass

    async def generate_privacy_report(self) -> dict:
        """Generate a complete privacy report from real data."""
        report = {
            "timestamp": datetime.datetime.now().isoformat(),
            "facts_known": {},
            "api_calls": {},
            "providers_used": [],
            "storage": {},
            "data_volume_estimate": {},
        }

        # 1. Facts extracted from conversations
        try:
            from core.memory import FridayMemory
            mem = FridayMemory()
            facts = getattr(mem, "_session_facts", {})
            report["facts_known"] = {
                "total_facts": len(facts),
                "categories": list(set(k.split(":")[0] for k in facts.keys())),
                "fact_keys": list(facts.keys())[:20],
            }
        except Exception:
            report["facts_known"] = {"error": "Memory unavailable"}

        # 2. API calls from audit log
        try:
            from core.ledger import get_ledger
            ledger = get_ledger()
            entries = ledger.get_audit_log()
            providers = set()
            components = {}
            for e in entries:
                comp = e.get("component", "unknown")
                components[comp] = components.get(comp, 0) + 1
            report["api_calls"] = {
                "total_logged": len(entries),
                "by_component": components,
            }
        except Exception:
            report["api_calls"] = {"error": "Ledger unavailable"}

        # 3. Providers used (from stats)
        try:
            from api.routes.stats import _request_log
            providers = set(r.get("provider", "?") for r in _request_log)
            report["providers_used"] = list(providers)
            report["data_volume_estimate"] = {
                "total_requests": len(_request_log),
                "total_tokens_in": sum(r.get("tokens_in", 0) for r in _request_log),
                "total_tokens_out": sum(r.get("tokens_out", 0) for r in _request_log),
            }
        except Exception:
            report["providers_used"] = []

        # 4. Storage status
        import os
        report["storage"] = {
            "supabase_configured": bool(os.getenv("SUPABASE_URL")),
            "local_memories": len(getattr(FridayMemory(), "_memories", [])) if True else 0,
            "audit_chain_file": os.path.exists("action_ledger_chain.json"),
            "pending_actions_file": os.path.exists("action_ledger_pending.json"),
        }

        return report

    async def get_data_sent_to_provider(self, provider: str) -> list:
        """Return all requests sent to a specific provider."""
        try:
            from api.routes.stats import _request_log
            return [r for r in _request_log if r.get("provider") == provider]
        except Exception:
            return []

    async def purge_provider_history(self, provider: str) -> dict:
        """Purge all history for a specific provider from local logs."""
        try:
            from api.routes.stats import _request_log
            original = len(_request_log)
            _request_log[:] = [r for r in _request_log if r.get("provider") != provider]
            purged = original - len(_request_log)
            return {
                "status": "success",
                "provider": provider,
                "entries_purged": purged,
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
