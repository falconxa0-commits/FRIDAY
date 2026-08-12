"""Tests for the injectable singleton pattern across core modules.

These tests verify that each ``get_*()`` accessor:
  1. Returns the singleton instance by default (identity preserved across calls).
  2. Accepts an optional ``instance`` parameter for dependency injection.
  3. After injection, returns the injected instance on subsequent calls.

The tests are isolated via fixtures that reset each module's
module-level singleton state before/after every test so order does
not matter.
"""

import importlib
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Registry of all injectable singletons under test.
#
# Each entry maps the accessor function name to a tuple of:
#   (module_path, module_level_variable_name, class_name)
#
# We list every modified module here so we can verify injection behaviour
# consistently, even though the task only requires testing 10+.
# ---------------------------------------------------------------------------
SINGLETON_REGISTRY = [
    ("core.ledger",                "get_ledger",                    "_ledger",       "ActionLedger"),
    ("core.task_system",           "get_task_queue",                 "_queue",        "TaskQueue"),
    ("core.knowledge_base",        "get_knowledge_base",            "_kb",           "KnowledgeBase"),
    ("core.engineering_org",       "get_engineering_org",           "_org",          "EngineeringOrg"),
    ("core.engineering_intelligence", "get_engineering_intelligence", "_intelligence", "EngineeringIntelligence"),
    ("core.architecture",          "get_architecture_analyzer",      "_analyzer",     "ArchitectureAnalyzer"),
    ("core.security_ops",          "get_security_operations",        "_sec_ops",      "SecurityOperations"),
    ("core.recommendation_engine", "get_recommendation_engine",      "_engine",       "RecommendationEngine"),
    ("core.health_monitor",        "get_health_monitor",            "_monitor",      "HealthMonitor"),
    ("core.regression_detector",   "get_regression_detector",       "_detector",     "RegressionDetector"),
    ("core.doc_validator",         "get_doc_validator",              "_validator",    "DocValidator"),
    ("core.benchmark_runner",      "get_benchmark_runner",          "_runner",       "BenchmarkRunner"),
    ("core.auto_fix",              "get_auto_fix_pipeline",         "_pipeline",     "AutoFixPipeline"),
    ("core.release_pipeline",      "get_release_pipeline",           "_pipeline",     "ReleasePipeline"),
    ("core.research_lab",          "get_research_lab",              "_lab",          "ResearchLab"),
    ("core.cost_tracker",          "get_cost_tracker",             "_tracker",      "CostTracker"),
    ("core.scheduler",             "get_scheduler",                "_scheduler",    "FridayScheduler"),
    ("core.sentinel",              "get_sentinel",                 "_sentinel",     "EthicalSentinel"),
    ("core.brain_bridge",          "get_brain_bridge",             "_bridge",       "BrainBridge"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_all_singletons():
    """Reset every module-level singleton before AND after each test.

    This guarantees test isolation regardless of execution order —
    a test that injects a mock cannot leak into another test.
    """
    snapshots = {}
    for module_path, _accessor, var_name, _cls in SINGLETON_REGISTRY:
        try:
            mod = importlib.import_module(module_path)
        except Exception:  # pragma: no cover — module import problems are surfaced by their own tests
            continue
        snapshots[module_path] = getattr(mod, var_name, None)
        # Reset to None before the test runs
        setattr(mod, var_name, None)

    yield

    # Restore originals after the test
    for module_path, _accessor, var_name, _cls in SINGLETON_REGISTRY:
        try:
            mod = importlib.import_module(module_path)
        except Exception:
            continue
        setattr(mod, var_name, snapshots.get(module_path, None))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(module_path, accessor_name, var_name):
    """Import a module + accessor + the underlying class type."""
    mod = importlib.import_module(module_path)
    accessor = getattr(mod, accessor_name)
    return mod, accessor, var_name


# ---------------------------------------------------------------------------
# Parametrized tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "module_path,accessor_name,var_name",
    [(m, a, v) for (m, a, v, _) in SINGLETON_REGISTRY],
)
def test_singleton_returns_same_instance_by_default(module_path, accessor_name, var_name):
    """Default ``get_*()`` calls must return the same object (singleton)."""
    _mod, accessor, _ = _load(module_path, accessor_name, var_name)
    first = accessor()
    second = accessor()
    assert first is second, (
        f"{accessor_name}() did not return the same instance on repeated calls"
    )


@pytest.mark.parametrize(
    "module_path,accessor_name,var_name",
    [(m, a, v) for (m, a, v, _) in SINGLETON_REGISTRY],
)
def test_get_function_accepts_instance_parameter(module_path, accessor_name, var_name):
    """The accessor must accept an ``instance`` keyword/positional argument."""
    _mod, accessor, _ = _load(module_path, accessor_name, var_name)
    injected = object()
    # Must not raise
    result = accessor(instance=injected)
    assert result is injected, (
        f"{accessor_name}(instance=...) did not return the injected object"
    )


@pytest.mark.parametrize(
    "module_path,accessor_name,var_name",
    [(m, a, v) for (m, a, v, _) in SINGLETON_REGISTRY],
)
def test_injected_instance_persists_on_subsequent_calls(module_path, accessor_name, var_name):
    """After injection, subsequent ``get_*()`` calls must return the injected instance."""
    _mod, accessor, _ = _load(module_path, accessor_name, var_name)
    injected = object()
    returned = accessor(instance=injected)
    assert returned is injected

    # Subsequent calls without the parameter must still return the injected object
    next_call = accessor()
    assert next_call is injected, (
        f"{accessor_name}() did not return the injected instance on subsequent call"
    )


@pytest.mark.parametrize(
    "module_path,accessor_name,var_name",
    [(m, a, v) for (m, a, v, _) in SINGLETON_REGISTRY],
)
def test_injection_overwrites_existing_singleton(module_path, accessor_name, var_name):
    """Injecting after a singleton was already created must replace it."""
    _mod, accessor, _ = _load(module_path, accessor_name, var_name)
    original = accessor()
    injected = object()
    returned = accessor(instance=injected)
    assert returned is injected
    assert returned is not original


@pytest.mark.parametrize(
    "module_path,accessor_name,var_name",
    [(m, a, v) for (m, a, v, _) in SINGLETON_REGISTRY],
)
def test_module_level_singleton_variable_exists(module_path, accessor_name, var_name):
    """Each module must keep its singleton backing variable at module scope."""
    mod, _accessor, _ = _load(module_path, accessor_name, var_name)
    assert hasattr(mod, var_name), (
        f"{module_path} must expose module-level {var_name} for the singleton"
    )


# ---------------------------------------------------------------------------
# Targeted tests for at least 10 specific accessors (per task spec)
# ---------------------------------------------------------------------------

TARGETED_MODULES = [
    "core.ledger",
    "core.task_system",
    "core.knowledge_base",
    "core.engineering_org",
    "core.engineering_intelligence",
    "core.architecture",
    "core.security_ops",
    "core.recommendation_engine",
    "core.health_monitor",
    "core.doc_validator",
    "core.auto_fix",
    "core.release_pipeline",
    "core.cost_tracker",
    "core.scheduler",
    "core.sentinel",
    "core.brain_bridge",
]


@pytest.mark.parametrize("module_path", TARGETED_MODULES)
def test_singleton_identity_is_object_id_stable(module_path):
    """A more explicit check: id() of two default calls must match."""
    # find accessor + class
    entry = next(e for e in SINGLETON_REGISTRY if e[0] == module_path)
    _, accessor_name, var_name, cls_name = entry
    mod, accessor, _ = _load(module_path, accessor_name, var_name)
    first_id = id(accessor())
    second_id = id(accessor())
    assert first_id == second_id


@pytest.mark.parametrize("module_path", TARGETED_MODULES)
def test_injection_with_real_class_instance(module_path):
    """Inject an actual instance of the class — not just a bare object."""
    entry = next(e for e in SINGLETON_REGISTRY if e[0] == module_path)
    _, accessor_name, var_name, cls_name = entry
    mod, accessor, _ = _load(module_path, accessor_name, var_name)
    cls = getattr(mod, cls_name)
    try:
        instance = cls()
    except TypeError:
        # Some constructors require args — fall back to a sentinel object.
        instance = object()
    accessor(instance=instance)
    again = accessor()
    assert again is instance


# ---------------------------------------------------------------------------
# Cross-module test: ensure resetting one module does not affect another
# ---------------------------------------------------------------------------

def test_modules_have_independent_singletons():
    """Resetting/injecting in module A must not affect module B."""
    from core.ledger import get_ledger
    from core.knowledge_base import get_knowledge_base

    # Force-construct both singletons
    a1 = get_ledger()
    b1 = get_knowledge_base()

    # Inject a custom instance into ledger
    injected = object()
    get_ledger(instance=injected)

    # Knowledge base must be unaffected
    assert get_knowledge_base() is b1
    assert get_ledger() is injected
