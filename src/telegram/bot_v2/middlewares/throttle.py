"""
ThrottleMiddleware - rate limiting middleware.

Features:
1. Per-user rate limiting
2. Prevent spam/abuse
3. Configurable rate limit policy
"""
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Update


class ThrottleMiddleware(BaseMiddleware):
    """Rate limiting middleware."""

    MAX_ENTRIES = 10000

    def __init__(self, rate_limit: float = 0.5):
        """
        Initialize rate limiting middleware.

        Args:
            rate_limit: Minimum interval between requests from the same user (seconds)
        """
        self.rate_limit = rate_limit
        self.user_last_request: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any]
    ) -> Any:
        """Rate limit check. Silently drops requests that exceed the rate limit."""
        user_id = None
        if event.message and event.message.from_user:
            user_id = event.message.from_user.id
        elif event.callback_query and event.callback_query.from_user:
            user_id = event.callback_query.from_user.id

        if user_id:
            now = time.time()
            last_request = self.user_last_request.get(user_id, 0)

            if now - last_request < self.rate_limit:
                return None

            self.user_last_request[user_id] = now

            # Evict stale entries to bound memory
            if len(self.user_last_request) > self.MAX_ENTRIES:
                cutoff = now - self.rate_limit * 10
                self.user_last_request = {
                    uid: ts for uid, ts in self.user_last_request.items()
                    if ts > cutoff
                }

        return await handler(event, data)
