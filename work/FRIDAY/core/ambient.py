"""Ambient Engine — continuously watches screen context and proactively surfaces help.

Notices things like:
  - Stuck on same screen for 30+ min → offer to help
  - Meeting in 8 min, still in code editor → remind
  - Error on screen → offer to diagnose
  - Long document open → offer to summarize

Patterns are evaluated against real screen context (provided by an
injectable analyzer). When a pattern matches, the engine calls a
callback to surface the suggestion (CLI print, toast, voice).

Used by `friday --watch` CLI mode.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pattern definitions — declarative, evaluated against real screen context
# ---------------------------------------------------------------------------

NOTICE_PATTERNS: List[dict] = [
    {
        "id": "stuck_on_screen",
        "condition": lambda ctx: (
            ctx.get("same_screen_duration_minutes", 0) > 30
            and not ctx.get("user_active", True)
        ),
        "message": "You've been on {screen_summary} for {same_screen_duration_minutes} min. Want help?",
        "cooldown_minutes": 20,
    },
    {
        "id": "meeting_soon",
        "condition": lambda ctx: (
            0 < ctx.get("minutes_until_next_meeting", 999) < 10
            and ctx.get("current_app", "") != "calendar"
        ),
        "message": "Meeting in {minutes_until_next_meeting} min. Want me to pull up your notes?",
        "cooldown_minutes": 60,
    },
    {
        "id": "error_on_screen",
        "condition": lambda ctx: bool(ctx.get("error_visible_on_screen", False)),
        "message": "I see an error on screen. Want me to look at it?",
        "cooldown_minutes": 5,
    },
    {
        "id": "long_document",
        "condition": lambda ctx: (
            ctx.get("document_word_count", 0) > 5000
            and ctx.get("reading_time_minutes", 0) > 10
        ),
        "message": "Long document open ({document_word_count} words). Want me to summarize?",
        "cooldown_minutes": 30,
    },
]


class AmbientEngine:
    """Continuously analyzes screen context and proactively surfaces help."""

    CHECK_INTERVAL = 60  # seconds between checks
    DEFAULT_SCREEN_ANALYZER = None  # injected in __init__

    def __init__(
        self,
        screen_analyzer: Optional[Callable[[], Dict[str, Any]]] = None,
        surface_callback: Optional[Callable[[dict], Any]] = None,
        patterns: Optional[List[dict]] = None,
    ):
        """
        Args:
            screen_analyzer: A callable that returns a dict of screen context.
                If None, uses a real (but limited) screenshot-based analyzer
                that may not work in headless environments.
            surface_callback: A callable that receives suggestion dicts.
                If None, suggestions are printed to stdout.
            patterns: Override the default NOTICE_PATTERNS list.
        """
        self.screen_analyzer = screen_analyzer or self._default_analyzer
        self.surface_callback = surface_callback or self._default_surface
        self.patterns = patterns or NOTICE_PATTERNS
        self.active = False
        self._last_fired: Dict[str, float] = {}  # pattern_id → timestamp

    # ------------------------------------------------------------------
    # Default analyzers / surfaces
    # ------------------------------------------------------------------

    def _default_analyzer(self) -> Dict[str, Any]:
        """Real but conservative screen analyzer.

        Uses mss + pytesseract if available, returns empty dict otherwise
        (headless environments). Never fabricates screen content.
        """
        ctx: Dict[str, Any] = {}
        try:
            import mss
            import pytesseract
            from PIL import Image
            import io

            with mss.mss() as sct:
                monitor = sct.monitors[1] if sct.monitors else None
                if monitor:
                    shot = sct.grab(monitor)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    text = pytesseract.image_to_string(img)
                    ctx["screen_text"] = text[:2000]
                    ctx["screen_summary"] = text[:200].replace("\n", " ").strip()
                    ctx["document_word_count"] = len(text.split())
                    # Crude error detection — look for common error patterns
                    ctx["error_visible_on_screen"] = any(
                        err in text.lower()
                        for err in ["error", "exception", "traceback", "failed"]
                    )
        except ImportError:
            ctx["screen_text"] = ""
            ctx["screen_summary"] = "(screen capture unavailable — install mss + pytesseract)"
        except Exception as exc:
            logger.debug("Screen analyzer failed: %s", exc)
            ctx["screen_summary"] = f"(analyzer error: {exc})"
        return ctx

    def _default_surface(self, suggestion: dict) -> None:
        """Default surface — print to stdout."""
        print(f"\n[Friday ambient] {suggestion['message']}\n")

    # ------------------------------------------------------------------
    # Pattern evaluation
    # ------------------------------------------------------------------

    def evaluate_patterns(self, screen_context: Dict[str, Any]) -> List[dict]:
        """Evaluate all patterns against the current screen context.

        Returns a list of suggestions that fired (with cooldown respected).
        """
        suggestions: List[dict] = []
        now = time.time()
        for pattern in self.patterns:
            pid = pattern["id"]
            try:
                matches = pattern["condition"](screen_context)
            except Exception as exc:
                logger.debug("Pattern %s raised: %s", pid, exc)
                continue
            if not matches:
                continue
            # Cooldown check
            last = self._last_fired.get(pid, 0)
            cooldown_seconds = pattern.get("cooldown_minutes", 30) * 60
            if now - last < cooldown_seconds:
                continue
            # Fire
            try:
                msg = pattern["message"].format(**screen_context)
            except KeyError:
                msg = pattern["message"]
            suggestions.append({
                "pattern_id": pid,
                "message": msg,
                "fired_at": datetime.datetime.now().isoformat(),
                "context": screen_context,
            })
            self._last_fired[pid] = now
        return suggestions

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run_loop(self) -> None:
        """Main ambient loop — runs continuously when --watch mode is active."""
        self.active = True
        logger.info("AmbientEngine loop started (interval=%ss)", self.CHECK_INTERVAL)
        try:
            while self.active:
                screen_context = await asyncio.to_thread(self.screen_analyzer)
                suggestions = self.evaluate_patterns(screen_context)
                for suggestion in suggestions:
                    try:
                        result = self.surface_callback(suggestion)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:
                        logger.error("Surface callback failed: %s", exc)
                await asyncio.sleep(self.CHECK_INTERVAL)
        except asyncio.CancelledError:
            logger.info("AmbientEngine loop cancelled")
        finally:
            self.active = False

    async def run_loop_cli(self, max_iterations: Optional[int] = None) -> None:
        """Run the loop in CLI mode — accepts Ctrl+C, optional iteration cap.

        Args:
            max_iterations: If set, stop after N iterations (useful for testing).
                If None, runs forever until Ctrl+C.
        """
        self.active = True
        print(f"Friday ambient engine active (check every {self.CHECK_INTERVAL}s)")
        print("Press Ctrl+C to stop.\n")
        iterations = 0
        try:
            while self.active:
                if max_iterations is not None and iterations >= max_iterations:
                    break
                iterations += 1
                screen_context = await asyncio.to_thread(self.screen_analyzer)
                suggestions = self.evaluate_patterns(screen_context)
                for s in suggestions:
                    self._default_surface(s)
                # Only sleep if we're going to loop again (avoids
                # hanging for CHECK_INTERVAL on the last iteration)
                if max_iterations is None or iterations < max_iterations:
                    await asyncio.sleep(self.CHECK_INTERVAL)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nAmbient engine stopped.")
        finally:
            self.active = False

    def stop(self) -> None:
        self.active = False


# Active intervention patterns — when accepted, actually execute the action
INTERVENTION_PATTERNS = [
    {
        "id": "stuck_on_bug",
        "condition": lambda ctx: (
            ctx.get("same_screen_duration_minutes", 0) > 25 and
            ctx.get("app", "") in ["vscode", "terminal", "code"] and
            not ctx.get("recent_activity", False)
        ),
        "message": "You've been on this code for {duration} minutes. Want me to look at what's on screen?",
        "action": "offer_screen_analysis",
        "cooldown_minutes": 30,
    },
    {
        "id": "meeting_prep",
        "condition": lambda ctx: ctx.get("minutes_until_next_meeting", 999) <= 10,
        "message": "You have a meeting in {minutes} minutes. Want me to pull up your notes?",
        "action": "fetch_meeting_context",
        "cooldown_minutes": 60,
    },
    {
        "id": "error_detected",
        "condition": lambda ctx: ctx.get("error_visible_on_screen", False),
        "message": "I see an error on screen. Want me to diagnose it?",
        "action": "analyze_error",
        "cooldown_minutes": 5,
    },
    {
        "id": "long_document",
        "condition": lambda ctx: (
            ctx.get("app", "") in ["chrome", "firefox", "safari"] and
            ctx.get("scroll_depth_percent", 100) < 20 and
            ctx.get("content_length_estimate", 0) > 5000
        ),
        "message": "That looks like a long read. Want me to summarize it?",
        "action": "summarize_page",
        "cooldown_minutes": 20,
    },
]


async def execute_intervention_action(action_id: str, context: dict) -> dict:
    """Execute the real action when a user accepts an ambient suggestion."""
    if action_id == "offer_screen_analysis":
        try:
            from vision.screen_analyzer import ScreenAnalyzer
            analyzer = ScreenAnalyzer()
            from vision.screen_reader import ScreenReader
            reader = ScreenReader()
            shot_path = reader.capture_screen()
            analysis = analyzer.analyze_screen(shot_path)
            return {"status": "success", "analysis": str(analysis)[:500]}
        except Exception as e:
            return {"status": "error", "message": f"Screen analysis failed: {e}"}

    elif action_id == "fetch_meeting_context":
        try:
            from core.memory import FridayMemory
            mem = FridayMemory()
            memories = mem.retrieve_relevant_memories("meeting notes agenda")
            return {"status": "success", "memories": memories[:5]}
        except Exception as e:
            return {"status": "error", "message": f"Context fetch failed: {e}"}

    elif action_id == "analyze_error":
        try:
            from vision.screen_reader import ScreenReader
            from vision.ocr import FridayOCR
            reader = ScreenReader()
            shot_path = reader.capture_screen()
            ocr = FridayOCR()
            error_text = ocr.extract_text(shot_path)
            return {"status": "success", "error_text": error_text[:500]}
        except Exception as e:
            return {"status": "error", "message": f"Error analysis failed: {e}"}

    elif action_id == "summarize_page":
        try:
            from control.browser_control import BrowserControl
            bc = BrowserControl()
            await bc.start(headless=True)
            # Get current page content
            content = await bc.page.evaluate("document.body.innerText")
            await bc.close()
            return {"status": "success", "summary": content[:500]}
        except Exception as e:
            return {"status": "error", "message": f"Page summary failed: {e}"}

    return {"status": "not_implemented", "message": f"Unknown action: {action_id}"}
