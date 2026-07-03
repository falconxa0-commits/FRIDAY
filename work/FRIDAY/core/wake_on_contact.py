"""Wake-on-First-Contact Module.

Detects the first wake-word activation after startup OR after a long idle
period (configurable, default 6 hours), and automatically offers the
Morning Briefing skill's content rather than waiting to be asked.

Conditions (any one triggers the briefing):
  - First wake-word activation after process start (``first_contact_only=True``)
  - Wake-word fires AND ``ContextAwareness.is_idle()`` returns True
    (i.e. real elapsed wall-clock time since last interaction exceeds
    the configured idle threshold)

The idle check is a REAL time-based condition — it reads
``ContextAwareness.seconds_since_last_interaction()`` and compares to a
threshold. It is NOT a flag set to make the demo look automatic.
"""

import logging
import time
from typing import Callable, Optional

logger = logging.getLogger("WakeOnContact")


class WakeOnContact:
    """Detect first-contact or post-idle wake-word and trigger morning briefing."""

    DEFAULT_MORNING_START = 6
    DEFAULT_MORNING_END = 11

    def __init__(
        self,
        morning_start: int = DEFAULT_MORNING_START,
        morning_end: int = DEFAULT_MORNING_END,
        on_trigger: Optional[Callable] = None,
        context=None,
        idle_threshold_seconds: Optional[int] = None,
    ):
        """Initialise the wake-on-contact detector.

        Args:
            morning_start: Earliest hour (24h) considered "morning".
            morning_end: Latest hour (24h) considered "morning".
            on_trigger: Callback (sync or async) to fire when the briefing
                should be triggered. Receives no arguments.
            context: A ``ContextAwareness`` instance. If provided, the idle
                threshold check uses ``context.is_idle()`` and updates
                ``context.update_interaction()`` on every wake.
            idle_threshold_seconds: Override the context's idle threshold
                for this detector. If None, uses the context's configured
                threshold.
        """
        self.morning_start = morning_start
        self.morning_end = morning_end
        self.on_trigger = on_trigger
        self.context = context
        self.idle_threshold_seconds = idle_threshold_seconds
        self._first_contact_occurred = False

    # ------------------------------------------------------------------
    # Time helpers
    # ------------------------------------------------------------------

    def is_morning_hours(self) -> bool:
        """Return True if current time is within morning hours."""
        from datetime import datetime
        hour = datetime.now().hour
        return self.morning_start <= hour < self.morning_end

    def _is_idle(self) -> bool:
        """Real time-based idle check via the injected context.

        Returns False if no context was provided.
        """
        if self.context is None:
            return False
        if self.idle_threshold_seconds is not None:
            return self.context.is_idle(self.idle_threshold_seconds)
        return self.context.is_idle()

    # ------------------------------------------------------------------
    # Trigger logic
    # ------------------------------------------------------------------

    def check_and_trigger(self) -> bool:
        """Check whether the morning briefing should fire on this wake-word.

        Returns True when EITHER:
          1. This is the first wake-word since process start, OR
          2. The real idle threshold has been exceeded since the last
             interaction (``ContextAwareness.is_idle()`` returns True).

        Returns True **at most once per triggering event** — the
        ``_first_contact_occurred`` flag prevents the first-contact branch
        from firing twice. The idle branch can fire repeatedly (once per
        idle window).

        Always updates ``context.last_interaction_time`` if a context is
        attached, so subsequent wake-words reset the idle timer.
        """
        try:
            from datetime import datetime
            triggered = False
            trigger_reason = None

            # Branch 1: first contact
            if not self._first_contact_occurred:
                self._first_contact_occurred = True
                if self.is_morning_hours():
                    triggered = True
                    trigger_reason = "first_contact_morning"

            # Branch 2: idle threshold exceeded (real time-based)
            if not triggered and self._is_idle():
                triggered = True
                trigger_reason = "idle_threshold_exceeded"

            # Update interaction timestamp regardless of outcome
            if self.context is not None:
                self.context.update_interaction()

            if triggered:
                idle_secs = (
                    self.context.seconds_since_last_interaction()
                    if self.context else 0
                )
                logger.info(
                    "Wake-on-contact: triggering morning briefing "
                    "(reason=%s, idle_seconds=%.0f, hour=%02d)",
                    trigger_reason, idle_secs, datetime.now().hour,
                )
                if self.on_trigger:
                    try:
                        result = self.on_trigger()
                        if result is not None:
                            logger.info(
                                "Morning briefing callback returned: %s",
                                type(result).__name__,
                            )
                    except Exception as exc:
                        logger.error("Morning briefing callback error: %s", exc)
                return True

            logger.info(
                "Wake-on-contact: no trigger (hour=%02d, idle=%s)",
                datetime.now().hour, self._is_idle(),
            )
            return False
        except Exception as exc:
            logger.error("WakeOnContact.check_and_trigger failed: %s", exc)
            return False

    async def check_and_trigger_async(self) -> bool:
        """Async version of ``check_and_trigger()``.

        If ``on_trigger`` is an async callable, it will be awaited.
        """
        import asyncio
        from datetime import datetime

        try:
            triggered = False
            trigger_reason = None

            if not self._first_contact_occurred:
                self._first_contact_occurred = True
                if self.is_morning_hours():
                    triggered = True
                    trigger_reason = "first_contact_morning"

            if not triggered and self._is_idle():
                triggered = True
                trigger_reason = "idle_threshold_exceeded"

            if self.context is not None:
                self.context.update_interaction()

            if triggered:
                idle_secs = (
                    self.context.seconds_since_last_interaction()
                    if self.context else 0
                )
                logger.info(
                    "Wake-on-contact: triggering morning briefing "
                    "(reason=%s, idle_seconds=%.0f, hour=%02d)",
                    trigger_reason, idle_secs, datetime.now().hour,
                )
                if self.on_trigger:
                    try:
                        result = self.on_trigger()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:
                        logger.error("Morning briefing async callback error: %s", exc)
                return True

            logger.info(
                "Wake-on-contact: no trigger (hour=%02d, idle=%s)",
                datetime.now().hour, self._is_idle(),
            )
            return False
        except Exception as exc:
            logger.error("WakeOnContact.check_and_trigger_async failed: %s", exc)
            return False

    def reset(self) -> None:
        """Reset the first-contact flag (e.g. for testing)."""
        self._first_contact_occurred = False
