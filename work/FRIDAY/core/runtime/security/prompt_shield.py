"""Prompt Injection Defense — input sanitization for LLM calls."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.security.prompt_shield")


@dataclass
class SanitizationResult:
    """Result of input sanitization."""
    original: str = ""
    sanitized: str = ""
    threats_detected: List[str] = field(default_factory=list)
    was_modified: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_length": len(self.original),
            "sanitized_length": len(self.sanitized),
            "threats_detected": self.threats_detected,
            "was_modified": self.was_modified,
            "timestamp": self.timestamp,
        }


class PromptShield:
    """Prompt injection defense layer."""

    INJECTION_PATTERNS = [
        (re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?", re.IGNORECASE), "ignore_instructions"),
        (re.compile(r"you\s+are\s+(?:now|actually)\s+", re.IGNORECASE), "role_hijack"),
        (re.compile(r"system\s*:\s*", re.IGNORECASE), "system_prefix"),
        (re.compile(r"(?:forget|disregard)\s+(?:everything|all|your)\s+", re.IGNORECASE), "memory_wipe"),
        (re.compile(r"(?:reveal|show|print|output)\s+(?:your|the|all)\s+(?:system\s+)?(?:prompt|instructions?|rules?)", re.IGNORECASE), "prompt_extraction"),
        (re.compile(r"(?:execute|run|eval|exec)\s+(?:code|command|script)", re.IGNORECASE), "code_execution_request"),
        (re.compile(r"(?:sudo|admin|root)\s+(?:mode|access|privileges)", re.IGNORECASE), "privilege_escalation"),
    ]

    SECRET_PATTERNS = [
        (re.compile(r"(?:sk-|sk_)[a-zA-Z0-9]{20,}"), "api_key"),
        (re.compile(r"(?:gh[pousr]_)[A-Za-z0-9]{36}"), "github_token"),
        (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"), "private_key"),
    ]

    def sanitize_input(self, text: str) -> SanitizationResult:
        """Sanitize user input before sending to LLM."""
        result = SanitizationResult(original=text)
        sanitized = text
        threats = []

        for pattern, threat_type in self.INJECTION_PATTERNS:
            matches = pattern.findall(sanitized)
            if matches:
                threats.append(threat_type)
                sanitized = pattern.sub(f"[FILTERED:{threat_type}]", sanitized)

        result.sanitized = sanitized
        result.threats_detected = threats
        result.was_modified = sanitized != text

        if threats:
            logger.warning(f"Prompt injection detected: {threats}")

        return result

    def validate_output(self, text: str) -> SanitizationResult:
        """Validate LLM output before sending to user."""
        result = SanitizationResult(original=text)
        sanitized = text
        threats = []

        for pattern, threat_type in self.SECRET_PATTERNS:
            matches = pattern.findall(sanitized)
            if matches:
                threats.append(f"leaked_{threat_type}")
                sanitized = pattern.sub(f"[REDACTED:{threat_type}]", sanitized)

        result.sanitized = sanitized
        result.threats_detected = threats
        result.was_modified = sanitized != text

        if threats:
            logger.warning(f"Secret leak detected in output: {threats}")

        return result

    def tag_roles(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Add role boundaries to prevent context bleeding."""
        tagged = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                tagged_content = f"[USER_INPUT_START]\n{content}\n[USER_INPUT_END]"
                tagged.append({"role": role, "content": tagged_content})
            else:
                tagged.append(msg)
        return tagged

    def get_stats(self) -> Dict[str, Any]:
        return {
            "injection_patterns": len(self.INJECTION_PATTERNS),
            "secret_patterns": len(self.SECRET_PATTERNS),
        }

    async def is_healthy(self) -> bool:
        return True

    async def stop(self) -> None:
        logger.info("Prompt shield stopped")


_shield: Optional[PromptShield] = None


def get_prompt_shield() -> PromptShield:
    global _shield
    if _shield is None:
        _shield = PromptShield()
    return _shield
