"""
Admin filter.

Checks memberships.is_admin field; blocks non-admin access.
"""
from typing import Union

from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message

from src.core.structured_logger import get_logger

from ..utils.async_wrapper import run_sync

logger = get_logger(__name__)


def is_admin_sync(user_id: int) -> bool:
    """
    Check admin status via ADMIN_TELEGRAM_ID env var first, then DB fallback.
    """
    from src.core.config import settings

    if settings.ADMIN_TELEGRAM_ID and user_id == settings.ADMIN_TELEGRAM_ID:
        return True

    try:
        from ..utils.db_provider import get_db
        result = get_db().execute_query(
            "SELECT is_admin FROM memberships WHERE telegram_id = %s AND is_admin = 1",
            (user_id,),
            fetch_one=True
        )
        return result is not None
    except Exception as e:
        logger.error(f"Admin check failed: {e}")
        return False


class AdminFilter(Filter):
    """Admin permission filter. Reads admin status from memberships.is_admin."""

    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        """Returns True if user is admin, False otherwise."""
        user_id = None

        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if user_id is None:
            return False

        return await run_sync(is_admin_sync, user_id)
