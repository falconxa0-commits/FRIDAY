"""Entry point for `python -m cli` and the `friday` console script."""
import asyncio
import sys

from cli.terminal import boot_sequence, run_tui


def main() -> int:
    args = sys.argv[1:]
    if not args:
        # Full TUI
        status = asyncio.run(boot_sequence())
        try:
            asyncio.run(run_tui(status))
        except KeyboardInterrupt:
            print("\nGoodbye.")
        return 0

    # Delegate to commands.py for subcommands
    from cli.commands import dispatch
    return dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
