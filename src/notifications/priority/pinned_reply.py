"""
Pinned message reply manager.

Records the Daily Pulse pinned message ID for each channel,
so P0 messages can reply to the pinned message for context association.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class PinnedMessageReplyManager:
    """Pinned message reply manager.

    - Records the day's pinned message ID (Daily Pulse) for each channel
    - Provides pinned ID when sending P0 messages for reply
    - Redis persistence (TTL=25 hours, covers cross-day scenarios)
    """

    # Redis key prefix
    KEY_PREFIX = "pinned_message"
    TTL_SECONDS = 25 * 3600  # 25 hours

    def __init__(self, cache_manager=None):
        """Initialize manager.

        Args:
            cache_manager: CacheManager instance (optional)
        """
        self._cache = cache_manager
        self._use_redis = cache_manager is not None

        # In-memory storage (fallback): {chat_id: {date: message_id}}
        self._memory_store: Dict[int, Dict[str, int]] = {}

        logger.info(
            f"PinnedMessageReplyManager initialized, "
            f"use_redis={self._use_redis}"
        )

    def _make_key(self, chat_id: int, date: str) -> str:
        """Generate Redis key.

        Format: pinned_message:{chat_id}:{date}
        """
        return f"{self.KEY_PREFIX}:{chat_id}:{date}"

    def _get_today_date(self) -> str:
        """Get today's date string (UTC)."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def set_pinned_message(self, chat_id: int, message_id: int) -> None:
        """Record pinned message ID (sync version, for non-async context).

        Args:
            chat_id: Channel/group ID
            message_id: Pinned message ID
        """
        date = self._get_today_date()
        key = self._make_key(chat_id, date)

        if self._use_redis:
            try:
                self._cache.set_sync(key, str(message_id), ttl=self.TTL_SECONDS)
                logger.info(
                    f"[PinnedReply] Recorded pinned message: "
                    f"chat_id={chat_id}, msg_id={message_id}, date={date}"
                )
                return
            except Exception as e:
                logger.warning(f"Redis set failed, fallback to memory: {e}")

        # Memory fallback
        if chat_id not in self._memory_store:
            self._memory_store[chat_id] = {}
        self._memory_store[chat_id][date] = message_id
        logger.info(
            f"[PinnedReply] Recorded pinned message (memory): "
            f"chat_id={chat_id}, msg_id={message_id}, date={date}"
        )

    async def set_pinned_message_async(self, chat_id: int, message_id: int) -> None:
        """Record pinned message ID (async version, for async context).

        Args:
            chat_id: Channel/group ID
            message_id: Pinned message ID
        """
        date = self._get_today_date()
        key = self._make_key(chat_id, date)

        if self._use_redis:
            try:
                await self._cache.set(key, str(message_id), ttl=self.TTL_SECONDS)
                logger.info(
                    f"[PinnedReply] Recorded pinned message: "
                    f"chat_id={chat_id}, msg_id={message_id}, date={date}"
                )
                return
            except Exception as e:
                logger.warning(f"Redis async set failed, fallback to memory: {e}")

        # Memory fallback
        if chat_id not in self._memory_store:
            self._memory_store[chat_id] = {}
        self._memory_store[chat_id][date] = message_id
        logger.info(
            f"[PinnedReply] Recorded pinned message (memory): "
            f"chat_id={chat_id}, msg_id={message_id}, date={date}"
        )

    def get_pinned_message(self, chat_id: int) -> Optional[int]:
        """Get today's pinned message ID (sync version, for non-async context).

        Args:
            chat_id: Channel/group ID

        Returns:
            Message ID, or None if not found
        """
        date = self._get_today_date()
        key = self._make_key(chat_id, date)

        if self._use_redis:
            try:
                value = self._cache.get_sync(key)
                if value:
                    return int(value)
            except Exception as e:
                logger.warning(f"Redis get failed, fallback to memory: {e}")

        # Memory fallback
        if chat_id in self._memory_store:
            return self._memory_store[chat_id].get(date)

        return None

    async def get_pinned_message_async(self, chat_id: int) -> Optional[int]:
        """Get today's pinned message ID (async version, for async context).

        Args:
            chat_id: Channel/group ID

        Returns:
            Message ID, or None if not found
        """
        date = self._get_today_date()
        key = self._make_key(chat_id, date)

        if self._use_redis:
            try:
                value = await self._cache.get(key)
                if value:
                    return int(value)
            except Exception as e:
                logger.warning(f"Redis async get failed, fallback to memory: {e}")

        # Memory fallback
        if chat_id in self._memory_store:
            return self._memory_store[chat_id].get(date)

        return None

    def clear_pinned_message(self, chat_id: int) -> None:
        """Clear pinned message record (admin function).

        Args:
            chat_id: Channel/group ID
        """
        date = self._get_today_date()
        key = self._make_key(chat_id, date)

        if self._use_redis:
            try:
                self._cache.delete_sync(key)
            except Exception as e:
                logger.warning(f"Redis delete failed: {e}")

        # Clear from memory
        if chat_id in self._memory_store:
            self._memory_store[chat_id].pop(date, None)

        logger.info(f"[PinnedReply] Cleared pinned message for chat_id={chat_id}")

    def get_status(self) -> Dict[str, int]:
        """Get status (for debugging).

        Returns:
            {chat_id: message_id} (today only)
        """
        date = self._get_today_date()
        result = {}

        for chat_id, dates in self._memory_store.items():
            if date in dates:
                result[str(chat_id)] = dates[date]

        return result


# ==========================================
# Global singleton
# ==========================================

_pinned_reply_manager: Optional[PinnedMessageReplyManager] = None


def get_pinned_reply_manager() -> PinnedMessageReplyManager:
    """Get global PinnedMessageReplyManager instance."""
    global _pinned_reply_manager
    if _pinned_reply_manager is None:
        try:
            from src.core.cache import CacheManager
            cache = CacheManager()
            _pinned_reply_manager = PinnedMessageReplyManager(cache_manager=cache)
        except Exception as e:
            logger.warning(f"CacheManager initialization failed, using memory mode: {e}")
            _pinned_reply_manager = PinnedMessageReplyManager(cache_manager=None)
    return _pinned_reply_manager
