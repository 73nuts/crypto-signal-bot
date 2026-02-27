"""
Global error handler (aiogram 3.x).

Catches and logs all unhandled exceptions.
"""
from aiogram import Router
from aiogram.types import ErrorEvent, Update

from src.telegram.i18n import t, get_user_language
from src.core.structured_logger import get_logger

logger = get_logger(__name__)

router = Router(name="errors")


@router.error()
async def global_error_handler(event: ErrorEvent) -> bool:
    """Catch all unhandled exceptions, log, and notify user. Returns True (error handled)."""
    logger.error(
        f"Exception while handling an update: {event.exception}",
        exc_info=event.exception
    )

    update: Update = event.update

    try:
        user_id = None
        message = None

        if update.message:
            message = update.message
            if update.message.from_user:
                user_id = update.message.from_user.id
        elif update.callback_query:
            message = update.callback_query.message
            if update.callback_query.from_user:
                user_id = update.callback_query.from_user.id

        if message and user_id:
            lang = get_user_language(user_id)

            await message.answer(
                t('errors.system_error', lang)
            )

    except Exception as e:
        logger.error(f"Failed to send error message to user: {e}")

    return True
