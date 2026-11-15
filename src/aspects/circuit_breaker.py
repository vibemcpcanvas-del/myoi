"""src/aspects/circuit_breaker.py
Aspect 2: Fail-Closed Circuit Breaker & Retry / Rate-Limiting Policy.
Decouples upstream fault tolerance, automatic circuit tripping, and fail-closed protection.
"""

from enum import Enum
import functools
import threading
import time
from typing import Any, Callable, Dict, Optional, Set, Tuple
from fastapi import HTTPException, status

from src.aspects.base import is_coroutine_callable


class CircuitState(str, Enum):
    CLOSED = "CLOSED"        # Normal operation: traffic flows through
    OPEN = "OPEN"            # Tripped: fail-closed, traffic immediately rejected
    HALF_OPEN = "HALF_OPEN"  # Recovery testing: canary traffic allowed


class CircuitBreakerOpenException(HTTPException):
    """Raised when an operation is blocked by an OPEN circuit breaker."""

    def __init__(self, breaker_name: str, cooldown_remaining: float, state: CircuitState = CircuitState.OPEN):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "circuit_breaker_open",
                "message": (
                    f"Circuit breaker '{breaker_name}' is OPEN (Fail-Closed active). "
                    f"Upstream inference is temporarily isolated. Retry in {cooldown_remaining:.1f}s."
                ),
                "circuitBreaker": {
                    "name": breaker_name,
                    "state": state.value,
                    "cooldownRemainingSec": round(cooldown_remaining, 2),
                },
            },
        )


class CircuitBreaker:
    """Fail-Closed Circuit Breaker state machine enforcing fail-closed invariants."""

    def __init__(
        self,
        name: str = "upstream_inference",
        failure_threshold: int = 3,
        recovery_timeout: float = 15.0,
        fatal_statuses: Optional[Set[int]] = None,
        half_open_max_canary: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.fatal_statuses: Set[int] = fatal_statuses if fatal_statuses is not None else {401, 403}
        self.half_open_max_canary = half_open_max_canary

        self._lock = threading.RLock()
        self.state: CircuitState = CircuitState.CLOSED
        self.consecutive_failures: int = 0
        self.total_trips: int = 0
        self.last_failure_time: float = 0.0
        self.last_state_change: float = time.time()
        self.last_error_message: Optional[str] = None
        self._half_open_in_flight: int = 0

    def can_execute(self) -> bool:
        """Determines if a request can proceed under the circuit breaker policy.
        Thread-safe and isolates canary testing in HALF_OPEN to prevent herd effect.
        """
        with self._lock:
            now = time.time()

            if self.state == CircuitState.CLOSED:
                return True

            if self.state == CircuitState.OPEN:
                elapsed = now - self.last_failure_time
                if elapsed >= self.recovery_timeout:
                    # Transition to HALF_OPEN to test recovery with a single canary request
                    self.state = CircuitState.HALF_OPEN
                    self.last_state_change = now
                    self._half_open_in_flight = 1
                    return True
                return False

            if self.state == CircuitState.HALF_OPEN:
                # Canary gate: only permit limited canary probes while evaluating recovery
                if self._half_open_in_flight < self.half_open_max_canary:
                    self._half_open_in_flight += 1
                    return True
                return False

            return False

    def remaining_cooldown(self) -> float:
        """Calculates remaining seconds before transitioning from OPEN to HALF_OPEN."""
        with self._lock:
            if self.state != CircuitState.OPEN:
                return 0.0
            elapsed = time.time() - self.last_failure_time
            remaining = self.recovery_timeout - elapsed
            return max(0.0, remaining)

    def record_success(self) -> None:
        """Records a successful operation and restores CLOSED state."""
        with self._lock:
            self.consecutive_failures = 0
            self._half_open_in_flight = 0
            if self.state != CircuitState.CLOSED:
                self.state = CircuitState.CLOSED
                self.last_state_change = time.time()

    def record_failure(self, status_code: Optional[int] = None, error_message: Optional[str] = None) -> bool:
        """Records a failure and trips to OPEN if threshold or fatal status is reached.
        Client errors (4xx other than fatal auth statuses) do NOT trip the circuit breaker.
        Returns True if recorded as an upstream failure, False if ignored client error.
        """
        with self._lock:
            # Client errors (400, 404, 413, 422, etc.) must not trip the upstream circuit
            if status_code is not None and 400 <= status_code < 500 and status_code not in self.fatal_statuses:
                if self.state == CircuitState.HALF_OPEN and self._half_open_in_flight > 0:
                    self._half_open_in_flight -= 1
                return False

            now = time.time()
            self.last_failure_time = now
            self.consecutive_failures += 1
            self.last_error_message = error_message
            self._half_open_in_flight = 0

            # Fail-Closed Invariant: 401/403 or immediate fatal status trips circuit immediately!
            is_fatal = status_code in self.fatal_statuses if status_code else False

            if self.state == CircuitState.HALF_OPEN or is_fatal or self.consecutive_failures >= self.failure_threshold:
                if self.state != CircuitState.OPEN:
                    self.total_trips += 1
                self.state = CircuitState.OPEN
                self.last_state_change = now
            return True

    def release_canary(self) -> None:
        """Releases an in-flight canary slot if an operation aborted without completing."""
        with self._lock:
            if self._half_open_in_flight > 0:
                self._half_open_in_flight -= 1

    def trip(self, reason: Optional[str] = None) -> None:
        """Manually forces the circuit breaker into an OPEN state (manual kill switch)."""
        with self._lock:
            self.state = CircuitState.OPEN
            self.last_failure_time = time.time()
            self.last_state_change = time.time()
            self.total_trips += 1
            self.last_error_message = reason or "Manually tripped"
            self._half_open_in_flight = 0

    def reset(self) -> None:
        """Resets the circuit breaker to CLOSED and clears failure count."""
        with self._lock:
            self.state = CircuitState.CLOSED
            self.consecutive_failures = 0
            self.last_state_change = time.time()
            self.last_error_message = None
            self._half_open_in_flight = 0

    def get_telemetry(self) -> Dict[str, Any]:
        """Provides real-time health and operational metrics of the circuit breaker."""
        with self._lock:
            return {
                "name": self.name,
                "state": self.state.value,
                "consecutiveFailures": self.consecutive_failures,
                "failureThreshold": self.failure_threshold,
                "totalTrips": self.total_trips,
                "cooldownRemainingSec": round(self.remaining_cooldown(), 2),
                "lastErrorMessage": self.last_error_message,
                "canaryInFlight": self._half_open_in_flight,
            }


# Global circuit breaker instance for upstream inference
default_inference_circuit_breaker = CircuitBreaker(
    name="upstream_inference_circuit",
    failure_threshold=3,
    recovery_timeout=15.0,
    fatal_statuses={401, 403},
)


def circuit_breaker(breaker: Optional[CircuitBreaker] = None) -> Callable:
    """AOP Decorator: Applies circuit breaker protection around synchronous or asynchronous functions."""
    cb = breaker or default_inference_circuit_breaker

    def decorator(func: Callable) -> Callable:
        if is_coroutine_callable(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                if not cb.can_execute():
                    raise CircuitBreakerOpenException(cb.name, cb.remaining_cooldown(), cb.state)

                try:
                    result = await func(*args, **kwargs)
                    cb.record_success()
                    return result
                except HTTPException as he:
                    recorded = cb.record_failure(status_code=he.status_code, error_message=str(he.detail))
                    if not recorded:
                        cb.release_canary()
                    raise he
                except Exception as ex:
                    cb.record_failure(status_code=500, error_message=str(ex))
                    raise ex

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                if not cb.can_execute():
                    raise CircuitBreakerOpenException(cb.name, cb.remaining_cooldown(), cb.state)

                try:
                    result = func(*args, **kwargs)
                    cb.record_success()
                    return result
                except HTTPException as he:
                    recorded = cb.record_failure(status_code=he.status_code, error_message=str(he.detail))
                    if not recorded:
                        cb.release_canary()
                    raise he
                except Exception as ex:
                    cb.record_failure(status_code=500, error_message=str(ex))
                    raise ex

            return sync_wrapper

    return decorator
