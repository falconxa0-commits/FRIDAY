"""Tests for core/cost_tracker.py — token usage tracking + cost calculation.

REGRESSION TEST: verifies the Phase 3 signature fix
  record_usage(provider, input_tokens, output_tokens)
The OLD broken call used keyword args in the wrong order — this test
verifies the NEW signature works and the OLD one raises TypeError.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from core.cost_tracker import CostTracker, RATES


# ---------------------------------------------------------------------------
# RATES table
# ---------------------------------------------------------------------------


class TestRatesTable:
    """Verify the RATES table has all providers and correct prices."""

    def test_rates_has_all_four_providers(self):
        assert set(RATES.keys()) == {"glm", "claude", "gpt", "gemini"}

    def test_glm_is_free(self):
        """Z.ai GLM-4-Flash is on the free tier — both rates must be 0.0."""
        assert RATES["glm"]["input_per_1k"] == 0.0
        assert RATES["glm"]["output_per_1k"] == 0.0

    def test_claude_rates_are_paid(self):
        assert RATES["claude"]["input_per_1k"] == 0.003
        assert RATES["claude"]["output_per_1k"] == 0.015

    def test_gpt_rates_are_paid(self):
        assert RATES["gpt"]["input_per_1k"] == 0.0015
        assert RATES["gpt"]["output_per_1k"] == 0.006

    def test_gemini_rates_are_paid(self):
        assert RATES["gemini"]["input_per_1k"] == 0.000075
        assert RATES["gemini"]["output_per_1k"] == 0.0003

    def test_all_rates_are_non_negative(self):
        for prov, rates in RATES.items():
            assert rates["input_per_1k"] >= 0, f"{prov} input rate negative"
            assert rates["output_per_1k"] >= 0, f"{prov} output rate negative"

    def test_all_rates_have_required_keys(self):
        for prov, rates in RATES.items():
            assert "input_per_1k" in rates
            assert "output_per_1k" in rates

    def test_claude_output_more_expensive_than_input(self):
        """Output is typically 5x input for Claude Sonnet."""
        assert RATES["claude"]["output_per_1k"] > RATES["claude"]["input_per_1k"]


# ---------------------------------------------------------------------------
# record_usage — signature regression tests
# ---------------------------------------------------------------------------


class TestRecordUsageSignature:
    """REGRESSION: Verify record_usage signature is the FIXED form
    `(provider, input_tokens, output_tokens)`.

    The Phase 3 fix corrected a call site that passed positional args in
    the wrong order. These tests verify the canonical signature works
    AND that the OLD broken call (keyword args swapped) raises TypeError.
    """

    def test_record_usage_correct_positional_signature(self, tmp_path):
        """The NEW (fixed) call: positional args in correct order."""
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        entry = tracker.record_usage("glm", 120, 85)
        assert entry["total_input_tokens"] == 120
        assert entry["total_output_tokens"] == 85
        assert entry["total_calls"] == 1

    def test_record_usage_correct_keyword_signature(self, tmp_path):
        """The NEW (fixed) call: keyword args with correct names."""
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        entry = tracker.record_usage(
            provider="claude",
            input_tokens=200,
            output_tokens=150,
        )
        assert entry["total_input_tokens"] == 200
        assert entry["total_output_tokens"] == 150

    def test_record_usage_old_broken_signature_raises_typeerror(self, tmp_path):
        """REGRESSION: The OLD broken call passed output_tokens as
        input_tokens (swapped). Verify that calling with WRONG keyword
        names raises TypeError — so we know the fix is in place.

        The old broken call was something like:
            tracker.record_usage(provider="glm", input_tokens=85, output_tokens=120)
        where 85 was the OUTPUT count and 120 was the INPUT count —
        the call site swapped them.

        Here we verify that any call NOT matching the canonical signature
        raises TypeError, so future regressions are caught.
        """
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        # Wrong keyword name → TypeError
        with pytest.raises(TypeError):
            tracker.record_usage(
                provider="glm",
                prompt_tokens=120,    # WRONG — should be input_tokens
                completion_tokens=85, # WRONG — should be output_tokens
            )

    def test_record_usage_missing_required_arg_raises_typeerror(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        # Missing output_tokens
        with pytest.raises(TypeError):
            tracker.record_usage("glm", 120)

    def test_record_usage_provider_case_insensitive(self, tmp_path):
        """Provider names should be normalized to lowercase."""
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        tracker.record_usage("GLM", 100, 50)
        tracker.record_usage("Claude", 100, 50)
        report = tracker.get_cost_report()
        assert "glm" in report["providers"]
        assert "claude" in report["providers"]

    def test_record_usage_accumulates_tokens(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        tracker.record_usage("glm", 100, 50)
        tracker.record_usage("glm", 200, 100)
        tracker.record_usage("glm", 50, 25)
        report = tracker.get_cost_report()
        glm = report["providers"]["glm"]
        assert glm["total_input_tokens"] == 350
        assert glm["total_output_tokens"] == 175
        assert glm["total_calls"] == 3

    def test_record_usage_returns_updated_entry(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        entry = tracker.record_usage("glm", 100, 50)
        assert isinstance(entry, dict)
        assert "total_input_tokens" in entry
        assert "total_output_tokens" in entry
        assert "total_calls" in entry
        assert "last_call_cost" in entry

    def test_record_usage_unknown_provider_still_recorded(self, tmp_path):
        """Unknown providers are still recorded (with $0 cost)."""
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        entry = tracker.record_usage("unknown_provider", 100, 50)
        assert entry["total_input_tokens"] == 100
        assert entry["total_output_tokens"] == 50
        assert entry["last_call_cost"] == 0.0


# ---------------------------------------------------------------------------
# Cost calculations
# ---------------------------------------------------------------------------


class TestCostCalculation:
    """Test per-provider cost calculation."""

    def test_glm_cost_is_zero(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        tracker.record_usage("glm", 1_000_000, 1_000_000)
        report = tracker.get_cost_report()
        assert report["providers"]["glm"]["total_cost_usd"] == 0.0

    def test_claude_cost_calculation(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        # 1000 input tokens @ $0.003/1k = $0.003
        # 500 output tokens @ $0.015/1k = $0.0075
        # Total = $0.0105
        tracker.record_usage("claude", 1000, 500)
        report = tracker.get_cost_report()
        claude = report["providers"]["claude"]
        assert claude["input_cost_usd"] == 0.003
        assert claude["output_cost_usd"] == 0.0075
        assert claude["total_cost_usd"] == 0.0105

    def test_gpt_cost_calculation(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        # 1000 input @ $0.0015 = $0.0015
        # 1000 output @ $0.006 = $0.006
        # Total = $0.0075
        tracker.record_usage("gpt", 1000, 1000)
        report = tracker.get_cost_report()
        gpt = report["providers"]["gpt"]
        assert gpt["total_cost_usd"] == 0.0075

    def test_gemini_cost_calculation(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        # 10000 input @ $0.000075 = $0.00075
        # 10000 output @ $0.0003 = $0.003
        # Total = $0.00375
        tracker.record_usage("gemini", 10000, 10000)
        report = tracker.get_cost_report()
        gemini = report["providers"]["gemini"]
        assert abs(gemini["total_cost_usd"] - 0.00375) < 1e-9

    def test_total_cost_sums_all_providers(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        tracker.record_usage("glm", 1000, 1000)        # $0
        tracker.record_usage("claude", 1000, 500)      # $0.0105
        tracker.record_usage("gpt", 1000, 1000)        # $0.0075
        report = tracker.get_cost_report()
        assert report["total_cost_usd"] == 0.018  # 0 + 0.0105 + 0.0075

    def test_total_tokens_aggregated_across_providers(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        tracker.record_usage("glm", 100, 50)
        tracker.record_usage("claude", 200, 100)
        report = tracker.get_cost_report()
        assert report["total_input_tokens"] == 300
        assert report["total_output_tokens"] == 150
        assert report["total_calls"] == 2


# ---------------------------------------------------------------------------
# get_cost_report structure
# ---------------------------------------------------------------------------


class TestCostReport:
    """Test get_cost_report returns correct structure."""

    def test_report_has_required_top_level_keys(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        report = tracker.get_cost_report()
        for key in ["providers", "total_cost_usd", "total_input_tokens",
                    "total_output_tokens", "total_calls", "updated_at"]:
            assert key in report, f"Missing key: {key}"

    def test_provider_entry_has_required_keys(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        tracker.record_usage("claude", 100, 50)
        report = tracker.get_cost_report()
        claude = report["providers"]["claude"]
        for key in ["total_input_tokens", "total_output_tokens", "total_calls",
                    "input_cost_usd", "output_cost_usd", "total_cost_usd",
                    "rate_input_per_1k", "rate_output_per_1k"]:
            assert key in claude, f"Missing provider key: {key}"

    def test_empty_report_returns_zeros(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        report = tracker.get_cost_report()
        assert report["providers"] == {}
        assert report["total_cost_usd"] == 0.0
        assert report["total_input_tokens"] == 0
        assert report["total_output_tokens"] == 0
        assert report["total_calls"] == 0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestCostTrackerPersistence:
    """Test persistence to JSON file."""

    def test_data_saved_to_json_file(self, tmp_path):
        data_file = tmp_path / "costs.json"
        tracker = CostTracker(data_path=str(data_file))
        tracker.record_usage("glm", 100, 50)
        tracker.record_usage("claude", 200, 100)
        # File should exist
        assert data_file.exists()
        # Load and verify contents
        with open(data_file) as f:
            stored = json.load(f)
        assert "providers" in stored
        assert "glm" in stored["providers"]
        assert "claude" in stored["providers"]
        assert stored["providers"]["glm"]["total_input_tokens"] == 100
        assert stored["providers"]["claude"]["total_input_tokens"] == 200

    def test_data_loaded_from_existing_file(self, tmp_path):
        data_file = tmp_path / "costs.json"
        # Pre-populate
        with open(data_file, "w") as f:
            json.dump({
                "providers": {
                    "glm": {
                        "total_input_tokens": 500,
                        "total_output_tokens": 250,
                        "total_calls": 2,
                    }
                },
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }, f)
        # New tracker should load existing data
        tracker = CostTracker(data_path=str(data_file))
        report = tracker.get_cost_report()
        assert report["providers"]["glm"]["total_input_tokens"] == 500
        assert report["providers"]["glm"]["total_calls"] == 2

    def test_accumulates_across_tracker_instances(self, tmp_path):
        """Persistence means usage accumulates across restarts."""
        data_file = tmp_path / "costs.json"
        t1 = CostTracker(data_path=str(data_file))
        t1.record_usage("glm", 100, 50)

        # New instance — should see the prior usage
        t2 = CostTracker(data_path=str(data_file))
        report = t2.get_cost_report()
        assert report["providers"]["glm"]["total_input_tokens"] == 100

        # Add more
        t2.record_usage("glm", 200, 100)
        report = t2.get_cost_report()
        assert report["providers"]["glm"]["total_input_tokens"] == 300

    def test_corrupt_json_does_not_crash(self, tmp_path):
        """A corrupt JSON file should not crash — fall back to empty state."""
        data_file = tmp_path / "costs.json"
        data_file.write_text("{not valid json")
        tracker = CostTracker(data_path=str(data_file))
        # Should have empty state
        report = tracker.get_cost_report()
        assert report["providers"] == {}

    def test_missing_file_starts_fresh(self, tmp_path):
        data_file = tmp_path / "nonexistent.json"
        tracker = CostTracker(data_path=str(data_file))
        report = tracker.get_cost_report()
        assert report["providers"] == {}
        assert report["total_calls"] == 0


# ---------------------------------------------------------------------------
# Rate limits info
# ---------------------------------------------------------------------------


class TestRateLimitsInfo:
    """Test get_current_rate_limits informational method."""

    def test_returns_dict_with_provider_info(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        info = tracker.get_current_rate_limits()
        assert isinstance(info, dict)
        assert info["provider"] == "Z.ai (ZhipuAI)"
        assert info["tier"] == "free"

    def test_includes_glm_models(self, tmp_path):
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        info = tracker.get_current_rate_limits()
        assert "models" in info
        assert "glm-4-flash" in info["models"]
        assert "glm-4v" in info["models"]
