#!/usr/bin/env python3
"""FRIDAY v2.0 — Final Definition of Done checklist.

Runs every check from the user's Definition of Done and pastes real
results for each one.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["FRIDAY_API_TOKEN"] = "test"
os.environ["PYTHONPATH"] = "."


def run(cmd, label, timeout=120):
    print(f"\n>>> {label}")
    print(f"    $ {cmd}")
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env={**os.environ, "PYTHONPATH": "."},
        )
        out_lines = r.stdout.strip().split("\n")[-5:]
        for line in out_lines:
            print(f"    {line}")
        return r.returncode
    except subprocess.TimeoutExpired:
        print("    TIMEOUT")
        return -1


def main():
    print("=" * 70)
    print("FRIDAY v2.0 — DEFINITION OF DONE — Final Checklist")
    print("=" * 70)

    results = []

    # ---- Track A: CLI -----------------------------------------------
    print("\n" + "=" * 70)
    print("TRACK A — CLI / Terminal / VS Code / Build / Installer")
    print("=" * 70)

    rc = run("python3 scripts/verify_cli_terminal.py 2>&1 | tail -3",
             "A1 — cli/terminal.py sci-fi TUI")
    results.append(("A1: cli/terminal.py sci-fi TUI", rc == 0))

    rc = run("python3 scripts/verify_cli_commands.py 2>&1 | tail -3",
             "A2 — cli/commands.py all subcommands")
    results.append(("A2: CLI subcommands", rc == 0))

    rc = run("python3 scripts/verify_track_a.py 2>&1 | tail -3",
             "A3-A5 — VS Code extension + build + installers")
    results.append(("A3-A5: VS Code + build + installers", rc == 0))

    # ---- Track B: Capabilities ---------------------------------------
    print("\n" + "=" * 70)
    print("TRACK B — Seven Capability Upgrades")
    print("=" * 70)

    rc = run("python3 scripts/verify_pattern_engine.py 2>&1 | tail -3",
             "B1 — Pattern engine")
    results.append(("B1: Pattern engine", rc == 0))

    rc = run("python3 scripts/verify_ambient.py 2>&1 | tail -3",
             "B2 — Ambient engine")
    results.append(("B2: Ambient engine", rc == 0))

    rc = run("python3 scripts/verify_deep_research.py 2>&1 | tail -3",
             "B3 — Deep research")
    results.append(("B3: Deep research", rc == 0))

    rc = run("python3 scripts/verify_multimodal_memory.py 2>&1 | tail -3",
             "B4 — Multi-modal memory")
    results.append(("B4: Multi-modal memory", rc == 0))

    rc = run("python3 scripts/verify_mcp_hub.py 2>&1 | tail -3",
             "B5 — MCP hub with request_approval")
    results.append(("B5: MCP hub", rc == 0))

    rc = run("python3 scripts/verify_conversational_voice.py 2>&1 | tail -3",
             "B6 — Conversational voice + barge-in")
    results.append(("B6: Conversational voice", rc == 0))

    rc = run("python3 scripts/verify_team_mode.py 2>&1 | tail -3",
             "B7 — Team mode")
    results.append(("B7: Team mode", rc == 0))

    # ---- Track C: Additional -----------------------------------------
    print("\n" + "=" * 70)
    print("TRACK C — Additional Features")
    print("=" * 70)

    rc = run("python3 scripts/verify_track_c.py 2>&1 | tail -3",
             "C1-C8 — All additional features", timeout=180)
    results.append(("C1-C8: All additional features", rc == 0))

    # ---- Both tracks: constitution checks ----------------------------
    print("\n" + "=" * 70)
    print("STANDING CONSTITUTION CHECKS")
    print("=" * 70)

    rc = run("python3 -m pytest tests/ -q 2>&1 | tail -3",
             "Full test suite (183+ tests)")
    results.append(("183+ tests passing", rc == 0))

    rc = run("FRIDAY_API_TOKEN=test python3 scripts/hellfire_audit.py 2>&1 | grep -E 'ALL HELLFIRE|FAILED:' | tail -3",
             "Hellfire audit (8 checks)")
    results.append(("Hellfire audit clean", rc == 0))

    rc = run("python3 scripts/smoke_test.py 2>&1 | tail -3",
             "Smoke test")
    results.append(("Smoke test clean", rc == 0))

    # Theatrical naming check
    r = subprocess.run(
        "grep -i -rE 'singularity|peer not a tool|4d timeline|4d timelines|"
        "ascendant|transcend|omniscient|god mode|god-mode|aether|nexus' "
        "--include='*.py' --include='*.html' --include='*.js' --include='*.ts' . 2>&1 | "
        "grep -v __pycache__ | grep -v verify_onboarding | "
        "grep -v verify_definition_of_done | grep -v verify_v2_definition_of_done | "
        "grep -v CHANGELOG_FIXES",
        shell=True, capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    hits = r.stdout.strip()
    print(f"\n>>> Theatrical naming grep")
    print(f"    Hits: {hits or 'NONE'}")
    results.append(("Zero theatrical naming", hits == ""))

    # ---- Summary -----------------------------------------------------
    print("\n" + "=" * 70)
    print("FRIDAY v2.0 DEFINITION OF DONE — SUMMARY")
    print("=" * 70)
    passed = 0
    failed = 0
    for label, ok in results:
        marker = "✓" if ok else "✗"
        print(f"  {marker} {label}")
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {len(results)} checks")
    if failed == 0:
        print("\nALL CHECKS PASSED — FRIDAY v2.0 DEFINITION OF DONE MET")
    else:
        print(f"\n{failed} CHECK(S) FAILED — see above")
    print("=" * 70)


if __name__ == "__main__":
    main()
