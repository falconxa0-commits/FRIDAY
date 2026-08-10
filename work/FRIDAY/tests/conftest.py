"""Pytest configuration — set up auth and isolate persistence for all tests."""
import os
import tempfile
from pathlib import Path

# Set FRIDAY_DEV_MODE=1 and clear token BEFORE any config imports
os.environ["FRIDAY_DEV_MODE"] = "1"
os.environ["FRIDAY_API_TOKEN"] = ""

# Set a consistent HMAC secret for all tests so the audit chain
# can be verified across different ActionLedger instances.
os.environ["FRIDAY_LEDGER_HMAC_SECRET"] = "test-hmac-secret-for-all-tests"

# Redirect ALL persistence to a temp directory so tests NEVER write
# to production data files. These env vars are read at module-import
# time, so they must be set BEFORE any test imports core modules.
_TEST_TMPDIR = Path(tempfile.mkdtemp(prefix="friday_test_"))
os.environ["FRIDAY_ENGINEERING_DIR"] = str(_TEST_TMPDIR / ".friday")
os.environ["FRIDAY_CHAIN_PATH"] = str(_TEST_TMPDIR / "chain.json")


def pytest_configure(config):
    """Called after pytest is configured but before tests are collected.

    Resets the ledger singleton so the next get_ledger() call creates
    a new instance with the env-var-patched paths.
    """
    try:
        from core.ledger import ActionLedger
        ActionLedger._HMAC_SECRET = None  # reset cached secret
        # Reset the singleton so the next get_ledger() call creates
        # a new instance with the env-var-patched paths.
        import core.ledger
        core.ledger._ledger = None
    except ImportError:
        pass  # module not available yet
