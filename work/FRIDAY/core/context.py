"""Context Awareness — real time-of-day mode detection + interaction tracking.

Tracks the last interaction timestamp and exposes a real time-based mode
detector so other modules (e.g. wake-on-contact) can decide whether to
trigger proactive behaviours based on actual elapsed wall-clock time.

Modes (24h clock, configurable):
  - morning  : 06:00 - 09:00  (briefing, weather, calendar)
  - work     : 09:00 - 18:00  (focused, minimal interruptions)
  - evening  : 18:00 - 22:00  (wind down, reminders)
  - night    : 22:00 - 06:00  (quiet, only urgent alerts)
"""
import datetime
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# Default idle threshold for wake-on-first-contact: 6 hours.
# Override per-instance via the constructor if needed.
DEFAULT_IDLE_THRESHOLD_SECONDS = 6 * 60 * 60


class ContextAwareness:
    """Track user activity, time-of-day mode, and idle state.

    The mode is computed from real wall-clock time on every call to
    ``get_current_context()`` / ``get_time_context()`` — there is no
    manually-set flag that the demo can flip. To test mode switching
    without waiting for the actual time of day, inject a ``now_provider``
    callable (any function returning a ``datetime.datetime``).
    """

    # Mode boundaries (24h hour values). Order matters — first match wins
    # when iterating from morning -> work -> evening -> night.
    MODE_BOUNDARIES = (
        ("morning", 6, 9),    # 06:00–09:00
        ("work",    9, 18),   # 09:00–18:00
        ("evening", 18, 22),  # 18:00–22:00
        ("night",   22, 24),  # 22:00–24:00  (first half of night)
        # 00:00–06:00 falls through to "night"
    )

    def __init__(self, now_provider: Optional[Callable[[], datetime.datetime]] = None):
        self.current_activity = "idle"
        self.privacy_mode = False
        # Real wall-clock timestamp of the last user interaction.
        self.last_interaction_time: float = time.time()
        self.idle_threshold_seconds: int = DEFAULT_IDLE_THRESHOLD_SECONDS
        # Inject a clock for tests; defaults to real wall-clock.
        self._now_provider = now_provider or (lambda: datetime.datetime.now())

    # ------------------------------------------------------------------
    # Time-of-day mode (real, based on actual hour)
    # ------------------------------------------------------------------

    def _now(self) -> datetime.datetime:
        return self._now_provider()

    def get_time_context(self) -> str:
        """Return the current time-of-day mode.

        Computed from the actual current hour via ``self._now()``.
        Returns one of: 'morning', 'work', 'evening', 'night'.
        """
        hour = self._now().hour
        for mode, start, end in self.MODE_BOUNDARIES:
            if start <= hour < end:
                return mode
        # 00:00–06:00 → night
        return "night"

    # ------------------------------------------------------------------
    # Interaction tracking
    # ------------------------------------------------------------------

    def update_interaction(self) -> None:
        """Record that a user interaction just happened (right now)."""
        self.last_interaction_time = time.time()
        logger.debug("Context: last_interaction_time updated to %s",
                     datetime.datetime.fromtimestamp(self.last_interaction_time))

    def set_last_interaction(self, ts: float) -> None:
        """Manually set the last-interaction timestamp.

        Used by tests / wake-on-contact simulation to backdate the
        last interaction. Not normally called by application code.
        """
        self.last_interaction_time = ts

    def seconds_since_last_interaction(self) -> float:
        """Real elapsed seconds since the last recorded interaction."""
        return time.time() - self.last_interaction_time

    def is_idle(self, threshold_seconds: Optional[int] = None) -> bool:
        """Return True if the idle threshold has been exceeded.

        Args:
            threshold_seconds: Override the instance threshold for this
                check. Defaults to ``self.idle_threshold_seconds``.
        """
        thr = threshold_seconds if threshold_seconds is not None else self.idle_threshold_seconds
        return self.seconds_since_last_interaction() > thr

    # ------------------------------------------------------------------
    # Activity / privacy (unchanged from original)
    # ------------------------------------------------------------------

    def set_activity(self, activity: str) -> None:
        self.current_activity = activity

    def get_current_context(self) -> dict:
        return {
            "time_of_day": self.get_time_context(),
            "activity": self.current_activity,
            "privacy_mode": self.privacy_mode,
            "seconds_since_last_interaction": self.seconds_since_last_interaction(),
            "is_idle": self.is_idle(),
        }
