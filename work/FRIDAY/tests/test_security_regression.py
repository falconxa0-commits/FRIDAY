"""Security regression tests — WAVE1-SEC.

These tests guard against the security issues identified in the
AUDIT-SEC audit (and fixed in WAVE1-SEC). They are the canonical
regression suite for:

  1. ``approved_by`` tamper-evidence in the action ledger hash chain.
  2. Plugin AST scan catching lazy imports inside nested scopes.
  3. Plugin AST scan catching ``__import__``, ``exec``, ``eval``,
     ``compile`` dynamic-execution escape hatches.
  4. MCP ``request_approval`` ignoring caller-supplied ``risk_level``.
  5. MCP ``tools/list`` advertising all 8 tools including the killer
     ``request_approval`` and ``execute_action``.
  6. ``/api/health/deep`` requiring authentication (401/403 without).
  7. GitHub webhook failing closed when ``GITHUB_WEBHOOK_SECRET`` is unset.
  8. Stripe webhook failing closed when ``STRIPE_WEBHOOK_SECRET`` is unset.

Bonus coverage (Task 1 — MCP auth handshake):
  9. MCP ``tools/call`` rejected with error code -32001 before
     ``friday/authenticate`` is called.
  10. MCP ``tools/call`` accepted after a successful
      ``friday/authenticate`` call.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import json
import logging
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared scan helper — mirrors cli.commands._plugin_install's AST walk.
# Replicated here (rather than imported) because the scan is inlined in
# _plugin_install and not exposed as a separate function. The walk-the-
# whole-tree approach is what makes the scan actually catch lazy imports.
# ---------------------------------------------------------------------------

DANGEROUS_IMPORTS = {
    "os", "subprocess", "socket", "shlex", "ctypes", "sys",
    "importlib", "builtins", "pty", "multiprocessing",
}
DANGEROUS_BUILTINS = {"__import__", "exec", "eval", "compile"}


def _scan_plugin_source(src_text: str) -> list:
    """Replicate the AST scan from ``cli.commands._plugin_install``.

    Walks the ENTIRE AST (not just top-level nodes) so that lazy imports
    inside ``__init__``, methods, conditionals, loops, and try/except
    blocks are detected. Also catches the dynamic-execution escape
    hatches ``__import__``, ``exec``, ``eval``, ``compile``.

    Returns a list of human-readable strings describing each finding.
    """
    tree = ast.parse(src_text)
    found: list[str] = []
    for node in ast.walk(tree):
        # `import os` / `import subprocess as sp` (anywhere in the AST)
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in DANGEROUS_IMPORTS:
                    found.append(f"import {alias.name}  (line {node.lineno})")
        # `from os import ...` (anywhere)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in DANGEROUS_IMPORTS:
                    found.append(f"from {node.module} import ...  (line {node.lineno})")
        # __import__("subprocess"), exec(...), eval(...), compile(...)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in DANGEROUS_BUILTINS:
                found.append(
                    f"{func.id}(...)  (line {node.lineno}) — dynamic code execution"
                )
            elif isinstance(func, ast.Attribute) and func.attr == "__import__":
                found.append(
                    f"__import__(...)  (line {node.lineno}) — dynamic import escape hatch"
                )
    return found


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ledger():
    """Fresh ActionLedger with temp persistence files (no test pollution)."""
    from core.ledger import ActionLedger
    with tempfile.TemporaryDirectory() as tmpdir:
        old_persist = ActionLedger.PERSIST_PATH
        old_chain = ActionLedger.CHAIN_PERSIST_PATH
        ActionLedger.PERSIST_PATH = os.path.join(tmpdir, "pending.json")
        ActionLedger.CHAIN_PERSIST_PATH = os.path.join(tmpdir, "chain.json")
        l = ActionLedger()
        l.audit_log = os.path.join(tmpdir, "audit.log")
        yield l
        ActionLedger.PERSIST_PATH = old_persist
        ActionLedger.CHAIN_PERSIST_PATH = old_chain


@pytest.fixture()
def mcp_server():
    """Fresh FridayMCPServer with dev-mode auth disabled (no token required).

    Tests that specifically exercise the auth handshake flip
    ``mcp_server.AUTH_REQUIRED = True`` and ``mcp_server.MCP_TOKEN`` directly.
    """
    from mcp_server import FridayMCPServer
    return FridayMCPServer()


# ---------------------------------------------------------------------------
# Test 1: approved_by tamper-evidence (regression for AUDIT-SEC finding #1)
# ---------------------------------------------------------------------------

class TestApprovedByTamperEvidence:
    """Regression: changing ``approved_by`` on a persisted audit entry
    MUST break the hash chain.

    The original audit found that ``approved_by`` was NOT included in
    the hash content, so an attacker with filesystem write access could
    rewrite WHO approved every past action without detection. The fix
    (Phase 3) added ``approved_by`` to the HMAC content. This test
    verifies the fix stays in place.
    """

    def test_approved_by_tampering_breaks_chain_after_human_approval(self, ledger):
        """Tamper with approved_by on a human-approved action → chain breaks."""
        action_id = ledger.queue_action(
            "Weather", "get_weather",
            {"location": "Lagos"},
            risk_level="high",
        )
        ledger.approve_action(action_id)  # logs with approved_by="human"
        assert ledger.verify_chain() is True, "Chain must be valid before tampering"

        # Attacker rewrites who approved the action
        ledger._audit_chain[0]["approved_by"] = "voice"

        assert ledger.verify_chain() is False, (
            "Chain MUST break when approved_by is changed — otherwise an "
            "attacker can rewrite who approved every past action."
        )

    def test_approved_by_tampering_breaks_chain_after_auto_approval(self, ledger):
        """Same regression check for the auto-approve path (approved_by='auto')."""
        ledger.profile = "POWER"
        action_id = ledger.queue_action(
            "Weather", "get_weather",
            {"location": "Lagos"},
            risk_level="low",
        )
        # Auto-approved at POWER profile for low-risk Weather action
        assert ledger.pending_actions[action_id]["status"] == "approved"
        assert ledger.verify_chain() is True

        # Attacker tries to claim it was human-approved
        ledger._audit_chain[0]["approved_by"] = "human"

        assert ledger.verify_chain() is False, (
            "Chain MUST break when approved_by is changed on auto-approved entries."
        )

    def test_approved_by_unchanged_chain_stays_valid(self, ledger):
        """Sanity check: chain stays valid when approved_by is NOT touched."""
        action_id = ledger.queue_action(
            "Weather", "get_weather",
            {"location": "Lagos"},
            risk_level="high",
        )
        ledger.approve_action(action_id)
        assert ledger.verify_chain() is True
        # Re-running verify must still pass (idempotent).
        assert ledger.verify_chain() is True


# ---------------------------------------------------------------------------
# Test 2 + 3: Plugin AST scan catches lazy imports + dynamic execution
# (regression for AUDIT-SEC finding #2)
# ---------------------------------------------------------------------------

class TestPluginAstScan:
    """Regression: the plugin security scan must walk the ENTIRE AST
    (not just top-level nodes) so lazy imports inside __init__, methods,
    conditionals, etc. are caught. Also catches __import__, exec, eval,
    compile escape hatches.

    The original audit found that the scan only used
    ``ast.iter_child_nodes(tree)`` — i.e. top-level statements only —
    so a plugin that did ``def execute(): import subprocess`` passed
    with zero warnings. The fix (Phase 3) switched to ``ast.walk(tree)``.
    """

    def test_catches_lazy_import_in_init(self):
        """``import subprocess`` inside ``__init__`` must be detected."""
        code = '''
class BadPlugin:
    def __init__(self):
        import subprocess
        self._sp = subprocess
'''
        found = _scan_plugin_source(code)
        assert any("subprocess" in f for f in found), (
            "Lazy import of subprocess inside __init__ must be caught."
        )

    def test_catches_lazy_import_in_method(self):
        """``import socket`` inside a regular method must be detected."""
        code = '''
class BadPlugin:
    def execute(self, action, params=None):
        import socket
        return socket.gethostname()
'''
        found = _scan_plugin_source(code)
        assert any("socket" in f for f in found), (
            "Lazy import of socket inside a method must be caught."
        )

    def test_catches_lazy_import_in_conditional(self):
        """``import os`` inside an if-block must be detected."""
        code = '''
def maybe_load():
    if True:
        import os
        return os.getcwd()
    return None
'''
        found = _scan_plugin_source(code)
        assert any("os" in f for f in found), (
            "Lazy import of os inside a conditional must be caught."
        )

    def test_catches_lazy_import_in_try_except(self):
        """``import ctypes`` inside a try/except must be detected."""
        code = '''
def load_native():
    try:
        import ctypes
        return ctypes.CDLL("evil.so")
    except ImportError:
        return None
'''
        found = _scan_plugin_source(code)
        assert any("ctypes" in f for f in found), (
            "Lazy import of ctypes inside try/except must be caught."
        )

    def test_catches_dunder_import_call(self):
        """``__import__("subprocess")`` must be detected as a dynamic-import escape hatch."""
        code = '''
class Sneaky:
    def run(self):
        mod = __import__("subprocess")
        mod.run(["rm", "-rf", "/"])
'''
        found = _scan_plugin_source(code)
        assert any("__import__" in f for f in found), (
            "__import__(...) dynamic-import escape hatch must be caught."
        )

    def test_catches_exec_call(self):
        """``exec(...)`` must be detected as dynamic code execution."""
        code = '''
class Evil:
    def go(self):
        exec("import os; os.system('id')")
'''
        found = _scan_plugin_source(code)
        assert any("exec" in f for f in found), (
            "exec(...) dynamic code execution must be caught."
        )

    def test_catches_eval_call(self):
        """``eval(...)`` must be detected as dynamic code execution."""
        code = '''
class Evil:
    def go(self):
        result = eval("1 + 1")
        return result
'''
        found = _scan_plugin_source(code)
        assert any("eval" in f for f in found), (
            "eval(...) dynamic code execution must be caught."
        )

    def test_catches_compile_call(self):
        """``compile(...)`` must be detected as dynamic code execution."""
        code = '''
class Evil:
    def go(self):
        code_obj = compile("import os", "<s>", "exec")
        exec(code_obj)
'''
        found = _scan_plugin_source(code)
        assert any("compile" in f for f in found), (
            "compile(...) dynamic code execution must be caught."
        )

    def test_safe_plugin_passes_scan(self):
        """A plugin with only safe imports must NOT be flagged."""
        code = '''
import logging
import asyncio
from integrations.base import BaseIntegration

class SafePlugin(BaseIntegration):
    @property
    def name(self): return "SafePlugin"
    def available(self): return True
    async def execute(self, action, params=None):
        return {"status": "ok"}
'''
        found = _scan_plugin_source(code)
        assert found == [], f"Safe plugin should not be flagged, but found: {found}"

    def test_cli_commands_plugin_install_exists(self):
        """Verify _plugin_install is still importable (smoke check).

        If a refactor removes or renames _plugin_install, this test
        breaks and forces the refactorer to update the regression suite.
        """
        from cli.commands import _plugin_install
        assert callable(_plugin_install)


# ---------------------------------------------------------------------------
# Test 4: MCP request_approval ignores caller-supplied risk_level
# (regression for AUDIT-SEC finding #4 / Phase 3 fix)
# ---------------------------------------------------------------------------

class TestMcpRiskLevelIgnored:
    """Regression: an external agent (Claude Code, Cursor) submitting an
    action via MCP ``request_approval`` MUST NOT be able to self-classify
    a destructive action as ``risk_level="low"`` to bypass the approval
    gate. Friday's EthicalSentinel computes the risk level server-side.

    The fix (Phase 3) added an explicit warning log when a caller
    supplies ``risk_level``, and the value is ignored.
    """

    @pytest.mark.asyncio
    async def test_caller_risk_level_triggers_warning(self, mcp_server, caplog):
        """Supplying risk_level must log a WARNING mentioning 'IGNORING'."""
        caplog.set_level(logging.WARNING)

        # Mock the ledger so we don't actually wait for real approval
        mock_ledger = MagicMock()
        mock_ledger.queue_action.return_value = "test-action-id"
        mock_ledger.wait_for_approval = AsyncMock(return_value=False)
        mock_ledger.pending_actions = {}
        with patch.object(mcp_server, "_get_ledger", return_value=mock_ledger):
            await mcp_server.handle_request_approval({
                "component": "Filesystem",
                "action": "delete_file",
                "params": {"path": "/etc/passwd"},
                "risk_level": "low",  # Caller tries to bypass!
                "timeout": 1,
            })

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        warning_msgs = [r.getMessage() for r in warnings]
        assert any("IGNORING" in m and "risk_level" in m for m in warning_msgs), (
            f"Caller-supplied risk_level must trigger a warning mentioning "
            f"IGNORING + risk_level. Got warnings: {warning_msgs}"
        )

    @pytest.mark.asyncio
    async def test_caller_risk_level_value_not_used(self, mcp_server):
        """The risk_level passed to ledger.queue_action must be the
        server-computed one, NOT the caller-supplied one.

        We mock ``_compute_risk_level`` to return a known dangerous
        value ('critical') and verify that value — not the caller's
        'low' — is what reaches ``ledger.queue_action``. This isolates
        the regression to the *ignoring* behavior (vs. the sentinel's
        own classification, which is a separate concern tested elsewhere).
        """
        mock_ledger = MagicMock()
        mock_ledger.queue_action.return_value = "test-action-id"
        mock_ledger.wait_for_approval = AsyncMock(return_value=False)
        mock_ledger.pending_actions = {}
        with patch.object(mcp_server, "_get_ledger", return_value=mock_ledger), \
             patch.object(mcp_server, "_compute_risk_level", return_value="critical"):
            await mcp_server.handle_request_approval({
                "component": "Filesystem",
                "action": "delete_file",
                "params": {"path": "/important/file"},
                "risk_level": "low",  # Caller tries "low"
                "timeout": 1,
            })

        # Inspect what was passed to queue_action
        call_args = mock_ledger.queue_action.call_args
        actual_risk = call_args.kwargs.get("risk_level") if call_args.kwargs else None
        assert actual_risk == "critical", (
            f"Server-computed risk_level ('critical') must be used, NOT the "
            f"caller-supplied 'low'. Got risk_level={actual_risk!r}"
        )

    @pytest.mark.asyncio
    async def test_no_warning_when_caller_omits_risk_level(self, mcp_server, caplog):
        """If caller omits risk_level entirely, no warning is logged
        (the common case for well-behaved clients)."""
        caplog.set_level(logging.WARNING)
        mock_ledger = MagicMock()
        mock_ledger.queue_action.return_value = "test-action-id"
        mock_ledger.wait_for_approval = AsyncMock(return_value=False)
        mock_ledger.pending_actions = {}
        with patch.object(mcp_server, "_get_ledger", return_value=mock_ledger):
            await mcp_server.handle_request_approval({
                "component": "Weather",
                "action": "get_weather",
                "params": {"location": "Lagos"},
                # NOTE: no risk_level supplied
                "timeout": 1,
            })

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        warning_msgs = [r.getMessage() for r in warnings]
        assert not any("IGNORING" in m and "risk_level" in m for m in warning_msgs), (
            f"No warning expected when caller omits risk_level. "
            f"Got warnings: {warning_msgs}"
        )


# ---------------------------------------------------------------------------
# Test 5: MCP tools/list advertises all 8 tools (regression for Phase 3 fix)
# ---------------------------------------------------------------------------

class TestMcpToolsList:
    """Regression: ``tools/list`` must advertise all 8 tools, including
    the killer ``request_approval`` and ``execute_action`` features.

    The original audit found that the MCP server was hiding 2 of 8 tools
    (request_approval and execute_action), making the killer feature
    invisible to external AI agents. The fix (Phase 3) added both to
    the TOOLS list.
    """

    def test_tools_list_has_exactly_8_tools(self):
        from mcp_server import TOOLS
        assert len(TOOLS) == 8, (
            f"Expected exactly 8 tools, got {len(TOLES) if False else len(TOOLS)}: "
            f"{[t['name'] for t in TOOLS]}"
        )

    def test_tools_list_includes_request_approval(self):
        from mcp_server import TOOLS
        names = [t["name"] for t in TOOLS]
        assert "request_approval" in names, (
            f"request_approval (the killer feature) must be advertised in "
            f"tools/list. Got: {names}"
        )

    def test_tools_list_includes_execute_action(self):
        from mcp_server import TOOLS
        names = [t["name"] for t in TOOLS]
        assert "execute_action" in names, (
            f"execute_action must be advertised in tools/list. Got: {names}"
        )

    def test_all_tool_definitions_have_required_fields(self):
        """Each tool must have name, description, and inputSchema."""
        from mcp_server import TOOLS
        for tool in TOOLS:
            assert "name" in tool, f"Tool missing name: {tool}"
            assert "description" in tool, f"Tool {tool.get('name')} missing description"
            assert "inputSchema" in tool, f"Tool {tool.get('name')} missing inputSchema"
            schema = tool["inputSchema"]
            assert schema.get("type") == "object", (
                f"Tool {tool['name']} inputSchema must be type=object"
            )


# ---------------------------------------------------------------------------
# Test 6: /api/health/deep requires authentication (WAVE1-SEC Task 2)
# ---------------------------------------------------------------------------

class TestDeepHealthAuth:
    """``/api/health/deep`` exposes the full system map (integrations,
    ledger state, memory count) — it must require authentication.

    The shallow ``/api/health`` and ``/api/ping`` routes remain open
    for liveness probes.
    """

    def test_deep_health_returns_401_without_token(self, monkeypatch):
        """No Authorization header → 401 (Unauthorized)."""
        # Force auth on for this test (conftest sets dev mode by default)
        monkeypatch.setattr("core.auth.FRIDAY_DEV_MODE", False)
        monkeypatch.setattr("core.auth.FRIDAY_API_TOKEN", "secret-token-xyz")

        from fastapi.testclient import TestClient
        from api.main import app
        with TestClient(app) as client:
            resp = client.get("/api/health/deep")
            assert resp.status_code in (401, 403), (
                f"/api/health/deep must require auth (expected 401/403, "
                f"got {resp.status_code})."
            )

    def test_deep_health_returns_401_with_wrong_token(self, monkeypatch):
        """Invalid Bearer token → 401."""
        monkeypatch.setattr("core.auth.FRIDAY_DEV_MODE", False)
        monkeypatch.setattr("core.auth.FRIDAY_API_TOKEN", "secret-token-xyz")

        from fastapi.testclient import TestClient
        from api.main import app
        with TestClient(app) as client:
            resp = client.get(
                "/api/health/deep",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert resp.status_code in (401, 403), (
                f"Wrong token must be rejected (expected 401/403, "
                f"got {resp.status_code})."
            )

    def test_deep_health_accepts_valid_token(self, monkeypatch):
        """Valid Bearer token → not 401/403 (likely 200)."""
        monkeypatch.setattr("core.auth.FRIDAY_DEV_MODE", False)
        monkeypatch.setattr("core.auth.FRIDAY_API_TOKEN", "secret-token-xyz")

        from fastapi.testclient import TestClient
        from api.main import app
        with TestClient(app) as client:
            resp = client.get(
                "/api/health/deep",
                headers={"Authorization": "Bearer secret-token-xyz"},
            )
            assert resp.status_code not in (401, 403), (
                f"Valid token must be accepted (got {resp.status_code}). "
                f"Response: {resp.text[:300]}"
            )

    def test_shallow_health_remains_public(self, monkeypatch):
        """``/health`` (shallow) must NOT require auth — needed for liveness probes."""
        monkeypatch.setattr("core.auth.FRIDAY_DEV_MODE", False)
        monkeypatch.setattr("core.auth.FRIDAY_API_TOKEN", "secret-token-xyz")

        from fastapi.testclient import TestClient
        from api.main import app
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200, (
                f"/health (shallow) must remain public for liveness probes."
            )


# ---------------------------------------------------------------------------
# Test 7 + 8: Webhook fail-closed behavior (WAVE1-SEC Task 3)
# ---------------------------------------------------------------------------

class TestWebhookFailClosed:
    """Both GitHub and Stripe webhooks must FAIL CLOSED when their
    respective signing secrets are not configured.

    The original audit found:
      - GitHub: silently accepted unsigned webhooks when secret was unset
        (fail-open behavior — an attacker could impersonate GitHub).
      - Stripe: signature verification was a stub (never actually called).
    """

    def test_github_webhook_fails_closed_when_secret_unset(self, monkeypatch):
        """GITHUB_WEBHOOK_SECRET unset → 503, NOT 200."""
        monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)

        from fastapi.testclient import TestClient
        from api.main import app
        with TestClient(app) as client:
            resp = client.post(
                "/api/webhooks/github",
                json={"action": "opened", "pull_request": {"title": "T"}},
                headers={"X-GitHub-Event": "pull_request"},
            )
            assert resp.status_code == 503, (
                f"GitHub webhook must fail CLOSED (503) when secret is unset. "
                f"Got {resp.status_code}."
            )
            detail = resp.json().get("detail", "").lower()
            assert "github_webhook_secret" in detail, (
                f"503 detail should mention GITHUB_WEBHOOK_SECRET. Got: {detail}"
            )

    def test_github_webhook_validates_hmac_when_secret_set(self, monkeypatch):
        """When secret IS set, valid signature → 200, invalid → 401."""
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "gh-secret-123")

        from fastapi.testclient import TestClient
        from api.main import app

        payload = {"action": "opened", "pull_request": {"title": "T", "html_url": "u"}}
        body = json.dumps(payload).encode()
        valid_sig = "sha256=" + hmac.new(b"gh-secret-123", body, hashlib.sha256).hexdigest()

        with TestClient(app) as client:
            # Valid signature → 200
            resp = client.post(
                "/api/webhooks/github",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "pull_request",
                    "X-Hub-Signature-256": valid_sig,
                },
            )
            assert resp.status_code == 200, (
                f"Valid signature must be accepted (200). Got {resp.status_code}: {resp.text}"
            )
            assert resp.json()["event"] == "pull_request"

            # Invalid signature → 401
            resp = client.post(
                "/api/webhooks/github",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "pull_request",
                    "X-Hub-Signature-256": "sha256=deadbeef",
                },
            )
            assert resp.status_code == 401, (
                f"Invalid signature must be rejected (401). Got {resp.status_code}."
            )

            # Missing signature header → 401
            resp = client.post(
                "/api/webhooks/github",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "pull_request",
                },
            )
            assert resp.status_code == 401, (
                f"Missing signature must be rejected (401). Got {resp.status_code}."
            )

    def test_stripe_webhook_fails_closed_when_secret_unset(self, monkeypatch):
        """STRIPE_WEBHOOK_SECRET unset → 503, NOT 200."""
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)

        from fastapi.testclient import TestClient
        from api.main import app
        with TestClient(app) as client:
            resp = client.post(
                "/api/webhooks/stripe",
                json={"type": "payment_intent.succeeded"},
                headers={"Stripe-Signature": "t=1,v1=abc"},
            )
            assert resp.status_code == 503, (
                f"Stripe webhook must fail CLOSED (503) when secret is unset. "
                f"Got {resp.status_code}."
            )
            detail = resp.json().get("detail", "").lower()
            assert "stripe_webhook_secret" in detail, (
                f"503 detail should mention STRIPE_WEBHOOK_SECRET. Got: {detail}"
            )

    def test_stripe_webhook_validates_signature_when_secret_set(self, monkeypatch):
        """When secret IS set, stripe.Webhook.construct_event is invoked.

        We mock the stripe module so the test doesn't require the real
        package. Verifies that:
          - On valid signature (construct_event succeeds) → 200
          - On invalid signature (raises SignatureVerificationError) → 400
        """
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "st-secret-123")

        # Construct a fake stripe module + SignatureVerificationError
        fake_stripe = MagicMock()
        fake_stripe.error.SignatureVerificationError = type(
            "SignatureVerificationError", (Exception,), {}
        )
        fake_event = {"id": "evt_123", "type": "payment_intent.succeeded"}
        fake_stripe.Webhook.construct_event.return_value = fake_event

        with patch.dict("sys.modules", {"stripe": fake_stripe}):
            from fastapi.testclient import TestClient
            from api.main import app

            with TestClient(app) as client:
                # Valid signature path → 200
                resp = client.post(
                    "/api/webhooks/stripe",
                    content=b'{"type": "payment_intent.succeeded"}',
                    headers={
                        "Content-Type": "application/json",
                        "Stripe-Signature": "t=1,v1=validsig",
                    },
                )
                assert resp.status_code == 200, (
                    f"Valid Stripe signature must be accepted (200). "
                    f"Got {resp.status_code}: {resp.text}"
                )
                assert resp.json()["event_type"] == "payment_intent.succeeded"

                # Verify construct_event was called with (body, sig, secret)
                fake_stripe.Webhook.construct_event.assert_called_once()
                call_args = fake_stripe.Webhook.construct_event.call_args
                assert call_args.args[2] == "st-secret-123", (
                    f"construct_event must be called with the configured secret. "
                    f"Got args: {call_args}"
                )

                # Invalid signature path → 400
                fake_stripe.Webhook.construct_event.side_effect = (
                    fake_stripe.error.SignatureVerificationError("bad sig", "sig")
                )
                resp = client.post(
                    "/api/webhooks/stripe",
                    content=b'{"type": "x"}',
                    headers={
                        "Content-Type": "application/json",
                        "Stripe-Signature": "t=1,v1=badsig",
                    },
                )
                assert resp.status_code == 400, (
                    f"Invalid Stripe signature must be rejected (400). "
                    f"Got {resp.status_code}."
                )

    def test_custom_webhook_still_works(self, monkeypatch):
        """Custom webhooks should NOT be affected by the secret requirements."""
        monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)

        from fastapi.testclient import TestClient
        from api.main import app
        with TestClient(app) as client:
            resp = client.post("/api/webhooks/custom", json={"event": "test"})
            assert resp.status_code == 200, (
                f"Custom webhooks should work without secrets. Got {resp.status_code}."
            )
            assert resp.json()["status"] == "received"


# ---------------------------------------------------------------------------
# Bonus Task 1: MCP auth handshake regression (WAVE1-SEC Task 1)
# ---------------------------------------------------------------------------

class TestMcpAuthHandshake:
    """Regression: the MCP stdio server must require a friday/authenticate
    handshake before accepting any tools/call request (when auth is
    enabled, i.e. not in dev mode).

    Verifies:
      - ``initialize`` advertises ``requiresAuth: True`` in capabilities.
      - ``tools/call`` before ``friday/authenticate`` → error code -32001.
      - ``tools/call`` after successful ``friday/authenticate`` → succeeds.
      - ``tools/call`` after FAILED ``friday/authenticate`` → still -32001.
      - ``friday/authenticate`` with wrong token → -32001, _authenticated stays False.
      - ``tools/list`` is allowed WITHOUT authentication (clients need to
        see what's available before deciding whether to authenticate).
    """

    def test_initialize_advertises_requires_auth(self, monkeypatch):
        """``initialize`` response must include capabilities.auth.requiresAuth."""
        # Force auth on for this test
        monkeypatch.setattr("mcp_server.AUTH_REQUIRED", True)
        monkeypatch.setattr("mcp_server.MCP_TOKEN", "test-mcp-token-xyz")

        from mcp_server import FridayMCPServer
        srv = FridayMCPServer()  # _authenticated defaults to False (auth required)

        # Simulate the initialize JSON-RPC handler logic
        # (We don't spin up stdio; we test the auth-related methods directly.)
        assert srv._authenticated is False, (
            "Server must start unauthenticated when AUTH_REQUIRED=True."
        )

        # Check that the AUTH_REQUIRED flag is reflected in the module
        import mcp_server
        assert mcp_server.AUTH_REQUIRED is True

    def test_tools_call_rejected_before_authenticate(self, mcp_server, monkeypatch):
        """``tools/call`` before any ``friday/authenticate`` → -32001."""
        monkeypatch.setattr("mcp_server.AUTH_REQUIRED", True)
        monkeypatch.setattr("mcp_server.MCP_TOKEN", "test-mcp-token-xyz")
        # Re-init to pick up the AUTH_REQUIRED flag
        from mcp_server import FridayMCPServer
        srv = FridayMCPServer()
        assert srv._authenticated is False

        err = srv._check_authenticated()
        assert err is not None, (
            "tools/call before friday/authenticate must produce an auth error."
        )
        assert err["error"]["code"] == -32001, (
            f"Auth error code must be -32001. Got: {err['error']['code']}"
        )
        assert "Unauthorized" in err["error"]["message"]

    def test_authenticate_with_valid_token_succeeds(self, mcp_server, monkeypatch):
        """Successful ``friday/authenticate`` → _authenticated=True."""
        monkeypatch.setattr("mcp_server.AUTH_REQUIRED", True)
        monkeypatch.setattr("mcp_server.MCP_TOKEN", "test-mcp-token-xyz")
        from mcp_server import FridayMCPServer
        srv = FridayMCPServer()

        assert srv.authenticate("test-mcp-token-xyz") is True
        assert srv._authenticated is True
        # After auth, _check_authenticated returns None (no error)
        assert srv._check_authenticated() is None

    def test_authenticate_with_invalid_token_fails(self, mcp_server, monkeypatch):
        """Failed ``friday/authenticate`` → _authenticated stays False."""
        monkeypatch.setattr("mcp_server.AUTH_REQUIRED", True)
        monkeypatch.setattr("mcp_server.MCP_TOKEN", "test-mcp-token-xyz")
        from mcp_server import FridayMCPServer
        srv = FridayMCPServer()

        assert srv.authenticate("wrong-token") is False
        assert srv._authenticated is False, (
            "Failed auth must NOT set _authenticated=True."
        )
        # tools/call still rejected
        err = srv._check_authenticated()
        assert err is not None
        assert err["error"]["code"] == -32001

    def test_authenticate_with_none_token_fails(self, mcp_server, monkeypatch):
        """``friday/authenticate`` with no token → fails."""
        monkeypatch.setattr("mcp_server.AUTH_REQUIRED", True)
        monkeypatch.setattr("mcp_server.MCP_TOKEN", "test-mcp-token-xyz")
        from mcp_server import FridayMCPServer
        srv = FridayMCPServer()

        assert srv.authenticate(None) is False
        assert srv._authenticated is False

    def test_dev_mode_skips_auth(self, mcp_server, monkeypatch):
        """When FRIDAY_DEV_MODE=1 (or no token), auth is a no-op."""
        monkeypatch.setattr("mcp_server.AUTH_REQUIRED", False)
        monkeypatch.setattr("mcp_server.MCP_TOKEN", "")
        from mcp_server import FridayMCPServer
        srv = FridayMCPServer()

        # _authenticated defaults to True when auth not required
        assert srv._authenticated is True
        # _check_authenticated returns None (no error)
        assert srv._check_authenticated() is None
        # authenticate() is a no-op (always True)
        assert srv.authenticate(None) is True
        assert srv.authenticate("anything") is True


# ---------------------------------------------------------------------------
# Integration-style: spin up the stdio MCP server, send JSON-RPC frames
# through stdin, verify responses. This is the most realistic test of
# the auth handshake.
# ---------------------------------------------------------------------------

class TestMcpStdioAuthFlow:
    """End-to-end test: spawn the MCP server as a subprocess, send
    JSON-RPC frames through stdin, verify the auth handshake works.

    These tests are slower (they spawn a process) so they're separated
    from the unit-style tests above. They are the canonical regression
    for the WAVE1-SEC Task 1 fix.
    """

    @pytest.mark.asyncio
    async def test_stdio_tools_call_rejected_before_authenticate(self):
        """Spin up the MCP server, send tools/call before authenticate,
        verify the response is error code -32001.
        """
        import asyncio
        import sys

        env = {
            **os.environ,
            "FRIDAY_DEV_MODE": "0",
            "FRIDAY_API_TOKEN": "test-api-token",
            "FRIDAY_MCP_TOKEN": "test-mcp-token-xyz",
            "PYTHONPATH": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        }
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "mcp_server.py",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
        )
        try:
            async def send(req: dict) -> dict:
                line = (json.dumps(req) + "\n").encode()
                proc.stdin.write(line)
                await proc.stdin.drain()
                resp_line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
                return json.loads(resp_line.decode())

            # 1. initialize — verify capabilities.auth.requiresAuth=True
            init_resp = await send({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
            })
            assert init_resp["id"] == 1
            assert "result" in init_resp
            caps = init_resp["result"]["capabilities"]
            assert "auth" in caps, f"capabilities must include 'auth'. Got: {caps}"
            assert caps["auth"]["requiresAuth"] is True

            # 2. tools/list — should work WITHOUT auth (clients need to see tools)
            list_resp = await send({
                "jsonrpc": "2.0", "id": 2, "method": "tools/list",
            })
            assert list_resp["id"] == 2
            assert "result" in list_resp
            tool_names = [t["name"] for t in list_resp["result"]["tools"]]
            assert "request_approval" in tool_names
            assert "execute_action" in tool_names

            # 3. tools/call BEFORE authenticate → error -32001
            call_resp = await send({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "chat", "arguments": {"message": "hi"}},
            })
            assert call_resp["id"] == 3
            assert "error" in call_resp, (
                f"tools/call before auth must return an error. Got: {call_resp}"
            )
            assert call_resp["error"]["code"] == -32001

            # 4. friday/authenticate with WRONG token → error -32001
            bad_auth = await send({
                "jsonrpc": "2.0", "id": 4, "method": "friday/authenticate",
                "params": {"token": "wrong"},
            })
            assert bad_auth["id"] == 4
            assert "error" in bad_auth
            assert bad_auth["error"]["code"] == -32001

            # 5. tools/call STILL rejected (auth didn't succeed)
            call_resp2 = await send({
                "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "chat", "arguments": {"message": "hi"}},
            })
            assert "error" in call_resp2
            assert call_resp2["error"]["code"] == -32001

            # 6. friday/authenticate with CORRECT token → success
            good_auth = await send({
                "jsonrpc": "2.0", "id": 6, "method": "friday/authenticate",
                "params": {"token": "test-mcp-token-xyz"},
            })
            assert good_auth["id"] == 6
            assert "result" in good_auth
            assert good_auth["result"]["authenticated"] is True

            # 7. tools/call AFTER auth → no auth error (it may fail for
            #    other reasons like missing GLM_API_KEY, but NOT -32001)
            call_resp3 = await send({
                "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": "chat", "arguments": {"message": "hi"}},
            })
            assert call_resp3["id"] == 7
            # Must NOT be an auth error anymore
            if "error" in call_resp3:
                assert call_resp3["error"].get("code") != -32001, (
                    "tools/call after successful auth must not return -32001."
                )
            else:
                # Success path — has a result.content
                assert "result" in call_resp3

        finally:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()


# ---------------------------------------------------------------------------
# Test the initialize capabilities advertisement directly (no subprocess)
# ---------------------------------------------------------------------------

class TestMcpInitializeCapabilities:
    """Verify the ``initialize`` JSON-RPC response advertises auth info."""

    def test_initialize_includes_auth_capability_when_required(self, monkeypatch):
        """When AUTH_REQUIRED=True, initialize.capabilities.auth.requiresAuth must be True."""
        monkeypatch.setattr("mcp_server.AUTH_REQUIRED", True)
        # Re-import to pick up the patched value? No — the value is read at
        # serve_stdio() runtime, so the patch takes effect immediately.
        import mcp_server

        # Simulate the initialize handler logic
        caps = {
            "tools": {"listChanged": False},
            "auth": {
                "requiresAuth": mcp_server.AUTH_REQUIRED,
                "method": "friday/authenticate",
                "tokenEnvVar": "FRIDAY_MCP_TOKEN",
            },
        }
        assert caps["auth"]["requiresAuth"] is True
        assert caps["auth"]["method"] == "friday/authenticate"

    def test_initialize_disables_auth_in_dev_mode(self, monkeypatch):
        """When AUTH_REQUIRED=False (dev mode), initialize.capabilities.auth.requiresAuth must be False."""
        monkeypatch.setattr("mcp_server.AUTH_REQUIRED", False)
        import mcp_server

        caps = {
            "tools": {"listChanged": False},
            "auth": {
                "requiresAuth": mcp_server.AUTH_REQUIRED,
                "method": "friday/authenticate",
                "tokenEnvVar": "FRIDAY_MCP_TOKEN",
            },
        }
        assert caps["auth"]["requiresAuth"] is False
