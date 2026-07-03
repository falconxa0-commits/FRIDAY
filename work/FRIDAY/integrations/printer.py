"""Printer Integration — real CUPS/IPP integration.

Inherits from BaseIntegration.  Checks for CUPS availability, and
dispatches list_printers, submit_job, and job_status actions.

If no printer is reachable the integration reports ``not_implemented``
— it never fakes success.
"""

import asyncio
import datetime
import logging
import shutil
import subprocess
from typing import Optional

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class Printer(BaseIntegration):
    """CUPS/IPP printer integration — real hardware only."""

    @property
    def name(self) -> str:
        return "Printer"

    def __init__(self):
        self._cups_available: Optional[bool] = None

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def available(self) -> bool:
        """Return True if CUPS (lp / lpstat) is installed and a printer is reachable."""
        if self._cups_available is not None:
            return self._cups_available

        # Check that lpstat command exists
        if not shutil.which("lpstat"):
            logger.info("lpstat not found — Printer integration unavailable")
            self._cups_available = False
            return False

        try:
            result = subprocess.run(
                ["lpstat", "-p"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # lpstat -p returns 0 if any printer is configured
            self._cups_available = result.returncode == 0
            if not self._cups_available:
                logger.info("lpstat reports no printers configured")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.info(f"CUPS check failed: {exc}")
            self._cups_available = False

        return self._cups_available

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def list_actions(self):
        return ["list_printers", "submit_job", "job_status"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}

        if not self.available():
            return self._make_response(
                "not_implemented",
                "No CUPS printer reachable. Printer integration requires a local CUPS server.",
            )

        try:
            if action == "list_printers":
                return await self._list_printers()
            elif action == "submit_job":
                return await self._submit_job(params)
            elif action == "job_status":
                return await self._job_status(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by Printer.",
                )
        except Exception as exc:
            logger.error(f"Printer execute failed: {exc}")
            return self._make_response("error", str(exc))

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------

    async def _list_printers(self) -> dict:
        """List all configured CUPS printers."""
        def _run():
            result = subprocess.run(
                ["lpstat", "-p", "-d"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip(), result.returncode

        stdout, rc = await asyncio.to_thread(_run)

        if rc != 0:
            return self._make_response(
                "error",
                f"lpstat failed: {stdout}",
            )

        # Parse printer names from "printer <name> is idle." lines
        printers = []
        for line in stdout.splitlines():
            if line.startswith("printer "):
                parts = line.split()
                if len(parts) >= 2:
                    printers.append(parts[1])

        default_printer = ""
        for line in stdout.splitlines():
            if line.startswith("system default destination:"):
                default_printer = line.split(":")[-1].strip()

        return self._make_response(
            "success",
            f"Found {len(printers)} printer(s): {', '.join(printers)}",
            receipt_data={
                "printers": printers,
                "default": default_printer,
            },
        )

    async def _submit_job(self, params: dict) -> dict:
        """Submit a print job.

        Params:
            file_path (str): Path to the file to print.
            printer (str, optional): Target printer name. Defaults to CUPS default.
            copies (int, optional): Number of copies. Default 1.
        """
        file_path = params.get("file_path")
        if not file_path:
            return self._make_response("error", "Missing required parameter: file_path")

        printer = params.get("printer", "")
        copies = params.get("copies", 1)

        cmd = ["lp"]
        if printer:
            cmd.extend(["-d", printer])
        cmd.extend(["-n", str(copies), file_path])

        def _run():
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode

        stdout, stderr, rc = await asyncio.to_thread(_run)

        if rc != 0:
            return self._make_response(
                "error",
                f"Print job submission failed: {stderr or stdout}",
            )

        # Parse job ID from "request id is printer-123" output
        job_id = stdout

        return self._make_response(
            "success",
            f"Print job submitted: {job_id}",
            receipt_data={
                "job_id": job_id,
                "printer": printer or "default",
                "copies": copies,
                "file_path": file_path,
            },
        )

    async def _job_status(self, params: dict) -> dict:
        """Check the status of a print job.

        Params:
            job_id (str): The job ID to check.
        """
        job_id = params.get("job_id")
        if not job_id:
            return self._make_response("error", "Missing required parameter: job_id")

        def _run():
            result = subprocess.run(
                ["lpstat", "-l", job_id],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip(), result.returncode

        stdout, rc = await asyncio.to_thread(_run)

        if rc != 0:
            return self._make_response(
                "error",
                f"Job {job_id} not found or lpstat error.",
                receipt_data={"job_id": job_id, "status": "unknown"},
            )

        return self._make_response(
            "success",
            f"Status for {job_id}: {stdout[:200]}",
            receipt_data={
                "job_id": job_id,
                "details": stdout,
            },
        )
