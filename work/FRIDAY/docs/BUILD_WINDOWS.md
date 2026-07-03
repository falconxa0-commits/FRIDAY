# Building Friday for Windows

This document describes how to produce a single `friday.exe` binary for Windows using PyInstaller.

## Prerequisites

- Python 3.10+ on Windows (or Linux/macOS with Wine for cross-compilation — not recommended)
- PyInstaller: `pip install pyinstaller`
- All Friday dependencies: `pip install -r requirements.txt`

## Build

```bash
# From the FRIDAY project root:
python build/build_windows.py
```

Output: `dist/friday.exe` (~30 MB single-file binary)

## What's Bundled

- Python interpreter
- All Friday core modules (`cli/`, `core/`, `api/`, `agents/`, etc.)
- Required data directories (`config/`, `skills/`, `docs/`)
- Hidden imports for `zhipuai`, `rich`, `textual`, `fastapi`, `uvicorn`, `pydantic`, `httpx`, `PIL`

## What's NOT Bundled

- `GLM_API_KEY` — must be set as an environment variable on the target machine
- Optional credentials (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.)
- The `assets/` directory (icons, etc.) — only `friday.ico` is embedded as the binary icon

## Distribute

1. Zip `dist/friday.exe` along with `README.md` and `.env.example`
2. Users unzip, set `GLM_API_KEY`, and run `friday.exe`

## Verification

After building, test the binary:

```cmd
friday.exe status
friday.exe chat "hello"
friday.exe trust
```

All three should produce real output identical to the Python `friday` command.

## Cross-Compilation Notes

PyInstaller does not support cross-compilation between platforms. To produce a Windows binary you must run the build on Windows (or under Wine — experimental, not recommended).

For macOS, run on macOS. For Linux, run on Linux. The `friday` source is platform-agnostic — only the binary itself is platform-specific.

## Troubleshooting

**"ModuleNotFoundError: No module named 'X'"** — add `--hidden-import X` to `build_windows.py`'s `hidden_imports` list.

**"friday.exe opens a console window then closes"** — run from CMD or PowerShell, not by double-clicking, so you can see the error output.

**Antivirus flags the .exe** — this is a known false positive with PyInstaller binaries. Sign the executable with a code-signing certificate to avoid this.
