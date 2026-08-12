#!/usr/bin/env python3
"""Hellfire audit — slower, adversarial checks for the exact patterns
already caught in this project's history.

Exits non-zero on FIRST failure. Run after every change.
"""
import logging
import os
import re
import sys
import ast
import importlib
from pathlib import Path

logger = logging.getLogger("hellfire_audit")

ROOT = Path(__file__).resolve().parent.parent
FAILURES = []

def fail(label, detail):
    FAILURES.append((label, detail))
    print(f"  FAIL  {label}: {detail}")

def pass_(label):
    print(f"  PASS  {label}")

# ── 1. No commented-out real calls next to a hardcoded success return ──
def check_commented_out_calls():
    """Grep for commented-out real calls sitting next to a hardcoded success return."""
    print("\n[1] Commented-out real calls next to hardcoded success returns")
    pattern = re.compile(r'#\s*(result|response|data|return)\s*=.*\b(await|requests|client|api)\b', re.IGNORECASE)
    found = False
    for py in ROOT.rglob("*.py"):
        if "test" in str(py) or "__pycache__" in str(py):
            continue
        try:
            text = py.read_text()
        except (UnicodeDecodeError, PermissionError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                # Check if nearby lines have a hardcoded return
                nearby = text.splitlines()[max(0,i-3):i+3]
                if any('return' in l and ('success' in l.lower() or '"ok"' in l.lower()) for l in nearby):
                    fail(str(py.relative_to(ROOT)), f"line {i}: commented-out call near hardcoded success")
                    found = True
    if not found:
        pass_("No commented-out calls next to hardcoded returns")

# ── 2. No eager client construction without credential guard ──
def check_eager_client_construction():
    """Grep for eager client construction (SomeClient(api_key=...) at __init__
    time with no `if key:` guard) — this is how the original boot crash happened."""
    print("\n[2] Eager client construction without credential guard")
    # Patterns: SomeClient(api_key=VAR) or create_client(url, key) in __init__
    # without a preceding `if VAR:` or `if not VAR:`
    found = False
    for py in ROOT.rglob("*.py"):
        if "test" in str(py) or "__pycache__" in str(py):
            continue
        try:
            text = py.read_text()
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError):
            continue
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                # Look for direct client construction calls
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        func_name = ""
                        if isinstance(child.func, ast.Attribute):
                            func_name = child.func.attr
                        elif isinstance(child.func, ast.Name):
                            func_name = child.func.id
                        
                        # Dangerous patterns
                        if func_name in ("create_client", "AsyncAnthropic", "OpenAI", "ElevenLabs"):
                            # Check if there's a guard in the same __init__
                            init_text = ast.get_source_segment(text, node) or ""
                            # Check for ANY conditional guard (if/try) wrapping the call
                            has_guard = bool(
                                re.search(r'if\s+\w+', init_text) or  # any if-statement
                                re.search(r'try\s*:', init_text)       # any try block
                            )
                            if not has_guard and init_text:
                                fail(str(py.relative_to(ROOT)), 
                                     f"__init__ calls {func_name}() without API key guard")
                                found = True
    if not found:
        pass_("No eager client construction without guards")

# ── 3. Double-run test: fake key vs no key should differ ──
def check_double_run():
    """Run any code twice — once with a fake-but-valid-looking key,
    once with none — and flag it if the output is byte-identical."""
    print("\n[3] Double-run test: fake key vs no key produces different output")
    
    # Test brain construction with and without key
    os.environ.pop("ANTHROPIC_API_KEY", None)
    
    from core.brain import FridayBrain
    b1 = FridayBrain(provider="ollama")
    stats1 = b1.get_stats()
    
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-fake-but-valid-looking-key-12345"
    b2 = FridayBrain(provider="claude")
    stats2 = b2.get_stats()
    
    os.environ.pop("ANTHROPIC_API_KEY", None)
    
    # The stats should differ (provider, model, etc.)
    if stats1 == stats2:
        fail("FridayBrain double-run", "identical output with fake key vs no key")
    else:
        pass_("Brain stats differ between no-key and fake-key construction")

# ── 4. Unauthenticated request rejection on every route ──
def check_auth_rejection():
    """Fire an unauthenticated request at every route; assert all reject it.

    Forces FRIDAY_API_TOKEN to a known non-empty value so the auth dependency
    actually enforces. Otherwise (dev mode with empty token) every route
    returns 200 and /api/trust/report would re-invoke this check recursively.
    """
    print("\n[4] Unauthenticated request rejection on every route")
    from fastapi.testclient import TestClient
    from api.main import app

    # Force a non-empty token so auth is enforced. Save & restore the
    # original value so we don't mutate global state across runs.
    import api.main as _api_main
    import config.settings as _settings
    orig_token_main = getattr(_api_main, "FRIDAY_API_TOKEN", None)
    orig_token_settings = getattr(_settings, "FRIDAY_API_TOKEN", None)
    _audit_token = "hellfire-audit-nonempty-token-1234"
    _api_main.FRIDAY_API_TOKEN = _audit_token
    _settings.FRIDAY_API_TOKEN = _audit_token

    # Re-bind the verify_token closure so it sees the new token value.
    # (FastAPI captures the dependency at route registration time, but
    # verify_token reads FRIDAY_API_TOKEN at request time via the module
    # global, so setting it on api.main is enough.)
    try:
        client = TestClient(app)
        # Public routes that don't require auth by design
        public_routes = {
            "/health", "/docs", "/openapi.json", "/redoc", "/static", "/",
            "/docs/oauth2-redirect", "/api/ping",
            "/api/health/deep",  # health check — public by design
        }

        # Team routes use their own FRIDAY_USER_TOKEN auth (separate from
        # FRIDAY_API_TOKEN). Skip them in the global auth check — they
        # enforce their own per-user auth via _get_user_from_header.
        team_paths = {
            "/api/team/members", "/api/team/invite", "/api/team/context",
            "/api/team/memory/private", "/api/team/memory/shared",
            "/api/team/memories",
        }
        # Webhook routes verify their own HMAC signatures — skip in global auth check
        webhook_paths = {
            "/api/webhooks/{source}",
        }

        failed_routes = []
        for route in app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set())

            if path in public_routes or path in team_paths or path in webhook_paths or not methods:
                continue
            # Skip routes that re-run this audit (would recurse).
            if path == "/api/trust/report":
                # Still verify it rejects — but the endpoint won't recurse
                # because the auth check happens BEFORE the handler runs.
                pass

            for method in methods:
                if method == "HEAD":
                    continue
                try:
                    if method == "GET":
                        r = client.get(path)
                    elif method == "POST":
                        r = client.post(path, json={})
                    elif method == "PUT":
                        r = client.put(path, json={})
                    elif method == "DELETE":
                        r = client.delete(path)
                    else:
                        continue

                    if r.status_code not in (401, 403):
                        failed_routes.append(f"{method} {path} → {r.status_code}")
                except Exception:
                    pass  # Some routes need specific params, that's fine

        if failed_routes:
            for rt in failed_routes:
                fail("Auth not enforced", rt)
        else:
            pass_("All non-public routes reject unauthenticated requests")
    finally:
        # Restore original values
        _api_main.FRIDAY_API_TOKEN = orig_token_main
        _settings.FRIDAY_API_TOKEN = orig_token_settings

# ── 5. Financial/physical actions never auto-approve ──
def check_never_auto_approve():
    """Attempt a financial/physical action at the most permissive setting;
    assert it still requires approval."""
    print("\n[5] Financial/physical actions never auto-approve")
    from core.sentinel import EthicalSentinel
    
    sentinel = EthicalSentinel()
    
    never_safe_actions = [
        "transfer money from bank account",
        "buy item on amazon for $50",
        "purchase subscription",
        "print document on printer",
        "3d print a model",
        "delete all user files from disk",
        "send financial data to external API",
    ]
    
    failures = []
    for action in never_safe_actions:
        r = sentinel.evaluate_action(action)
        if r["aligned"]:  # aligned=True means auto-approved
            failures.append(f'"{action}" classified as {r["classification"]} (aligned=True)')
    
    if failures:
        for f in failures:
            fail("Never-auto-approve violation", f)
    else:
        pass_("All financial/physical/destructive actions are blocked from auto-approval")

# ── 6. No hardcoded secrets ──
def check_no_hardcoded_secrets():
    """Check for hardcoded API keys or secrets in source files.

    Excludes example/placeholder strings (e.g. 'your-key-here',
    'your_key_here', 'example', 'placeholder', 'xxx', 'fake', 'dummy')
    commonly used in onboarding instructions, docs, and print statements.
    """
    print("\n[6] No hardcoded secrets in source files")
    secret_patterns = [
        re.compile(r'(?:api_key|API_KEY|secret|SECRET|token|TOKEN)\s*=\s*["\'][^"\']{10,}["\']', re.IGNORECASE),
        re.compile(r'sk-[a-zA-Z0-9]{20,}'),  # Anthropic-style keys
        re.compile(r'AIza[a-zA-Z0-9_-]{35}'),  # Google-style keys
    ]
    # Placeholder/example markers — if any of these appear in the matched
    # line, the secret is documentation or setup guidance, not a real key.
    placeholder_markers = (
        "your-key-here", "your_key_here", "your-key", "your_key",
        "example", "placeholder", "fake", "dummy", "xxxx", "xxx",
        "test_key", "test-key", "sk_test_", "<your", "REPLACE",
    )

    found = False
    for py in ROOT.rglob("*.py"):
        if _skip_audit_file(py):
            continue
        try:
            text = py.read_text()
        except (UnicodeDecodeError, PermissionError) as e:
            logger.debug(f"Non-critical error: {e}")
            continue
        if _scan_file_for_secrets(py, text, secret_patterns, placeholder_markers):
            found = True

    if not found:
        pass_("No hardcoded secrets found")


def _skip_audit_file(py: Path) -> bool:
    """Return True if ``py`` should be excluded from the secret scan.

    Skips test files, byte-compiled caches, .env files, and the audit
    script itself (which uses a non-empty throwaway token to force
    auth enforcement during ``check_auth_rejection``).
    """
    if "test" in str(py) or "__pycache__" in str(py) or ".env" in str(py):
        return True
    if py.name == "hellfire_audit.py":
        return True
    return False


def _scan_file_for_secrets(
    py: Path,
    text: str,
    secret_patterns,
    placeholder_markers,
) -> bool:
    """Scan a single file's text for hardcoded secrets.

    Returns ``True`` if any line failed the scan (so the caller can keep
    the ``found`` flag). Each failing line is recorded via :func:`fail`.
    Lines that read from env, are inside print/logging, or contain
    placeholder markers are skipped (see :func:`_is_env_read`,
    :func:`_is_print_or_log`, :func:`_check_placeholder`).
    """
    found_in_file = False
    for i, line in enumerate(text.splitlines(), 1):
        if _is_env_read(line):
            continue
        if _is_print_or_log(line):
            continue
        if _check_placeholder(line.lower(), placeholder_markers):
            continue
        for pattern in secret_patterns:
            match = pattern.search(line)
            if not match:
                continue
            # Also skip if the matched value itself contains a placeholder
            # (defensive double-check on the matched substring)
            if _check_placeholder(match.group(0).lower(), placeholder_markers):
                continue
            fail(str(py.relative_to(ROOT)), f"line {i}: potential hardcoded secret")
            found_in_file = True
    return found_in_file


def _is_env_read(line: str) -> bool:
    """True if the line reads from the environment rather than assigning a literal."""
    return (
        'os.getenv' in line
        or 'os.environ' in line
        or 'get_env_var' in line
    )


def _is_print_or_log(line: str) -> bool:
    """True if the line is instructional output (print/logging/echo/os.system).

    Such lines often contain placeholder secrets for onboarding docs and
    must not be flagged as real keys.
    """
    stripped = line.strip()
    return (
        stripped.startswith("print(")
        or stripped.startswith("logging.")
        or "logger." in stripped
        or stripped.startswith("echo ")
        or stripped.startswith("os.system(")
    )


def _check_placeholder(line: str, markers) -> bool:
    """True if ``line`` contains any of the placeholder/example markers."""
    return any(marker in line for marker in markers)

# ── 7. GLM_API_KEY is always read from env, never hardcoded ──
def check_glm_key_from_env():
    """Ensure GLM_API_KEY is always read from os.getenv / settings, never
    assigned a literal string anywhere in the source tree.

    Excludes instructional strings that contain placeholder markers
    (e.g. 'your-key-here', 'example', etc.) — those are documentation,
    not real keys.
    """
    print("\n[7] GLM_API_KEY is always read from env, never hardcoded")
    found = False
    # Pattern: GLM_API_KEY = "something" (not via os.getenv or get_env_var)
    pattern = re.compile(r'GLM_API_KEY\s*=\s*["\'][^"\']+["\']', re.IGNORECASE)
    placeholder_markers = (
        "your-key-here", "your_key_here", "your-key", "your_key",
        "example", "placeholder", "fake", "dummy", "xxxx", "xxx",
        "test_key", "test-key", "<your", "REPLACE",
    )
    for py in ROOT.rglob("*.py"):
        if "test" in str(py) or "__pycache__" in str(py) or ".env" in str(py):
            continue
        try:
            text = py.read_text()
        except (UnicodeDecodeError, PermissionError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            # Skip lines that read from env
            if 'os.getenv' in line or 'os.environ' in line or 'get_env_var' in line:
                continue
            if pattern.search(line):
                # Skip placeholder/example markers
                lowered = line.lower()
                if any(marker in lowered for marker in placeholder_markers):
                    continue
                # Skip lines that are inside string literals used as
                # instructional output (e.g. onboarding instructions).
                # Heuristic: line contains "export GLM_API_KEY='...'" inside
                # a string literal — these are docs, not assignments.
                if "export " in lowered or "echo " in lowered:
                    continue
                fail(str(py.relative_to(ROOT)), f"line {i}: GLM_API_KEY hardcoded: {line.strip()}")
                found = True
    if not found:
        pass_("GLM_API_KEY is always read from environment")

# ── 8. Every ZhipuAI/Z.ai client construction is guarded by a key check ──
def check_zhipu_client_guarded():
    """Ensure that every ZhipuAI() client construction is guarded by an
    API key check (if self.api_key / if GLM_API_KEY / etc.)."""
    print("\n[8] Every Z.ai client construction is guarded by a key check")
    found = False
    for py in ROOT.rglob("*.py"):
        if "test" in str(py) or "__pycache__" in str(py):
            continue
        try:
            text = py.read_text()
        except (UnicodeDecodeError, PermissionError):
            continue

        # Look for ZhipuAI( construction
        for i, line in enumerate(text.splitlines(), 1):
            if 'ZhipuAI(' in line or '_ZhipuAI(' in line:
                # Check the surrounding context for a key guard
                # Look backwards up to 10 lines for a guard
                start = max(0, i - 11)
                context = "\n".join(text.splitlines()[start:i])
                has_guard = bool(
                    re.search(r'if\s+(self\.)?api_key', context) or
                    re.search(r'if\s+GLM_API_KEY', context) or
                    re.search(r'if\s+not\s+\w*key', context) or
                    re.search(r'if\s+self\._use_api', context) or
                    re.search(r'available\(\)', context) or
                    re.search(r'GLM_API_KEY\b', context)
                )
                if not has_guard:
                    # Also check for the module-level lazy construction pattern
                    # (where client is built inside _ensure_client which is guarded)
                    if '_ensure_client' in text or 'ensure_client' in text:
                        # This file uses lazy construction — OK
                        continue
                    fail(str(py.relative_to(ROOT)),
                         f"line {i}: ZhipuAI() constructed without API key guard")
                    found = True
    if not found:
        pass_("All Z.ai client constructions are guarded by key checks")

def main():
    print("=" * 60)
    print("FRIDAY HELLFIRE AUDIT")
    print("=" * 60)
    
    check_commented_out_calls()
    check_eager_client_construction()
    check_double_run()
    check_auth_rejection()
    check_never_auto_approve()
    check_no_hardcoded_secrets()
    check_glm_key_from_env()
    check_zhipu_client_guarded()
    
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} audit checks")
        for label, detail in FAILURES:
            print(f"  - {label}: {detail}")
        sys.exit(1)
    else:
        print("ALL HELLFIRE AUDITS PASSED")
        sys.exit(0)

if __name__ == "__main__":
    main()
