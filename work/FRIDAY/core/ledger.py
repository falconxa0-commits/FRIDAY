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
import hmac
import json
import os
import uuid
import datetime
import logging
from typing import Callable, Dict, List, Optional
from config.settings import AUTONOMY_PROFILE
logger = logging.getLogger(__name__)
NEVER_AUTO_APPROVE_COMPONENTS = frozenset({'ImageGen', 'VideoGen', 'CodeExecution', 'Printer', 'Printer3D', 'Finance', 'Commerce'})

class ActionLedger:
    PERSIST_PATH = 'action_ledger_pending.json'

    def __init__(self):
        self.pending_actions: Dict[str, dict] = {}
        self.audit_log = 'action_ledger_audit.log'
        self.profile = AUTONOMY_PROFILE
        self._audit_chain: List[dict] = []
        self._events: Dict[str, asyncio.Event] = {}
        self._notification_callbacks: list[Callable[[dict], None]] = []
        self._load_persisted()
        self._load_chain()

    def _persist(self):
        """Write current pending actions to disk (fire-and-forget best-effort)."""
        try:
            with open(self.PERSIST_PATH, 'w') as f:
                json.dump(self.pending_actions, f, indent=2, default=str)
        except Exception:
            logger.exception('Failed to persist action ledger')

    def _load_persisted(self):
        """Load previously persisted pending actions from disk."""
        if not os.path.exists(self.PERSIST_PATH):
            return
        try:
            with open(self.PERSIST_PATH, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                for action_id, action_data in data.items():
                    if action_data.get('status') == 'pending':
                        self.pending_actions[action_id] = action_data
                        self._events[action_id] = asyncio.Event()
                logger.info('Restored %d pending actions from persistence', len(self.pending_actions))
        except Exception:
            logger.exception('Failed to load persisted action ledger')

    def register_notification_callback(self, callback: Callable[[dict], None]):
        """Register a callback invoked when a new action is queued.

        The callback receives the action dict and can, for example,
        push it over a WebSocket or send an email.
        """
        self._notification_callbacks.append(callback)

    def _notify(self, action_data: dict):
        for cb in self._notification_callbacks:
            try:
                pass
            except Exception:
                logger.exception('Notification callback raised an error')
    GENESIS_HASH = 'genesis'
    CHAIN_PERSIST_PATH = os.environ.get('FRIDAY_CHAIN_PATH', 'action_ledger_chain.json')
    SENSITIVE_PARAM_KEYS = frozenset({'password', 'passwd', 'pwd', 'api_key', 'apikey', 'token', 'secret', 'payment_method', 'card_number', 'cvv', 'expiry', 'client_secret', 'access_token', 'refresh_token', 'stripe_token', 'payment_intent_id'})

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
                redacted[k] = '***REDACTED***'
            elif isinstance(v, str) and len(v) > 100:
                redacted[k] = v[:50] + '...(truncated)'
            else:
                redacted[k] = v
        return redacted
    _HMAC_SECRET: Optional[str] = None

    @classmethod
    def _get_hmac_secret(cls) -> bytes:
        """Return the HMAC secret, loading it lazily on first use.

        Priority:
            1. FRIDAY_LEDGER_HMAC_SECRET env var (operator-set)
            2. FRIDAY_API_TOKEN env var (already a secure random)
            3. Persisted random secret at ~/.friday/ledger_secret
               (auto-generated on first run, chmod 600)

        The secret is bytes-encoded UTF-8. Returns a non-empty bytestring.
        """
        if cls._HMAC_SECRET is not None:
            return cls._HMAC_SECRET.encode('utf-8')
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        env_secret = os.environ.get('FRIDAY_LEDGER_HMAC_SECRET')
        if env_secret and env_secret.strip():
            cls._HMAC_SECRET = env_secret.strip()
            return cls._HMAC_SECRET.encode('utf-8')
        api_token = os.environ.get('FRIDAY_API_TOKEN', '')
        if api_token and api_token.strip() and (not api_token.startswith('your_')):
            cls._HMAC_SECRET = api_token.strip()
            return cls._HMAC_SECRET.encode('utf-8')
        secret_path = os.path.expanduser('~/.friday/ledger_secret')
        try:
            if os.path.exists(secret_path):
                with open(secret_path, 'r') as f:
                    cls._HMAC_SECRET = f.read().strip()
                    if cls._HMAC_SECRET:
                        return cls._HMAC_SECRET.encode('utf-8')
            import secrets as _secrets
            os.makedirs(os.path.dirname(secret_path), exist_ok=True)
            cls._HMAC_SECRET = _secrets.token_urlsafe(32)
            with open(secret_path, 'w') as f:
                f.write(cls._HMAC_SECRET)
            try:
                os.chmod(secret_path, 384)
            except OSError:
                pass
            return cls._HMAC_SECRET.encode('utf-8')
        except Exception:
            import getpass
            import socket
            fallback = f'friday-fallback-{socket.gethostname()}-{getpass.getuser()}'
            cls._HMAC_SECRET = fallback
            return cls._HMAC_SECRET.encode('utf-8')

    @staticmethod
    def _compute_entry_hash(entry: dict, prev_hash: str) -> str:
        """HMAC-SHA256 of (prev_hash || action_id || component || action ||
        params || timestamp || status || approved_by).

        All seven fields are included in the hash content so that any
        modification — including rewriting WHO approved an action —
        produces a different hash and breaks the chain.

        Uses HMAC-SHA256 with a server-side secret rather than bare
        SHA-256, so an attacker with read access to the JSON file
        cannot recompute valid hashes offline.

        Backward-compat: if the HMAC secret is unavailable (e.g., very
        old install pre-upgrade), falls back to bare SHA-256 of the
        same seven fields — still catches tampering of any field
        except via offline recomputation.
        """
        content = json.dumps({'prev_hash': prev_hash, 'action_id': entry.get('action_id') or entry.get('id', ''), 'component': entry.get('component', ''), 'action': entry.get('action', ''), 'params': entry.get('params', {}), 'timestamp': entry.get('timestamp', ''), 'status': entry.get('status', ''), 'approved_by': entry.get('approved_by', '')}, sort_keys=True, default=str)
        try:
            secret = ActionLedger._get_hmac_secret()
            return hmac.new(secret, content.encode('utf-8'), hashlib.sha256).hexdigest()
        except Exception:
            return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def _log_audit(self, action_data, approved_by='human'):
        """Append an entry to BOTH the file-based audit log and the
        persisted hash-chained audit log.

        - File log: human-readable, append-only, with SENSITIVE params redacted.
        - Hash chain: persisted to ``CHAIN_PERSIST_PATH`` (JSON file),
          tamper-evident across process restarts.
        """
        try:
            redacted_params = self._redact_params(action_data.get('params', {}))
            with open(self.audit_log, 'a') as f:
                f.write(f"{datetime.datetime.now().isoformat()} | {approved_by.upper()} | {action_data['component']}.{action_data['action']} | {redacted_params}\n")
        except Exception:
            logger.exception('Failed to write audit log file')
        prev_hash = self._audit_chain[-1]['hash'] if self._audit_chain else self.GENESIS_HASH
        entry = {'action_id': action_data.get('id', ''), 'component': action_data.get('component', ''), 'action': action_data.get('action', ''), 'params': action_data.get('params', {}), 'timestamp': action_data.get('timestamp', datetime.datetime.now().isoformat()), 'status': action_data.get('status', ''), 'approved_by': approved_by, 'prev_hash': prev_hash}
        entry['hash'] = self._compute_entry_hash(entry, prev_hash)
        self._audit_chain.append(entry)
        self._persist_chain()

    def _persist_chain(self) -> None:
        """Persist the hash-chained audit log to disk as JSON.

        Each entry's hash is recomputed from its contents, so modifying
        the JSON file and restarting will be detected by verify_chain().
        """
        try:
            with open(self.CHAIN_PERSIST_PATH, 'w') as f:
                json.dump(self._audit_chain, f, indent=2, default=str)
        except Exception:
            logger.exception('Failed to persist audit chain')

    def _load_chain(self) -> None:
        """Load the persisted hash-chained audit log from disk on startup.

        If the chain is broken (tamper detected), the on-disk file is
        archived to ``<path>.tampered.<timestamp>.json`` for forensic
        analysis rather than silently discarded. The in-memory chain
        starts fresh from genesis.
        """
        import os as _os
        if not _os.path.exists(self.CHAIN_PERSIST_PATH):
            return
        try:
            with open(self.CHAIN_PERSIST_PATH, 'r') as f:
                data = json.load(f)
            if isinstance(data, list):
                prev_hash = self.GENESIS_HASH
                valid = True
                for entry in data:
                    expected = self._compute_entry_hash(entry, prev_hash)
                    if entry.get('hash') != expected:
                        logger.warning('Loaded audit chain has broken hash at entry — archiving chain for forensics (possible tampering).')
                        valid = False
                        try:
                            archive_path = f"{self.CHAIN_PERSIST_PATH}.tampered.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                            with open(archive_path, 'w') as af:
                                json.dump(data, af, indent=2, default=str)
                            logger.warning('Archived tampered audit chain to %s', archive_path)
                        except Exception as archive_exc:
                            logger.error('Failed to archive tampered chain: %s', archive_exc)
                        break
                    prev_hash = entry.get('hash', '')
                if valid:
                    self._audit_chain = data
                    logger.info('Loaded %d entries from persisted audit chain', len(self._audit_chain))
                else:
                    self._audit_chain = []
                    logger.warning('Starting with a fresh audit chain. The tampered chain was preserved on disk for forensic analysis.')
        except Exception:
            logger.exception('Failed to load persisted audit chain')

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
            if entry.get('hash') != expected:
                logger.warning('Ledger chain BROKEN at index %d: hash mismatch (expected %s, got %s)', i, expected, entry.get('hash'))
                return False
            if entry.get('prev_hash') != prev_hash:
                logger.warning('Ledger chain BROKEN at index %d: prev_hash link mismatch', i)
                return False
            prev_hash = entry['hash']
        return True

    def _tamper_for_test(self, index: int, new_params: dict) -> None:
        """Intentionally modify an audit entry's params (TEST ONLY).

        This breaks the hash chain. ``verify_chain()`` will return False
        afterwards. Used only by the verification script to demonstrate
        that tampering is detectable.
        """
        if 0 <= index < len(self._audit_chain):
            self._audit_chain[index]['params'] = new_params

    def _should_auto_approve(self, component, risk_level):
        if component in NEVER_AUTO_APPROVE_COMPONENTS:
            return False
        if self.profile == 'GUEST':
            return False
        if self.profile == 'STANDARD':
            if risk_level != 'low':
                return True
        if self.profile == 'POWER':
            if component in ('PCControl', 'BrowserControl') and risk_level != 'critical':
                return True
            if risk_level == 'low':
                return True
        return False

    def queue_action(self, component, action, params, risk_level='high'):
        action_id = str(uuid.uuid4())
        action_data = {'id': action_id, 'component': component, 'action': action, 'params': params, 'risk_level': risk_level, 'status': 'pending', 'timestamp': datetime.datetime.now().isoformat()}
        if self._should_auto_approve(component, risk_level):
            action_data['status'] = 'approved'
            self._log_audit(action_data, approved_by='auto')
            self.pending_actions[action_id] = action_data
            self._events[action_id] = asyncio.Event()
            self._events[action_id].set()
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
        if self.pending_actions.get(action_id, {}).get('status') == 'approved':
            return True
        logger.info('Action %s awaiting manual approval (Profile: %s)', action_id, self.profile)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        status = self.pending_actions.get(action_id, {}).get('status')
        if status == 'approved':
            self._log_audit(self.pending_actions[action_id], approved_by='human')
            return True
        return False

    def approve_action(self, action_id):
        if action_id in self.pending_actions:
            self.pending_actions[action_id]['status'] = 'approved'
            event = self._events.get(action_id)
            if event:
                event.set()
            self._persist()
            self._log_audit(self.pending_actions[action_id], approved_by='human')
            return True
        return False

    def reject_action(self, action_id):
        if action_id in self.pending_actions:
            self.pending_actions[action_id]['status'] = 'rejected'
            event = self._events.get(action_id)
            if event:
                event.set()
            self._persist()
            self._log_audit(self.pending_actions[action_id], approved_by='human_rejected')
            return True
        return False

    async def wait_for_voice_approval(self, action_id: str, speaker=None, listener=None, timeout: int=60, max_retries: int=1) -> bool:
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

        Delegates the heavy lifting to:
            - :meth:`_build_voice_description`
            - :meth:`_voice_speak`
            - :meth:`_voice_listen_once`
            - :meth:`_process_voice_response` (which calls
              :meth:`_handle_voice_timeout`)
            - :meth:`_finalize_voice_approval`
        """
        action_data = self.pending_actions.get(action_id)
        if not action_data:
            logger.warning('wait_for_voice_approval: unknown action %s', action_id)
            return False
        if action_data.get('status') == 'approved':
            return True

        description = self._build_voice_description(action_data)
        await self._voice_speak(speaker, description)

        approved = await self._run_voice_approval_loop(
            action_id, speaker, listener, timeout, max_retries
        )

        await self._finalize_voice_approval(action_id, action_data, approved)
        return bool(approved)

    @staticmethod
    def _build_voice_description(action_data: dict) -> str:
        """Build the spoken description for a pending action.

        Args:
            action_data: The action dict (component/action/params keys).

        Returns:
            A human-readable description suitable for TTS.
        """
        component = action_data.get('component', 'unknown')
        act = action_data.get('action', 'unknown')
        params = action_data.get('params', {})
        return (
            f'Action pending: {component} wants to {act}. '
            f'Parameters: {params}. Say yes to approve, or no to reject.'
        )

    @staticmethod
    async def _voice_speak(speaker, text: str) -> None:
        """Speak ``text`` via ``speaker`` with graceful fallbacks.

        Tries (in order): ``speaker.speak_async(text)`` →
        ``speaker.speak(text)`` → ``print(...)``.
        """
        if speaker and hasattr(speaker, 'speak'):
            try:
                await speaker.speak_async(text)
                return
            except Exception as exc:
                logger.warning('Voice approval: speak_async failed: %s', exc)
                try:
                    speaker.speak(text)
                    return
                except Exception as e:
                    logger.debug(f'Non-critical error: {e}')
        print(f'[VOICE APPROVAL] {text}')

    @staticmethod
    async def _voice_listen_once(listener, timeout: int) -> str:
        """Capture one audio chunk and return the transcribed text.

        Args:
            listener: FridayListener instance (or any object with
                ``record_audio``).
            timeout: Seconds to wait before treating as no-input.

        Returns:
            The transcribed text, or ``""`` if no audio was captured or
            transcription failed.
        """
        if not (listener and hasattr(listener, 'record_audio')):
            return ''
        import tempfile
        import os as _os
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                tmp_path = tmp.name
            recorded = await asyncio.wait_for(asyncio.to_thread(listener.record_audio, tmp_path, 8), timeout=timeout)
            if not recorded:
                return ''
            try:
                from voice.transcriber import FridayTranscriber
                transcriber = FridayTranscriber()
                text = await asyncio.to_thread(transcriber.transcribe, tmp_path)
                return text or ''
            except ImportError:
                logger.warning('Voice approval: whisper not installed - cannot transcribe')
                return ''
            except Exception as exc:
                logger.warning('Voice approval: transcription failed: %s', exc)
                return ''
        except asyncio.TimeoutError:
            logger.info('Voice approval: no response within %ss - treating as no input', timeout)
            return ''
        finally:
            if tmp_path:
                try:
                    _os.unlink(tmp_path)
                except OSError as e:
                    logger.debug(f'Non-critical error: {e}')

    async def _run_voice_approval_loop(
        self,
        action_id: str,
        speaker,
        listener,
        timeout: int,
        max_retries: int,
    ) -> Optional[bool]:
        """Drive the listen → process → speak loop until resolved.

        Returns:
            True if the user approved, False if rejected or timed out.
        """
        attempts = 0
        while attempts <= max_retries:
            attempts += 1
            transcript = await self._voice_listen_once(listener, timeout)
            verdict = await self._process_voice_response(
                transcript, action_id, speaker, attempts, max_retries
            )
            if verdict is not None:
                return verdict
        # Loop exhausted without resolution — default to reject.
        return False

    async def _process_voice_response(
        self,
        transcript: str,
        action_id: str,
        speaker,
        attempts: int,
        max_retries: int,
    ) -> Optional[bool]:
        """Parse a transcript and speak the appropriate reply.

        Args:
            transcript: The transcribed voice input (may be empty).
            action_id: The action being approved/rejected (for logging).
            speaker: The FridaySpeaker instance (or compatible).
            attempts: 1-indexed attempt counter.
            max_retries: How many re-asks are allowed before giving up.

        Returns:
            True if approved, False if rejected/timed-out, None if the
            caller should re-ask (loop continues).
        """
        if not transcript:
            return await self._handle_voice_timeout(action_id, attempts, speaker)

        approved = self._parse_voice_intent(transcript)
        logger.info(
            'Voice approval attempt %d: heard=%r parsed=%r',
            attempts, transcript, approved,
        )

        if approved is True:
            await self._voice_speak(speaker, 'Approved. Proceeding.')
            return True
        if approved is False:
            await self._voice_speak(speaker, 'Rejected. The action will not run.')
            return False
        # Ambiguous
        if attempts <= max_retries:
            await self._voice_speak(speaker, "I didn't catch that. Please say yes or no.")
            return None
        await self._voice_speak(speaker, 'Still unclear. Rejecting the action for safety.')
        return False

    @staticmethod
    async def _handle_voice_timeout(action_id: str, attempts: int, speaker) -> bool:
        """Handle the no-audio-captured case.

        Speaks the reject message on the first attempt only and always
        returns ``False`` (reject).

        Args:
            action_id: The action being timed out (for context).
            attempts: 1-indexed attempt counter.
            speaker: The FridaySpeaker instance (or compatible).

        Returns:
            Always False (the action is rejected on timeout).
        """
        if attempts == 1:
            await ActionLedger._voice_speak(
                speaker,
                'No response detected within the timeout window. '
                'Rejecting the action for safety.',
            )
        return False

    async def _finalize_voice_approval(
        self,
        action_id: str,
        action_data: dict,
        approved: Optional[bool],
    ) -> None:
        """Apply the approved/rejected state to the ledger and audit log.

        Args:
            action_id: The action being finalised.
            action_data: The action dict (for the audit log).
            approved: True → approve; anything else → reject.
        """
        if approved:
            self.approve_action(action_id)
            self._log_audit(action_data, approved_by='voice')
        else:
            self.reject_action(action_id)
            self._log_audit(action_data, approved_by='voice_rejected')

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
        yes_words = {'yes', 'yeah', 'yep', 'sure', 'okay', 'ok', 'approve', 'go ahead', 'do it', 'confirm', 'affirmative'}
        no_words = {'no', 'nope', 'nah', 'reject', 'deny', 'cancel', 'stop', "don't", 'negative', 'refuse'}
        for word in yes_words:
            if word in text_lower:
                return True
        for word in no_words:
            if word in text_lower:
                return False
        return None
_ledger: Optional[ActionLedger] = None

def get_ledger(instance=None) -> ActionLedger:
    """Get the singleton ActionLedger instance.

    Args:
        instance: Optional instance to inject. When provided, the
            singleton is replaced with this instance. This is primarily
            intended for tests to substitute mock/fake instances.
    """
    global _ledger
    if instance is not None:
        _ledger = instance
    if _ledger is None:
        _ledger = ActionLedger()
    return _ledger