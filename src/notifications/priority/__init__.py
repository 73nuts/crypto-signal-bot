"""
Message priority layering system.

Reduces notification noise:
- PriorityCalculator: Priority calculation (P0/P1/P2)
- CircuitBreaker: Circuit breaker (>10 messages/min triggers 5-min break)
- SlidingWindowLimiter: Sliding window rate limiting
- DigestManager: Message aggregation (triggered at 5 minutes or 3 messages)
- PinnedMessageReplyManager: Pinned message reply
"""

from src.notifications.priority.calculator import (
    Priority,
    PriorityCalculator,
    get_priority_calculator,
)
from src.notifications.priority.circuit_breaker import (
    CircuitBreaker,
    get_circuit_breaker,
)
from src.notifications.priority.sliding_window import (
    SlidingWindowLimiter,
    get_sliding_window_limiter,
)
from src.notifications.priority.digest_manager import (
    DigestManager,
    get_digest_manager,
)
from src.notifications.priority.pinned_reply import (
    PinnedMessageReplyManager,
    get_pinned_reply_manager,
)

__all__ = [
    # Calculator
    'Priority',
    'PriorityCalculator',
    'get_priority_calculator',
    # CircuitBreaker
    'CircuitBreaker',
    'get_circuit_breaker',
    # SlidingWindow
    'SlidingWindowLimiter',
    'get_sliding_window_limiter',
    # Digest
    'DigestManager',
    'get_digest_manager',
    # PinnedReply
    'PinnedMessageReplyManager',
    'get_pinned_reply_manager',
]
