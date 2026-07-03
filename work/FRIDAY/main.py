#!/usr/bin/env python3
"""FRIDAY AI Assistant — legacy main.py entry point.

This file previously contained a large FridayOrchestrator class that
wired up 9 subsystems (evolution, synthesis, monologue, continuum,
ux_engine, etc.). Those subsystems were dead code — never reachable
from the CLI (`friday` command) or the API (`uvicorn api.main:app`),
and contained theatrical-naming class names that violated the
project's constitution.

As of v2.1, this file is a thin compatibility wrapper that delegates
to the CLI. The canonical entry points are:

    friday              # CLI / sci-fi TUI  (cli/__main__.py)
    uvicorn api.main:app  # API server       (api/main.py)

This file is kept so `python main.py` still works for any existing
documentation or scripts that reference it.
"""
import sys
import os

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    """Delegate to the CLI entry point."""
    from cli.__main__ import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
