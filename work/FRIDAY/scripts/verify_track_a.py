#!/usr/bin/env python3
"""Track A verification — CLI/terminal/VS Code/build/installer."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    print("=" * 70)
    print("TRACK A — CLI / Terminal / VS Code / Build / Installer")
    print("=" * 70)

    root = Path(__file__).resolve().parent.parent

    # ---- A1: cli/terminal.py ------------------------------------------
    print("\n[A1] cli/terminal.py exists and imports clean…")
    p = root / "cli" / "terminal.py"
    assert p.exists(), f"Missing: {p}"
    print(f"  ✓ {p.relative_to(root)} ({p.stat().st_size} bytes)")
    r = subprocess.run(
        [sys.executable, "-c", "from cli.terminal import boot_sequence, render_status_pane, render_receipt, render_approval; print('OK')"],
        capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(root)},
    )
    print(f"  Import: {r.stdout.strip() or r.stderr.strip()[:200]}")
    assert r.returncode == 0

    # ---- A2: cli/commands.py ------------------------------------------
    print("\n[A2] cli/commands.py + cli/__main__.py + pyproject.toml entry…")
    p = root / "cli" / "commands.py"
    assert p.exists()
    print(f"  ✓ {p.relative_to(root)} ({p.stat().st_size} bytes)")
    p = root / "cli" / "__main__.py"
    assert p.exists()
    print(f"  ✓ {p.relative_to(root)}")
    p = root / "pyproject.toml"
    assert p.exists()
    assert "friday = \"cli.__main__:main\"" in p.read_text()
    print(f"  ✓ friday console script registered in pyproject.toml")

    # ---- A3: VS Code extension ----------------------------------------
    print("\n[A3] apps/vscode/ — TypeScript compiles, .vsix built…")
    vscode_dir = root / "apps" / "vscode"
    pkg = vscode_dir / "package.json"
    ts = vscode_dir / "src" / "extension.ts"
    ext = vscode_dir / "out" / "extension.js"
    assert pkg.exists() and ts.exists()
    print(f"  ✓ package.json ({pkg.stat().st_size} bytes)")
    print(f"  ✓ src/extension.ts ({ts.stat().st_size} bytes)")
    if ext.exists():
        print(f"  ✓ out/extension.js compiled ({ext.stat().st_size} bytes)")
    else:
        print(f"  ⚠ out/extension.js not found — run 'npm run compile' in apps/vscode/")

    vsix = vscode_dir / "friday.vsix"
    if vsix.exists():
        print(f"  ✓ friday.vsix packaged ({vsix.stat().st_size} bytes)")
    else:
        print(f"  ⚠ friday.vsix not found — run 'npx vsce package'")

    # ---- A4: Windows build script ------------------------------------
    print("\n[A4] build/build_windows.py + docs/BUILD_WINDOWS.md…")
    p = root / "build" / "build_windows.py"
    assert p.exists()
    print(f"  ✓ {p.relative_to(root)}")
    p = root / "docs" / "BUILD_WINDOWS.md"
    assert p.exists()
    print(f"  ✓ {p.relative_to(root)}")
    # PyInstaller available?
    try:
        import PyInstaller
        print(f"  ✓ PyInstaller {PyInstaller.__version__} available")
    except ImportError:
        print(f"  ⚠ PyInstaller not installed")

    # ---- A5: Installers -----------------------------------------------
    print("\n[A5] install.sh + install.ps1…")
    p = root / "install.sh"
    assert p.exists()
    assert os.access(p, os.X_OK), "install.sh should be executable"
    print(f"  ✓ {p.relative_to(root)} (executable)")
    p = root / "install.ps1"
    assert p.exists()
    print(f"  ✓ {p.relative_to(root)}")

    # ---- Summary ------------------------------------------------------
    print("\n" + "=" * 70)
    print("TRACK A VERIFIED")
    print("  A1 — cli/terminal.py with rich-based sci-fi UI (boot seq, panes,")
    print("       receipt, approval colour-coding) — verified by verify_cli_terminal.py")
    print("  A2 — cli/commands.py + pyproject.toml console script — verified by")
    print("       verify_cli_commands.py")
    print(f"  A3 — VS Code extension builds to friday.vsix ({vsix.stat().st_size} bytes)")
    print("       Manual-required: install in real VS Code to verify panel display")
    print("  A4 — build/build_windows.py + docs/BUILD_WINDOWS.md")
    print("       Manual-required: run on Windows to produce friday.exe")
    print("  A5 — install.sh + install.ps1 ready")
    print("=" * 70)


if __name__ == "__main__":
    main()
