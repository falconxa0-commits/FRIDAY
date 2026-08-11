"""Model Driver — base class for LLM provider drivers.

Model drivers provide a uniform interface to different LLM providers
(GLM, Claude, Gemini, GPT, local models).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class ModelResult:
    """Result of a model execution."""
    status: str = "pending"
    text: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    model: str = ""
    duration_seconds: float = 0.0
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text[:200],
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "model": self.model,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class ModelDriver(abc.ABC):
    """Abstract base class for model drivers."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @property
    @abc.abstractmethod
    def provider(self) -> str: ...

    @abc.abstractmethod
    async def execute(self, params: Dict[str, Any]) -> ModelResult: ...

    @abc.abstractmethod
    def is_available(self) -> bool: ...


class GLMModelDriver(ModelDriver):
    """Model driver for ZhipuAI GLM."""

    @property
    def name(self) -> str:
        return "glm"

    @property
    def provider(self) -> str:
        return "zhipuai"

    def is_available(self) -> bool:
        import os
        return bool(os.environ.get("GLM_API_KEY"))

    async def execute(self, params: Dict[str, Any]) -> ModelResult:
        # Placeholder — real implementation would call GLMBrain
        return ModelResult(
            status="success" if self.is_available() else "unavailable",
            text="",
            model="glm-4-flash",
        )


class ClaudeModelDriver(ModelDriver):
    """Model driver for Anthropic Claude."""

    @property
    def name(self) -> str:
        return "claude"

    @property
    def provider(self) -> str:
        return "anthropic"

    def is_available(self) -> bool:
        import os
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    async def execute(self, params: Dict[str, Any]) -> ModelResult:
        return ModelResult(
            status="success" if self.is_available() else "unavailable",
            text="",
            model="claude-sonnet-4-20250514",
        )
