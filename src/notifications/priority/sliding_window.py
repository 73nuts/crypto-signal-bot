"""
Sliding window rate limiter.

Per-event-type hourly rate limiting; over-limit messages go to Digest queue (not dropped).

Rate limits:
- alert: 20/hour
- spread: 10/hour
- orderbook: 10/hour
"""

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SlidingWindowLimiter:
    """Sliding window rate limiter.

    - Per-event-type rate limiting
    - 1-hour sliding window
    - Returns False when over limit (message should go to Digest)
    """

    # Config: per-type hourly limit
    LIMITS: Dict[str, int] = {
        'alert': 20,      # Alert: 20/hour
        'spread': 10,     # Spread: 10/hour
        'orderbook': 10,  # Orderbook: 10/hour
    }

    WINDOW_SECONDS = 3600  # 1-hour window

    def __init__(self, cache_manager=None):
        """Initialize rate limiter.

        Args:
            cache_manager: CacheManager instance (optional, for Redis persistence)
        """
        self._cache = cache_manager
        self._use_redis = cache_manager is not None

        # In-memory storage: {event_type: [timestamp, ...]}
        self._counters: Dict[str, List[float]] = {}

        logger.info(
            f"SlidingWindowLimiter initialized, "
            f"limits={self.LIMITS}, "
            f"window={self.WINDOW_SECONDS}s, "
            f"use_redis={self._use_redis}"
        )

    def check_and_record(self, event_type: str) -> bool:
        """Check whether message can be sent and record it.

        Args:
            event_type: Event type (alert/spread/orderbook)

        Returns:
            True: Can send
            False: Over limit, should go to Digest
        """
        limit = self.LIMITS.get(event_type, 10)
        now = time.time()

        # Get or init counter
        if event_type not in self._counters:
            self._counters[event_type] = []

        # Clean expired records
        self._counters[event_type] = [
            ts for ts in self._counters[event_type]
            if now - ts < self.WINDOW_SECONDS
        ]

        # Check limit
        current_count = len(self._counters[event_type])
        if current_count >= limit:
            logger.debug(
                f"[SlidingWindow] {event_type} rate limited: "
                f"{current_count}/{limit} in last hour"
            )
            return False

        # Record current message
        self._counters[event_type].append(now)

        logger.debug(
            f"[SlidingWindow] {event_type} allowed: "
            f"{current_count + 1}/{limit} in last hour"
        )
        return True

    def get_remaining(self, event_type: str) -> int:
        """Get remaining quota.

        Args:
            event_type: Event type

        Returns:
            Remaining messages allowed
        """
        limit = self.LIMITS.get(event_type, 10)
        now = time.time()

        if event_type not in self._counters:
            return limit

        # Count after cleaning expired records
        recent = [
            ts for ts in self._counters[event_type]
            if now - ts < self.WINDOW_SECONDS
        ]

        return max(0, limit - len(recent))

    def get_status(self) -> Dict[str, dict]:
        """Get rate limit status for all types (for debugging).

        Returns:
            {event_type: {count, limit, remaining}}
        """
        now = time.time()
        status = {}

        for event_type, limit in self.LIMITS.items():
            if event_type in self._counters:
                recent = [
                    ts for ts in self._counters[event_type]
                    if now - ts < self.WINDOW_SECONDS
                ]
                count = len(recent)
            else:
                count = 0

            status[event_type] = {
                'count': count,
                'limit': limit,
                'remaining': max(0, limit - count),
            }

        return status

    def reset(self, event_type: Optional[str] = None) -> None:
        """Reset rate limit counters (admin function).

        Args:
            event_type: Specific type to reset; None resets all
        """
        if event_type:
            self._counters.pop(event_type, None)
            logger.info(f"[SlidingWindow] Reset counter for {event_type}")
        else:
            self._counters.clear()
            logger.info("[SlidingWindow] Reset all counters")


# ==========================================
# Global singleton
# ==========================================

_sliding_window_limiter: Optional[SlidingWindowLimiter] = None


def get_sliding_window_limiter() -> SlidingWindowLimiter:
    """Get global SlidingWindowLimiter instance."""
    global _sliding_window_limiter
    if _sliding_window_limiter is None:
        try:
            from src.core.cache import CacheManager
            cache = CacheManager()
            _sliding_window_limiter = SlidingWindowLimiter(cache_manager=cache)
        except Exception as e:
            logger.warning(f"CacheManager initialization failed, using memory mode: {e}")
            _sliding_window_limiter = SlidingWindowLimiter(cache_manager=None)
    return _sliding_window_limiter
