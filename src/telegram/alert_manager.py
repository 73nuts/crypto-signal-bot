"""
Alert manager.

Layer 6: Monitoring and alerting layer MVP.

Features:
1. Payment exception alerts
2. Amount mismatch alerts
3. Kick failure alerts
4. API error alerts

Delivery: sends Telegram messages directly to the admin.
Supports both sync and async invocation.

Deduplication:
- Uses Redis for dedup; same alert type sent at most once every 5 minutes
- Shared dedup state across containers
- Falls back to sending on Redis failure (alerting is better than silence)
"""

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from src.core.cache import CacheBackend, get_cache
from src.core.config import settings


class AlertManager:
    """Alert manager (singleton)."""

    _instance: Optional['AlertManager'] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, bot: Optional[Bot] = None):
        """
        Initialize the alert manager.

        Args:
            bot: Telegram Bot instance (optional, can be injected later)
        """
        if self._initialized:
            return

        self.logger = logging.getLogger(__name__)
        self.bot = bot
        self.admin_id = settings.ADMIN_TELEGRAM_ID

        # Dedup config
        self._dedup_ttl = 300  # 5-minute dedup window
        self._cache = None  # lazy init

        if not self.admin_id:
            self.logger.warning("ADMIN_TELEGRAM_ID not configured, alerts disabled")
        else:
            self.logger.info(f"Alert manager initialized: admin_id={self.admin_id}")

        self._initialized = True

    def _get_cache(self):
        """Lazily initialize the cache manager."""
        if self._cache is None:
            self._cache = get_cache().setup(CacheBackend.REDIS)
        return self._cache

    def _make_dedup_key(self, error_type: str, error_msg: str) -> str:
        """
        Build the dedup key.

        Format: alert:dedup:{error_type}:{msg_hash_8chars}
        Uses the first 8 chars of the message hash to avoid excessive Redis memory.
        """
        msg_hash = hashlib.md5(error_msg.encode()).hexdigest()[:8]
        return f"alert:dedup:{error_type}:{msg_hash}"

    async def _should_send(self, error_type: str, error_msg: str) -> bool:
        """
        Check whether the alert should be sent (dedup check).

        Returns:
            True: should send (key absent or Redis failure)
            False: skip (same alert sent within the last 5 minutes)
        """
        try:
            cache = self._get_cache()
            dedup_key = self._make_dedup_key(error_type, error_msg)

            existing = await cache.get(dedup_key)
            if existing is not None:
                self.logger.debug(f"Alert dedup skip: {dedup_key}")
                return False

            await cache.set(dedup_key, "1", ttl=self._dedup_ttl)
            return True

        except Exception as e:
            # Fall through on Redis failure (alerting is better than silence)
            self.logger.warning(f"Alert dedup check failed, falling through: {e}")
            return True

    def _should_send_sync(self, error_type: str, error_msg: str) -> bool:
        """
        Synchronous dedup check.

        Returns:
            True: should send
            False: skip
        """
        try:
            cache = self._get_cache()
            dedup_key = self._make_dedup_key(error_type, error_msg)

            existing = cache.get_sync(dedup_key)
            if existing is not None:
                self.logger.debug(f"Alert dedup skip: {dedup_key}")
                return False

            cache.set_sync(dedup_key, "1", ttl=self._dedup_ttl)
            return True

        except Exception as e:
            self.logger.warning(f"Alert dedup check failed, falling through: {e}")
            return True

    def set_bot(self, bot: Bot):
        """Inject a Bot instance."""
        self.bot = bot

    async def notify_admin(self, error_msg: str, error_type: str = "SYSTEM"):
        """
        Send an alert message to the admin.

        Args:
            error_msg: Error message
            error_type: Error category (PAYMENT/AMOUNT/GROUP/API/SYSTEM)

        Deduplication:
            Same alert type is sent at most once per 5 minutes to avoid flooding.
        """
        if not self.admin_id:
            self.logger.warning(f"Alert not sent (no admin ID): [{error_type}] {error_msg}")
            return

        if not self.bot:
            self.logger.warning(f"Alert not sent (no Bot instance): [{error_type}] {error_msg}")
            return

        if not await self._should_send(error_type, error_msg):
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        alert_text = (
            f"[!] <b>SYSTEM ALERT</b>\n\n"
            f"<b>Type:</b> {error_type}\n"
            f"<b>Time:</b> {timestamp}\n"
            f"<b>Detail:</b>\n<code>{error_msg}</code>"
        )

        try:
            await self.bot.send_message(
                chat_id=self.admin_id,
                text=alert_text,
                parse_mode='HTML'
            )
            self.logger.info(f"Alert sent: [{error_type}] {error_msg[:50]}...")
        except TelegramAPIError as e:
            self.logger.error(f"Failed to send alert: {e}")

    async def alert_payment_error(self, order_id: str, error: str):
        """Alert on payment processing failure."""
        await self.notify_admin(
            f"Order {order_id} payment processing failed\nReason: {error}",
            "PAYMENT"
        )

    async def alert_amount_mismatch(
        self,
        order_id: str,
        expected: str,
        actual: str
    ):
        """Alert on amount mismatch."""
        await self.notify_admin(
            f"Order {order_id} amount mismatch\n"
            f"Expected: {expected} USDT\n"
            f"Actual: {actual} USDT\n"
            f"Diff: {float(expected) - float(actual):.2f} USDT",
            "AMOUNT"
        )

    async def alert_kick_failed(self, user_id: int, group_id: int, error: str):
        """Alert on kick failure."""
        await self.notify_admin(
            f"Failed to kick user\n"
            f"User ID: {user_id}\n"
            f"Group ID: {group_id}\n"
            f"Reason: {error}",
            "GROUP"
        )

    async def alert_api_error(self, api_name: str, error: str):
        """Alert on API call failure."""
        await self.notify_admin(
            f"API call failed: {api_name}\nReason: {error}",
            "API"
        )

    async def alert_callback_failed(
        self,
        order_id: str,
        telegram_id: int,
        error: str
    ):
        """Alert on callback failure (compensation task created)."""
        await self.notify_admin(
            f"Payment callback failed, compensation task created\n"
            f"Order: {order_id}\n"
            f"User: {telegram_id}\n"
            f"Reason: {error}",
            "CALLBACK"
        )

    async def alert_callback_final_failed(
        self,
        order_id: str,
        telegram_id: int,
        retry_count: int
    ):
        """Alert on callback final failure (manual intervention required)."""
        await self.notify_admin(
            f"Callback retries exhausted, manual intervention required!\n"
            f"Order: {order_id}\n"
            f"User: {telegram_id}\n"
            f"Retried: {retry_count} times\n"
            f"Check the callback_retry_tasks table",
            "CALLBACK_CRITICAL"
        )

    async def alert_critical(self, message: str):
        """Critical error alert."""
        await self.notify_admin(message, "CRITICAL")

    # ============================================
    # Sync wrappers (for use in synchronous context)
    # ============================================
    def _run_async(self, coro):
        """Run an async coroutine from a synchronous context."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Event loop already running: schedule as fire-and-forget task
                asyncio.create_task(coro)
            else:
                loop.run_until_complete(coro)
        except RuntimeError:
            asyncio.run(coro)

    def sync_alert_payment_error(self, order_id: str, error: str):
        """Sync: payment processing error alert."""
        self._run_async(self.alert_payment_error(order_id, error))

    def sync_alert_amount_mismatch(self, order_id: str, expected: str, actual: str):
        """Sync: amount mismatch alert."""
        self._run_async(self.alert_amount_mismatch(order_id, expected, actual))

    def sync_alert_api_error(self, api_name: str, error: str):
        """Sync: API call error alert."""
        self._run_async(self.alert_api_error(api_name, error))

    def sync_alert_callback_failed(
        self,
        order_id: str,
        telegram_id: int,
        error: str
    ):
        """Sync: callback failure alert."""
        self._run_async(self.alert_callback_failed(order_id, telegram_id, error))

    def sync_alert_callback_final_failed(
        self,
        order_id: str,
        telegram_id: int,
        retry_count: int
    ):
        """Sync: callback final failure alert."""
        self._run_async(self.alert_callback_final_failed(
            order_id, telegram_id, retry_count
        ))

    def sync_alert_critical(self, message: str):
        """Sync: critical error alert."""
        self._run_async(self.alert_critical(message))


# Global singleton
alert_manager = AlertManager()
