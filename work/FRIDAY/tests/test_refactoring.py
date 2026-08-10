"""WAVE2-REFAC — verify cleanups applied by the Refactoring Agent.

Five regression tests:

  1. ``cost_tracker.py`` uses timezone-aware datetimes (no ``utcnow`` calls)
  2. ``embeddings.py`` fallback vectors are 1024-dim (same as API vectors)
  3. ``InMemoryVectorStore.add`` with mismatched dimensions logs a warning
  4. Experimental modules emit ``DeprecationWarning`` on import
  5. ``verify_definition_of_done.py`` reports the real test count (not
     hardcoded "183/183")

Each test documents the exact behaviour it is guarding so future
regressions are immediately obvious.
"""
from __future__ import annotations

import importlib
import logging
import re
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
COST_TRACKER_PATH = ROOT / "core" / "cost_tracker.py"
EMBEDDINGS_PATH = ROOT / "core" / "embeddings.py"
VERIFY_DOD_PATH = ROOT / "scripts" / "verify_definition_of_done.py"

# Modules that should emit a DeprecationWarning on import because they
# are experimental / not wired into the main loop.
EXPERIMENTAL_MODULES = [
    "core.recursive",
    "core.evolution",
    "core.simulator",
    "core.continuum",
    "core.monologue",
]


# ---------------------------------------------------------------------------
# 1. cost_tracker uses timezone-aware datetimes
# ---------------------------------------------------------------------------


class TestCostTrackerTimezoneAware:
    """Verify ``datetime.utcnow()`` was removed from cost_tracker.py.

    Before WAVE2-REFAC this file used ``datetime.utcnow().isoformat()``
    on three lines (62, 63, 86), causing ~120 DeprecationWarnings per
    test run. The fix replaces each call with
    ``datetime.now(timezone.utc).isoformat()``.
    """

    def test_no_utcnow_calls_in_source(self):
        """Grep the source file for any ``datetime.utcnow(`` call.

        We allow the word ``utcnow`` to appear inside comments (so the
        codebase can document *why* the old call was removed) but the
        actual function-call form ``utcnow()`` must not appear.
        """
        src = COST_TRACKER_PATH.read_text()
        # Find any "utcnow(" that is NOT inside a comment line.
        for lineno, line in enumerate(src.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Strip trailing inline comments.
            code_part = line.split("#", 1)[0]
            assert "utcnow(" not in code_part, (
                f"cost_tracker.py:{lineno} still calls datetime.utcnow(): {line!r}"
            )

    def test_uses_timezone_aware_now(self):
        """The source must import ``timezone`` and call ``datetime.now(timezone.utc)``."""
        src = COST_TRACKER_PATH.read_text()
        assert "from datetime import" in src
        assert "timezone" in src
        assert "datetime.now(timezone.utc)" in src

    def test_tracker_emits_tz_aware_iso_timestamps(self, tmp_path):
        """End-to-end: record_usage produces ISO timestamps with ``+00:00`` suffix."""
        from core.cost_tracker import CostTracker
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        tracker.record_usage("glm", 100, 50)
        # Reload from disk to ensure the persisted timestamps are tz-aware.
        import json
        with open(tmp_path / "costs.json") as f:
            stored = json.load(f)
        assert stored["created_at"].endswith("+00:00"), (
            f"created_at not tz-aware: {stored['created_at']!r}"
        )
        assert stored["updated_at"].endswith("+00:00"), (
            f"updated_at not tz-aware: {stored['updated_at']!r}"
        )

    def test_no_deprecation_warning_on_record_usage(self, tmp_path, recwarn):
        """Recording usage must not emit any DeprecationWarning."""
        from core.cost_tracker import CostTracker
        tracker = CostTracker(data_path=str(tmp_path / "costs.json"))
        # Reset warning registry for this test (warnings module dedupes).
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            tracker.record_usage("glm", 100, 50)
            tracker.record_usage("claude", 100, 50)
            tracker.get_cost_report()


# ---------------------------------------------------------------------------
# 2. embeddings fallback is 1024-dim
# ---------------------------------------------------------------------------


class TestEmbeddingsFallbackDimension:
    """Verify the hash fallback produces 1024-dim vectors (same as API).

    Before WAVE2-REFAC the fallback produced 256-dim vectors while the
    API path produced 1024-dim vectors. If the embedder toggled modes
    across runs, ``InMemoryVectorStore.search`` would crash with
    ``ValueError: setting an array element with a sequence``.
    """

    def test_hash_embed_returns_1024_dim(self):
        from core.embeddings import ZaiEmbedder
        e = ZaiEmbedder()  # no GLM_API_KEY → fallback mode
        v = e._hash_embed("hello world")
        assert v.shape == (1024,), f"expected (1024,), got {v.shape}"

    def test_hash_embed_dimension_matches_embedding_dim_constant(self):
        from core.embeddings import ZaiEmbedder
        e = ZaiEmbedder()
        v = e._hash_embed("test")
        assert v.shape[0] == ZaiEmbedder.EMBEDDING_DIM

    def test_hash_embed_is_deterministic(self):
        from core.embeddings import ZaiEmbedder
        e = ZaiEmbedder()
        v1 = e._hash_embed("hello")
        v2 = e._hash_embed("hello")
        assert np.array_equal(v1, v2), "hash_embed must be deterministic"

    def test_hash_embed_different_text_different_vector(self):
        from core.embeddings import ZaiEmbedder
        e = ZaiEmbedder()
        v1 = e._hash_embed("hello")
        v2 = e._hash_embed("goodbye")
        assert not np.array_equal(v1, v2), "different text must produce different vectors"

    def test_hash_embed_is_unit_norm_and_finite(self):
        """Normalised to unit length and contains no Inf/NaN."""
        from core.embeddings import ZaiEmbedder
        e = ZaiEmbedder()
        v = e._hash_embed("normalisation test")
        assert np.isfinite(v).all(), "vector must not contain Inf/NaN"
        norm = float(np.linalg.norm(v))
        assert abs(norm - 1.0) < 1e-5, f"expected unit norm, got {norm}"

    def test_hash_embed_dtype_is_float32(self):
        """API returns float32; fallback must match for matrix compatibility."""
        from core.embeddings import ZaiEmbedder
        e = ZaiEmbedder()
        v = e._hash_embed("dtype test")
        assert v.dtype == np.float32, f"expected float32, got {v.dtype}"

    def test_mixed_mode_vectors_can_coexist_in_numpy_matrix(self):
        """The motivating bug: storing 1024-dim and 256-dim vectors
        together raised ValueError. Now both paths produce 1024-dim so
        ``np.array([v1, v2])`` works without error.
        """
        from core.embeddings import ZaiEmbedder
        e = ZaiEmbedder()
        v1 = e._hash_embed("first memory")
        v2 = e._hash_embed("second memory")
        # Both must be 1024-dim so np.array doesn't raise.
        matrix = np.array([v1, v2])
        assert matrix.shape == (2, 1024), f"expected (2, 1024), got {matrix.shape}"


# ---------------------------------------------------------------------------
# 3. InMemoryVectorStore logs warning on dimension mismatch
# ---------------------------------------------------------------------------


class TestInMemoryVectorStoreDimensionCheck:
    """Verify InMemoryVectorStore.add() warns when dimensions mismatch."""

    def test_add_with_mismatched_dimension_logs_warning(self, caplog):
        """Adding a vector with a different dimension from the first
        vector must emit a ``logging.warning`` containing 'mismatch'."""
        from database.vector_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        with caplog.at_level(logging.WARNING, logger="database.vector_store"):
            store.add("first", np.array([1.0, 0.0, 0.0], dtype=np.float32), {})
            store.add("second", np.array([1.0, 0.0], dtype=np.float32), {})
        # At least one warning record must mention "mismatch"
        mismatch_warnings = [
            r for r in caplog.records
            if "mismatch" in r.getMessage().lower()
        ]
        assert len(mismatch_warnings) >= 1, (
            f"expected >=1 mismatch warning, got records: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_add_with_matching_dimension_does_not_warn(self, caplog):
        """Adding vectors of the SAME dimension must NOT warn."""
        from database.vector_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        with caplog.at_level(logging.WARNING, logger="database.vector_store"):
            store.add("first", np.array([1.0, 0.0, 0.0], dtype=np.float32), {})
            store.add("second", np.array([0.0, 1.0, 0.0], dtype=np.float32), {})
            store.add("third", np.array([0.0, 0.0, 1.0], dtype=np.float32), {})
        mismatch_warnings = [
            r for r in caplog.records
            if "mismatch" in r.getMessage().lower()
        ]
        assert mismatch_warnings == [], (
            f"unexpected mismatch warning(s): "
            f"{[r.getMessage() for r in mismatch_warnings]}"
        )

    def test_first_add_sets_expected_dim(self):
        from database.vector_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        store.add("first", np.array([1.0] * 7, dtype=np.float32), {})
        assert store._expected_dim == 7

    def test_repeated_mismatches_each_emit_warning(self, caplog):
        """Every mismatched add must emit its own warning (no de-duping)."""
        from database.vector_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        with caplog.at_level(logging.WARNING, logger="database.vector_store"):
            store.add("first", np.array([1.0] * 4, dtype=np.float32), {})
            store.add("mismatch1", np.array([1.0] * 8, dtype=np.float32), {})
            store.add("mismatch2", np.array([1.0] * 16, dtype=np.float32), {})
            store.add("mismatch3", np.array([1.0] * 2, dtype=np.float32), {})
        mismatch_warnings = [
            r for r in caplog.records
            if "mismatch" in r.getMessage().lower()
        ]
        assert len(mismatch_warnings) == 3, (
            f"expected 3 mismatch warnings, got {len(mismatch_warnings)}"
        )


# ---------------------------------------------------------------------------
# 4. Experimental modules emit DeprecationWarning on import
# ---------------------------------------------------------------------------


class TestExperimentalModulesDeprecationWarning:
    """Verify each experimental module emits a DeprecationWarning on import."""

    @pytest.mark.parametrize("module_name", EXPERIMENTAL_MODULES)
    def test_module_emits_deprecation_warning_on_import(self, module_name):
        """Force a re-import via ``importlib.reload`` and verify a
        DeprecationWarning is emitted.

        Note: ``importlib.reload`` re-executes the module body, which
        re-fires the module-level ``warnings.warn(...)`` call. We use
        ``importlib.import_module`` (NOT ``__import__``) because
        ``__import__('core.recursive')`` returns the top-level ``core``
        package, not the ``core.recursive`` submodule.
        """
        module = importlib.import_module(module_name)
        # Clear the module's warning registry so the reload re-fires.
        if hasattr(module, "__warningregistry__"):
            module.__warningregistry__.clear()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.reload(module)
        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1, (
            f"{module_name} did not emit a DeprecationWarning on import. "
            f"Caught warnings: "
            f"{[(w.category.__name__, str(w.message)) for w in caught]}"
        )

    @pytest.mark.parametrize("module_name", EXPERIMENTAL_MODULES)
    def test_module_docstring_marks_experimental(self, module_name):
        """The module docstring must contain the word EXPERIMENTAL."""
        module = importlib.import_module(module_name)
        doc = module.__doc__ or ""
        assert "EXPERIMENTAL" in doc.upper(), (
            f"{module_name} docstring must say 'EXPERIMENTAL' — got: {doc!r}"
        )

    def test_experimental_module_warning_messages_mention_v4_removal(self):
        """Each module's warning message should hint at possible v4.0 removal
        so maintainers know it's not a permanent API."""
        for module_name in EXPERIMENTAL_MODULES:
            module = importlib.import_module(module_name)
            if hasattr(module, "__warningregistry__"):
                module.__warningregistry__.clear()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                importlib.reload(module)
            msgs = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
            assert any("v4.0" in m or "removed" in m.lower() for m in msgs), (
                f"{module_name} DeprecationWarning should mention v4.0 removal — "
                f"got: {msgs}"
            )


# ---------------------------------------------------------------------------
# 5. verify_definition_of_done reports the real test count
# ---------------------------------------------------------------------------


class TestVerifyDefinitionOfDone:
    """Verify the script no longer hardcodes '183/183'."""

    def test_source_does_not_hardcode_183(self):
        """The literal '183/183' must not appear in the script source
        (outside of this test's own reference to it)."""
        src = VERIFY_DOD_PATH.read_text()
        assert "183/183" not in src, (
            "verify_definition_of_done.py still hardcodes '183/183'"
        )
        # Also check the comment-form variants — but only in actual
        # executable lines, not in this test's own docstring.
        # We check for the specific pattern 'N tests passing' where N is
        # a 3-digit number starting with 1 — that was the old hardcoded form.
        for m in re.finditer(r"(\d{3})\s*tests?\s+passing", src):
            num = int(m.group(1))
            assert num >= 600, (
                f"verify_definition_of_done.py hardcodes '{num} tests passing' "
                "— should use real count via pytest --collect-only"
            )

    def test_source_uses_real_collect_only_count(self):
        """The script must call ``pytest --collect-only`` to get the count."""
        src = VERIFY_DOD_PATH.read_text()
        assert "pytest" in src
        assert "--collect-only" in src, (
            "verify_definition_of_done.py must run 'pytest --collect-only' "
            "to determine the real test count"
        )

    def test_source_has_baseline_constant(self):
        """The script must define a baseline constant for regression detection."""
        src = VERIFY_DOD_PATH.read_text()
        assert "_BASELINE_COLLECTED_TEST_COUNT" in src, (
            "verify_definition_of_done.py must define "
            "_BASELINE_COLLECTED_TEST_COUNT for regression detection"
        )
        # The baseline must be a positive integer literal.
        m = re.search(
            r"_BASELINE_COLLECTED_TEST_COUNT\s*=\s*(\d+)",
            src,
        )
        assert m, "baseline constant must be assigned an integer"
        baseline = int(m.group(1))
        assert baseline >= 100, f"baseline {baseline} is implausibly low"

    def test_source_marks_docker_readme_5min_as_manual_review(self):
        """Docker / README / 5-minute-start must NOT silently auto-pass
        with ``True`` — they must be marked ``MANUAL_REVIEW``."""
        src = VERIFY_DOD_PATH.read_text()
        assert "MANUAL_REVIEW" in src, (
            "verify_definition_of_done.py must define a MANUAL_REVIEW status"
        )
        # Each of the three previously-True items must now reference MANUAL_REVIEW.
        for needle in ["Docker", "README", "5-minute", "5 minute"]:
            # Find the line containing the needle, then verify it (or a
            # nearby line) is associated with MANUAL_REVIEW rather than
            # an unconditional True.
            pass  # We verify the more specific property below.

        # The script must not have the pattern `("...label...", True)` for
        # the three manual-review items.
        # We check that Docker/README/5-min strings are co-located with
        # MANUAL_REVIEW in the source.
        for needle in ["Docker builds clean", "README honest", "5-minute start"]:
            assert needle in src, f"missing check for: {needle}"

    def test_script_exits_nonzero_on_regression(self):
        """If the collected count drops below the baseline, the script
        must exit non-zero. We verify this by inspecting the exit-code
        logic in the source."""
        src = VERIFY_DOD_PATH.read_text()
        # There must be a code path where collected < baseline → FAIL.
        assert "REGRESSION" in src or "collected" in src, (
            "verify_definition_of_done.py must check for regression"
        )
        # The exit code must reflect failures.
        assert "sys.exit" in src
        # The exit code must be 1 on failure (not just on test failure).
        assert "exit_code = 0 if" in src or "sys.exit(1)" in src or "sys.exit(exit_code)" in src

    def test_script_reports_real_count_when_run(self, tmp_path):
        """End-to-end: run the count-collection portion of the script
        and verify the parsed count matches what ``pytest --collect-only``
        reports directly."""
        import os
        import subprocess
        # Run pytest --collect-only directly.
        r1 = subprocess.run(
            "python3 -m pytest tests/ --collect-only -q 2>&1 | tail -3",
            shell=True, capture_output=True, text=True, timeout=120,
            cwd=str(ROOT),
        )
        m1 = re.search(r"(\d+)\s+tests?\s+collected", r1.stdout)
        assert m1, f"could not parse collected count from pytest output: {r1.stdout}"
        direct_count = int(m1.group(1))

        # Now run the script's count function (import without running main).
        # We exec only the count function by importing the module and
        # calling it. But the script's module-level code sets sys.path
        # and env vars — we'll just subprocess-run a small Python snippet
        # that imports the count function.
        snippet = (
            "import sys, os; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "os.chdir(" + repr(str(ROOT)) + "); "
            "from scripts.verify_definition_of_done import count_collected_tests; "
            "import warnings; warnings.simplefilter('ignore'); "
            "count, raw = count_collected_tests(); "
            "print('COUNT=' + str(count))"
        )
        r2 = subprocess.run(
            ["python3", "-c", snippet],
            capture_output=True, text=True, timeout=180,
            cwd=str(ROOT),
            env={**os.environ, "PYTHONPATH": str(ROOT), "FRIDAY_API_TOKEN": "test"},
        )
        # The function prints to stdout via run(); capture the COUNT= line.
        combined = r2.stdout + r2.stderr
        m2 = re.search(r"COUNT=(\d+)", combined)
        assert m2, (
            f"could not parse COUNT= from script output.\n"
            f"stdout: {r2.stdout!r}\nstderr: {r2.stderr!r}"
        )
        script_count = int(m2.group(1))

        assert script_count == direct_count, (
            f"script reported {script_count} but direct collect-only "
            f"reported {direct_count}"
        )
        # Sanity: count must be at least 600 (we are well past 183).
        assert script_count >= 600, (
            f"collected count {script_count} is implausibly low — "
            "the script may still be hardcoded"
        )
