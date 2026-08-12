"""Complexity-reduction regression tests for IRON-CROWN-SWARM1.

These tests verify that the helpers extracted from the ten high-complexity
functions identified by EngineeringIntelligence still exist, still have
the expected signatures, and still produce correct results. They are
intentionally small and behavioural — they don't try to exercise every
edge of the refactored code paths (that's what the existing module
test files do); they just lock in the refactor's existence so it
cannot silently regress.

Run::

    python -m pytest tests/test_iron_crown_complexity.py -q
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# sandbox.py — SubprocessSandbox.execute() helpers + _sandbox_worker helpers
# ---------------------------------------------------------------------------
def test_sandbox_execute_helpers_exist():
    from core.runtime.security.sandbox import SubprocessSandbox

    for name in (
        "_validate_capabilities",
        "_validate_picklable",
        "_prepare_sandbox_config",
        "_run_subprocess",
        "_classify_outcome",
        "_apply_terminal_payload",
        "_check_memory_usage",
        "_audit_execution",
    ):
        assert hasattr(SubprocessSandbox, name), f"missing helper: {name}"


def test_sandbox_worker_helpers_exist():
    from core.runtime.security import sandbox

    for name in (
        "_worker_apply_env_isolation",
        "_worker_become_session_leader",
        "_worker_parse_resource_limits",
        "_worker_apply_rlimits",
        "_worker_install_alarm",
        "_worker_invoke_function",
    ):
        assert hasattr(sandbox, name), f"missing module-level helper: {name}"


def test_validate_capabilities_returns_none_when_no_policy():
    from core.runtime.security.sandbox import SandboxConfig, SubprocessSandbox

    sandbox = SubprocessSandbox()
    config = SandboxConfig(capabilities=["brain.chat"])
    # No policy_engine passed — should short-circuit to None.
    assert sandbox._validate_capabilities(config, None) is None


def test_validate_capabilities_returns_denied_result():
    from core.runtime.security.sandbox import SandboxConfig, SubprocessSandbox

    sandbox = SubprocessSandbox()
    policy = SimpleNamespace(
        evaluate=lambda cap: SimpleNamespace(allowed=False, reason="nope"),
    )
    config = SandboxConfig(capabilities=["brain.chat"])
    denied = sandbox._validate_capabilities(config, policy)
    assert denied is not None
    assert denied.status == "failed"
    assert "brain.chat" in denied.error


def test_validate_picklable_accepts_normal_function():
    from core.runtime.security.sandbox import SubprocessSandbox

    # Module-level functions are picklable; lambdas/closures are not.
    # Using a top-level def keeps this test portable across CPython.
    import core.runtime.security.sandbox as sandbox_mod

    candidate = SubprocessSandbox._validate_picklable(
        sandbox_mod.SubprocessSandbox, (), {},
    )
    assert candidate is None


def test_validate_picklable_rejects_closure():
    from core.runtime.security.sandbox import SubprocessSandbox

    # A function that closes over a local variable is not picklable on
    # spawn-based start methods — the closure cell can't be serialised.
    closure_val = "secret"

    def closured():
        return closure_val

    failed = SubprocessSandbox._validate_picklable(closured, (), {})
    assert failed is not None
    assert failed.status == "failed"
    assert "picklable" in failed.error


def test_prepare_sandbox_config_copies_fields():
    from core.runtime.security.sandbox import SandboxConfig, SubprocessSandbox

    sandbox = SubprocessSandbox(env_whitelist=["PATH", "HOME"])
    config = SandboxConfig(
        max_memory_mb=64,
        max_cpu_seconds=10,
        max_filesystem_paths=["/tmp/x"],
        network_allowed=False,
        env_vars={"FOO": "bar"},
    )
    cfg = sandbox._prepare_sandbox_config(config)
    assert cfg["max_memory_mb"] == 64
    assert cfg["max_cpu_seconds"] == 10
    assert cfg["max_filesystem_paths"] == ["/tmp/x"]
    assert cfg["env_vars"] == {"FOO": "bar"}
    assert cfg["env_whitelist"] == ["PATH", "HOME"]
    # Mutating the returned dict must NOT mutate the original config.
    cfg["env_vars"]["BAZ"] = "qux"
    assert "BAZ" not in config.env_vars


def test_worker_parse_resource_limits_returns_tuple():
    from core.runtime.security.sandbox import _worker_parse_resource_limits

    config_dict = {
        "max_memory_mb": "128",
        "max_cpu_seconds": "20",
        "max_filesystem_paths": ["/a", "/b"],
        "network_allowed": True,
    }
    mem, cpu, fs, net = _worker_parse_resource_limits(config_dict)
    assert (mem, cpu, fs, net) == (128, 20, ["/a", "/b"], True)


def test_worker_apply_env_isolation_whitelists_and_overlays():
    from core.runtime.security.sandbox import _worker_apply_env_isolation

    # Track every interaction with the fake os.environ in order.
    log: list = []

    class FakeEnviron(dict):
        def clear(self):
            log.append(("clear",))
            super().clear()

        def update(self, d):
            log.append(("update", dict(d)))
            super().update(d)

    fake_os = SimpleNamespace(environ=FakeEnviron({
        "PATH": "/usr/bin", "SECRET": "shh", "HOME": "/home/u",
    }))
    config_dict = {
        "env_whitelist": ["PATH", "HOME"],
        "env_vars": {"CUSTOM": "1"},
    }
    _worker_apply_env_isolation(fake_os, config_dict)

    # After clear() + update(kept), SECRET should be gone and PATH/HOME kept.
    # Then update(extra_env) adds CUSTOM.
    final_environ = dict(fake_os.environ)
    assert "SECRET" not in final_environ
    assert final_environ["PATH"] == "/usr/bin"
    assert final_environ["HOME"] == "/home/u"
    assert final_environ["CUSTOM"] == "1"


def test_classify_outcome_signal_killed_with_sigxcpu():
    import signal as _signal

    from core.runtime.security.sandbox import SandboxResult, SubprocessSandbox

    result = SandboxResult()
    SubprocessSandbox._classify_outcome(-_signal.SIGXCPU, [], result)
    assert result.status == "timeout"
    assert "SIGXCPU" in result.error


def test_classify_outcome_clean_exit_no_payload():
    from core.runtime.security.sandbox import SandboxResult, SubprocessSandbox

    result = SandboxResult()
    result.exit_code = 0
    SubprocessSandbox._classify_outcome(0, [], result)
    assert result.status == "success"
    assert result.output is None


def test_apply_terminal_payload_success_kind():
    from core.runtime.security.sandbox import SandboxResult, SubprocessSandbox

    result = SandboxResult()
    payload = [("_success", {"hello": "world"})]
    SubprocessSandbox._apply_terminal_payload(payload, result)
    assert result.status == "success"
    assert result.output == {"hello": "world"}


def test_apply_terminal_payload_failed_kind_with_dict():
    from core.runtime.security.sandbox import SandboxResult, SubprocessSandbox

    result = SandboxResult()
    payload = [("_failed", {"type": "ValueError", "message": "bad input"})]
    SubprocessSandbox._apply_terminal_payload(payload, result)
    assert result.status == "failed"
    assert result.error == "bad input"


def test_apply_terminal_payload_resource_warning_without_terminal():
    from core.runtime.security.sandbox import SandboxResult, SubprocessSandbox

    result = SandboxResult()
    payload = [("_resource_warning", "RLIMIT_AS unsupported")]
    SubprocessSandbox._apply_terminal_payload(payload, result)
    assert result.status == "failed"
    assert "Resource limit application failed" in result.error


def test_check_memory_usage_does_not_flip_below_ceiling(monkeypatch):
    from core.runtime.security.sandbox import (
        SandboxConfig, SandboxResult, SubprocessSandbox,
    )

    # Pin child RSS delta to 0 so the memory check is deterministic.
    monkeypatch.setattr(
        SubprocessSandbox, "_child_rss_kb", staticmethod(lambda: 0),
    )
    config = SandboxConfig(max_memory_mb=256)
    result = SandboxResult(status="success")
    SubprocessSandbox._check_memory_usage(config, result, peak_rss_bytes=0, rss_before_kb=0)
    assert result.memory_used_mb == 0
    assert result.status == "success"


def test_check_memory_usage_flips_when_exceeded(monkeypatch):
    from core.runtime.security.sandbox import (
        SandboxConfig, SandboxResult, SubprocessSandbox,
    )

    # Pin child RSS delta to 0 — the live peak alone drives the violation.
    monkeypatch.setattr(
        SubprocessSandbox, "_child_rss_kb", staticmethod(lambda: 0),
    )
    config = SandboxConfig(max_memory_mb=10)
    result = SandboxResult(status="success")
    # 50 MB peak — well above the 10 MB * 1.25 = 12.5 MB ceiling.
    SubprocessSandbox._check_memory_usage(
        config, result,
        peak_rss_bytes=50 * 1024 * 1024,
        rss_before_kb=0,
    )
    assert result.status == "failed"
    assert "Memory limit exceeded" in result.error


# ---------------------------------------------------------------------------
# engineering_council.py — _review_from_perspective() helpers
# ---------------------------------------------------------------------------
def test_engineering_council_review_helpers_exist():
    from core.engineering_council import EngineeringCouncil

    for name in (
        "_review_security",
        "_review_architecture",
        "_review_performance",
        "_review_reliability",
        "_review_devops",
        "_review_compliance",
        "_determine_verdict",
    ):
        assert hasattr(EngineeringCouncil, name), f"missing helper: {name}"


def test_review_security_flags_secret_keywords():
    from core.engineering_council import EngineeringCouncil

    risks, concerns, alts, recs, conf = [], [], [], [], 70
    new_conf = EngineeringCouncil._review_security(
        "implement api_key rotation and token refresh",
        risks, concerns, alts, recs, conf,
    )
    assert "Key management complexity" in risks
    assert "Secret rotation may cause downtime" in concerns
    assert "Implement zero-downtime key rotation" in recs
    assert new_conf == 80


def test_review_security_sandbox_recommendation():
    from core.engineering_council import EngineeringCouncil

    recs: list = []
    EngineeringCouncil._review_security(
        "run plugin in sandbox isolation",
        [], [], [], recs, 70,
    )
    assert any("seccomp" in r for r in recs)


def test_review_architecture_singleton_pattern():
    from core.engineering_council import EngineeringCouncil

    risks, concerns, alts, recs = [], [], [], []
    conf = EngineeringCouncil._review_architecture(
        "replace global singleton with DI",
        risks, concerns, alts, recs, 70,
    )
    assert "Breaking change to singleton consumers" in risks
    assert any("factory pattern" in a.lower() for a in alts)
    assert conf == 65


def test_determine_verdict_approve_when_clean():
    from core.engineering_council import EngineeringCouncil, Verdict

    assert EngineeringCouncil._determine_verdict([], []) == Verdict.APPROVE


def test_determine_verdict_reject_when_many_risks():
    from core.engineering_council import EngineeringCouncil, Verdict

    risks = ["a", "b", "c", "d"]
    assert EngineeringCouncil._determine_verdict(risks, []) == Verdict.REJECT


def test_determine_verdict_approve_with_concerns_otherwise():
    from core.engineering_council import EngineeringCouncil, Verdict

    risks = ["some real risk"]
    assert EngineeringCouncil._determine_verdict(risks, []) == Verdict.APPROVE_WITH_CONCERNS


@pytest.mark.asyncio
async def test_review_from_perspective_dispatches_to_security_helper():
    from core.engineering_council import (
        EngineeringCouncil, ReviewerRole, CouncilDecision,
    )

    council = EngineeringCouncil()
    decision = CouncilDecision(
        title="x", description="rotate the api_key periodically", context="",
    )
    review = await council._review_from_perspective(
        ReviewerRole.CHIEF_SECURITY_OFFICER, decision,
    )
    assert review.role == ReviewerRole.CHIEF_SECURITY_OFFICER
    assert any("Key management" in r for r in review.risks)


# ---------------------------------------------------------------------------
# pattern_engine.py — discover_patterns() helpers
# ---------------------------------------------------------------------------
def test_pattern_engine_helpers_exist():
    from core.pattern_engine import PatternEngine

    for name in (
        "_collect_pattern_matches",
        "_score_patterns",
        "_filter_top_patterns",
        "_maybe_add_glm_pattern",
    ):
        assert hasattr(PatternEngine, name), f"missing helper: {name}"


def test_collect_pattern_matches_groups_by_family():
    from core.pattern_engine import PatternEngine

    engine = PatternEngine()
    engine.interactions = [
        {"action_type": "chat", "content": "hello world programming",
         "timestamp": "2024-01-01T08:00:00"},
        {"action_type": "chat", "content": "more programming",
         "timestamp": "2024-01-01T09:00:00"},
        {"action_type": "chat", "content": "programming again",
         "timestamp": "2024-01-01T10:00:00"},
    ]
    matches = engine._collect_pattern_matches()
    assert set(matches.keys()) == {"action_type", "keyword", "action_pair", "time_of_day"}
    assert matches["action_type"]["chat"]["count"] == 3
    assert "programming" in matches["keyword"]


def test_filter_top_patterns_drops_below_min_occurrences():
    from core.pattern_engine import MIN_PATTERN_OCCURRENCES, PatternEngine

    scored = [
        {"family": "action_type", "type": "action_type_frequency",
         "key": "chat", "count": MIN_PATTERN_OCCURRENCES,
         "examples": [0, 1, 2], "description": "d"},
        {"family": "action_type", "type": "action_type_frequency",
         "key": "tool", "count": MIN_PATTERN_OCCURRENCES - 1,
         "examples": [3], "description": "d"},
    ]
    patterns = PatternEngine._filter_top_patterns(scored)
    assert len(patterns) == 1
    assert patterns[0]["evidence_count"] == MIN_PATTERN_OCCURRENCES


def test_filter_top_patterns_caps_keyword_top10():
    from core.pattern_engine import MIN_PATTERN_OCCURRENCES, PatternEngine

    # Each scored keyword has count >= MIN_PATTERN_OCCURRENCES so all of them
    # survive the filter; the cap of 10 should kick in.
    scored = [
        {"family": "keyword", "type": "keyword_frequency",
         "key": f"word{i}", "count": 100 - i, "examples": [], "description": "d"}
        for i in range(20)
    ]
    assert all(s["count"] >= MIN_PATTERN_OCCURRENCES for s in scored)
    patterns = PatternEngine._filter_top_patterns(scored)
    # Cap is 10 for the keyword family.
    assert len(patterns) == 10


@pytest.mark.asyncio
async def test_maybe_add_glm_pattern_skips_when_few_patterns():
    from core.pattern_engine import PatternEngine

    engine = PatternEngine()
    # 1 pattern < 2 threshold — should return input unchanged.
    out = await engine._maybe_add_glm_pattern([{"type": "x"}])
    assert out == [{"type": "x"}]


# ---------------------------------------------------------------------------
# hellfire_audit.py — check_no_hardcoded_secrets() helpers
# ---------------------------------------------------------------------------
def test_hellfire_audit_helpers_exist():
    from scripts import hellfire_audit

    for name in (
        "_skip_audit_file",
        "_scan_file_for_secrets",
        "_is_env_read",
        "_is_print_or_log",
        "_check_placeholder",
    ):
        assert hasattr(hellfire_audit, name), f"missing helper: {name}"


def test_skip_audit_file_skips_tests_and_audit_itself():
    from scripts.hellfire_audit import _skip_audit_file
    from pathlib import Path

    assert _skip_audit_file(Path("/repo/tests/test_foo.py"))
    assert _skip_audit_file(Path("/repo/scripts/hellfire_audit.py"))
    assert _skip_audit_file(Path("/repo/.env"))
    assert not _skip_audit_file(Path("/repo/core/brain.py"))


def test_is_env_read_detects_getenv():
    from scripts.hellfire_audit import _is_env_read

    assert _is_env_read("key = os.getenv('KEY')")
    assert _is_env_read("os.environ['KEY'] = 'x'")
    assert _is_env_read("val = get_env_var('KEY')")
    assert not _is_env_read("key = 'literal-string'")


def test_is_print_or_log_detects_instructional_output():
    from scripts.hellfire_audit import _is_print_or_log

    assert _is_print_or_log("print('API_KEY = your-key-here')")
    assert _is_print_or_log("logger.info('setup instructions')")
    assert _is_print_or_log("echo export GLM_API_KEY='your-key'")
    assert not _is_print_or_log("API_KEY = 'sk-1234567890abcdefghij'")


def test_check_placeholder_detects_markers():
    from scripts.hellfire_audit import _check_placeholder

    markers = ("example", "your-key-here")
    assert _check_placeholder("set your-key-here to start", markers)
    assert _check_placeholder("this is an example string", markers)
    assert not _check_placeholder("sk-1234567890abcdefghij", markers)


def test_scan_file_for_secrets_finds_real_secret(monkeypatch, tmp_path):
    import re
    from scripts import hellfire_audit

    # _scan_file_for_secrets reports findings relative to ROOT, so we
    # monkey-patch ROOT to the tmp_path the test file lives in.
    monkeypatch.setattr(hellfire_audit, "ROOT", tmp_path)
    patterns = [re.compile(r'API_KEY\s*=\s*["\'][^"\']{10,}["\']', re.IGNORECASE)]
    markers = ("example", "placeholder", "your-key-here")

    before = len(hellfire_audit.FAILURES)
    p = tmp_path / "mod.py"
    p.write_text('API_KEY = "sk-1234567890abcdefghij"\n')
    found = hellfire_audit._scan_file_for_secrets(p, p.read_text(), patterns, markers)
    assert found
    assert len(hellfire_audit.FAILURES) == before + 1
    # Cleanup so other tests aren't affected.
    hellfire_audit.FAILURES.pop()


def test_scan_file_for_secrets_skips_placeholder(monkeypatch, tmp_path):
    import re
    from scripts import hellfire_audit

    monkeypatch.setattr(hellfire_audit, "ROOT", tmp_path)
    patterns = [re.compile(r'API_KEY\s*=\s*["\'][^"\']{10,}["\']', re.IGNORECASE)]
    markers = ("example", "placeholder", "your-key-here")

    before = len(hellfire_audit.FAILURES)
    p = tmp_path / "mod.py"
    p.write_text('API_KEY = "your-key-here"\n')
    found = hellfire_audit._scan_file_for_secrets(p, p.read_text(), patterns, markers)
    assert not found
    assert len(hellfire_audit.FAILURES) == before


# ---------------------------------------------------------------------------
# security_ops.py — SBOMGenerator.generate() helpers
# ---------------------------------------------------------------------------
def test_sbom_generator_helpers_exist():
    from core.security_ops import SBOMGenerator

    for name in (
        "_parse_pyproject_deps",
        "_parse_requirements_deps",
        "_merge_deps",
        "_enrich_versions",
    ):
        assert hasattr(SBOMGenerator, name), f"missing helper: {name}"


def test_parse_pyproject_deps_reads_optional_groups(tmp_path):
    from core.security_ops import SBOMEntry, SBOMGenerator

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        "name = 'demo'\n"
        "dependencies = ['requests>=2.0']\n"
        "[project.optional-dependencies]\n"
        "dev = ['pytest', 'ruff>=0.1']\n",
    )
    entries = SBOMGenerator._parse_pyproject_deps(pyproject)
    names = {e.name for e in entries}
    assert {"requests", "pytest", "ruff"}.issubset(names)
    assert all(isinstance(e, SBOMEntry) for e in entries)
    assert all(e.source == "pyproject.toml" for e in entries)


def test_parse_requirements_deps_skips_comments_and_blanks(tmp_path):
    from core.security_ops import SBOMGenerator

    reqs = tmp_path / "requirements.txt"
    reqs.write_text(
        "# Top-level comment\n"
        "\n"
        "requests>=2.0\n"
        "flask\n"
    )
    entries = SBOMGenerator._parse_requirements_deps(reqs)
    assert {e.name for e in entries} == {"requests", "flask"}


def test_merge_deps_dedups_by_name():
    from core.security_ops import SBOMEntry, SBOMGenerator

    existing = [SBOMEntry(name="requests", version="2.0", source="pyproject.toml")]
    new = [
        SBOMEntry(name="requests", version="2.31", source="requirements.txt"),
        SBOMEntry(name="flask", version="3.0", source="requirements.txt"),
    ]
    merged = SBOMGenerator._merge_deps(existing, new)
    # Dedup keeps the first-seen source — `requests` from pyproject.toml.
    names = [e.name for e in merged]
    assert names.count("requests") == 1
    assert "flask" in names


# ---------------------------------------------------------------------------
# council_mode.py — compare_responses() helpers
# ---------------------------------------------------------------------------
def test_council_mode_helpers_exist():
    from core import council_mode

    for name in (
        "_split_sentences",
        "_find_agreements_and_unique",
        "_find_sentence_matches",
        "_find_disagreements",
    ):
        assert hasattr(council_mode, name), f"missing helper: {name}"


def test_split_sentences_filters_short_fragments():
    from core.council_mode import _split_sentences

    text = "Hi. This is a real sentence. short"
    out = _split_sentences(text)
    assert "This is a real sentence" in out
    assert all(len(s) > 15 for s in out)


def test_find_disagreements_catches_however():
    from core.council_mode import _find_disagreements

    providers = ["glm", "claude"]
    sentences = {
        "glm": ["This is fine."],
        "claude": ["However, that is wrong."],
    }
    found = _find_disagreements(providers, sentences)
    assert any("[claude]" in d and "However" in d for d in found)


def test_compare_responses_single_provider_returns_note():
    from core.council_mode import compare_responses

    out = compare_responses({"glm": "only one voice here"})
    assert "note" in out
    assert out["agreements"] == []
    assert out["disagreements"] == []


# ---------------------------------------------------------------------------
# api/main.py — websocket_endpoint() helpers
# ---------------------------------------------------------------------------
def test_websocket_endpoint_helpers_exist():
    from api import main

    for name in (
        "_authenticate_websocket",
        "_init_glm_brain",
        "_handle_ws_message",
        "_process_chat_request",
        "_process_branch_request",
    ):
        assert hasattr(main, name), f"missing helper: {name}"


def test_init_glm_brain_returns_none_when_provider_not_glm(monkeypatch):
    from api import main

    monkeypatch.setattr(main, "BRAIN_PROVIDER", "claude")
    assert main._init_glm_brain() is None


@pytest.mark.asyncio
async def test_process_branch_request_pushes_pending_actions():
    from api.main import _process_branch_request

    sent: list = []

    class FakeWS:
        async def send_json(self, payload):
            sent.append(payload)

    class FakeLedger:
        pending_actions = {"abc": {"action": "abc"}, "def": {"action": "def"}}

    await _process_branch_request(FakeWS(), FakeLedger())
    assert sent and "pending_actions" in sent[0]
    assert len(sent[0]["pending_actions"]) == 2


# ---------------------------------------------------------------------------
# brain.py — _glm_stream_with_tools() helpers
# ---------------------------------------------------------------------------
def test_brain_helpers_exist():
    from core.brain import FridayBrain

    for name in (
        "_call_glm_with_tools",
        "_build_assistant_tool_message",
        "_process_tool_call",
        "_should_continue_tool_loop",
        "_stream_final_response",
    ):
        assert hasattr(FridayBrain, name), f"missing helper: {name}"


def test_should_continue_tool_loop_boundary():
    from core.brain import FridayBrain

    assert FridayBrain._should_continue_tool_loop(0, 5) is True
    assert FridayBrain._should_continue_tool_loop(4, 5) is True
    assert FridayBrain._should_continue_tool_loop(5, 5) is False


def test_build_assistant_tool_message_shape():
    from core.brain import FridayBrain

    message = SimpleNamespace(content="calling tools", tool_calls=None)
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="lookup", arguments='{"q": "x"}'),
    )
    out = FridayBrain._build_assistant_tool_message(message, [tc])
    assert out["role"] == "assistant"
    assert out["content"] == "calling tools"
    assert out["tool_calls"][0]["id"] == "call_1"
    assert out["tool_calls"][0]["function"]["name"] == "lookup"
    assert out["tool_calls"][0]["function"]["arguments"] == '{"q": "x"}'


# ---------------------------------------------------------------------------
# cli/commands.py — _plugin_install() helpers
# ---------------------------------------------------------------------------
def test_plugin_install_helpers_exist():
    from cli import commands

    for name in ("_scan_plugin_ast", "_copy_plugin"):
        assert hasattr(commands, name), f"missing helper: {name}"


def test_scan_plugin_ast_returns_empty_for_clean_source():
    from cli.commands import _scan_plugin_ast

    syntax_err, findings = _scan_plugin_ast(
        "import json\nimport re\n\ndef x():\n    return 1\n"
    )
    assert syntax_err is None
    assert findings == []


def test_scan_plugin_ast_flags_dangerous_imports():
    from cli.commands import _scan_plugin_ast

    syntax_err, findings = _scan_plugin_ast("import os\nimport subprocess\n")
    assert syntax_err is None
    assert any("import os" in f for f in findings)
    assert any("import subprocess" in f for f in findings)


def test_scan_plugin_ast_flags_dynamic_exec():
    from cli.commands import _scan_plugin_ast

    syntax_err, findings = _scan_plugin_ast('exec("print(1)")\n')
    assert syntax_err is None
    assert any("exec(...)" in f and "dynamic code execution" in f for f in findings)


def test_scan_plugin_ast_returns_syntax_error_for_bad_source():
    from cli.commands import _scan_plugin_ast

    syntax_err, findings = _scan_plugin_ast("def x(:\n  pass\n")
    assert syntax_err is not None
    assert findings == []


def test_copy_plugin_copies_and_prints(tmp_path):
    from cli.commands import _copy_plugin

    src = tmp_path / "src.py"
    src.write_text("# plugin\n")
    target = tmp_path / "out" / "plugin.py"
    target.parent.mkdir()
    # _copy_plugin uses shutil + the global console — no return value to assert.
    _copy_plugin(src, target, "demo")
    assert target.read_text() == "# plugin\n"


# ---------------------------------------------------------------------------
# Signatures — guard against accidental signature drift.
# ---------------------------------------------------------------------------
def test_sandbox_helper_signatures():
    from core.runtime.security.sandbox import SubprocessSandbox

    sig = inspect.signature(SubprocessSandbox._check_memory_usage)
    # Should accept (config, result, peak_rss_bytes, rss_before_kb).
    assert "config" in sig.parameters
    assert "result" in sig.parameters
    assert "peak_rss_bytes" in sig.parameters
    assert "rss_before_kb" in sig.parameters


def test_engineering_council_helper_signatures():
    from core.engineering_council import EngineeringCouncil

    sig = inspect.signature(EngineeringCouncil._determine_verdict)
    assert "risks" in sig.parameters
    assert "concerns" in sig.parameters


def test_pattern_engine_helper_signatures():
    from core.pattern_engine import PatternEngine

    sig = inspect.signature(PatternEngine._filter_top_patterns)
    assert "scored" in sig.parameters
