import asyncio
import logging
from control.app_launcher import AppLauncher
from control.pc_control import PCControl
from integrations.smart_home import SmartHomeIntegration

logger = logging.getLogger(__name__)


class WorkspaceOrchestrator:
    def __init__(self):
        self.launcher = AppLauncher()
        self.pc = PCControl()
        self.smarthome = SmartHomeIntegration()

    async def set_coding_mode(self):
        logger.info("Friday: Setting up Coding Mode. Focus enabled.")
        # SmartHomeIntegration.execute is async — must be awaited
        await self.smarthome.execute("control_lights", {"state": "dim"})
        self.launcher.launch("VS Code")
        self.launcher.launch("Chrome")
        # PCControl.press_shortcut is async — must be awaited
        await self.pc.press_shortcut("win", "left")
        return "Workspace optimized for coding. Good luck, sir."

    async def set_presentation_mode(self):
        logger.info("Friday: Setting up Presentation Mode.")
        await self.smarthome.execute("control_lights", {"state": "bright"})
        self.launcher.launch("PowerPoint")
        return "Workspace ready for the presentation."

    async def execute_workflow(self, workflow_name):
        if workflow_name == "coding":
            return await self.set_coding_mode()
        elif workflow_name == "presentation":
            return await self.set_presentation_mode()
        return "Unknown workflow."
