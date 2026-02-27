"""
Telegram Bot singleton manager (aiogram).

Provides a global unique Bot instance for notification push.

Architecture:
- Migrated from python-telegram-bot to aiogram 3.x
- Removed Application mode, uses Bot instance directly
- Maintains interface compatibility

Usage:
    from src.notifications.telegram_app import get_bot, run_async

    # Get Bot instance
    bot = get_bot()

    # Execute async task from sync code
    run_async(bot.send_message(chat_id=xxx, text='hello'))
"""

import asyncio
import logging
from typing import Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.core.config import settings

logger = logging.getLogger(__name__)

# Global singletons
_bot: Optional[Bot] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_initialized: bool = False


def _get_token() -> Optional[str]:
    """Get Telegram Bot Token."""
    return settings.get_secret("TELEGRAM_BOT_TOKEN")


def get_bot() -> Optional[Bot]:
    """Get the global Bot singleton.

    Returns:
        Bot instance, or None if token is not configured
    """
    global _bot, _initialized

    if _initialized:
        return _bot

    token = _get_token()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not configured, Bot unavailable")
        _initialized = True
        return None

    try:
        # Create Bot instance (aiogram 3.x)
        _bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

        logger.info("Telegram Bot initialized (aiogram 3.x)")
        _initialized = True
        return _bot

    except Exception as e:
        logger.error(f"Telegram Bot initialization failed: {e}")
        _initialized = True
        return None


def get_loop() -> asyncio.AbstractEventLoop:
    """Get the persistent event loop.

    Note: This loop is not auto-closed; lifecycle is managed externally.

    Returns:
        Event loop instance
    """
    global _loop

    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        logger.debug("Created new persistent event loop")

    return _loop


def run_async(coro):
    """Execute an async coroutine from sync or async context.

    Detects whether an event loop is already running:
    - No running loop: uses run_until_complete()
    - Running loop: uses create_task() to schedule

    Args:
        coro: Async coroutine

    Returns:
        Coroutine result (sync mode) or Task object (async mode)
    """
    try:
        loop = asyncio.get_running_loop()
        # Already in an event loop, schedule with create_task
        return asyncio.create_task(coro)
    except RuntimeError:
        # No running loop, use traditional approach
        loop = get_loop()
        return loop.run_until_complete(coro)


def is_ready() -> bool:
    """Check if Telegram service is available."""
    return get_bot() is not None


# Compatibility shim for old interface
def get_application():
    """[Deprecated] Returns None to maintain interface compatibility.

    aiogram does not use Application mode; use Bot directly.
    """
    logger.warning("get_application() is deprecated, use get_bot()")
    return None


# Module-level convenience functions
async def send_message(chat_id: int, text: str, **kwargs):
    """Convenience function to send a message."""
    bot = get_bot()
    if bot is None:
        logger.warning("Bot unavailable, skipping send")
        return None
    return await bot.send_message(chat_id=chat_id, text=text, **kwargs)


async def send_photo(chat_id: int, photo, **kwargs):
    """Convenience function to send a photo."""
    bot = get_bot()
    if bot is None:
        logger.warning("Bot unavailable, skipping send")
        return None
    return await bot.send_photo(chat_id=chat_id, photo=photo, **kwargs)


async def close_bot() -> None:
    """Close Bot instance and release aiohttp session resources.

    Should be called before program exit to avoid "Unclosed client session" warnings.
    """
    global _bot, _initialized

    if _bot is not None:
        try:
            await _bot.session.close()
            logger.info("Telegram Bot session closed")
        except Exception as e:
            logger.warning(f"Error closing Bot session: {e}")
        finally:
            _bot = None
            _initialized = False
