import logging
import datetime
from typing import Optional

from integrations.base import BaseIntegration
from config.settings import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

logger = logging.getLogger(__name__)


class SpotifyIntegration(BaseIntegration):
    """Control Spotify playback via spotipy (async-safe wrappers)."""

    @property
    def name(self) -> str:
        return "Spotify"

    def __init__(self):
        self.sp = None
        if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
            try:
                import spotipy
                from spotipy.oauth2 import SpotifyOAuth

                auth_manager = SpotifyOAuth(
                    client_id=SPOTIFY_CLIENT_ID,
                    client_secret=SPOTIFY_CLIENT_SECRET,
                    redirect_uri="http://localhost:8888/callback",
                    scope="user-modify-playback-state user-read-playback-state",
                )
                self.sp = spotipy.Spotify(auth_manager=auth_manager)
            except Exception:
                logger.exception("Spotify init failed")

    def available(self) -> bool:
        return self.sp is not None

    def list_actions(self):
        return ["play", "pause", "current_track", "search"]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        if not self.available():
            return self._make_response(
                "not_implemented",
                "Spotify credentials not configured.",
            )

        params = params or {}

        try:
            if action == "play":
                return await self._play(params)
            elif action == "pause":
                return await self._pause()
            elif action == "current_track":
                return await self._current_track()
            elif action == "search":
                return await self._search(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by Spotify.",
                )
        except Exception as exc:
            logger.error("Spotify execute failed: %s", exc)
            return self._make_response("error", str(exc))

    # ---- action implementations -------------------------------------

    async def _play(self, params: dict) -> dict:
        track = params.get("track_name")
        if track:
            results = self.sp.search(q=track, type="track", limit=1)
            items = results.get("tracks", {}).get("items", [])
            if items:
                track_uri = items[0]["uri"]
                self.sp.start_playback(uris=[track_uri])
                return self._make_response(
                    "success",
                    f"Playing '{items[0]['name']}' by {items[0]['artists'][0]['name']}.",
                    receipt_data={"track_uri": track_uri},
                )
            return self._make_response("error", f"Track '{track}' not found.")
        self.sp.start_playback()
        return self._make_response("success", "Spotify: Resuming playback.")

    async def _pause(self) -> dict:
        self.sp.pause_playback()
        return self._make_response("success", "Spotify paused.")

    async def _current_track(self) -> dict:
        playback = self.sp.current_playback()
        if playback and playback.get("item"):
            item = playback["item"]
            name = item["name"]
            artists = ", ".join(a["name"] for a in item["artists"])
            return self._make_response(
                "success",
                f"Now playing: {name} by {artists}",
                receipt_data=playback,
            )
        return self._make_response("success", "Nothing is currently playing.")

    async def _search(self, params: dict) -> dict:
        query = params.get("query", "")
        q_type = params.get("type", "track")
        if not query:
            return self._make_response("error", "Missing 'query' parameter.")
        results = self.sp.search(q=query, type=q_type, limit=5)
        items = results.get(f"{q_type}s", {}).get("items", [])
        names = [i.get("name", "Unknown") for i in items]
        return self._make_response(
            "success",
            f"Top {q_type} results for '{query}': {', '.join(names)}",
            receipt_data={"names": names},
        )
