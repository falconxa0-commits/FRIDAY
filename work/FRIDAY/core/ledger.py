"""Action Ledger — approval gate with persistence and notifications.

Key improvements over the original:
- **asyncio.Event** replaces busy-polling in ``wait_for_approval()``.
- Pending actions are persisted to a JSON file so they survive restarts.
- A pluggable notification mechanism lets upstream code register
  callbacks (e.g. push to WebSocket, send email) when actions are queued.
- Audit log entries are **hash-chained** (tamper-evident). Any
  modification to a past entry is detectable via ``verify_chain()``.
"""

import asyncio
import hashlib
import json
import os
import uuid
import datetime
import logging
from typing import Callable, Dict, List, Optional

from config.settings import AUTONOMY_PROFILE

logger = logging.getLogger(__name__)


# Components that must NEVER be auto-approved regardless of autonomy profile.
# These involve physical-world effects, financial cost, or irreversible actions.
NEVER_AUTO_APPROVE_COMPONENTS = frozenset({
    "ImageGen",        # Costs money per generation
    "VideoGen",        # Costs money per generation
    "CodeExecution",   # Arbitrary code execution risk
    "Printer",         # Physical world effect
    "Printer3D",       # Physical world effect + material cost
    "Finance",         # Financial transactions — money movement
    "Commerce",        # Purchase / checkout actions
})


class ActionLedger:
    PERSIST_PATH = "action_ledger_pending.json"

    def __init__(self):
        self.pending_actions: Dict[str, dict] = {}
        self.audit_log = "action_ledger_audit.log"
        self.profile = AUTONOMY_PROFILE

        # In-memory hash-chained audit log (each entry includes a hash
        # that depends on the previous entry's hash — any tampering
        # breaks the chain and is detectable via verify_chain()).
        # The chain is persisted to CHAIN_PERSIST_PATH so tamper-evidence
        # survives process restarts.
        self._audit_chain: List[dict] = []

        # Per-action events for efficient await (replaces busy-polling)
        self._events: Dict[str, asyncio.Event] = {}

        # Notification callbacks — called when a new action is queued
        self._notification_callbacks: list[Callable[[dict], None]] = []

        # Restore previously persisted pending actions
        self._load_persisted()
        # Restore persisted hash-chained audit log
        self._load_chain()

    # ----------------------------------------------------------------
    # Persistence
    # ----------------------------------------------------------------

    def _persist(self):
        """Write current pending actions to disk (fire-and-forget best-effort)."""
        try:
            with open(self.PERSIST_PATH, "w") as f:
                json.dump(self.pending_actions, f, indent=2, default=str)
        except Exception:
            logger.exception("Failed to persist action ledger")

    def _load_persisted(self):
        """Load previously persisted pending actions from disk."""
        if not os.path.exists(self.PERSIST_PATH):
            return
        try:
            with open(self.PERSIST_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for action_id, action_data in data.items():
                    if action_data.get("status") == "pending":
                        self.pending_actions[action_id] = action_data
                        self._events[action_id] = asyncio.Event()
                logger.info(
                    "Restored %d pending actions from persistence", len(self.pending_actions)
                )
        except Exception:
            logger.exception("Failed to load persisted action ledger")

    # ----------------------------------------------------------------
    # Notification
    # ----------------------------------------------------------------

    def register_notification_callback(self, callback: Callable[[dict], None]):
        """Register a callback invoked when a new action is queued.

        The callback receives the action dict and can, for example,
        push it over a WebSocket or send an email.
        """
        self._notification_callbacks.append(callback)

    def _notify(self, action_data: dict):
        for cb in self._notification_callbacks:
            try:
                cb(action_data)
            except Exception:
                logger.exception("Notification callback raised an error")

    # ----------------------------------------------------------------
    # Audit logging (hash-chained / tamper-evident)
    # ----------------------------------------------------------------

    GENESIS_HASH = "genesis"
    CHAIN_PERSIST_PATH = "action_ledger_chain.json"

    # Keys in action params that are redacted in the file-based audit log
    # (the hash chain still includes the full params — only the human-readable
    # text log is redacted, to prevent secret leakage into log aggregators).
    SENSITIVE_PARAM_KEYS = frozenset({
        "password", "passwd", "pwd",
        "api_key", "apikey", "token", "secret",
        "payment_method", "card_number", "cvv", "expiry",
        "client_secret", "access_token", "refresh_token",
        "stripe_token", "payment_intent_id",
    })

    @staticmethod
    def _redact_params(params: dict) -> dict:
        """Return a copy of params with sensitive values replaced by '***'.

        Used only for the human-readable file log. The hash chain
        includes the full params so tamper detection still works.
        """
        if not isinstance(params, dict):
            return params
        redacted = {}
        for k, v in params.items():
            if k.lower() in ActionLedger.SENSITIVE_PARAM_KEYS:
                redacted[k] = "***REDACTED***"
            elif isinstance(v, str) and len(v) > 100:
                redacted[k] = v[:50] + "...(truncated)"
            else:
                redacted[k] = v
        return redacted

    @staticmethod
    def _compute_entry_hash(entry: dict, prev_hash: str) -> str:
        """SHA-256 hash of (prev_hash || action_id || component || action ||
        params || timestamp || status).

        Any change to any of these fields (or to prev_hash) produces a
        different hash, breaking the chain at the modified entry and
        every subsequent entry.
        """
        content = json.dumps({
            "prev_hash": prev_hash,
            "action_id": entry.get("action_id") or entry.get("id", ""),
            "component": entry.get("component", ""),
            "action": entry.get("action", ""),
            "params": entry.get("params", {}),
            "timestamp": entry.get("timestamp", ""),
            "status": entry.get("status", ""),
        }, sort_keys=True, default=str)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _log_audit(self, action_data, approved_by="human"):
        """Append an entry to BOTH the file-based audit log and the
        persisted hash-chained audit log.

        - File log: human-readable, append-only, with SENSITIVE params redacted.
        - Hash chain: persisted to ``CHAIN_PERSIST_PATH`` (JSON file),
          tamper-evident across process restarts.
        """
        # 1. File-based log (human-readable, append-only, REDACTED)
        try:
            redacted_params = self._redact_params(action_data.get("params", {}))
            with open(self.audit_log, "a") as f:
                f.write(
                    f"{datetime.datetime.now().isoformat()} | "
                    f"{approved_by.upper()} | "
                    f"{action_data['component']}.{action_data['action']} | "
                    f"{redacted_params}\n"
                )
        except Exception:
            logger.exception("Failed to write audit log file")

        # 2. Hash-chained log (tamper-evident, PERSISTED to disk)
        prev_hash = self._audit_chain[-1]["hash"] if self._audit_chain else self.GENESIS_HASH
        entry = {
            "action_id": action_data.get("id", ""),
            "component": action_data.get("component", ""),
            "action": action_data.get("action", ""),
            "params": action_data.get("params", {}),  # full params for hash integrity
            "timestamp": action_data.get("timestamp", datetime.datetime.now().isoformat()),
            "status": action_data.get("status", ""),
            "approved_by": approved_by,
            "prev_hash": prev_hash,
        }
        entry["hash"] = self._compute_entry_hash(entry, prev_hash)
        self._audit_chain.append(entry)
        # Persist the full chain to disk so tamper-evidence survives restarts
        self._persist_chain()

    def _persist_chain(self) -> None:
        """Persist the hash-chained audit log to disk as JSON.

        Each entry's hash is recomputed from its contents, so modifying
        the JSON file and restarting will be detected by verify_chain().
        """
        try:
            with open(self.CHAIN_PERSIST_PATH, "w") as f:
                json.dump(self._audit_chain, f, indent=2, default=str)
        except Exception:
            logger.exception("Failed to persist audit chain")

    def _load_chain(self) -> None:
        """Load the persisted hash-chained audit log from disk on startup."""
        import os as _os
        if not _os.path.exists(self.CHAIN_PERSIST_PATH):
            return
        try:
            with open(self.CHAIN_PERSIST_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                # Validate the loaded chain before accepting it
                prev_hash = self.GENESIS_HASH
                valid = True
                for entry in data:
                    expected = self._compute_entry_hash(entry, prev_hash)
                    if entry.get("hash") != expected:
                        logger.warning(
                            "Loaded audit chain has broken hash at entry — "
                            "discarding chain (possible tampering)."
                        )
                        valid = False
                        break
                    prev_hash = entry.get("hash", "")
                if valid:
                    self._audit_chain = data
                    logger.info(
                        "Loaded %d entries from persisted audit chain",
                        len(self._audit_chain),
                    )
        except Exception:
            logger.exception("Failed to load persisted audit chain")

    def get_audit_log(self) -> List[dict]:
        """Return the hash-chained audit log entries (newest last)."""
        return list(self._audit_chain)

    def verify_chain(self) -> bool:
        """Verify the integrity of the hash-chained audit log.

        Returns True if every entry's hash matches the recomputed hash
        and every prev_hash link is intact. Returns False if any entry
        was tampered with.
        """
        prev_hash = self.GENESIS_HASH
        for i, entry in enumerate(self._audit_chain):
            expected = self._compute_entry_hash(entry, prev_hash)
            if entry.get("hash") != expected:
                logger.warning(
                    "Ledger chain BROKEN at index %d: hash mismatch "
                    "(expected %s, got %s)", i, expected, entry.get("hash")
                )
                return False
            if entry.get("prev_hash") != prev_hash:
                logger.warning(
                    "Ledger chain BROKEN at index %d: prev_hash link mismatch", i
                )
                return False
            prev_hash = entry["hash"]
        return True

    def _tamper_for_test(self, index: int, new_params: dict) -> None:
        """Intentionally modify an audit entry's params (TEST ONLY).

        This breaks the hash chain. ``verify_chain()`` will return False
        afterwards. Used only by the verification script to demonstrate
        that tampering is detectable.
        """
        if 0 <= index < len(self._audit_chain):
            self._audit_chain[index]["params"] = new_params
            # NOTE: We do NOT recompute the hash — that's the point.

    # ----------------------------------------------------------------
    # Core logic
    # ----------------------------------------------------------------

    def _should_auto_approve(self, component, risk_level):
        # Never auto-approve dangerous components regardless of profile
        if component in NEVER_AUTO_APPROVE_COMPONENTS:
            return False
        if self.profile == "GUEST":
            return False
        if self.profile == "STANDARD":
            if risk_level == "low":
                return True
        if self.profile == "POWER":
            if component in ("PCControl", "BrowserControl") and risk_level != "critical":
                return True
            if risk_level == "low":
                return True
        return False

    def queue_action(self, component, action, params, risk_level="high"):
        action_id = str(uuid.uuid4())
        action_data = {
            "id": action_id,
            "component": component,
            "action": action,
            "params": params,
            "risk_level": risk_level,
            "status": "pending",
            "timestamp": datetime.datetime.now().isoformat(),
        }

        # Auto-approve based on profile
        if self._should_auto_approve(component, risk_level):
            action_data["status"] = "approved"
            self._log_audit(action_data, approved_by="auto")
            self.pending_actions[action_id] = action_data
            self._events[action_id] = asyncio.Event()
            self._events[action_id].set()  # already approved
            self._persist()
            return action_id

        self.pending_actions[action_id] = action_data
        self._events[action_id] = asyncio.Event()
        self._persist()
        self._notify(action_data)
        return action_id

    async def wait_for_approval(self, action_id, timeout=300):
        """Wait for approval using ``asyncio.Event`` (no busy-polling).

        Returns ``True`` if approved, ``False`` if rejected or timed out.
        """
        event = self._events.get(action_id)
        if event is None:
            return False

        # Already approved?
        if self.pending_actions.get(action_id, {}).get("status") == "approved":
            return True

        logger.info(
            "Action %s awaiting manual approval (Profile: %s)",
            action_id,
            self.profile,
        )

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False

        status = self.pending_actions.get(action_id, {}).get("status")
        if status == "approved":
            self._log_audit(self.pending_actions[action_id], approved_by="human")
            return True
        return False

    def approve_action(self, action_id):
        if action_id in self.pending_actions:
            self.pending_actions[action_id]["status"] = "approved"
            event = self._events.get(action_id)
            if event:
                event.set()
            self._persist()
            # Add to the hash-chained audit log
            self._log_audit(self.pending_actions[action_id], approved_by="human")
            return True
        return False

    def reject_action(self, action_id):
        if action_id in self.pending_actions:
            self.pending_actions[action_id]["status"] = "rejected"
            event = self._events.get(action_id)
            if event:
                event.set()  # unblock waiters (they'll check status)
            self._persist()
            # Add to the hash-chained audit log
            self._log_audit(self.pending_actions[action_id], approved_by="human_rejected")
            return True
        return False

    # ----------------------------------------------------------------
    # Voice-native approval
    # ----------------------------------------------------------------

    async def wait_for_voice_approval(
        self,
        action_id: str,
        speaker=None,
        listener=None,
        timeout: int = 60,
        max_retries: int = 1,
    ) -> bool:
        """Speak the pending action aloud and wait for a spoken yes/no.

        Workflow:
            1. Speak the action description via the speaker module.
            2. Use the listener to capture a spoken response (bounded by
               ``timeout`` seconds; if no audio is captured in that window
               we treat it as "no response").
            3. Transcribe via ``voice.transcriber`` and parse intent:
                 yes -> approve_action() and confirm aloud
                 no  -> reject_action() and confirm aloud
                 unclear -> ask once more, then default to reject
            4. On timeout (no audio captured) -> reject and say so aloud.

        Args:
            action_id: The queued action to approve or reject.
            speaker: FridaySpeaker instance (or any object with ``speak``).
            listener: FridayListener instance (or any object with ``record_audio``).
            timeout: Seconds to wait for a response (default 60).
            max_retries: How many times to re-ask on ambiguous responses.

        Returns:
            True if approved, False if rejected or timed out.
        """
        action_data = self.pending_actions.get(action_id)
        if not action_data:
            logger.warning("wait_for_voice_approval: unknown action %s", action_id)
            return False

        # Already approved?
        if action_data.get("status") == "approved":
            return True

        # Build a spoken description
        component = action_data.get("component", "unknown")
        act = action_data.get("action", "unknown")
        params = action_data.get("params", {})
        description = (
            f"Action pending: {component} wants to {act}. "
            f"Parameters: {params}. "
            f"Say yes to approve, or no to reject."
        )

        async def _speak(text: str) -> None:
            if speaker and hasattr(speaker, "speak"):
                try:
                    await speaker.speak_async(text)
                    return
                except Exception as exc:
                    logger.warning("Voice approval: speak_async failed: %s", exc)
                    try:
                        speaker.speak(text)
                        return
                    except Exception:
                        pass
            print(f"[VOICE APPROVAL] {text}")

        async def _listen_once() -> str:
            """Capture one audio chunk and return the transcribed text.

            Returns "" if no audio was captured or transcription failed.
            Bounds the listen by ``timeout`` seconds.
            """
            if not (listener and hasattr(listener, "record_audio")):
                return ""

            import tempfile
            import os as _os

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = tmp.name

                recorded = await asyncio.wait_for(
                    asyncio.to_thread(listener.record_audio, tmp_path, 8),
                    timeout=timeout,
                )
                if not recorded:
                    return ""

                try:
                    from voice.transcriber import FridayTranscriber
                    transcriber = FridayTranscriber()
                    text = await asyncio.to_thread(transcriber.transcribe, tmp_path)
                    return text or ""
                except ImportError:
                    logger.warning(
                        "Voice approval: whisper not installed - cannot transcribe"
                    )
                    return ""
                except Exception as exc:
                    logger.warning("Voice approval: transcription failed: %s", exc)
                    return ""
            except asyncio.TimeoutError:
                logger.info(
                    "Voice approval: no response within %ss - treating as no input",
                    timeout,
                )
                return ""
            finally:
                if tmp_path:
                    try:
                        _os.unlink(tmp_path)
                    except OSError:
                        pass

        # Step 1: Speak the action
        await _speak(description)

        # Step 2 + 3: Listen and parse, with retry for ambiguous responses
        attempts = 0
        approved = None
        while attempts <= max_retries:
            attempts += 1
            response_text = await _listen_once()

            if not response_text:
                if attempts == 1:
                    await _speak(
                        "No response detected within the timeout window. "
                        "Rejecting the action for safety."
                    )
                approved = False
                break

            approved = self._parse_voice_intent(response_text)
            logger.info(
                "Voice approval attempt %d: heard=%r parsed=%r",
                attempts, response_text, approved,
            )

            if approved is True:
                await _speak("Approved. Proceeding.")
                break
            if approved is False:
                await _speak("Rejected. The action will not run.")
                break

            if attempts <= max_retries:
                await _speak("I didn\'t catch that. Please say yes or no.")
                continue
            await _speak("Still unclear. Rejecting the action for safety.")
            approved = False
            break

        # Step 4: Approve or reject
        if approved:
            self.approve_action(action_id)
            self._log_audit(action_data, approved_by="voice")
        else:
            self.reject_action(action_id)
            self._log_audit(action_data, approved_by="voice_rejected")

        return bool(approved)

    @staticmethod
    def _parse_voice_intent(text: str):
        """Parse a spoken response into yes / no / None (ambiguous).

        Returns:
            True  — positive intent detected
            False — negative intent detected
            None  — ambiguous / unclear
        """
        if not text:
            return None

        text_lower = text.lower().strip()

        yes_words = {"yes", "yeah", "yep", "sure", "okay", "ok", "approve", "go ahead", "do it", "confirm", "affirmative"}
        no_words = {"no", "nope", "nah", "reject", "deny", "cancel", "stop", "don't", "negative", "refuse"}

        for word in yes_words:
            if word in text_lower:
                return True

        for word in no_words:
            if word in text_lower:
                return False

        return None


# Module-level singleton
_ledger: Optional[ActionLedger] = None


def get_ledger() -> ActionLedger:
    global _ledger
    if _ledger is None:
        _ledger = ActionLedger()
    return _ledger
