"""Cost Tracker — Track token usage and estimated costs per provider.

Real published per-token rates (as of 2024-2025):
    GLM (Z.ai free tier):  $0.00 / 1K tokens  (input & output)
    Claude (Sonnet):       $0.003 / 1K input   $0.015 / 1K output
    GPT-4o:                $0.0015 / 1K input  $0.006 / 1K output
    Gemini 2.0 Flash:      $0.000075 / 1K input $0.0003 / 1K output

Data is persisted to a JSON file so that costs accumulate across restarts.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table  (USD per 1K tokens)
# ---------------------------------------------------------------------------
RATES: Dict[str, Dict[str, float]] = {
    "glm": {
        "input_per_1k": 0.0,
        "output_per_1k": 0.0,
    },
    "claude": {
        "input_per_1k": 0.003,
        "output_per_1k": 0.015,
    },
    "gpt": {
        "input_per_1k": 0.0015,
        "output_per_1k": 0.006,
    },
    "gemini": {
        "input_per_1k": 0.000075,
        "output_per_1k": 0.0003,
    },
}

# Default persistence path
_DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "cost_tracker_data.json"


class CostTracker:
    """Track token counts and compute estimated costs per provider.

    Usage::

        tracker = CostTracker()
        tracker.record_usage("glm", input_tokens=120, output_tokens=85)
        tracker.record_usage("claude", input_tokens=200, output_tokens=150)
        report = tracker.get_cost_report()
    """

    def __init__(self, data_path: Optional[str] = None):
        self._data_path = Path(data_path) if data_path else _DEFAULT_DATA_PATH
        self._data: Dict[str, Any] = {
            "providers": {},
            # Timezone-aware UTC ISO timestamps (replaces deprecated
            # datetime.utcnow() — see WAVE2-REFAC).
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Batch write support: instead of saving on every record_usage()
        # call, we mark dirty and flush periodically or on demand.
        self._dirty: bool = False
        self._dirty_count: int = 0
        self._FLUSH_THRESHOLD: int = 10  # flush after 10 records
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        """Load persisted data from the JSON file."""
        if not self._data_path.exists():
            return
        try:
            with open(self._data_path, "r") as f:
                stored = json.load(f)
            if isinstance(stored, dict) and "providers" in stored:
                self._data = stored
                logger.info("CostTracker: loaded data from %s", self._data_path)
        except Exception as exc:
            logger.warning("CostTracker: failed to load data: %s", exc)

    def _save(self):
        """Persist current data to the JSON file."""
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            with open(self._data_path, "w") as f:
                json.dump(self._data, f, indent=2, default=str)
        except Exception as exc:
            logger.warning("CostTracker: failed to save data: %s", exc)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_usage(
        self,
        provider: str,
        input_tokens: int,
        output_tokens: int,
    ) -> Dict[str, Any]:
        """Record token usage for a provider and return the updated entry.

        Args:
            provider: Provider name (e.g. "glm", "claude", "gpt", "gemini").
            input_tokens: Number of input (prompt) tokens consumed.
            output_tokens: Number of output (completion) tokens produced.

        Returns:
            The updated provider entry dict.
        """
        provider = provider.lower()
        if provider not in self._data["providers"]:
            self._data["providers"][provider] = {
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_calls": 0,
            }

        entry = self._data["providers"][provider]
        entry["total_input_tokens"] += input_tokens
        entry["total_output_tokens"] += output_tokens
        entry["total_calls"] += 1

        # Compute cost for this call
        rates = RATES.get(provider, {"input_per_1k": 0.0, "output_per_1k": 0.0})
        call_cost = (
            (input_tokens / 1000.0) * rates["input_per_1k"]
            + (output_tokens / 1000.0) * rates["output_per_1k"]
        )
        entry["last_call_cost"] = round(call_cost, 8)

        # Mark dirty instead of saving on every call.
        # Previously, _save() was called on EVERY record_usage() — a
        # synchronous JSON file write on every chat request (1-10ms each).
        # Now we batch: the data is saved when flush() is called, or
        # when get_cost_report() is called (read-after-write consistency),
        # or when the dirty count exceeds _FLUSH_THRESHOLD.
        self._dirty = True
        self._dirty_count += 1
        if self._dirty_count >= self._FLUSH_THRESHOLD:
            self._save()
            self._dirty = False
            self._dirty_count = 0
        logger.debug(
            "CostTracker: recorded %s +%d/%d tokens ($%.6f)",
            provider,
            input_tokens,
            output_tokens,
            call_cost,
        )
        return entry

    def flush(self) -> None:
        """Persist any unsaved data to disk.

        Call this at the end of a request cycle or on graceful shutdown
        to ensure no cost data is lost.
        """
        if self._dirty:
            self._save()
            self._dirty = False
            self._dirty_count = 0

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def get_cost_report(self) -> Dict[str, Any]:
        """Return a full cost report with per-provider breakdown and totals.

        Flushes any pending dirty data to disk first to ensure
        read-after-write consistency.

        Returns:
            Dict with:
                - ``providers``: per-provider stats and costs
                - ``total_cost_usd``: sum of all provider costs
                - ``total_input_tokens``: sum across providers
                - ``total_output_tokens``: sum across providers
                - ``total_calls``: sum across providers
        """
        # Flush any pending dirty data to ensure the report reflects
        # all recent record_usage() calls.
        self.flush()
        report_providers: Dict[str, Any] = {}
        total_cost = 0.0
        total_input = 0
        total_output = 0
        total_calls = 0

        for provider, entry in self._data["providers"].items():
            rates = RATES.get(provider, {"input_per_1k": 0.0, "output_per_1k": 0.0})
            input_cost = (entry["total_input_tokens"] / 1000.0) * rates["input_per_1k"]
            output_cost = (entry["total_output_tokens"] / 1000.0) * rates["output_per_1k"]
            provider_cost = input_cost + output_cost

            report_providers[provider] = {
                "total_input_tokens": entry["total_input_tokens"],
                "total_output_tokens": entry["total_output_tokens"],
                "total_calls": entry["total_calls"],
                "input_cost_usd": round(input_cost, 6),
                "output_cost_usd": round(output_cost, 6),
                "total_cost_usd": round(provider_cost, 6),
                "rate_input_per_1k": rates["input_per_1k"],
                "rate_output_per_1k": rates["output_per_1k"],
            }

            total_cost += provider_cost
            total_input += entry["total_input_tokens"]
            total_output += entry["total_output_tokens"]
            total_calls += entry["total_calls"]

        return {
            "providers": report_providers,
            "total_cost_usd": round(total_cost, 6),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_calls": total_calls,
            "updated_at": self._data.get("updated_at"),
        }

    def get_current_rate_limits(self) -> Dict[str, Any]:
        """Return Z.ai free tier rate limits and pricing information.

        This is informational — the actual rate limiting is enforced
        by the Z.ai API itself, not by FRIDAY.
        """
        return {
            "provider": "Z.ai (ZhipuAI)",
            "tier": "free",
            "models": {
                "glm-4-flash": {
                    "cost": "$0.00 (free tier)",
                    "rate_limit": "~100 requests/minute (varies by account)",
                    "context_window": "128K tokens",
                },
                "glm-4v": {
                    "cost": "$0.00 (free tier)",
                    "rate_limit": "~50 requests/minute (varies by account)",
                    "context_window": "8K tokens (image + text)",
                },
                "cogview-3": {
                    "type": "image_generation",
                    "cost": "Free tier: limited generations/day",
                    "note": "Rate-limited; may require paid tier for heavy use",
                },
                "cogvideox": {
                    "type": "video_generation",
                    "cost": "Free tier: limited generations/day",
                    "note": "Rate-limited; may require paid tier for heavy use",
                },
            },
            "web_search": {
                "cost": "Included with GLM-4 (free tier)",
                "rate_limit": "Subject to GLM-4 rate limits",
            },
            "pricing_url": "https://open.bigmodel.cn/pricing",
            "notes": [
                "GLM-4-Flash is free for all users.",
                "Image and video generation have daily limits on free tier.",
                "Rate limits may vary based on account status.",
                "Paid tiers offer higher rate limits and additional models.",
            ],
        }
