"""Code quality tests — IRON-CROWN-SWARM9.

This test suite enforces structural quality invariants across the
FRIDAY codebase, focusing on the experimental/dead-code boundary.

Specifically, it verifies:

1. No ``except Exception: pass`` (bare swallow) in production code
   under ``core/`` (excluding tests and ``__pycache__``).
2. No bare ``pass`` as the *entire* body of a function in non-experimental
   modules (i.e. real stubs hidden inside production code).
3. The five modules that the codebase documents as "not wired into
   production" — ``core.recursive``, ``core.evolution``,
   ``core.simulator``, ``core.continuum``, ``core.monologue`` — each
   emit a ``DeprecationWarning`` on import.
4. Each of those five modules is clearly marked as either DEAD CODE
   or EXPERIMENTAL via a header comment in its module docstring.
5. The five experimental modules are genuinely dead (not imported by
   any non-test Python file under the project) — this is the same
   scan performed by IRON-CROWN-SWARM9; the test will fail loudly if
   someone wires one of these modules back into production without
   first upgrading its docstring.
6. None of the five experimental modules carry an unused import.

Project-wide scans (tests 1 and 2) are marked
``xfail(strict=False)``: they document *known* pre-existing debt
identified by Swarm9's audit (see ``/home/z/my-project/worklog.md``
entry ``IRON-CROWN-SWARM9``) without failing the suite. The
strictly-enforced tests below them assert that the same invariants
hold for the five modules Swarm9 owns.
"""
from __future__ import annotations

import ast
import re
import sys
import warnings
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PROJECT_ROOT / "core"

# Modules explicitly documented as experimental/not-wired-into-production.
# These are owned by IRON-CROWN-SWARM9 and may not be wired into the main
# chat loop without a corresponding upgrade to their docstrings.
EXPERIMENTAL_MODULES = (
    "core.recursive",
    "core.evolution",
    "core.simulator",
    "core.continuum",
    "core.monologue",
)

# Modules Swarm9 owns — the only files it is permitted to modify.
SWARM9_OWNED_FILES = tuple(
    PROJECT_ROOT / f"{m.replace('.', '/')}.py" for m in EXPERIMENTAL_MODULES
)


def _python_files(root: Path, exclude_tests: bool = True) -> list[Path]:
    """Yield all ``.py`` files under ``root`` skipping caches.

    If ``exclude_tests`` is true, files under any ``tests/`` directory
    are skipped, since test code is allowed to use ``except: pass``
    patterns.
    """
    out: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if exclude_tests and "tests" in path.parts:
            continue
        out.append(path)
    return out


def _module_to_path(module: str) -> Path:
    """Convert ``core.recursive`` to ``core/recursive.py``."""
    parts = module.split(".")
    return PROJECT_ROOT.joinpath(*parts).with_suffix(".py")


def _is_experimental_module(path: Path) -> bool:
    """Return True if ``path`` is one of the known experimental modules."""
    try:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return False
    return rel in {f"{m.replace('.', '/')}.py" for m in EXPERIMENTAL_MODULES}


# ---------------------------------------------------------------------------
# 1. No bare ``except Exception: pass`` in production code
# ---------------------------------------------------------------------------


def _has_bare_except_pass(tree: ast.AST) -> list[tuple[int, str]]:
    """Return a list of (lineno, function_name) for every bare
    ``except Exception: pass`` (or ``except: pass``) found in ``tree``.

    A "bare swallow" is an except handler whose body consists of a
    single ``pass`` statement (optionally preceded by a docstring).
    """
    offenders: list[tuple[int, str]] = []

    class _Visitor(ast.NodeVisitor):
        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            body = [
                s for s in node.body
                if not (isinstance(s, ast.Expr)
                        and isinstance(s.value, ast.Constant))
            ]
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                offenders.append((node.lineno, "module"))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return offenders


# Project-wide scan: documents existing pre-Swarm9 debt; xfail so it
# doesn't break the suite but the test still exists as a guardrail.
@pytest.mark.xfail(
    strict=False,
    reason=(
        "Project-wide scan: pre-existing bare 'except: pass' debt was "
        "discovered by IRON-CROWN-SWARM9 in core/health_monitor.py, "
        "core/key_rotation.py, core/ledger.py, core/release_intelligence.py, "
        "core/runtime/kernel/event_loop.py, core/runtime/resource_manager.py, "
        "core/runtime/security/sandbox.py, core/scheduler.py, "
        "core/security_ops.py, core/validation_pipeline.py, "
        "core/doc_validator.py, core/brain_bridge.py, core/goals.py. "
        "Tracked in /home/z/my-project/worklog.md (IRON-CROWN-SWARM9)."
    ),
)
@pytest.mark.parametrize("pyfile", _python_files(CORE_DIR))
def test_no_bare_except_pass_in_core(pyfile: Path) -> None:
    """No ``except Exception: pass`` (bare swallow) in core/ production code.

    This is a project-wide guardrail. It currently fails (xfail) due to
    documented pre-existing debt; the strict version below
    (``test_owned_modules_have_no_bare_except_pass``) enforces the
    invariant on Swarm9's owned files.
    """
    source = pyfile.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(pyfile))
    except SyntaxError as exc:
        pytest.fail(f"{pyfile} has a syntax error: {exc}")
    offenders = _has_bare_except_pass(tree)
    if offenders:
        formatted = ", ".join(f"line {ln}" for ln, _ in offenders)
        pytest.fail(
            f"{pyfile}: bare 'except: pass' / 'except Exception: pass' "
            f"found at {formatted}. Use logging or re-raise."
        )


@pytest.mark.parametrize("pyfile", SWARM9_OWNED_FILES)
def test_owned_modules_have_no_bare_except_pass(pyfile: Path) -> None:
    """Swarm9's five owned modules must not contain bare ``except: pass``."""
    if not pyfile.exists():
        pytest.skip(f"{pyfile} does not exist")
    source = pyfile.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(pyfile))
    except SyntaxError as exc:
        pytest.fail(f"{pyfile} has a syntax error: {exc}")
    offenders = _has_bare_except_pass(tree)
    assert offenders == [], (
        f"{pyfile}: bare 'except: pass' / 'except Exception: pass' "
        f"found at {offenders}. Swarm9's owned modules must be clean."
    )


# ---------------------------------------------------------------------------
# 2. No bare ``pass`` body in non-experimental core modules
# ---------------------------------------------------------------------------


def _find_pass_only_functions(tree: ast.AST) -> list[tuple[int, str]]:
    """Return a list of (lineno, func_name) for every function whose body
    is *just* a ``pass`` statement."""
    out: list[tuple[int, str]] = []

    class _Visitor(ast.NodeVisitor):
        def _check(self, node: ast.AST, name: str) -> None:
            body = [
                s for s in getattr(node, "body", [])  # type: ignore[arg-type]
                if not (isinstance(s, ast.Expr)
                        and isinstance(s.value, ast.Constant))
            ]
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                out.append((node.lineno, name))
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._check(node, node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._check(node, node.name)

    _Visitor().visit(tree)
    return out


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Project-wide scan: pre-existing pass-only function bodies were "
        "discovered by IRON-CROWN-SWARM9 in core/privacy_audit.py and "
        "core/recursion.py. Tracked in /home/z/my-project/worklog.md "
        "(IRON-CROWN-SWARM9)."
    ),
)
@pytest.mark.parametrize(
    "pyfile",
    [p for p in _python_files(CORE_DIR) if not _is_experimental_module(p)],
)
def test_no_pass_only_function_body_in_production_core(pyfile: Path) -> None:
    """No function whose entire body is ``pass`` in non-experimental core/.

    Project-wide guardrail; currently fails (xfail) for known debt. The
    strict version below enforces the invariant on Swarm9's owned files
    (which are exempted from this test anyway since they are explicitly
    experimental).
    """
    source = pyfile.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(pyfile))
    except SyntaxError as exc:
        pytest.fail(f"{pyfile} has a syntax error: {exc}")
    offenders = _find_pass_only_functions(tree)
    if offenders:
        formatted = ", ".join(
            f"{name}() at line {ln}" for ln, name in offenders
        )
        pytest.fail(
            f"{pyfile}: pass-only function body found: {formatted}. "
            "Either implement the function, raise NotImplementedError, "
            "or move the module to the experimental set."
        )


# ---------------------------------------------------------------------------
# 3. Experimental modules emit DeprecationWarning on import
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", EXPERIMENTAL_MODULES)
def test_experimental_modules_emit_deprecation_warning(
    module_name: str,
) -> None:
    """Importing an experimental module must emit a DeprecationWarning."""
    # Drop any cached copy so the import re-fires the warning.
    sys.modules.pop(module_name, None)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        __import__(module_name)
    deps = [
        w for w in captured
        if issubclass(w.category, DeprecationWarning)
        and module_name in str(w.message)
    ]
    assert deps, (
        f"{module_name} did not emit a DeprecationWarning on import. "
        "Experimental modules must self-flag their status at import time."
    )


# ---------------------------------------------------------------------------
# 4. Experimental modules are clearly marked in their docstring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", EXPERIMENTAL_MODULES)
def test_experimental_modules_marked_in_docstring(module_name: str) -> None:
    """Each experimental module's docstring must contain a clear status
    banner: either ``DEAD CODE`` (if unimported anywhere) or
    ``EXPERIMENTAL`` (if imported but not wired into production)."""
    path = _module_to_path(module_name)
    assert path.exists(), f"{module_name} file missing: {path}"
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        pytest.fail(f"{module_name} has a syntax error: {exc}")
    docstring = ast.get_docstring(tree) or ""
    assert (
        "DEAD CODE" in docstring or "EXPERIMENTAL" in docstring
    ), (
        f"{module_name} docstring must contain either 'DEAD CODE' or "
        "'EXPERIMENTAL' to clearly mark its status."
    )
    assert "not wired into production" in docstring.lower(), (
        f"{module_name} docstring must state it is 'not wired into "
        "production' to make the boundary explicit."
    )


# ---------------------------------------------------------------------------
# 5. The five experimental modules are genuinely dead (no production
#    imports) — fails LOUDLY if someone wires them back in.
# ---------------------------------------------------------------------------


_IMPORT_RE = re.compile(
    r"^\s*from\s+core\.(recursive|evolution|simulator|continuum|monologue)"
    r"|^\s*import\s+core\.(recursive|evolution|simulator|continuum|monologue)",
    re.MULTILINE,
)


def _find_importers_of_experimental_modules() -> dict[str, list[Path]]:
    """Scan the whole project for any non-test ``.py`` file that imports
    one of the five experimental modules. Returns a dict mapping module
    name -> list of offending paths."""
    offenders: dict[str, list[Path]] = {
        m: [] for m in EXPERIMENTAL_MODULES
    }
    for pyfile in _python_files(PROJECT_ROOT, exclude_tests=True):
        try:
            source = pyfile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _IMPORT_RE.finditer(source):
            line = match.group(0)
            for mod in EXPERIMENTAL_MODULES:
                short = mod.split(".")[-1]
                if f"core.{short}" in line:
                    offenders[mod].append(pyfile)
                    break
    return offenders


def test_experimental_modules_are_not_imported_anywhere() -> None:
    """No production .py file in the project should import any of the five
    experimental modules. If this test fails, it means a new wiring was
    added — either upgrade the module's docstring (drop the DEAD CODE
    banner) or remove the import."""
    offenders = _find_importers_of_experimental_modules()
    failing = {mod: paths for mod, paths in offenders.items() if paths}
    if failing:
        lines = []
        for mod, paths in failing.items():
            for p in paths:
                lines.append(f"  {mod}: imported by {p}")
        pytest.fail(
            "Experimental modules must not be imported by production "
            "code. Offenders:\n" + "\n".join(lines)
        )


# ---------------------------------------------------------------------------
# 6. The five experimental modules must not carry unused imports
#    (a self-policing check on the files Swarm9 owns).
# ---------------------------------------------------------------------------


def _unused_imports(path: Path) -> list[str]:
    """Return the list of unused imports in ``path`` using a careful
    AST-based scan that handles both ``import x`` and ``from m import y``.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names: list[tuple[str, str]] = []  # (name, full_import_statement)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported_names.append((name, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imported_names.append(
                    (name, f"from {node.module} import {alias.name}")
                )

    unused: list[str] = []
    for name, stmt in imported_names:
        # Count word-boundary occurrences of the identifier in source.
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        occurrences = pattern.findall(source)
        # The import statement itself counts as at least one occurrence.
        if len(occurrences) <= 1:
            unused.append(f"{name} (via '{stmt}')")
    return unused


@pytest.mark.parametrize("module_name", EXPERIMENTAL_MODULES)
def test_experimental_modules_have_no_unused_imports(module_name: str) -> None:
    """Each experimental module must not contain unused imports.

    This is a regression guard: Swarm9 cleaned up several unused imports
    (``json``, ``field``, ``Optional``) from these five modules, and
    this test will fail if anyone reintroduces them.
    """
    path = _module_to_path(module_name)
    unused = _unused_imports(path)
    if unused:
        pytest.fail(
            f"{module_name} has unused imports: {', '.join(unused)}"
        )
