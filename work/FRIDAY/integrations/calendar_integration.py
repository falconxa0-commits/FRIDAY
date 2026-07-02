import os
import logging
import datetime
from typing import Optional

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class CalendarIntegration(BaseIntegration):
    """Google Calendar integration (async-safe wrappers around googleapiclient)."""

    @property
    def name(self) -> str:
        return "Calendar"

    def __init__(self, token_path: str = "token.json"):
        self.creds = None
        self.service = None

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            if os.path.exists(token_path):
                self.creds = Credentials.from_authorized_user_file(token_path)
            if self.creds:
                self.service = build("calendar", "v3", credentials=self.creds)
        except Exception:
            logger.exception("CalendarIntegration: failed to initialise Google client")

    def available(self) -> bool:
        return self.service is not None

    def list_actions(self):
        return ["get_todays_events", "get_upcoming_events", "create_event"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        if not self.available():
            return self._make_response(
                "not_implemented",
                "Google Calendar not authenticated.",
            )

        params = params or {}

        try:
            if action == "get_todays_events":
                return await self._get_todays_events(params)
            elif action == "get_upcoming_events":
                return await self._get_upcoming_events(params)
            elif action == "create_event":
                return await self._create_event(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by Calendar.",
                )
        except Exception as exc:
            logger.error("Calendar execute failed: %s", exc)
            return self._make_response("error", str(exc))

    # ---- action implementations -------------------------------------

    async def _get_todays_events(self, params: dict) -> dict:
        now = datetime.datetime.utcnow().isoformat() + "Z"
        end_of_day = (
            datetime.datetime.utcnow().replace(hour=23, minute=59, second=59)
        ).isoformat() + "Z"

        events_result = (
            self.service.events()
            .list(
                calendarId="primary",
                timeMin=now,
                timeMax=end_of_day,
                maxResults=10,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = events_result.get("items", [])
        return self._make_response(
            "success",
            f"Found {len(events)} events for today.",
            receipt_data=events,
        )

    async def _get_upcoming_events(self, params: dict) -> dict:
        max_results = params.get("max_results", 10)
        now = datetime.datetime.utcnow().isoformat() + "Z"

        events_result = (
            self.service.events()
            .list(
                calendarId="primary",
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = events_result.get("items", [])
        return self._make_response(
            "success",
            f"Found {len(events)} upcoming events.",
            receipt_data=events,
        )

    async def _create_event(self, params: dict) -> dict:
        summary = params.get("summary")
        start = params.get("start")
        end = params.get("end")
        if not summary or not start or not end:
            return self._make_response(
                "error",
                "Missing required fields: summary, start, end.",
            )

        event_body = {
            "summary": summary,
            "start": {"dateTime": start, "timeZone": "UTC"},
            "end": {"dateTime": end, "timeZone": "UTC"},
        }

        result = (
            self.service.events()
            .insert(calendarId="primary", body=event_body)
            .execute()
        )
        return self._make_response(
            "success",
            f"Event '{summary}' created.",
            receipt_data=result,
        )
