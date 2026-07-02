"""Proactive Engine — generates briefings, checks for interruptions,
and switches context modes based on time of day.

Context modes:
  - morning   (6–10)    : Upbeat, briefing-focused
  - work      (10–18)   : Productivity-focused, interruptions suppressed
  - gaming    (18–22)   : Relaxed, entertainment-oriented
  - night     (22–6)    : Quiet, minimal interruptions, sleep-friendly

The engine actually changes behaviour based on these conditions.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("ProactiveEngine")


# ──────────────────────────────────────────────────────────────────────────────
# Context modes
# ──────────────────────────────────────────────────────────────────────────────

CONTEXT_MODES = {
    "morning": {
        "description": "Morning mode — upbeat, briefing-focused",
        "hours": (6, 10),
        "interruption_frequency": 300,    # seconds between checks
        "briefing_enabled": True,
        "notification_style": "verbose",
        "personality_tone": "energetic",
    },
    "work": {
        "description": "Work mode — productivity-focused, minimal interruptions",
        "hours": (10, 18),
        "interruption_frequency": 600,    # less frequent
        "briefing_enabled": False,
        "notification_style": "concise",
        "personality_tone": "professional",
    },
    "gaming": {
        "description": "Gaming/leisure mode — relaxed, entertainment-oriented",
        "hours": (18, 22),
        "interruption_frequency": 900,    # even less frequent
        "briefing_enabled": False,
        "notification_style": "fun",
        "personality_tone": "casual",
    },
    "night": {
        "description": "Night mode — quiet, sleep-friendly",
        "hours": (22, 6),
        "interruption_frequency": 0,      # no interruptions
        "briefing_enabled": False,
        "notification_style": "silent",
        "personality_tone": "whisper",
    },
}


def get_current_context_mode() -> str:
    """Determine the current context mode based on time of day."""
    hour = datetime.now().hour
    for mode_name, config in CONTEXT_MODES.items():
        start, end = config["hours"]
        if start <= end:
            if start <= hour < end:
                return mode_name
        else:
            # Wraps midnight (e.g., 22–6)
            if hour >= start or hour < end:
                return mode_name
    return "work"  # fallback


class ProactiveEngine:
    """Generates proactive briefings and checks for time-sensitive interruptions.

    Context-mode switching is tied to time of day and actually changes
    the engine's behaviour (interruption frequency, notification style,
    personality tone).
    """

    def __init__(self, brain, speaker=None, connector=None):
        self.brain = brain
        self.speaker = speaker
        self.connector = connector or getattr(brain, "connector", None)
        self.logger = logging.getLogger("ProactiveEngine")
        self._last_briefing_date: Optional[str] = None
        self._current_mode: Optional[str] = None

    # ------------------------------------------------------------------
    # Context mode
    # ------------------------------------------------------------------

    @property
    def current_mode(self) -> str:
        """Return the current context mode, refreshing from the clock."""
        self._current_mode = get_current_context_mode()
        return self._current_mode

    def get_mode_config(self) -> dict:
        """Return the configuration for the current context mode."""
        mode = self.current_mode
        return CONTEXT_MODES.get(mode, CONTEXT_MODES["work"])

    def should_interrupt(self) -> bool:
        """Return True if interruptions are allowed in the current mode."""
        config = self.get_mode_config()
        return config["interruption_frequency"] > 0

    # ------------------------------------------------------------------
    # Daily briefing
    # ------------------------------------------------------------------

    async def daily_briefing(self) -> str:
        """Generate a proactive morning briefing using the connector.

        Only runs in morning mode.  All data fetching goes through the
        universal connector so that every action passes through the
        ledger's approval gate.
        """
        mode = self.current_mode
        config = self.get_mode_config()

        if not config.get("briefing_enabled"):
            return "Briefing skipped — current mode is '{}''. Use morning mode (6–10 AM) for briefings.".format(mode)

        if self.connector is None:
            self.logger.warning("No connector available for daily briefing")
            return "Briefing unavailable: no connector configured."

        # Fetch data through the connector
        weather_res = await self.connector.execute_action(
            "Weather", "get_weather", {"location": "default"}
        )
        calendar_res = await self.connector.execute_action(
            "Calendar", "get_todays_events"
        )
        email_res = await self.connector.execute_action(
            "Gmail", "get_unread_emails"
        )

        weather_msg = weather_res.get("message", "Weather unavailable")
        calendar_msg = calendar_res.get("message", "Calendar unavailable")
        email_msg = email_res.get("message", "Email unavailable")

        # Adapt prompt to current personality tone
        tone = config.get("personality_tone", "professional")

        prompt = (
            f"Generate a proactive morning briefing with a {tone} tone "
            f"based on the following data:\n"
            f"Weather: {weather_msg}\n"
            f"Calendar: {calendar_msg}\n"
            f"Emails: {email_msg}\n\n"
            "Make it sound warm, witty, and extremely helpful. "
            "Highlight any urgent items or time-sensitive events."
        )

        briefing = ""
        async for chunk in self.brain.chat_stream(prompt):
            briefing += chunk

        self._last_briefing_date = datetime.now().strftime("%Y-%m-%d")

        if self.speaker and hasattr(self.speaker, "speak"):
            self.speaker.speak(briefing)

        return briefing

    # ------------------------------------------------------------------
    # Interruption checking (mode-aware)
    # ------------------------------------------------------------------

    async def check_for_interruptions(self, interval_seconds: int = None) -> None:
        """Periodically check for upcoming calendar events and urgent items.

        The interval is determined by the current context mode.  Night
        mode disables interruptions entirely.
        """
        config = self.get_mode_config()
        interval = interval_seconds or config.get("interruption_frequency", 300)

        if interval <= 0:
            self.logger.info("Interruptions disabled in current mode: %s", self.current_mode)
            return

        while True:
            # Re-check mode on each iteration (time may have changed)
            current_config = self.get_mode_config()
            if current_config.get("interruption_frequency", 0) <= 0:
                self.logger.info("Switched to non-interrupt mode — pausing checks")
                await asyncio.sleep(60)
                continue

            try:
                await self._check_upcoming_events()
            except Exception as exc:
                self.logger.error(f"Interruption check failed: {exc}")

            await asyncio.sleep(current_config.get("interruption_frequency", interval))

    async def _check_upcoming_events(self) -> None:
        """Check for events starting within the next 15 minutes."""
        if self.connector is None:
            return

        config = self.get_mode_config()
        style = config.get("notification_style", "concise")

        try:
            result = await self.connector.execute_action(
                "Calendar", "get_upcoming_events",
                {"minutes_ahead": 15},
            )

            if result.get("status") != "success":
                return

            events = result.get("receipt", {}).get("data", [])
            if not events:
                return

            # Alert about upcoming events (style-adapted)
            for event in events:
                summary = event.get("summary", "Upcoming event")
                start = event.get("start", "soon")

                if style == "verbose":
                    message = f"Good morning! Just a heads up — you have '{summary}' starting at {start}. Don't forget!"
                elif style == "concise":
                    message = f"Reminder: '{summary}' at {start}."
                elif style == "fun":
                    message = f"Hey! '{summary}' is about to start at {start}. Time to go!"
                elif style == "silent":
                    # Log only, don't speak
                    self.logger.info(f"[SILENT] Upcoming event: '{summary}' at {start}")
                    continue
                else:
                    message = f"Heads up — you have '{summary}' starting at {start}."

                self.logger.info(message)

                if self.speaker and hasattr(self.speaker, "speak"):
                    self.speaker.speak(message)

        except Exception as exc:
            self.logger.warning(f"Could not check upcoming events: {exc}")
