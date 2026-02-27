"""
Telegram message pinning utilities.

Unpins all existing pins, then pins the new message.
"""

import logging
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)


async def update_pinned_message(
    bot: Bot,
    chat_id: int,
    message_id: int,
    silent: bool = True
) -> bool:
    """
    Replace the pinned message in a chat (unpin all -> pin new).

    Returns True on success; failure does not raise (logged as warning).
    """
    try:
        await bot.unpin_all_chat_messages(chat_id=chat_id)
        logger.debug(f"Unpinned all messages: chat_id={chat_id}")

        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=message_id,
            disable_notification=silent
        )
        logger.info(f"Message pinned: chat_id={chat_id}, msg_id={message_id}")
        return True

    except TelegramAPIError as e:
        logger.warning(f"Pin operation failed: chat_id={chat_id}, error={e}")
        return False
