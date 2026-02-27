"""
Bot command menu management (aiogram 3.x).

Manages the Telegram bot command menu (hamburger menu + autocomplete).
"""
from typing import List

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

from src.telegram.i18n import t
from src.core.structured_logger import get_logger

logger = get_logger(__name__)

COMMAND_LIST = [
    'start',
    'subscribe',
    'status',
    'feedback',
    'language',
    'trader',
    'help',
]


def get_commands_for_language(lang: str) -> List[BotCommand]:
    """Return BotCommand list for the given language code."""
    commands = []
    for cmd in COMMAND_LIST:
        description = t(f'commands.{cmd}', lang)
        commands.append(BotCommand(command=cmd, description=description))
    return commands


async def setup_default_commands(bot: Bot) -> None:
    """Set default (English) command menu on bot startup."""
    try:
        en_commands = get_commands_for_language('en')
        await bot.set_my_commands(
            commands=en_commands,
            scope=BotCommandScopeDefault()
        )

        logger.info(f"Default commands set: {len(en_commands)} commands")

    except Exception as e:
        logger.error(f"Failed to setup default commands: {e}")


async def set_user_commands(bot: Bot, user_id: int, lang: str) -> bool:
    """Set per-user command menu (called on language switch). Returns True on success."""
    try:
        commands = get_commands_for_language(lang)
        await bot.set_my_commands(
            commands=commands,
            scope=BotCommandScopeChat(chat_id=user_id)
        )

        logger.info(f"User commands set: user_id={user_id}, lang={lang}")
        return True

    except Exception as e:
        logger.error(f"Failed to set user commands: user_id={user_id}, error={e}")
        return False


async def delete_user_commands(bot: Bot, user_id: int) -> bool:
    """Delete user-specific command menu (reverts to default). Returns True on success."""
    try:
        await bot.delete_my_commands(
            scope=BotCommandScopeChat(chat_id=user_id)
        )

        logger.info(f"User commands deleted: user_id={user_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to delete user commands: user_id={user_id}, error={e}")
        return False
