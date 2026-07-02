#!/usr/bin/env python3
"""FRIDAY AI Assistant — Main entry point.

Usage:
    python main.py [options]

Options:
    --no-voice    Disable voice input/output entirely
    --provider    Override the LLM provider (glm, claude, gemini, local)
    --profile     Override the autonomy profile (GUEST, STANDARD, POWER)
"""

import argparse
import asyncio
import logging
import signal
import sys
from typing import Optional

from config.settings import LOG_FILE, LOG_LEVEL, BRAIN_PROVIDER, AUTONOMY_PROFILE

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(level: Optional[str] = None) -> None:
    """Configure root logger with console + file output."""
    lvl = getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, mode="a"),
        ],
    )

logger = logging.getLogger("friday.main")


# ---------------------------------------------------------------------------
# Graceful subsystem initialization
# ---------------------------------------------------------------------------

def _init_subsystem(name: str, factory, *args, **kwargs):
    """Attempt to initialise a subsystem; return None on failure."""
    try:
        obj = factory(*args, **kwargs)
        logger.info(f"Subsystem '{name}' initialized.")
        return obj
    except Exception as exc:
        logger.warning(f"Subsystem '{name}' failed to initialize: {exc}")
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class FridayOrchestrator:
    def __init__(
        self,
        provider: Optional[str] = None,
        no_voice: bool = False,
        profile: Optional[str] = None,
    ) -> None:
        self.provider = provider or BRAIN_PROVIDER
        self.autonomy_profile = profile or AUTONOMY_PROFILE
        self.no_voice = no_voice
        self._shutdown = False

        # --- Core subsystems (graceful, ordered for dependency injection) ---
        # 1. Memory (no deps)
        self.memory = _init_subsystem(
            "memory", lambda: __import__("core.memory", fromlist=["FridayMemory"]).FridayMemory()
        )
        # 2. Emotions (no deps)
        self.emotions = _init_subsystem(
            "emotions", lambda: __import__("core.emotions", fromlist=["EmotionsEngine"]).EmotionsEngine()
        )
        # 3. Personality (no deps)
        self.personality = _init_subsystem("personality", self._create_personality)

        # 4. Brain (depends on memory, emotions, personality)
        self.brain = _init_subsystem("brain", self._create_brain)

        if not no_voice:
            self.speaker = _init_subsystem("speaker", self._create_speaker)
        else:
            self.speaker = None
            logger.info("Voice disabled (--no-voice).")

        self.ux_engine = _init_subsystem(
            "ux_engine", lambda: __import__("core.ux_engine", fromlist=["CinematicUXEngine"]).CinematicUXEngine(self.speaker)
        )
        self.onboarding = _init_subsystem(
            "onboarding", lambda: __import__("core.onboarding", fromlist=["AutoOnboarding"]).AutoOnboarding()
        )

        # 5. Ledger (for sentinel integration)
        self.ledger = _init_subsystem(
            "ledger", lambda: __import__("core.ledger", fromlist=["get_ledger"]).get_ledger()
        )

        # 6. Systems that depend on brain
        self.evolution = _init_subsystem(
            "evolution", lambda: __import__("core.evolution", fromlist=["EvolutionEngine"]).EvolutionEngine(self.brain) if self.brain else None
        )
        self.sentinel = _init_subsystem(
            "sentinel", lambda: __import__("core.sentinel", fromlist=["EthicalSentinel"]).EthicalSentinel(ledger=self.ledger)
        )
        self.synthesis = _init_subsystem(
            "synthesis", lambda: __import__("core.synthesis", fromlist=["LifeSynthesisEngine"]).LifeSynthesisEngine(self.brain, self.brain.connector if self.brain else None) if self.brain else None
        )

        # 7. Monologue (depends on memory + brain)
        self.monologue = _init_subsystem(
            "monologue", lambda: __import__("core.monologue", fromlist=["InnerMonologue"]).InnerMonologue(self.memory, self.brain) if self.memory else None
        )

        # 8. Continuum (depends on memory)
        self.continuum = _init_subsystem(
            "continuum", lambda: __import__("core.continuum", fromlist=["QuantumContinuum"]).QuantumContinuum(self.memory) if self.memory else None
        )

    # --- Factory helpers (isolated to avoid import crashes) ---

    def _create_brain(self):
        from core.brain import FridayBrain
        return FridayBrain(
            memory=self.memory,
            emotions=self.emotions,
            personality=self.personality,
        )

    @staticmethod
    def _create_personality():
        from core.personality import FridayPersonality
        return FridayPersonality()

    @staticmethod
    def _create_speaker():
        from voice.speaker import FridaySpeaker
        return FridaySpeaker()

    # --- Lifecycle ---

    async def start(self) -> None:
        """Boot up Friday and enter the interactive REPL."""
        logger.info("FRIDAY Orchestrator starting…")
        print("\n[FRIDAY ONLINE]")

        # Scheduler
        try:
            from core.scheduler import get_scheduler
            await get_scheduler().start()
        except Exception as exc:
            logger.warning(f"Scheduler failed to start: {exc}")

        # UX transition
        if self.ux_engine:
            try:
                await self.ux_engine.trigger_transition("Startup")
            except Exception as exc:
                logger.warning(f"UX transition failed: {exc}")

        # Welcome message
        welcome = ""
        if self.onboarding:
            try:
                welcome = self.onboarding.get_welcome_message()
            except Exception as exc:
                logger.warning(f"Onboarding message failed: {exc}")
                welcome = "Hello. FRIDAY online."

        if self.speaker:
            self.speaker.speak(welcome)
        else:
            print(f"Friday: {welcome}")

        # Reflection
        if self.monologue:
            try:
                reflection = self.monologue.reflect_on_interactions()
                if self.speaker:
                    self.speaker.speak(reflection)
                else:
                    print(f"Friday: {reflection}")
            except Exception as exc:
                logger.warning(f"Monologue reflection failed: {exc}")

        # Holistic advice
        if self.synthesis:
            try:
                advice = await self.synthesis.generate_holistic_advice()
                if self.speaker:
                    self.speaker.speak(advice)
                else:
                    print(f"Friday: {advice}")
            except Exception as exc:
                logger.warning(f"Synthesis advice failed: {exc}")

        # Sentinel report
        if self.sentinel:
            try:
                report = self.sentinel.get_sentinel_report()
                print(f"Sentinel: {report}")
            except Exception as exc:
                logger.warning(f"Sentinel report failed: {exc}")

        print("\n[FRIDAY IS WATCHING]  Type 'quit' or Ctrl-C to exit.\n")

        # Interactive REPL
        await self._repl()

    async def _repl(self) -> None:
        """Simple async REPL loop for chatting with Friday from CLI."""
        while not self._shutdown:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("You> ").strip()
                )
            except (EOFError, KeyboardInterrupt):
                print("\n[Shutdown signal received]")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print("Friday: Goodbye.")
                break

            if self.brain is None:
                print("Friday: Brain is not available. Please check your provider configuration.")
                continue

            try:
                response = ""
                async for chunk in self.brain.chat_stream(user_input):
                    response += chunk
                    print(chunk, end="", flush=True)
                print()  # newline after stream

                # Optionally speak the response
                if self.speaker:
                    self.speaker.speak(response)

            except Exception as exc:
                logger.error(f"Brain chat error: {exc}")
                print(f"Friday: I encountered an error — {exc}")

    def shutdown(self) -> None:
        """Flag the REPL to exit."""
        self._shutdown = True
        logger.info("Shutdown requested.")


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FRIDAY AI Assistant")
    parser.add_argument(
        "--no-voice", action="store_true", help="Disable voice input/output"
    )
    parser.add_argument(
        "--provider",
        choices=["glm", "claude", "gemini", "local"],
        help="Override the LLM provider",
    )
    parser.add_argument(
        "--profile",
        choices=["GUEST", "STANDARD", "POWER"],
        help="Override the autonomy profile",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()
    args = parse_args()

    logger.info(
        f"Starting FRIDAY — provider={args.provider or BRAIN_PROVIDER}, "
        f"profile={args.profile or AUTONOMY_PROFILE}, "
        f"voice={'off' if args.no_voice else 'on'}"
    )

    orchestrator = FridayOrchestrator(
        provider=args.provider,
        no_voice=args.no_voice,
        profile=args.profile,
    )

    # Graceful shutdown on SIGINT / SIGTERM
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, orchestrator.shutdown)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        loop.run_until_complete(orchestrator.start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
        logger.info("FRIDAY shut down cleanly.")


if __name__ == "__main__":
    main()
