"""
Telegram VIP signal sender (sync wrapper).

Wraps the async VipSignalSender as a synchronous interface for unified Notifier calls.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from aiogram import Bot

from src.core.config import settings
from src.telegram.database.base import DatabaseManager
from src.telegram.vip_signal_sender import VipSignalSender


class TelegramSender:
    """Telegram VIP signal sender."""

    # action -> signal_type mapping
    ACTION_TYPE_MAP = {
        'LONG': 'SWING',
        'SHORT': 'SWING',
        'SELL': 'SWING',
    }

    def __init__(self):
        """Initialize Telegram sender."""
        self.logger = logging.getLogger(__name__)
        self.enabled = False
        self.bot: Optional[Bot] = None
        self.vip_sender: Optional[VipSignalSender] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._init_bot()

    def _init_bot(self):
        """Initialize Bot instance."""
        token = settings.get_secret('TELEGRAM_BOT_TOKEN')
        if not token:
            self.logger.warning("TELEGRAM_BOT_TOKEN not configured, Telegram push disabled")
            return

        # Check channel config (signals pushed to Premium Channel)
        premium_channels = settings.get_channels_by_level('PREMIUM')
        has_premium = any(premium_channels.values())

        if not has_premium:
            self.logger.warning("TELEGRAM_CHANNEL_PREMIUM not configured, Telegram push disabled")
            return

        try:
            self.bot = Bot(token=token)
            db_manager = DatabaseManager()
            self.vip_sender = VipSignalSender(self.bot, db_manager)
            self.enabled = True
            self.logger.info(f"Telegram push enabled: PREMIUM Channels={premium_channels}")
        except Exception as e:
            self.logger.error(f"Telegram initialization failed: {e}")
            self.enabled = False

    def send_signal(self, signal_data: Dict[str, Any]) -> bool:
        """Send VIP signal to Telegram Channel.

        Args:
            signal_data: Raw signal dict, must contain 'action' field

        Returns:
            True: Send succeeded
            False: Send failed or disabled
        """
        if not self.enabled or not self.vip_sender:
            self.logger.debug("Telegram push not enabled, skipping")
            return False

        # Determine signal type from action
        action = signal_data.get('action', '')
        signal_type = self.ACTION_TYPE_MAP.get(action)

        if not signal_type:
            self.logger.warning(f"Unknown action type: {action}, skipping Telegram push")
            return False

        try:
            # Use persistent event loop to avoid "Event loop is closed" errors.
            # asyncio.run() creates and closes a new loop each time, causing httpx state issues.
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)

            result = self._loop.run_until_complete(
                self.vip_sender.send_signal(signal_data, signal_type)
            )
            if result:
                self.logger.info(f"Telegram signal sent: {action} -> {signal_type}")
            return result
        except Exception as e:
            self.logger.error(f"Telegram push error: {e}")
            return False

    def is_enabled(self) -> bool:
        """Check if enabled."""
        return self.enabled

    def send_tp_sl_notification(
        self,
        symbol: str,
        event_type: str,
        price: float,
        pnl: float,
        pnl_percent: float,
        signal_id: int = None,
        position_id: int = None,
        signal_type: str = 'SWING'
    ) -> bool:
        """Send take-profit/stop-loss notification.

        Sync wrapper for PositionManager calls.

        Args:
            symbol: Asset symbol
            event_type: Event type 'TP1' | 'TP2' | 'SL'
            price: Trigger price
            pnl: Realized PnL (USD)
            pnl_percent: Realized PnL percentage
            signal_id: Original signal ID (for reply)
            position_id: Position ID
            signal_type: Signal type 'SWING'

        Returns:
            True: Send succeeded
            False: Send failed or disabled
        """
        if not self.enabled or not self.vip_sender:
            self.logger.debug("Telegram push not enabled, skipping TP/SL notification")
            return False

        try:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)

            result = self._loop.run_until_complete(
                self.vip_sender.send_tp_sl_notification(
                    symbol=symbol,
                    event_type=event_type,
                    price=price,
                    pnl=pnl,
                    pnl_percent=pnl_percent,
                    signal_id=signal_id,
                    position_id=position_id,
                    signal_type=signal_type
                )
            )
            if result:
                self.logger.info(
                    f"Telegram TP/SL notification sent: {event_type} {symbol}"
                )
            return result
        except Exception as e:
            self.logger.error(f"Telegram TP/SL notification error: {e}")
            return False
