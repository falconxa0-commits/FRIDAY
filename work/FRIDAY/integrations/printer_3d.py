"""3D Printer Integration — real OctoPrint/Moonraker integration.

Inherits from BaseIntegration.  Checks for OctoPrint or Moonraker API
reachability, and dispatches get_status, start_job, and cancel_job.

If no 3D printer is reachable the integration reports ``not_implemented``
— it never fakes success.  Never auto-approves actions.
"""

import asyncio
import datetime
import logging
import os
from typing import Optional

import httpx

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class Printer3D(BaseIntegration):
    """OctoPrint/Moonraker 3D printer integration — real hardware only."""

    @property
    def name(self) -> str:
        return "Printer3D"

    def __init__(self):
        self.octoprint_url = os.getenv("OCTOPRINT_URL", "http://localhost:5000")
        self.octoprint_key = os.getenv("OCTOPRINT_API_KEY", "")
        self.moonraker_url = os.getenv("MOONRAKER_URL", "http://localhost:7125")
        self._backend: Optional[str] = None  # "octoprint" or "moonraker"

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def available(self) -> bool:
        """Return True if OctoPrint or Moonraker API is reachable."""
        # Try OctoPrint first
        try:
            headers = {}
            if self.octoprint_key:
                headers["X-Api-Key"] = self.octoprint_key
            resp = httpx.get(
                f"{self.octoprint_url}/api/version",
                headers=headers,
                timeout=3,
            )
            if resp.status_code == 200:
                self._backend = "octoprint"
                logger.info("OctoPrint is reachable at %s", self.octoprint_url)
                return True
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        except Exception as exc:
            logger.debug(f"OctoPrint check error: {exc}")

        # Try Moonraker
        try:
            resp = httpx.get(
                f"{self.moonraker_url}/printer/info",
                timeout=3,
            )
            if resp.status_code == 200:
                self._backend = "moonraker"
                logger.info("Moonraker is reachable at %s", self.moonraker_url)
                return True
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        except Exception as exc:
            logger.debug(f"Moonraker check error: {exc}")

        logger.info("No 3D printer backend reachable")
        return False

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def list_actions(self):
        return ["get_status", "start_job", "cancel_job"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}

        if not self.available():
            return self._make_response(
                "not_implemented",
                "No 3D printer reachable. Requires OctoPrint or Moonraker server.",
            )

        try:
            if action == "get_status":
                return await self._get_status()
            elif action == "start_job":
                return await self._start_job(params)
            elif action == "cancel_job":
                return await self._cancel_job(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by Printer3D.",
                )
        except Exception as exc:
            logger.error(f"Printer3D execute failed: {exc}")
            return self._make_response("error", str(exc))

    # ------------------------------------------------------------------
    # OctoPrint helpers
    # ------------------------------------------------------------------

    def _octoprint_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.octoprint_key:
            headers["X-Api-Key"] = self.octoprint_key
        return headers

    async def _get_status(self) -> dict:
        """Get current printer status."""
        if self._backend == "octoprint":
            return await self._octoprint_get_status()
        elif self._backend == "moonraker":
            return await self._moonraker_get_status()
        return self._make_response("error", "No backend selected")

    async def _octoprint_get_status(self) -> dict:
        def _run():
            return httpx.get(
                f"{self.octoprint_url}/api/printer",
                headers=self._octoprint_headers(),
                timeout=10,
            )

        resp = await asyncio.to_thread(_run)

        if resp.status_code != 200:
            return self._make_response("error", f"OctoPrint API error: {resp.status_code}")

        data = resp.json()
        state = data.get("state", {})
        return self._make_response(
            "success",
            f"Printer state: {state.get('text', 'unknown')}",
            receipt_data={
                "backend": "octoprint",
                "state": state,
                "temperature": data.get("temperature", {}),
            },
        )

    async def _moonraker_get_status(self) -> dict:
        def _run():
            return httpx.get(
                f"{self.moonraker_url}/printer/objects/query?printer",
                timeout=10,
            )

        resp = await asyncio.to_thread(_run)

        if resp.status_code != 200:
            return self._make_response("error", f"Moonraker API error: {resp.status_code}")

        data = resp.json()
        status = data.get("result", {}).get("status", {})
        printer_obj = status.get("printer", {})
        return self._make_response(
            "success",
            f"Printer state: {printer_obj.get('state', 'unknown')}",
            receipt_data={
                "backend": "moonraker",
                "state": printer_obj,
            },
        )

    async def _start_job(self, params: dict) -> dict:
        """Start a print job.

        Params:
            file_path (str): The file to print (on the server filesystem).
        """
        file_path = params.get("file_path")
        if not file_path:
            return self._make_response("error", "Missing required parameter: file_path")

        if self._backend == "octoprint":
            return await self._octoprint_start_job(file_path)
        elif self._backend == "moonraker":
            return await self._moonraker_start_job(file_path)
        return self._make_response("error", "No backend selected")

    async def _octoprint_start_job(self, file_path: str) -> dict:
        def _run():
            return httpx.post(
                f"{self.octoprint_url}/api/files/local/{file_path}",
                headers=self._octoprint_headers(),
                json={"command": "select", "print": True},
                timeout=10,
            )

        resp = await asyncio.to_thread(_run)

        if resp.status_code in (200, 201, 204):
            return self._make_response(
                "success",
                f"Print job started: {file_path}",
                receipt_data={"backend": "octoprint", "file": file_path},
            )
        return self._make_response("error", f"OctoPrint start failed: {resp.status_code} {resp.text[:200]}")

    async def _moonraker_start_job(self, file_path: str) -> dict:
        def _run():
            return httpx.post(
                f"{self.moonraker_url}/printer/print/start",
                json={"filename": file_path},
                timeout=10,
            )

        resp = await asyncio.to_thread(_run)

        if resp.status_code == 200:
            return self._make_response(
                "success",
                f"Print job started: {file_path}",
                receipt_data={"backend": "moonraker", "file": file_path},
            )
        return self._make_response("error", f"Moonraker start failed: {resp.status_code} {resp.text[:200]}")

    async def _cancel_job(self, params: dict) -> dict:
        """Cancel the current print job."""
        if self._backend == "octoprint":
            return await self._octoprint_cancel_job()
        elif self._backend == "moonraker":
            return await self._moonraker_cancel_job()
        return self._make_response("error", "No backend selected")

    async def _octoprint_cancel_job(self) -> dict:
        def _run():
            return httpx.post(
                f"{self.octoprint_url}/api/job",
                headers=self._octoprint_headers(),
                json={"command": "cancel"},
                timeout=10,
            )

        resp = await asyncio.to_thread(_run)

        if resp.status_code in (200, 204):
            return self._make_response("success", "Print job cancelled", receipt_data={"backend": "octoprint"})
        return self._make_response("error", f"Cancel failed: {resp.status_code}")

    async def _moonraker_cancel_job(self) -> dict:
        def _run():
            return httpx.post(
                f"{self.moonraker_url}/printer/print/cancel",
                timeout=10,
            )

        resp = await asyncio.to_thread(_run)

        if resp.status_code == 200:
            return self._make_response("success", "Print job cancelled", receipt_data={"backend": "moonraker"})
        return self._make_response("error", f"Cancel failed: {resp.status_code}")
