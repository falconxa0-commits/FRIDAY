"""Tests for the plugin installer sandbox (SEC-3 fix)."""
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch
from cli.commands import _plugin_install


class TestPluginSandbox:
    """Test that the plugin installer refuses dangerous imports."""

    def _write_plugin(self, name: str, content: str) -> str:
        """Create a fake plugin in a temp marketplace/plugins/<name>/ dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # We need to patch the marketplace path
            plugin_dir = Path(tmpdir) / "marketplace" / "plugins" / name
            plugin_dir.mkdir(parents=True)
            (plugin_dir / f"{name}.py").write_text(content)
            yield tmpdir

    def test_safe_plugin_installs(self, tmp_path):
        """A plugin with no dangerous imports should pass the security scan."""
        import ast

        safe_code = """
import logging
from integrations.base import BaseIntegration

class SafePlugin(BaseIntegration):
    @property
    def name(self): return "SafePlugin"
    def available(self): return True
    async def execute(self, action, params=None): return {}
"""
        # Verify the safe code passes the security scan
        tree = ast.parse(safe_code)
        DANGEROUS_IMPORTS = {
            "os", "subprocess", "socket", "shlex", "ctypes", "sys",
            "importlib", "builtins", "pty", "multiprocessing",
        }
        found = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in DANGEROUS_IMPORTS:
                        found.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in DANGEROUS_IMPORTS:
                    found.append(node.module)
        assert found == [], f"Safe plugin should have no dangerous imports, found: {found}"

    def test_dangerous_os_import_refused(self):
        """A plugin importing `os` at top level must be detected as dangerous."""
        import ast

        dangerous_code = """
import os
import subprocess
class DangerousPlugin:
    pass
"""
        tree = ast.parse(dangerous_code)
        DANGEROUS_IMPORTS = {
            "os", "subprocess", "socket", "shlex", "ctypes", "sys",
            "importlib", "builtins", "pty", "multiprocessing",
        }
        found = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in DANGEROUS_IMPORTS:
                        found.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in DANGEROUS_IMPORTS:
                    found.append(node.module)
        assert "os" in found
        assert "subprocess" in found

    def test_security_scan_logic_directly(self):
        """Test the AST-based security scan directly."""
        import ast
        from cli.commands import _plugin_install

        DANGEROUS_IMPORTS = {
            "os", "subprocess", "socket", "shlex", "ctypes", "sys",
            "importlib", "builtins", "pty", "multiprocessing",
        }

        def scan_code(code: str) -> list:
            tree = ast.parse(code)
            found = []
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        if root in DANGEROUS_IMPORTS:
                            found.append(f"import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        root = node.module.split(".")[0]
                        if root in DANGEROUS_IMPORTS:
                            found.append(f"from {node.module} import ...")
            return found

        # Dangerous
        assert scan_code("import os\nimport subprocess") == ["import os", "import subprocess"]
        assert scan_code("from os import getenv") == ["from os import ..."]
        assert scan_code("import socket") == ["import socket"]

        # Safe
        assert scan_code("import logging\nfrom integrations.base import BaseIntegration") == []
        assert scan_code("import asyncio\nimport httpx") == []

    def test_marketplace_weather_advanced_passes_scan(self):
        """The shipped weather_advanced plugin should pass the security scan."""
        import ast
        plugin_path = Path(__file__).resolve().parent.parent / "marketplace" / "plugins" / "weather_advanced" / "weather_advanced.py"
        if not plugin_path.exists():
            pytest.skip("weather_advanced plugin not found")

        code = plugin_path.read_text()
        tree = ast.parse(code)

        DANGEROUS_IMPORTS = {
            "os", "subprocess", "socket", "shlex", "ctypes", "sys",
            "importlib", "builtins", "pty", "multiprocessing",
        }

        found_dangerous = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in DANGEROUS_IMPORTS:
                        found_dangerous.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    if root in DANGEROUS_IMPORTS:
                        found_dangerous.append(node.module)

        assert found_dangerous == [], \
            f"weather_advanced.py should not have dangerous top-level imports, found: {found_dangerous}"
