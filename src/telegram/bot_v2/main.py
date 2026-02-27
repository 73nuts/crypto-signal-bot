"""
Telegram Bot entry point (aiogram 3.x).

Responsibilities:
1. Configure Dispatcher and Bot
2. Register Router tree
3. Configure Middleware stack
4. Manage lifecycle events
5. Set up Bot command menu
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault

from src.core.structured_logger import get_logger

# Middlewares
from .middlewares import AuthMiddleware, I18nMiddleware

# Routers
from .routers import (
    admin_router,
    errors_router,
    feedback_router,
    join_request_router,
    language_router,
    menu_router,
    sector_admin_router,
    subscription_router,
    trader_router,
    user_router,
)

logger = get_logger(__name__)


async def setup_commands(bot: Bot) -> None:
    """Set up Bot command menu."""
    commands_en = [
        BotCommand(command="start", description="Get started"),
        BotCommand(command="subscribe", description="View subscription plans"),
        BotCommand(command="status", description="Check your account"),
        BotCommand(command="feedback", description="Submit feedback"),
        BotCommand(command="language", description="Change language"),
        BotCommand(command="trader", description="Trader Program"),
        BotCommand(command="help", description="Help & support"),
    ]

    try:
        await bot.set_my_commands(commands_en, scope=BotCommandScopeDefault())
        logger.info("Bot commands set successfully")
    except Exception as e:
        logger.error(f"Failed to set bot commands: {e}")


async def setup_description(bot: Bot) -> None:
    """Set up Bot description (shown on the Bot info page)."""
    desc_en = (
        "Ignis Quant - AI-powered crypto trading signals.\n\n"
        "What we offer:\n"
        "- 24/7 Scanner (200+ coins)\n"
        "- Daily Pulse (AI market analysis)\n"
        "- Swing Signals (trend-following trades)\n\n"
        "Tap /start to begin."
    )

    try:
        await bot.set_my_description(desc_en)
        logger.info("Bot description set successfully")
    except Exception as e:
        logger.error(f"Failed to set bot description: {e}")


async def on_startup(bot: Bot) -> None:
    """Startup hook."""
    import asyncio

    from src.core.cache import CacheManager, get_cache

    logger.info("Bot starting...")

    # Set main event loop so sync cache operations dispatch correctly
    CacheManager.set_main_loop(asyncio.get_running_loop())

    get_cache().setup()
    logger.info("Cache initialized with main loop")

    await setup_commands(bot)
    await setup_description(bot)
    logger.info("Bot startup complete")


async def on_shutdown(bot: Bot) -> None:
    """Shutdown hook. Flushes DigestManager to ensure queued messages are sent."""
    logger.info("Bot shutting down...")

    # v2.2.0: Graceful Shutdown - flush DigestManager
    try:
        from src.notifications.priority import get_digest_manager
        digest_mgr = get_digest_manager()
        await digest_mgr.flush_all()
        digest_mgr.stop_timer()
        logger.info("DigestManager flushed on shutdown")
    except Exception as e:
        logger.warning(f"DigestManager flush failed: {e}")

    logger.info("Bot stopped")


def create_dispatcher() -> Dispatcher:
    """
    Create and configure the Dispatcher.

    Registration order:
    1. Middleware (outer registered first)
    2. Routers (by priority)
    """
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Middleware execution order: I18n -> Auth -> Handler
    dp.update.outer_middleware(I18nMiddleware())
    dp.update.outer_middleware(AuthMiddleware())
    logger.info("Middlewares registered: I18n, Auth")

    # 1. Error handler (highest priority)
    dp.include_router(errors_router)

    # 2. FSM state routers (before basic commands)
    dp.include_router(feedback_router)
    dp.include_router(trader_router)

    # 3. Basic user commands
    dp.include_router(user_router)
    dp.include_router(language_router)

    # 4. Subscription and payment
    dp.include_router(subscription_router)

    # 5. Admin features
    dp.include_router(admin_router)
    dp.include_router(sector_admin_router)

    # 6. Special event handlers
    dp.include_router(join_request_router)

    # 7. Menu router (last, as fallback)
    dp.include_router(menu_router)

    logger.info("All routers registered")

    return dp


def create_bot() -> Bot:
    """Create Bot instance."""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not configured")

    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )


async def main():
    """Entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger.info("Ignis Telegram Bot v2 (aiogram 3.x) starting...")

    bot = create_bot()
    dp = create_dispatcher()

    try:
        logger.info("Starting polling...")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
