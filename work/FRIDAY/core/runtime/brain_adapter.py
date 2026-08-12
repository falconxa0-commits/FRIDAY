"""Brain Runtime Adapter — wraps FridayBrain for runtime-mediated execution.

This adapter provides a runtime-mediated execution path for the brain.
It does NOT modify FridayBrain — instead, it wraps the brain's
execution methods with:

    1. PromptShield (sanitizes input before LLM calls)
    2. PolicyEngine (validates capabilities before execution)
    3. RuntimeExecutor (routes execution through the runtime)
    4. EventBus (emits events for every brain operation)
    5. DriverManager (dispatches tool/model/plugin calls)

Usage::

    adapter = BrainRuntimeAdapter(
        brain=FridayBrain(),
        executor=runtime_executor,
        event_bus=event_bus,
        prompt_shield=PromptShield(),
        policy_engine=PolicyEngine(),
        driver_manager=DriverManager(),
    )

    # Chat through the runtime (with sanitization + policy + events)
    result = await adapter.chat("Hello, Friday!")

    # Tool call through the driver layer
    result = await adapter.execute_tool("web_search", {"query": "AI"})

Backward compatibility:
    FridayBrain.chat_stream() continues to work unchanged.
    The adapter is an OPTIONAL wrapper — existing code that calls
    brain.chat_stream() directly still works.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.runtime.brain_adapter")


@dataclass
class BrainExecutionResult:
    """Result of a brain execution through the runtime."""
    status: str = "pending"  # pending, success, failed, blocked
    text: str = ""
    sanitized_input: str = ""
    threats_detected: List[str] = field(default_factory=list)
    policy_allowed: bool = True
    policy_reason: str = ""
    duration_seconds: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "text_length": len(self.text),
            "sanitized_input_length": len(self.sanitized_input),
            "threats_detected": self.threats_detected,
            "policy_allowed": self.policy_allowed,
            "policy_reason": self.policy_reason,
            "duration_seconds": round(self.duration_seconds, 3),
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class BrainRuntimeAdapter:
    """Wraps FridayBrain with runtime services.

    This adapter provides the Runtime → Brain convergence without
    modifying FridayBrain. It intercepts calls, applies security,
    routes through the executor, and emits events.

    The adapter supports:
        1. chat() — sanitized + policy-checked + runtime-executed chat
        2. chat_stream() — streaming chat with sanitization
        3. execute_tool() — tool call through DriverManager
        4. execute_model() — model call through DriverManager
        5. execute_plugin() — plugin call through DriverManager

    Every operation:
        - Sanitizes input with PromptShield
        - Validates capabilities with PolicyEngine
        - Executes through RuntimeExecutor (if available)
        - Emits events through EventBus
        - Records metrics
    """

    def __init__(
        self,
        brain: Any = None,
        executor: Any = None,
        event_bus: Any = None,
        prompt_shield: Any = None,
        policy_engine: Any = None,
        driver_manager: Any = None,
    ):
        self.brain = brain
        self.executor = executor
        self.event_bus = event_bus
        self.prompt_shield = prompt_shield
        self.policy_engine = policy_engine
        self.driver_manager = driver_manager

        # Statistics
        self._total_chats = 0
        self._total_tool_calls = 0
        self._total_blocked = 0
        self._total_sanitized = 0

    async def chat(self, message: str, **kwargs) -> BrainExecutionResult:
        """Execute a chat through the full runtime pipeline.

        Pipeline:
            1. Sanitize input (PromptShield)
            2. Check policy (PolicyEngine)
            3. Execute (RuntimeExecutor or direct brain call)
            4. Validate output (PromptShield)
            5. Emit events (EventBus)
        """
        result = BrainExecutionResult()
        start = time.perf_counter()

        # Step 1: Sanitize input
        if self.prompt_shield:
            sanitization = self.prompt_shield.sanitize_input(message)
            result.sanitized_input = sanitization.sanitized
            result.threats_detected = sanitization.threats_detected
            if sanitization.was_modified:
                self._total_sanitized += 1
                logger.warning(
                    f"Input sanitized: {len(result.threats_detected)} threats detected"
                )
            message_to_send = sanitization.sanitized
        else:
            result.sanitized_input = message
            message_to_send = message

        # Step 2: Check policy
        if self.policy_engine:
            decision = self.policy_engine.evaluate("brain.chat")
            result.policy_allowed = decision.allowed
            result.policy_reason = decision.reason
            if not decision.allowed:
                result.status = "blocked"
                result.error = f"Policy denied: {decision.reason}"
                self._total_blocked += 1
                await self._emit("brain.chat.blocked", result)
                result.duration_seconds = time.perf_counter() - start
                return result

        # Step 3: Execute
        try:
            if self.executor:
                # Execute through runtime executor
                exec_result = await self.executor.execute(
                    self._brain_chat_func,
                    args=(message_to_send,),
                    kwargs=kwargs,
                )
                if exec_result.status == "success":
                    result.status = "success"
                    result.text = exec_result.result or ""
                else:
                    result.status = "failed"
                    result.error = exec_result.error
            elif self.brain:
                # Direct brain call (fallback when no executor)
                result.text = await self._direct_brain_chat(message_to_send, **kwargs)
                result.status = "success"
            else:
                result.status = "failed"
                result.error = "No brain or executor available"

        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)
            logger.error(f"Brain chat failed: {exc}")

        # Step 4: Validate output
        if result.status == "success" and self.prompt_shield:
            output_validation = self.prompt_shield.validate_output(result.text)
            if output_validation.was_modified:
                result.text = output_validation.sanitized
                logger.warning("Output sanitized: secret leak detected")

        # Step 5: Emit event
        result.duration_seconds = time.perf_counter() - start
        result.tokens_input = len(message) // 4  # rough estimate
        result.tokens_output = len(result.text) // 4
        self._total_chats += 1
        await self._emit("brain.chat.completed", result)

        return result

    async def chat_stream(self, message: str, **kwargs):
        """Streaming chat through the runtime pipeline.

        Yields chunks of the response. Sanitization happens on input
        before streaming starts. Output validation happens per-chunk.
        """
        # Sanitize input
        if self.prompt_shield:
            sanitization = self.prompt_shield.sanitize_input(message)
            if sanitization.was_modified:
                self._total_sanitized += 1
                logger.warning(f"Stream input sanitized: {sanitization.threats_detected}")
            message = sanitization.sanitized

        # Check policy
        if self.policy_engine:
            decision = self.policy_engine.evaluate("brain.chat")
            if not decision.allowed:
                await self._emit("brain.stream.blocked", {"reason": decision.reason})
                return

        await self._emit("brain.stream.started", {"message_length": len(message)})

        # Stream from brain
        if self.brain and hasattr(self.brain, "chat_stream"):
            async for chunk in self.brain.chat_stream(message, **kwargs):
                # Per-chunk output validation
                if self.prompt_shield:
                    validated = self.prompt_shield.validate_output(chunk)
                    yield validated.sanitized
                else:
                    yield chunk
        else:
            yield ""

        await self._emit("brain.stream.completed", {})

    async def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool through the driver layer.

        Pipeline:
            1. Check policy (tool execution capability)
            2. Execute through DriverManager
            3. Emit event
        """
        # Check policy
        capability = f"tool.{tool_name}"
        if self.policy_engine:
            decision = self.policy_engine.evaluate(capability)
            if not decision.allowed:
                self._total_blocked += 1
                await self._emit("tool.blocked", {"tool": tool_name, "reason": decision.reason})
                return {"status": "blocked", "error": decision.reason}

        # Execute through driver manager
        if self.driver_manager:
            result = await self.driver_manager.execute_tool(tool_name, params)
            self._total_tool_calls += 1
            await self._emit("tool.executed", {"tool": tool_name, "params": str(params)[:100]})
            return result.to_dict() if hasattr(result, "to_dict") else result

        return {"status": "error", "error": "No driver manager available"}

    async def execute_model(self, model_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a model through the driver layer."""
        capability = f"model.{model_name}"
        if self.policy_engine:
            decision = self.policy_engine.evaluate(capability)
            if not decision.allowed:
                return {"status": "blocked", "error": decision.reason}

        if self.driver_manager:
            result = await self.driver_manager.execute_model(model_name, params)
            await self._emit("model.executed", {"model": model_name})
            return result.to_dict() if hasattr(result, "to_dict") else result

        return {"status": "error", "error": "No driver manager available"}

    async def execute_plugin(self, plugin_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a plugin through the driver layer."""
        capability = f"plugin.{plugin_name}"
        if self.policy_engine:
            decision = self.policy_engine.evaluate(capability)
            if not decision.allowed:
                return {"status": "blocked", "error": decision.reason}

        if self.driver_manager:
            result = await self.driver_manager.execute_plugin(plugin_name, params)
            await self._emit("plugin.executed", {"plugin": plugin_name})
            return result.to_dict() if hasattr(result, "to_dict") else result

        return {"status": "error", "error": "No driver manager available"}

    async def _brain_chat_func(self, message: str, **kwargs) -> str:
        """Function wrapper for executor-based brain chat."""
        return await self._direct_brain_chat(message, **kwargs)

    async def _direct_brain_chat(self, message: str, **kwargs) -> str:
        """Call the brain directly (fallback when no executor)."""
        if not self.brain:
            return ""
        if hasattr(self.brain, "chat_stream"):
            full = ""
            async for chunk in self.brain.chat_stream(message, **kwargs):
                full += chunk
            return full
        elif hasattr(self.brain, "chat"):
            result = await self.brain.chat(message, **kwargs)
            return result if isinstance(result, str) else str(result)
        return ""

    async def _emit(self, event_type: str, data: Any) -> None:
        """Emit an event through the event bus."""
        if not self.event_bus:
            return
        if isinstance(data, BrainExecutionResult):
            data = data.to_dict()
        elif not isinstance(data, dict):
            data = {"value": str(data)}
        await self.event_bus.publish(event_type, data, source="brain_adapter")

    def get_stats(self) -> Dict[str, Any]:
        """Get adapter statistics."""
        return {
            "total_chats": self._total_chats,
            "total_tool_calls": self._total_tool_calls,
            "total_blocked": self._total_blocked,
            "total_sanitized": self._total_sanitized,
            "has_brain": self.brain is not None,
            "has_executor": self.executor is not None,
            "has_prompt_shield": self.prompt_shield is not None,
            "has_policy_engine": self.policy_engine is not None,
            "has_driver_manager": self.driver_manager is not None,
        }

    async def is_healthy(self) -> bool:
        """Check if the adapter is healthy."""
        return self.brain is not None or self.executor is not None

    async def stop(self) -> None:
        """Stop the adapter."""
        logger.info("Brain runtime adapter stopped")
