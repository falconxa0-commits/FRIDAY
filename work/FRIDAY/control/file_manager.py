import os
import shutil
import logging
from config.settings import WORKSPACE_ROOT
from core.ledger import get_ledger

class FileManager:
    def __init__(self):
        self.workspace_root = os.path.abspath(WORKSPACE_ROOT)
        if not os.path.exists(self.workspace_root):
            os.makedirs(self.workspace_root)
        self.ledger = get_ledger()

    async def _gate(self, action, params, risk_level="medium"):
        action_id = self.ledger.queue_action("FileManager", action, params, risk_level=risk_level)
        if await self.ledger.wait_for_approval(action_id):
            return True
        return False

    def _safe_path(self, path):
        abs_path = os.path.abspath(os.path.join(self.workspace_root, path))
        # FIXED: Use commonpath to avoid sibling-directory bug
        if os.path.commonpath([abs_path, self.workspace_root]) != self.workspace_root:
            raise PermissionError(f"Access denied: {path} is outside the workspace root.")
        return abs_path

    async def create_file(self, path, content=""):
        if not await self._gate("create_file", {"path": path}, risk_level="medium"):
            return "Error: Action rejected by user."
        target = self._safe_path(path)
        with open(target, "w") as f:
            f.write(content)
        return f"File created at {path}"

    async def delete_file(self, path):
        if not await self._gate("delete_file", {"path": path}, risk_level="critical"):
            return "Error: Action rejected by user."
        
        target = self._safe_path(path)
        if os.path.isfile(target):
            os.remove(target)
            return f"File {path} deleted."
        elif os.path.isdir(target):
            shutil.rmtree(target)
            return f"Directory {path} deleted."
        return f"Path {path} not found."

    def list_files(self, directory="."):
        target = self._safe_path(directory)
        return os.listdir(target)
