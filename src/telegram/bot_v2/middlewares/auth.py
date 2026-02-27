"""
AuthMiddleware - permission check middleware.

Features:
1. Query user membership info (with TTL in-memory cache)
2. Inject user_info into handler context
3. Mark admin/Premium status
"""
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from aiogram import BaseMiddleware
from aiogram.types import Update

from src.core.config import settings
from src.core.structured_logger import get_logger

from ..utils.async_wrapper import run_sync

logger = get_logger(__name__)

# TTL cache: {telegram_id: (user_info_dict, timestamp)}
_auth_cache: Dict[int, Tuple[Dict[str, Any], float]] = {}
_CACHE_TTL = 30  # seconds


def _get_membership_info(telegram_id: int) -> Dict[str, Any]:
    """Sync function to fetch user membership info (for run_sync)."""
    from ..utils.db_provider import get_member_service
    return get_member_service().check_membership_valid(telegram_id)


def invalidate_auth_cache(telegram_id: int) -> None:
    """Invalidate cached auth info for a user. Call after write operations."""
    _auth_cache.pop(telegram_id, None)


class AuthMiddleware(BaseMiddleware):
    """Permission check middleware. Queries membership info and injects into handler context."""

    def __init__(self):
        super().__init__()
        self._admin_id: Optional[int] = settings.ADMIN_TELEGRAM_ID

    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any]
    ) -> Any:
        """
        Query user info and inject into handler context.

        Injects into data:
        - user: membership info dict (check_membership_valid return value)
        - is_admin: whether user is admin
        - is_premium: whether Premium member (level >= 2)
        - telegram_id: user Telegram ID
        """
        user_id = self._extract_user_id(event)

        if user_id:
            user_info = self._get_from_cache(user_id)
            if user_info is None:
                try:
                    user_info = await run_sync(_get_membership_info, user_id)
                    _auth_cache[user_id] = (user_info, time.monotonic())
                except Exception as e:
                    logger.warning(f"User info query failed: telegram_id={user_id}, error={e}")
                    user_info = self._empty_user_info()

            data['user'] = user_info
            data['telegram_id'] = user_id
            data['is_admin'] = (user_id == self._admin_id)
            data['is_premium'] = (user_info.get('level') or 0) >= 2
        else:
            # Cannot identify user (e.g. channel_post)
            data['user'] = None
            data['telegram_id'] = None
            data['is_admin'] = False
            data['is_premium'] = False

        return await handler(event, data)

    def _get_from_cache(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Return cached user_info if fresh, else None."""
        entry = _auth_cache.get(telegram_id)
        if entry is None:
            return None
        user_info, ts = entry
        if time.monotonic() - ts > _CACHE_TTL:
            del _auth_cache[telegram_id]
            return None
        return user_info

    def _extract_user_id(self, event: Update) -> Optional[int]:
        """Extract user ID from Update event."""
        if event.message and event.message.from_user:
            return event.message.from_user.id
        elif event.callback_query and event.callback_query.from_user:
            return event.callback_query.from_user.id
        elif event.inline_query and event.inline_query.from_user:
            return event.inline_query.from_user.id
        elif event.chat_join_request and event.chat_join_request.from_user:
            return event.chat_join_request.from_user.id
        return None

    def _empty_user_info(self) -> Dict[str, Any]:
        """Return empty user info structure."""
        return {
            'active': False,
            'membership_type': None,
            'level': None,
            'expire_date': None,
            'days_remaining': None,
            'is_whitelist': False
        }
