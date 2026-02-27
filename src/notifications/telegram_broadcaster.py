"""
Telegram unified push interface.

Responsibilities:
  - Unified management of Channel/Group/DM push
  - Smart routing: Channel preferred, fallback to Group
  - Uses global Application manager, reuses connection pool

Usage:
  broadcaster = TelegramBroadcaster()
  await broadcaster.send_to_channel("message", level='PREMIUM')
"""

import logging
from typing import Dict, Optional, List

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from src.core.config import settings
from src.notifications.telegram_app import get_bot, run_async
from src.telegram.utils.pinning import update_pinned_message


class TelegramBroadcaster:
    """Telegram unified push interface."""

    def __init__(self, bot: Bot = None):
        """Initialize broadcaster.

        Args:
            bot: Reuse an existing Bot instance; if None, get from global manager
        """
        self.logger = logging.getLogger(__name__)
        self.bot = bot if bot else get_bot()

        if self.bot:
            self.logger.info("TelegramBroadcaster using global Bot instance")
        else:
            self.logger.warning("TELEGRAM_BOT_TOKEN not configured, Bot unavailable")

        # Cache push targets
        self._signal_targets: Dict[str, str] = {}
        self._groups: Dict[str, str] = {}
        self._load_targets()

    def _load_targets(self) -> None:
        """Load push target config."""
        self._signal_targets = settings.get_all_signal_targets()
        # Groups replaced by Channels; use channels as groups
        self._groups = settings.get_all_telegram_channels()

        for level, target in self._signal_targets.items():
            if target:
                target_type = 'Channel' if settings.get_telegram_channel(level) else 'Group'
                self.logger.info(f"Signal push target {level}: {target_type} ({target})")

    @property
    def is_ready(self) -> bool:
        """Check if ready."""
        return self.bot is not None

    # ===== Core async methods =====

    async def send_to_channel(
        self,
        message: str,
        level: str = 'PREMIUM',
        parse_mode: str = 'HTML',
        photo_url: str = None,
        disable_notification: bool = False,
        pin: bool = False,
    ) -> Optional[int]:
        """Push to signal channel (Channel preferred, fallback to Group).

        Args:
            message: Message content
            level: 'BASIC' | 'PREMIUM'
            parse_mode: Parse mode
            photo_url: Optional image URL
            disable_notification: Send silently
            pin: Whether to pin the message

        Returns:
            message_id, or None on failure
        """
        if not self.is_ready:
            self.logger.warning("Bot not ready, skipping push")
            return None

        target = self._signal_targets.get(level)
        if not target:
            self.logger.warning(f"No push target configured for {level}")
            return None

        try:
            if photo_url:
                msg = await self.bot.send_photo(
                    chat_id=int(target),
                    photo=photo_url,
                    caption=message[:1024] if len(message) > 1024 else message,
                    parse_mode=parse_mode,
                    disable_notification=disable_notification,
                )
            else:
                msg = await self.bot.send_message(
                    chat_id=int(target),
                    text=message,
                    parse_mode=parse_mode,
                    disable_web_page_preview=True,
                    disable_notification=disable_notification,
                )
            self.logger.debug(f"Push succeeded [{level}]: msg_id={msg.message_id}")

            # Pin message if requested
            if pin and msg:
                await update_pinned_message(
                    bot=self.bot,
                    chat_id=int(target),
                    message_id=msg.message_id,
                    silent=True
                )

            return msg.message_id

        except TelegramAPIError as e:
            self.logger.error(f"Push failed [{level}]: {e}")
            return None

    async def send_to_group(
        self,
        message: str,
        level: str = 'PREMIUM',
        parse_mode: str = 'HTML',
    ) -> Optional[int]:
        """Push to discussion group (Group only, not Channel).

        Args:
            message: Message content
            level: 'BASIC' | 'PREMIUM'
            parse_mode: Parse mode

        Returns:
            message_id, or None on failure
        """
        if not self.is_ready:
            return None

        target = self._groups.get(level)
        if not target:
            self.logger.warning(f"No group configured for {level}")
            return None

        try:
            msg = await self.bot.send_message(
                chat_id=int(target),
                text=message,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            return msg.message_id
        except TelegramAPIError as e:
            self.logger.error(f"Group push failed [{level}]: {e}")
            return None

    async def send_to_user(
        self,
        user_id: int,
        message: str,
        parse_mode: str = 'HTML',
    ) -> Optional[int]:
        """Push to a user DM.

        Args:
            user_id: User Telegram ID
            message: Message content
            parse_mode: Parse mode

        Returns:
            message_id, or None on failure
        """
        if not self.is_ready:
            return None

        try:
            msg = await self.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=parse_mode,
            )
            return msg.message_id
        except TelegramAPIError as e:
            self.logger.error(f"DM push failed [user={user_id}]: {e}")
            return None

    async def broadcast_signal(
        self,
        message: str,
        levels: List[str] = None,
        photo_url: str = None,
        parse_mode: str = 'HTML',
    ) -> Dict[str, Optional[int]]:
        """Broadcast signal to multiple channels.

        Args:
            message: Message content
            levels: List of push levels, default ['PREMIUM']
            photo_url: Optional image
            parse_mode: Parse mode

        Returns:
            {level: message_id}
        """
        if levels is None:
            levels = ['PREMIUM']

        results = {}
        for level in levels:
            msg_id = await self.send_to_channel(
                message=message,
                level=level,
                photo_url=photo_url,
                parse_mode=parse_mode,
            )
            results[level] = msg_id

        return results

    # ===== Sync wrappers (use global event loop) =====

    def send_to_channel_sync(
        self,
        message: str,
        level: str = 'PREMIUM',
        parse_mode: str = 'HTML',
        photo_url: str = None,
        pin: bool = False,
    ) -> Optional[int]:
        """Sync version: push to signal channel."""
        return run_async(self.send_to_channel(message, level, parse_mode, photo_url, pin=pin))

    def send_to_group_sync(
        self,
        message: str,
        level: str = 'PREMIUM',
        parse_mode: str = 'HTML',
    ) -> Optional[int]:
        """Sync version: push to discussion group."""
        return run_async(self.send_to_group(message, level, parse_mode))

    def broadcast_signal_sync(
        self,
        message: str,
        levels: List[str] = None,
        photo_url: str = None,
    ) -> Dict[str, Optional[int]]:
        """Sync version: broadcast signal."""
        return run_async(self.broadcast_signal(message, levels, photo_url))


# Singleton
_broadcaster: Optional[TelegramBroadcaster] = None


def get_broadcaster() -> TelegramBroadcaster:
    """Get TelegramBroadcaster singleton."""
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = TelegramBroadcaster()
    return _broadcaster
