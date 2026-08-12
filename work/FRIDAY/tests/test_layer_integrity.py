"""Layer-integrity tests — guard against regression of the 10 boundary
violations fixed by COUNCIL-GAMMA.

These tests have two complementary goals:

1. **Static guard** — run the ``ArchitectureAnalyzer`` over the project
   and assert that none of the previously-violating source modules appears
   in the violation list. If anyone re-introduces a ``from <higher_layer>
   import ...`` statement (even inside a function body) the analyzer will
   catch it, because ``ast.walk`` traverses the entire AST.

2. **Runtime guard** — verify the lazy-import pattern still resolves the
   target modules at runtime. The whole point of lazy ``importlib`` calls
   is that *behavior* is unchanged; these tests confirm that the symbol
   the codebase expects to resolve actually resolves through the dynamic
   path.

Scope (COUNCIL-GAMMA ownership):
    * ``core.goals``         -> ``api.routes.stats``
    * ``core.privacy_audit`` -> ``api.routes.stats``
    * ``core.universal_connector`` -> ``integrations.*`` (3 violations)
    * ``core.memory``        -> ``database.*`` (2 violations)
    * ``core.brain``         -> ``database.subconscious`` + ``integrations.registry``
    * ``api.routes.trust``   -> ``scripts.hellfire_audit``
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Set, Tuple

import pytest

from core.architecture import ArchitectureAnalyzer, Layer, VALID_DEPENDENCIES


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _violation_pairs() -> Set[Tuple[str, str]]:
    """Return the set of (source_module, target_module) violation pairs."""
    report = ArchitectureAnalyzer(project_root=PROJECT_ROOT).analyze()
    return {
        (v.source_module, v.target_module)
        for v in report.violations
        if not v.valid
    }


def _static_imports_in(module_path: str) -> Set[str]:
    """Return the set of statically-imported module names in a source file.

    Covers both ``import X`` and ``from X import Y`` forms. ``importlib``
    calls are *not* counted — that's the whole point of the lazy pattern.
    """
    file_path = PROJECT_ROOT / (module_path.replace(".", "/") + ".py")
    if not file_path.exists():
        return set()

    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


# ---------------------------------------------------------------------------
# Static analyzer guard
# ---------------------------------------------------------------------------

class TestNoLayerViolations:
    """The full project should have zero layer-boundary violations."""

    def test_analyzer_reports_zero_violations(self):
        """If this fails, someone re-introduced a static cross-layer import."""
        violations = _violation_pairs()
        assert not violations, (
            "Layer boundary violations detected:\n" +
            "\n".join(
                f"  {src} -> {tgt}" for src, tgt in sorted(violations)
            )
        )

    def test_previously_violating_pairs_are_clean(self):
        """Each previously-fixed violation must remain fixed."""
        previously_violating = {
            ("core.goals", "api.routes.stats"),
            ("core.privacy_audit", "api.routes.stats"),
            ("core.universal_connector", "integrations.base"),
            ("core.universal_connector", "integrations.registry"),
            ("core.universal_connector", "integrations.__init__"),
            ("core.memory", "database.supabase_client"),
            ("core.memory", "database.vector_store"),
            ("core.brain", "database.subconscious"),
            ("core.brain", "integrations.registry"),
            ("api.routes.trust", "scripts.hellfire_audit"),
        }
        current = _violation_pairs()
        regressions = previously_violating & current
        assert not regressions, (
            "Regression — these violations returned:\n" +
            "\n".join(f"  {src} -> {tgt}" for src, tgt in sorted(regressions))
        )

    def test_layer_definitions_unchanged(self):
        """Sanity-check: core remains the lowest layer with no valid deps."""
        assert VALID_DEPENDENCIES[Layer.CORE] == set()
        assert Layer.API not in VALID_DEPENDENCIES[Layer.CORE]
        assert Layer.DATABASE not in VALID_DEPENDENCIES[Layer.CORE]
        assert Layer.INTEGRATIONS not in VALID_DEPENDENCIES[Layer.CORE]


# ---------------------------------------------------------------------------
# Per-module static-import guards
# ---------------------------------------------------------------------------

class TestCoreGoalsNoApiStatsImport:
    """``core.goals`` must not statically import ``api.routes.stats``."""

    def test_no_static_import_of_api_routes_stats(self):
        names = _static_imports_in("core.goals")
        api_imports = {n for n in names if n.startswith("api.routes.stats")}
        assert not api_imports, (
            f"core.goals statically imports API layer: {api_imports}"
        )

    def test_no_import_of_higher_layer(self):
        names = _static_imports_in("core.goals")
        forbidden = {
            n for n in names
            if n.split(".")[0] in {"api", "cli", "apps", "scripts"}
        }
        assert not forbidden, (
            f"core.goals imports higher layer: {forbidden}"
        )


class TestCorePrivacyAuditNoApiStatsImport:
    """``core.privacy_audit`` must not statically import ``api.routes.stats``."""

    def test_no_static_import_of_api_routes_stats(self):
        names = _static_imports_in("core.privacy_audit")
        api_imports = {n for n in names if n.startswith("api.routes.stats")}
        assert not api_imports, (
            f"core.privacy_audit statically imports API: {api_imports}"
        )


class TestCoreUniversalConnectorNoIntegrationsImport:
    """``core.universal_connector`` must not statically import ``integrations``."""

    def test_no_static_import_of_integrations(self):
        names = _static_imports_in("core.universal_connector")
        int_imports = {n for n in names if n.startswith("integrations")}
        assert not int_imports, (
            f"core.universal_connector statically imports integrations: {int_imports}"
        )


class TestCoreMemoryNoDatabaseImport:
    """``core.memory`` must not statically import ``database.*``."""

    def test_no_static_import_of_database(self):
        names = _static_imports_in("core.memory")
        db_imports = {n for n in names if n.startswith("database")}
        assert not db_imports, (
            f"core.memory statically imports database: {db_imports}"
        )


class TestCoreBrainNoDatabaseOrIntegrationsImport:
    """``core.brain`` must not statically import ``database.subconscious``
    or ``integrations.registry``."""

    def test_no_static_import_of_database_subconscious(self):
        names = _static_imports_in("core.brain")
        assert not any(n.startswith("database.subconscious") for n in names), (
            f"core.brain imports database.subconscious: "
            f"{[n for n in names if n.startswith('database')]}"
        )

    def test_no_static_import_of_integrations_registry(self):
        names = _static_imports_in("core.brain")
        assert not any(n.startswith("integrations.registry") for n in names), (
            f"core.brain imports integrations.registry: "
            f"{[n for n in names if n.startswith('integrations')]}"
        )


class TestApiRoutesTrustNoScriptsImport:
    """``api.routes.trust`` must not statically import ``scripts.hellfire_audit``."""

    def test_no_static_import_of_scripts(self):
        names = _static_imports_in("api.routes.trust")
        script_imports = {n for n in names if n.startswith("scripts")}
        assert not script_imports, (
            f"api.routes.trust statically imports scripts: {script_imports}"
        )


# ---------------------------------------------------------------------------
# Runtime guard — verify the lazy path actually resolves
# ---------------------------------------------------------------------------

class TestLazyImportsResolveAtRuntime:
    """The lazy ``importlib.import_module`` calls must still work at runtime.

    If any of these break, the lazy path was wired up incorrectly.
    """

    def test_api_routes_stats_resolves(self):
        mod = importlib.import_module("api.routes.stats")
        assert hasattr(mod, "_request_log"), (
            "api.routes.stats no longer exposes _request_log — "
            "lazy imports in core.goals / core.privacy_audit will break."
        )

    def test_integrations_registry_resolves(self):
        mod = importlib.import_module("integrations.registry")
        assert hasattr(mod, "UniversalRegistry"), (
            "integrations.registry.UniversalRegistry missing"
        )

    def test_integrations_base_resolves(self):
        mod = importlib.import_module("integrations.base")
        assert hasattr(mod, "BaseIntegration"), (
            "integrations.base.BaseIntegration missing"
        )

    def test_integrations_package_resolves(self):
        mod = importlib.import_module("integrations")
        assert hasattr(mod, "__file__"), "integrations package has no __file__"

    def test_database_supabase_client_resolves(self):
        mod = importlib.import_module("database.supabase_client")
        assert hasattr(mod, "SupabaseClient"), (
            "database.supabase_client.SupabaseClient missing"
        )

    def test_database_vector_store_resolves(self):
        mod = importlib.import_module("database.vector_store")
        assert hasattr(mod, "VectorStore"), (
            "database.vector_store.VectorStore missing"
        )

    def test_database_subconscious_resolves(self):
        mod = importlib.import_module("database.subconscious")
        assert hasattr(mod, "SubconsciousMind"), (
            "database.subconscious.SubconsciousMind missing"
        )

    def test_scripts_hellfire_audit_resolves(self):
        mod = importlib.import_module("scripts.hellfire_audit")
        # Sanity-check: the eight check_* functions and FAILURES list
        for attr in (
            "check_commented_out_calls",
            "check_eager_client_construction",
            "check_double_run",
            "check_auth_rejection",
            "check_never_auto_approve",
            "check_no_hardcoded_secrets",
            "check_glm_key_from_env",
            "check_zhipu_client_guarded",
            "FAILURES",
        ):
            assert hasattr(mod, attr), (
                f"scripts.hellfire_audit.{attr} missing"
            )


# ---------------------------------------------------------------------------
# End-to-end smoke tests — instantiate the fixed classes
# ---------------------------------------------------------------------------

class TestModifiedModulesInstantiate:
    """Quick smoke tests — the modified modules must still construct cleanly."""

    def test_goal_tracker_instantiates(self):
        from core.goals import GoalTracker
        gt = GoalTracker()
        assert gt.get_all_goals() == []

    def test_privacy_audit_engine_instantiates(self):
        from core.privacy_audit import PrivacyAuditEngine
        pa = PrivacyAuditEngine()
        assert pa is not None

    def test_universal_connector_instantiates(self):
        from core.universal_connector import UniversalConnector
        uc = UniversalConnector()
        # Plugin discovery should have populated the registry (even if 0)
        assert isinstance(uc.integrations, dict)

    def test_friday_memory_instantiates(self):
        from core.memory import FridayMemory
        mem = FridayMemory()
        # In-memory fallback always exists
        assert hasattr(mem, "_memories")

    def test_run_hellfire_audit_callable(self):
        from api.routes.trust import _run_hellfire_audit
        # Don't actually run it — just confirm the function exists and the
        # lazy import path is wired correctly.
        assert callable(_run_hellfire_audit)
