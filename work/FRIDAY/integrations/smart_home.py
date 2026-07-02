import logging
import datetime
from typing import Optional

import httpx

from config.settings import HOME_ASSISTANT_TOKEN
from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class SmartHomeIntegration(BaseIntegration):
    """Control Home Assistant entities via its REST API (async)."""

    @property
    def name(self) -> str:
        return "HomeAssistant"

    def __init__(self):
        self.base_url = "http://homeassistant.local:8123/api"
        self.headers = {
            "Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}",
            "content-type": "application/json",
        }

    def available(self) -> bool:
        return bool(HOME_ASSISTANT_TOKEN)

    def list_actions(self):
        return ["control_lights", "get_entity_state", "call_service"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        if not self.available():
            return self._make_response(
                "not_implemented",
                "Home Assistant token not configured.",
            )

        params = params or {}

        try:
            if action == "control_lights":
                return await self._control_lights(params)
            elif action == "get_entity_state":
                return await self._get_entity_state(params)
            elif action == "call_service":
                return await self._call_service(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by HomeAssistant.",
                )
        except Exception as exc:
            logger.error("SmartHome execute failed: %s", exc)
            return self._make_response("error", str(exc))

    # ---- action implementations -------------------------------------

    async def _control_lights(self, params: dict) -> dict:
        state = params.get("state", "on")
        entity_id = params.get("entity_id", "light.all")
        domain = "light"
        service = "turn_on" if state == "on" else "turn_off"
        url = f"{self.base_url}/services/{domain}/{service}"

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                headers=self.headers,
                json={"entity_id": entity_id},
            )

        if resp.status_code == 200:
            return self._make_response(
                "success",
                f"Lights {state} request processed for {entity_id}.",
                receipt_data={"entity_id": entity_id, "state": state},
            )
        return self._make_response(
            "error",
            f"Home Assistant returned {resp.status_code}: {resp.text}",
        )

    async def _get_entity_state(self, params: dict) -> dict:
        entity_id = params.get("entity_id")
        if not entity_id:
            return self._make_response("error", "Missing 'entity_id' parameter.")
        url = f"{self.base_url}/states/{entity_id}"

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=self.headers)

        if resp.status_code == 200:
            data = resp.json()
            return self._make_response(
                "success",
                f"State of {entity_id}: {data.get('state', 'unknown')}",
                receipt_data=data,
            )
        return self._make_response(
            "error",
            f"Failed to get state for {entity_id}: {resp.status_code}",
        )

    async def _call_service(self, params: dict) -> dict:
        domain = params.get("domain")
        service = params.get("service")
        service_data = params.get("service_data", {})

        if not domain or not service:
            return self._make_response(
                "error", "Missing 'domain' and/or 'service' parameters."
            )
        url = f"{self.base_url}/services/{domain}/{service}"

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url, headers=self.headers, json=service_data
            )

        if resp.status_code == 200:
            return self._make_response(
                "success",
                f"Service {domain}.{service} called successfully.",
                receipt_data=resp.json(),
            )
        return self._make_response(
            "error",
            f"Service call failed: {resp.status_code} — {resp.text}",
        )
