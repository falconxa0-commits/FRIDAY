import os
import logging
import datetime
from typing import Optional

from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class GmailIntegration(BaseIntegration):
    """Gmail integration that reads emails via the Google API.

    Falls back gracefully when credentials are absent.
    """

    @property
    def name(self) -> str:
        return "Gmail"

    def __init__(self, token_path: str = "token.json"):
        self.creds = None
        self.service = None

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            if os.path.exists(token_path):
                self.creds = Credentials.from_authorized_user_file(token_path)
            if self.creds:
                self.service = build("gmail", "v1", credentials=self.creds)
        except Exception:
            logger.exception("GmailIntegration: failed to initialise Google client")

    def available(self) -> bool:
        return self.service is not None

    def list_actions(self):
        return ["get_unread_emails", "search_emails"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        if not self.available():
            return self._make_response(
                "not_implemented",
                "Gmail not connected. Provide a valid token.json to enable.",
            )

        params = params or {}

        try:
            if action == "get_unread_emails":
                return await self._get_unread_emails(params)
            elif action == "search_emails":
                return await self._search_emails(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by Gmail integration.",
                )
        except Exception as exc:
            logger.error("Gmail execute failed: %s", exc)
            return self._make_response("error", str(exc))

    # ---- action implementations -------------------------------------

    async def _get_unread_emails(self, params: dict) -> dict:
        import asyncio
        max_results = params.get("max_results", 10)

        def _fetch():
            return (
                self.service.users()
                .messages()
                .list(userId="me", q="is:unread", maxResults=max_results)
                .execute()
            )
        results = await asyncio.to_thread(_fetch)
        messages = results.get("messages", [])
        return self._make_response(
            "success",
            f"Found {len(messages)} unread emails.",
            receipt_data={"messages": messages},
        )

    async def _search_emails(self, params: dict) -> dict:
        import asyncio
        query = params.get("query", "")
        if not query:
            return self._make_response("error", "Missing 'query' parameter.")
        max_results = params.get("max_results", 10)

        def _fetch():
            return (
                self.service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
        results = await asyncio.to_thread(_fetch)
        messages = results.get("messages", [])
        return self._make_response(
            "success",
            f"Found {len(messages)} emails matching '{query}'.",
            receipt_data={"messages": messages},
        )
