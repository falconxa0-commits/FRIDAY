"""Circuit Breaker — protects against cascading Redis failures.

Implements a 3-state circuit breaker:
    CLOSED → (failures exceed threshold) → OPEN
    OPEN → (after cooldown) → HALF_OPEN
    HALF_OPEN → (probe succeeds) → CLOSED
    HALF_OPEN → (probe fails) → OPEN

Prevents retry storms, connection storms, and cascading failures
when Redis is unavailable.

Usage::

    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=30)
    if cb.can_execute():
        try:
            result = await redis_operation()
            cb.record_success()
        except Exception:
            cb.record_failure()
    else:
        # circuit is open — use fallback
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("friday.runtime.v5.circuit_breaker")


class CircuitState(str, Enum):
    CLOSED = "closed"        # normal operation
    OPEN = "open"            # failing — reject all requests
    HALF_OPEN = "half_open"  # testing if Redis recovered


@dataclass
class CircuitBreakerStats:
    """Circuit breaker statistics."""
    total_requests: int = 0
    total_successes: int = 0
    total_failures: int = 0
    total_rejected: int = 0  # requests rejected because circuit was open
    consecutive_failures: int = 0
    state_changes: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    opened_at: float = 0.0  # when circuit opened (epoch)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "total_rejected": self.total_rejected,
            "consecutive_failures": self.consecutive_failures,
            "state_changes": self.state_changes,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
        }


class CircuitBreaker:
    """Circuit breaker for Redis operations.

    States:
        CLOSED: Normal operation. Failures are counted.
        OPEN: All requests rejected. After cooldown, transitions to HALF_OPEN.
        HALF_OPEN: Single probe request allowed. Success → CLOSED. Failure → OPEN.

    Configuration:
        failure_threshold: Failures before opening (default 5)
        cooldown_seconds: Time before probe (default 30)
        max_consecutive_rejections: Max rejections before forced half-open (default 100)
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        max_consecutive_rejections: int = 100,
    ):
        self._state = CircuitState.CLOSED
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._max_consecutive_rejections = max_consecutive_rejections
        self._stats = CircuitBreakerStats()
        self._consecutive_rejections = 0

    @property
    def state(self) -> CircuitState:
        """Current circuit state (computed for HALF_OPEN transition)."""
        if self._state == CircuitState.OPEN:
            # Check if cooldown has elapsed
            if self._stats.opened_at > 0:
                elapsed = time.time() - self._stats.opened_at
                if elapsed >= self._cooldown_seconds:
                    self._transition(CircuitState.HALF_OPEN)
                    logger.info("Circuit breaker: OPEN → HALF_OPEN (cooldown elapsed)")
        elif self._state == CircuitState.HALF_OPEN:
            # Force back to OPEN after too many rejections during half-open
            if self._consecutive_rejections >= self._max_consecutive_rejections:
                self._transition(CircuitState.OPEN)
                logger.warning("Circuit breaker: HALF_OPEN → OPEN (max rejections)")
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    @property
    def is_closed(self) -> bool:
        return self.state == CircuitState.CLOSED

    def can_execute(self) -> bool:
        """Check if a request can be executed."""
        current_state = self.state
        if current_state == CircuitState.OPEN:
            self._stats.total_rejected += 1
            self._consecutive_rejections += 1
            return False
        # CLOSED or HALF_OPEN — allow execution
        self._stats.total_requests += 1
        return True

    def record_success(self) -> None:
        """Record a successful operation."""
        self._stats.total_successes += 1
        self._stats.consecutive_failures = 0
        self._stats.last_success_time = time.time()
        self._consecutive_rejections = 0

        if self._state == CircuitState.HALF_OPEN:
            self._transition(CircuitState.CLOSED)
            logger.info("Circuit breaker: HALF_OPEN → CLOSED (probe succeeded)")

    def record_failure(self) -> None:
        """Record a failed operation."""
        self._stats.total_failures += 1
        self._stats.consecutive_failures += 1
        self._stats.last_failure_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            # Probe failed — back to OPEN
            self._transition(CircuitState.OPEN)
            logger.warning("Circuit breaker: HALF_OPEN → OPEN (probe failed)")
        elif self._state == CircuitState.CLOSED:
            if self._stats.consecutive_failures >= self._failure_threshold:
                self._transition(CircuitState.OPEN)
                logger.warning(
                    f"Circuit breaker: CLOSED → OPEN "
                    f"({self._stats.consecutive_failures} consecutive failures)"
                )

    def _transition(self, new_state: CircuitState) -> None:
        """Transition to a new state."""
        old_state = self._state
        self._state = new_state
        self._stats.state_changes += 1
        if new_state == CircuitState.OPEN:
            self._stats.opened_at = time.time()
        elif new_state == CircuitState.CLOSED:
            self._stats.opened_at = 0.0
            self._consecutive_rejections = 0

    def reset(self) -> None:
        """Reset the circuit breaker to CLOSED state."""
        self._transition(CircuitState.CLOSED)
        self._stats.consecutive_failures = 0
        self._consecutive_rejections = 0
        logger.info("Circuit breaker reset to CLOSED")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "failure_threshold": self._failure_threshold,
            "cooldown_seconds": self._cooldown_seconds,
            **self._stats.to_dict(),
        }

    async def is_healthy(self) -> bool:
        return self._state != CircuitState.OPEN
