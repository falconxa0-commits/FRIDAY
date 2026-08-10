"""Tests for the control subsystem — pc_control, browser_control, file_manager,
app_launcher, workspace.

SECURITY-CRITICAL: verifies path traversal protection in file_manager.

Hardware dependencies (pyautogui, Playwright browser, subprocess) are mocked.
"""
from __future__ import annotations

import asyncio
import os
import platform
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_workspace(tmp_path, monkeypatch):
    """Create a temp workspace dir + patch WORKSPACE_ROOT for file_manager."""
    ws = tmp_path / "ws"
    ws.mkdir()
    # config.settings.WORKSPACE_ROOT is read at file_manager import time
    # via `from config.settings import WORKSPACE_ROOT`. So patch the bound
    # reference inside file_manager.
    import control.file_manager as fm
    monkeypatch.setattr(fm, "WORKSPACE_ROOT", str(ws))
    return ws


@pytest.fixture()
def ledger_mock():
    """Mock ledger that auto-approves all queued actions."""
    ledger = MagicMock()
    ledger.queue_action.return_value = "test-action-id"
    # wait_for_approval is async — return True (approved)
    ledger.wait_for_approval = AsyncMock(return_value=True)
    return ledger


@pytest.fixture()
def fake_pyaudio_for_pc(monkeypatch):
    """Inject a minimal fake pyaudio so PCControl can construct."""
    fake = types.ModuleType("pyaudio")
    fake.paInt16 = 8
    fake.FAILSAFE = True
    fake.PyAudio = MagicMock
    monkeypatch.setitem(sys.modules, "pyaudio", fake)
    return fake


# ---------------------------------------------------------------------------
# control/file_manager.py — SECURITY-CRITICAL: path traversal protection
# ---------------------------------------------------------------------------


class TestSafePathTraversal:
    """SECURITY: Verify _safe_path blocks path traversal outside workspace.

    Regression test for the commonpath security fix.
    """

    def test_safe_path_normal_relative_path(self, temp_workspace):
        from control.file_manager import FileManager
        fm = FileManager()
        result = fm._safe_path("notes.txt")
        assert result == os.path.join(str(temp_workspace), "notes.txt")

    def test_safe_path_rejects_dotdot_traversal(self, temp_workspace):
        """`../../etc/passwd` MUST be rejected — this is the security fix."""
        from control.file_manager import FileManager
        fm = FileManager()
        with pytest.raises(PermissionError, match="outside the workspace"):
            fm._safe_path("../../etc/passwd")

    def test_safe_path_rejects_absolute_path_outside_workspace(self, temp_workspace):
        from control.file_manager import FileManager
        fm = FileManager()
        with pytest.raises(PermissionError, match="outside the workspace"):
            fm._safe_path("/etc/passwd")

    def test_safe_path_rejects_absolute_path_to_home(self, temp_workspace):
        from control.file_manager import FileManager
        fm = FileManager()
        with pytest.raises(PermissionError, match="outside the workspace"):
            fm._safe_path("/home/user/.ssh/id_rsa")

    def test_safe_path_rejects_traversal_to_sibling_directory(self, temp_workspace, tmp_path):
        """commonpath catches sibling-dir traversal that startswith missed."""
        from control.file_manager import FileManager
        # Create a sibling directory whose name is a prefix of workspace
        sibling = tmp_path / "ws_evil"
        sibling.mkdir()
        fm = FileManager()
        # Try ../ws_evil/x — commonpath should reject since it's outside ws
        with pytest.raises(PermissionError):
            fm._safe_path("../ws_evil/x")

    def test_safe_path_allows_subdirectory(self, temp_workspace):
        from control.file_manager import FileManager
        fm = FileManager()
        result = fm._safe_path("subdir/file.txt")
        assert result.endswith(os.path.join("subdir", "file.txt"))

    def test_safe_path_allows_dot_in_filename(self, temp_workspace):
        from control.file_manager import FileManager
        fm = FileManager()
        result = fm._safe_path("file.tar.gz")
        assert result.endswith("file.tar.gz")

    def test_safe_path_normalizes_dot_segments(self, temp_workspace):
        """`./file.txt` should be normalized and accepted (still inside workspace)."""
        from control.file_manager import FileManager
        fm = FileManager()
        result = fm._safe_path("./file.txt")
        assert result == os.path.join(str(temp_workspace), "file.txt")


class TestFileManagerCRUD:
    """Test FileManager create/delete/list operations."""

    @pytest.mark.asyncio
    async def test_create_file_writes_content(self, temp_workspace, ledger_mock):
        from control.file_manager import FileManager
        fm = FileManager()
        fm.ledger = ledger_mock
        result = await fm.create_file("hello.txt", content="hello world")
        assert "File created" in result
        assert (temp_workspace / "hello.txt").read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_create_file_rejects_traversal(self, temp_workspace, ledger_mock):
        from control.file_manager import FileManager
        fm = FileManager()
        fm.ledger = ledger_mock
        # Even with ledger approval, traversal must be blocked
        with pytest.raises(PermissionError):
            await fm.create_file("../../etc/passwd", content="evil")

    @pytest.mark.asyncio
    async def test_delete_file_removes_existing_file(self, temp_workspace, ledger_mock):
        from control.file_manager import FileManager
        fm = FileManager()
        fm.ledger = ledger_mock
        (temp_workspace / "to_delete.txt").write_text("delete me")
        result = await fm.delete_file("to_delete.txt")
        assert "deleted" in result
        assert not (temp_workspace / "to_delete.txt").exists()

    @pytest.mark.asyncio
    async def test_delete_file_removes_directory(self, temp_workspace, ledger_mock):
        from control.file_manager import FileManager
        fm = FileManager()
        fm.ledger = ledger_mock
        (temp_workspace / "to_delete_dir").mkdir()
        (temp_workspace / "to_delete_dir" / "inner.txt").write_text("inner")
        result = await fm.delete_file("to_delete_dir")
        assert "deleted" in result
        assert not (temp_workspace / "to_delete_dir").exists()

    @pytest.mark.asyncio
    async def test_delete_file_returns_not_found_for_missing(self, temp_workspace, ledger_mock):
        from control.file_manager import FileManager
        fm = FileManager()
        fm.ledger = ledger_mock
        result = await fm.delete_file("nonexistent.txt")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_create_file_rejected_by_ledger(self, temp_workspace, ledger_mock):
        from control.file_manager import FileManager
        fm = FileManager()
        ledger_mock.wait_for_approval = AsyncMock(return_value=False)
        fm.ledger = ledger_mock
        result = await fm.create_file("blocked.txt", content="x")
        assert "rejected" in result.lower()
        assert not (temp_workspace / "blocked.txt").exists()

    def test_list_files_returns_directory_contents(self, temp_workspace, ledger_mock):
        from control.file_manager import FileManager
        fm = FileManager()
        fm.ledger = ledger_mock
        (temp_workspace / "a.txt").write_text("a")
        (temp_workspace / "b.txt").write_text("b")
        files = fm.list_files(".")
        assert "a.txt" in files
        assert "b.txt" in files


# ---------------------------------------------------------------------------
# control/pc_control.py — ledger gate + headless detection
# ---------------------------------------------------------------------------


class TestHeadlessDetection:
    """Test _is_headless() detects DISPLAY / WAYLAND_DISPLAY env vars."""

    def test_headless_when_no_display_vars(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("FRIDAY_FORCE_HEADLESS", raising=False)
        from control.pc_control import _is_headless
        assert _is_headless() is True

    def test_not_headless_when_display_set(self, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("FRIDAY_FORCE_HEADLESS", raising=False)
        from control.pc_control import _is_headless
        assert _is_headless() is False

    def test_not_headless_when_wayland_display_set(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.delenv("FRIDAY_FORCE_HEADLESS", raising=False)
        from control.pc_control import _is_headless
        assert _is_headless() is False

    def test_force_headless_0_overrides_missing_display(self, monkeypatch):
        """FRIDAY_FORCE_HEADLESS=0 forces non-headless even without DISPLAY."""
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("FRIDAY_FORCE_HEADLESS", "0")
        from control.pc_control import _is_headless
        assert _is_headless() is False


class TestPCControlHeadlessMode:
    """PCControl must gracefully degrade in headless mode."""

    @pytest.mark.asyncio
    async def test_move_and_click_returns_error_in_headless(self, monkeypatch, ledger_mock):
        # Force headless
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("FRIDAY_FORCE_HEADLESS", raising=False)

        from control.pc_control import PCControl
        pc = PCControl()
        pc.ledger = ledger_mock
        result = await pc.move_and_click(100, 200)
        assert result["status"] == "error"
        assert "headless" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_type_text_returns_error_in_headless(self, monkeypatch, ledger_mock):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("FRIDAY_FORCE_HEADLESS", raising=False)

        from control.pc_control import PCControl
        pc = PCControl()
        pc.ledger = ledger_mock
        result = await pc.type_text("hello")
        assert result["status"] == "error"
        assert "headless" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_press_shortcut_returns_error_in_headless(self, monkeypatch, ledger_mock):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("FRIDAY_FORCE_HEADLESS", raising=False)

        from control.pc_control import PCControl
        pc = PCControl()
        pc.ledger = ledger_mock
        result = await pc.press_shortcut("ctrl", "c")
        assert result["status"] == "error"
        assert "headless" in result["message"].lower()


class TestPCControlLedgerGate:
    """Verify PCControl gates every action through the ledger."""

    @pytest.mark.asyncio
    async def test_move_and_click_goes_through_ledger(self, monkeypatch, ledger_mock, fake_pyaudio_for_pc):
        # Force non-headless so we can test the ledger gate path
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("FRIDAY_FORCE_HEADLESS", raising=False)

        from control.pc_control import PCControl
        pc = PCControl()
        pc.ledger = ledger_mock
        # Inject a fake pyautogui
        pc._pyautogui = MagicMock()
        pc.headless = False

        result = await pc.move_and_click(100, 200)
        assert result["status"] == "success"
        ledger_mock.queue_action.assert_called_once()
        # Verify the component is PCControl
        args, kwargs = ledger_mock.queue_action.call_args
        assert args[0] == "PCControl"
        assert args[1] == "move_and_click"

    @pytest.mark.asyncio
    async def test_move_and_click_rejected_by_ledger(self, monkeypatch, ledger_mock, fake_pyaudio_for_pc):
        """When the ledger denies approval, action must NOT execute."""
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("FRIDAY_FORCE_HEADLESS", raising=False)

        from control.pc_control import PCControl
        pc = PCControl()
        ledger_mock.wait_for_approval = AsyncMock(return_value=False)
        pc.ledger = ledger_mock
        pc._pyautogui = MagicMock()
        pc.headless = False

        result = await pc.move_and_click(100, 200)
        assert result["status"] == "error"
        assert "rejected" in result["message"].lower()
        # Crucially: pyautogui must NOT have been called
        pc._pyautogui.moveTo.assert_not_called()
        pc._pyautogui.click.assert_not_called()


class TestPCControlFailsafe:
    """Verify FAILSAFE is set on pyautogui (mouse-to-corner emergency stop)."""

    def test_failsafe_set_when_pyautogui_available(self, monkeypatch):
        """If pyautogui is importable, FAILSAFE must be True."""
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("FRIDAY_FORCE_HEADLESS", raising=False)

        fake_pa = types.ModuleType("pyautogui")
        fake_pa.FAILSAFE = False  # Will be set to True by PCControl
        monkeypatch.setitem(sys.modules, "pyautogui", fake_pa)

        from control.pc_control import PCControl
        PCControl()
        assert fake_pa.FAILSAFE is True


class TestPCControlScreenshotReceipt:
    """Test that PCControl produces screenshot receipts."""

    @pytest.mark.asyncio
    async def test_screenshot_receipt_in_headless_returns_placeholder(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("FRIDAY_FORCE_HEADLESS", raising=False)

        from control.pc_control import PCControl
        pc = PCControl()
        receipt = await pc._get_screenshot_receipt()
        assert receipt["type"] == "screenshot_unavailable"
        assert receipt["data"] == "headless_mode"
        assert "timestamp" in receipt


# ---------------------------------------------------------------------------
# control/browser_control.py — ledger gate + execute_script risk
# ---------------------------------------------------------------------------


class TestBrowserControlLedgerGate:
    """BrowserControl must gate every action through the ledger."""

    @pytest.mark.asyncio
    async def test_navigate_uses_low_risk_level(self, ledger_mock):
        from control.browser_control import BrowserControl
        bc = BrowserControl()
        bc.ledger = ledger_mock
        bc.page = MagicMock()
        bc.page.goto = AsyncMock()
        bc.page.screenshot = AsyncMock()
        await bc.navigate("https://example.com")
        # Verify queue_action was called with risk_level="low"
        _, kwargs = ledger_mock.queue_action.call_args
        assert kwargs["risk_level"] == "low"

    @pytest.mark.asyncio
    async def test_click_element_uses_medium_risk(self, ledger_mock):
        from control.browser_control import BrowserControl
        bc = BrowserControl()
        bc.ledger = ledger_mock
        bc.page = MagicMock()
        bc.page.click = AsyncMock()
        bc.page.screenshot = AsyncMock()
        await bc.click_element("#submit")
        _, kwargs = ledger_mock.queue_action.call_args
        assert kwargs["risk_level"] == "medium"

    @pytest.mark.asyncio
    async def test_type_text_uses_medium_risk(self, ledger_mock):
        from control.browser_control import BrowserControl
        bc = BrowserControl()
        bc.ledger = ledger_mock
        bc.page = MagicMock()
        bc.page.fill = AsyncMock()
        bc.page.screenshot = AsyncMock()
        await bc.type_text("#username", "friday")
        _, kwargs = ledger_mock.queue_action.call_args
        assert kwargs["risk_level"] == "medium"

    @pytest.mark.asyncio
    async def test_execute_script_uses_critical_risk(self, ledger_mock):
        """SECURITY: execute_script must ALWAYS be classified as 'critical'."""
        from control.browser_control import BrowserControl
        bc = BrowserControl()
        bc.ledger = ledger_mock
        bc.page = MagicMock()
        bc.page.evaluate = AsyncMock(return_value="result")
        bc.page.screenshot = AsyncMock()
        await bc.execute_script("document.title")
        _, kwargs = ledger_mock.queue_action.call_args
        assert kwargs["risk_level"] == "critical"

    @pytest.mark.asyncio
    async def test_execute_script_rejected_returns_error(self, ledger_mock):
        from control.browser_control import BrowserControl
        bc = BrowserControl()
        ledger_mock.wait_for_approval = AsyncMock(return_value=False)
        bc.ledger = ledger_mock
        bc.page = MagicMock()
        bc.page.evaluate = AsyncMock()
        result = await bc.execute_script("document.cookie")
        assert result["status"] == "error"
        assert "rejected" in result["message"].lower()
        bc.page.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_navigate_rejected_returns_error(self, ledger_mock):
        from control.browser_control import BrowserControl
        bc = BrowserControl()
        ledger_mock.wait_for_approval = AsyncMock(return_value=False)
        bc.ledger = ledger_mock
        bc.page = MagicMock()
        bc.page.goto = AsyncMock()
        result = await bc.navigate("https://evil.example.com")
        assert result["status"] == "error"
        bc.page.goto.assert_not_called()


# ---------------------------------------------------------------------------
# control/app_launcher.py — cross-platform launcher
# ---------------------------------------------------------------------------


class TestAppLauncher:
    """Test AppLauncher — uses os.startfile / open -a / xdg-open by platform."""

    def test_launch_on_linux_uses_xdg_open(self, monkeypatch):
        from control.app_launcher import AppLauncher
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        with patch("control.app_launcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = AppLauncher().launch("firefox")
        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "xdg-open"
        assert args[1] == "firefox"

    def test_launch_on_macos_uses_open_a(self, monkeypatch):
        from control.app_launcher import AppLauncher
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        with patch("control.app_launcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = AppLauncher().launch("Safari")
        assert result is True
        args = mock_run.call_args[0][0]
        assert args == ["open", "-a", "Safari"]

    def test_launch_on_windows_uses_startfile(self, monkeypatch):
        from control.app_launcher import AppLauncher
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        # Inject a fake os.startfile (not available on Linux)
        with patch("control.app_launcher.os.startfile", create=True) as mock_startfile:
            result = AppLauncher().launch("notepad")
        assert result is True
        mock_startfile.assert_called_once_with("notepad")

    def test_launch_returns_false_on_exception(self, monkeypatch):
        from control.app_launcher import AppLauncher
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        with patch("control.app_launcher.subprocess.run", side_effect=FileNotFoundError("no xdg-open")):
            result = AppLauncher().launch("nonexistent-app")
        assert result is False


# ---------------------------------------------------------------------------
# control/workspace.py — hardcoded workflow detection
# ---------------------------------------------------------------------------


class TestWorkspaceOrchestrator:
    """Test WorkspaceOrchestrator — hardcoded 'coding' and 'presentation' workflows."""

    @pytest.mark.asyncio
    async def test_execute_workflow_coding(self):
        from control.workspace import WorkspaceOrchestrator
        wo = WorkspaceOrchestrator()
        # Mock the collaborators
        wo.smarthome = MagicMock()
        wo.smarthome.execute = AsyncMock()
        wo.launcher = MagicMock()
        wo.pc = MagicMock()
        wo.pc.press_shortcut = AsyncMock()
        result = await wo.execute_workflow("coding")
        assert "coding" in result.lower()
        wo.launcher.launch.assert_any_call("VS Code")
        wo.launcher.launch.assert_any_call("Chrome")
        wo.pc.press_shortcut.assert_called_once_with("win", "left")

    @pytest.mark.asyncio
    async def test_execute_workflow_presentation(self):
        from control.workspace import WorkspaceOrchestrator
        wo = WorkspaceOrchestrator()
        wo.smarthome = MagicMock()
        wo.smarthome.execute = AsyncMock()
        wo.launcher = MagicMock()
        result = await wo.execute_workflow("presentation")
        assert "presentation" in result.lower()
        wo.launcher.launch.assert_called_with("PowerPoint")

    @pytest.mark.asyncio
    async def test_execute_workflow_unknown_returns_unknown_message(self):
        from control.workspace import WorkspaceOrchestrator
        wo = WorkspaceOrchestrator()
        wo.smarthome = MagicMock()
        wo.smarthome.execute = AsyncMock()
        wo.launcher = MagicMock()
        wo.pc = MagicMock()
        wo.pc.press_shortcut = AsyncMock()
        result = await wo.execute_workflow("nonexistent_workflow")
        assert "unknown" in result.lower()

    @pytest.mark.asyncio
    async def test_coding_mode_dims_lights(self):
        """Coding workflow should dim smart-home lights."""
        from control.workspace import WorkspaceOrchestrator
        wo = WorkspaceOrchestrator()
        wo.smarthome = MagicMock()
        wo.smarthome.execute = AsyncMock()
        wo.launcher = MagicMock()
        wo.pc = MagicMock()
        wo.pc.press_shortcut = AsyncMock()
        await wo.execute_workflow("coding")
        wo.smarthome.execute.assert_called_with("control_lights", {"state": "dim"})

    @pytest.mark.asyncio
    async def test_presentation_mode_brightens_lights(self):
        from control.workspace import WorkspaceOrchestrator
        wo = WorkspaceOrchestrator()
        wo.smarthome = MagicMock()
        wo.smarthome.execute = AsyncMock()
        wo.launcher = MagicMock()
        await wo.execute_workflow("presentation")
        wo.smarthome.execute.assert_called_with("control_lights", {"state": "bright"})
