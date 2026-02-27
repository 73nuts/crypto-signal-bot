"""
Circuit breaker.

Triggers on message volume spikes, forcing all messages into the Digest queue.
Prevents users from receiving excessive vibration alerts during extreme market volatility.

Trigger condition: >10 messages in 1 minute
Cooldown: 5 minutes
Behavior: All messages during circuit-open period go to Digest queue
"""

import logging
import time
from typing import List, Optional

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit breaker.

    - Records message timestamps (sliding window)
    - Trips when threshold exceeded within 1 minute
    - is_open() returns True while tripped
    - Auto-recovers after cooldown
    """

    # Config
    WINDOW_SECONDS = 60      # Window: 1 minute
    THRESHOLD = 10           # Threshold: 10 messages/minute
    COOLDOWN_SECONDS = 300   # Cooldown: 5 minutes

    def __init__(self, cache_manager=None):
        """Initialize circuit breaker.

        Args:
            cache_manager: CacheManager instance (optional, for Redis persistence)
        """
        self._cache = cache_manager
        self._use_redis = cache_manager is not None

        # In-memory storage (fallback)
        self._window_counter: List[float] = []  # Timestamp list
        self._open_time: Optional[float] = None  # Trip start time

        logger.info(
            f"CircuitBreaker initialized, "
            f"threshold={self.THRESHOLD}/{self.WINDOW_SECONDS}s, "
            f"cooldown={self.COOLDOWN_SECONDS}s, "
            f"use_redis={self._use_redis}"
        )

    def is_open(self) -> bool:
        """Check whether the circuit breaker is open.

        Returns:
            True: Tripped, all messages should go to Digest
            False: Normal mode
        """
        if self._open_time is None:
            return False

        elapsed = time.time() - self._open_time
        if elapsed >= self.COOLDOWN_SECONDS:
            # Auto-recover
            self._open_time = None
            logger.info(
                f"[CircuitBreaker] Auto-recovered after {self.COOLDOWN_SECONDS}s cooldown"
            )
            return False

        return True

    def record_message(self) -> bool:
        """Record a message and check whether to trip the circuit breaker.

        Returns:
            True: Normal, message can continue processing
            False: Circuit tripped, message should go to Digest
        """
        now = time.time()

        # Clean expired records
        self._window_counter = [
            ts for ts in self._window_counter
            if now - ts < self.WINDOW_SECONDS
        ]

        # Record current message
        self._window_counter.append(now)

        # Check threshold
        if len(self._window_counter) > self.THRESHOLD:
            if self._open_time is None:
                self._open_time = now
                logger.warning(
                    f"[CircuitBreaker] TRIPPED! "
                    f"{len(self._window_counter)} messages in {self.WINDOW_SECONDS}s "
                    f"(threshold={self.THRESHOLD}). "
                    f"Cooldown for {self.COOLDOWN_SECONDS}s"
                )
            return False

        return True

    def get_status(self) -> dict:
        """Get circuit breaker status (for debugging).

        Returns:
            Status dict
        """
        now = time.time()
        is_open = self.is_open()

        remaining = 0
        if is_open and self._open_time:
            remaining = max(0, self.COOLDOWN_SECONDS - (now - self._open_time))

        # Count after cleaning expired records
        recent_count = len([
            ts for ts in self._window_counter
            if now - ts < self.WINDOW_SECONDS
        ])

        return {
            'is_open': is_open,
            'remaining_seconds': round(remaining, 1),
            'recent_message_count': recent_count,
            'threshold': self.THRESHOLD,
            'window_seconds': self.WINDOW_SECONDS,
        }

    def reset(self) -> None:
        """Manually reset circuit breaker (admin function)."""
        self._window_counter.clear()
        self._open_time = None
        logger.info("[CircuitBreaker] Manually reset")


# ==========================================
# Global singleton
# ==========================================

_circuit_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    """Get global CircuitBreaker instance."""
    global _circuit_breaker
    if _circuit_breaker is None:
        try:
            from src.core.cache import CacheManager
            cache = CacheManager()
            _circuit_breaker = CircuitBreaker(cache_manager=cache)
        except Exception as e:
            logger.warning(f"CacheManager initialization failed, using memory mode: {e}")
            _circuit_breaker = CircuitBreaker(cache_manager=None)
    return _circuit_breaker
