"""
Circuit breaker pattern implementation for protecting against cascading failures.
"""
import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

from app import logger


class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation, requests allowed
    OPEN = "open"            # Failure state, requests blocked
    HALF_OPEN = "half_open"  # Testing recovery, limited requests allowed


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 3       # Failures before opening circuit
    recovery_timeout: float = 30.0   # Seconds to wait before testing recovery
    half_open_max_calls: int = 1     # Test calls allowed in half-open state
    success_threshold: int = 1       # Successes needed to close circuit from half-open


@dataclass
class CircuitStats:
    failures: int = 0
    successes: int = 0
    last_failure_time: float = 0
    state: CircuitState = CircuitState.CLOSED
    half_open_calls: int = 0


class CircuitBreaker:
    """
    Circuit breaker for fault tolerance when communicating with nodes.

    States:
    - CLOSED: Normal operation, all requests pass through
    - OPEN: After failure_threshold failures, requests are blocked
    - HALF_OPEN: After recovery_timeout, allows limited test requests

    Usage:
        breaker = CircuitBreaker()

        if await breaker.is_allowed(node_id):
            try:
                result = await make_request(node_id)
                await breaker.record_success(node_id)
            except Exception:
                await breaker.record_failure(node_id)
    """

    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        self.config = config or CircuitBreakerConfig()
        self._circuits: Dict[int, CircuitStats] = {}
        self._lock = asyncio.Lock()

    def _get_stats(self, node_id: int) -> CircuitStats:
        """Get or create stats for a node."""
        if node_id not in self._circuits:
            self._circuits[node_id] = CircuitStats()
        return self._circuits[node_id]

    async def is_allowed(self, node_id: int) -> bool:
        """
        Check if requests to this node are allowed.
        Returns True if the circuit is closed or half-open with capacity.
        """
        async with self._lock:
            stats = self._get_stats(node_id)
            now = time.time()

            if stats.state == CircuitState.CLOSED:
                return True

            if stats.state == CircuitState.OPEN:
                # Check if recovery timeout has passed
                time_since_failure = now - stats.last_failure_time
                if time_since_failure >= self.config.recovery_timeout:
                    stats.state = CircuitState.HALF_OPEN
                    stats.half_open_calls = 0
                    stats.successes = 0
                    logger.info(
                        f"Circuit for node {node_id} transitioning to HALF_OPEN "
                        f"after {time_since_failure:.1f}s"
                    )
                    return True
                return False

            if stats.state == CircuitState.HALF_OPEN:
                # Allow limited test calls
                if stats.half_open_calls < self.config.half_open_max_calls:
                    stats.half_open_calls += 1
                    return True
                return False

            return False

    async def record_success(self, node_id: int):
        """Record a successful request to a node."""
        async with self._lock:
            stats = self._get_stats(node_id)
            stats.successes += 1

            if stats.state == CircuitState.HALF_OPEN:
                # Success in half-open state - check if we should close
                if stats.successes >= self.config.success_threshold:
                    stats.state = CircuitState.CLOSED
                    stats.failures = 0
                    stats.successes = 0
                    logger.info(f"Circuit for node {node_id} CLOSED (recovered)")

            elif stats.state == CircuitState.CLOSED:
                # Reset failure count on success
                stats.failures = 0

    async def record_failure(self, node_id: int):
        """Record a failed request to a node."""
        async with self._lock:
            stats = self._get_stats(node_id)
            stats.failures += 1
            stats.last_failure_time = time.time()

            if stats.state == CircuitState.HALF_OPEN:
                # Failure in half-open - back to open
                stats.state = CircuitState.OPEN
                logger.warning(
                    f"Circuit for node {node_id} OPEN (failed during recovery)"
                )

            elif stats.state == CircuitState.CLOSED:
                # Check if we should open the circuit
                if stats.failures >= self.config.failure_threshold:
                    stats.state = CircuitState.OPEN
                    logger.warning(
                        f"Circuit for node {node_id} OPEN "
                        f"(threshold {self.config.failure_threshold} reached)"
                    )

    def get_state(self, node_id: int) -> CircuitState:
        """Get current circuit state for a node."""
        return self._get_stats(node_id).state

    def get_stats(self, node_id: int) -> CircuitStats:
        """Get full stats for a node."""
        return self._get_stats(node_id)

    async def reset(self, node_id: int):
        """Reset circuit state for a node."""
        async with self._lock:
            if node_id in self._circuits:
                self._circuits[node_id] = CircuitStats()
                logger.info(f"Circuit for node {node_id} reset")

    async def reset_all(self):
        """Reset all circuit states."""
        async with self._lock:
            self._circuits.clear()
            logger.info("All circuits reset")

    def get_open_circuits(self) -> list[int]:
        """Get list of node IDs with open circuits."""
        return [
            node_id for node_id, stats in self._circuits.items()
            if stats.state == CircuitState.OPEN
        ]

    def __repr__(self) -> str:
        open_count = len(self.get_open_circuits())
        total = len(self._circuits)
        return f"CircuitBreaker(open={open_count}/{total})"
