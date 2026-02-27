"""
Kick retry manager.

Responsibilities:
1. Record failed kicks to Redis
2. Retry on the next scheduled task run
3. Alert after max retries exhausted

Design:
- Redis JSON list: [{"telegram_id": 123, "channel_key": "PREMIUM_zh", "retry_count": 1}, ...]
- Max 3 retries; error-logged and admin-alerted after exhaustion
"""

import logging
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from src.core.cache import CacheBackend, get_cache
from src.telegram.alert_manager import alert_manager

logger = logging.getLogger(__name__)


@dataclass
class KickRetryItem:
    """A single kick retry record."""

    telegram_id: int
    channel_key: str  # e.g., "PREMIUM_zh"
    retry_count: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Optional["KickRetryItem"]:
        try:
            return cls(
                telegram_id=int(data["telegram_id"]),
                channel_key=str(data["channel_key"]),
                retry_count=int(data["retry_count"]),
            )
        except (KeyError, ValueError, TypeError):
            return None


class KickRetryManager:
    """Kick retry manager."""

    RETRY_KEY = "kick:retry:queue"
    MAX_RETRIES = 3
    TTL_SECONDS = 86400 * 7  # 7-day expiry

    def __init__(self):
        self._cache = get_cache().setup(CacheBackend.REDIS)

    async def add_failed_kick(
        self, telegram_id: int, failed_channels: Dict[str, bool]
    ) -> int:
        """Record failed channels for a kick. Returns the number of items added."""
        added = 0
        for channel_key, success in failed_channels.items():
            if not success:
                item = KickRetryItem(
                    telegram_id=telegram_id, channel_key=channel_key, retry_count=1
                )
                await self._add_item(item)
                added += 1
                logger.info(
                    f"Kick retry queued: telegram_id={telegram_id}, channel={channel_key}"
                )

        return added

    async def get_pending_retries(self) -> List[KickRetryItem]:
        """Return all pending kick retry items."""
        try:
            data = await self._cache.get(self.RETRY_KEY)
            if not data:
                return []

            items = []
            for item_dict in data:
                item = KickRetryItem.from_dict(item_dict)
                if item:
                    items.append(item)

            return items

        except Exception as e:
            logger.error(f"Failed to fetch retry queue: {e}")
            return []

    async def process_retry(self, item: KickRetryItem, success: bool) -> None:
        """Process a retry result: remove from queue, re-enqueue on failure, alert on exhaustion."""
        await self._remove_item(item)

        if success:
            logger.info(
                f"Kick retry succeeded: telegram_id={item.telegram_id}, "
                f"channel={item.channel_key}"
            )
        elif item.retry_count >= self.MAX_RETRIES:
            logger.error(
                f"Kick retries exhausted: telegram_id={item.telegram_id}, "
                f"channel={item.channel_key}, retries={item.retry_count}"
            )
            await alert_manager.notify_admin(
                f"Kick retries exhausted — manual action required\n"
                f"User: {item.telegram_id}\n"
                f"Channel: {item.channel_key}\n"
                f"Attempts: {item.retry_count}",
                "KICK_CRITICAL",
            )
        else:
            new_item = KickRetryItem(
                telegram_id=item.telegram_id,
                channel_key=item.channel_key,
                retry_count=item.retry_count + 1,
            )
            await self._add_item(new_item)
            logger.warning(
                f"Kick retry failed, re-queued: telegram_id={item.telegram_id}, "
                f"channel={item.channel_key}, next_retry={new_item.retry_count}"
            )

    async def get_stats(self) -> Dict[str, int]:
        """Return retry queue statistics."""
        try:
            items = await self.get_pending_retries()

            return {
                "pending_count": len(items),
                "unique_users": len(set(i.telegram_id for i in items)),
                "retry_1": sum(1 for i in items if i.retry_count == 1),
                "retry_2": sum(1 for i in items if i.retry_count == 2),
                "retry_3": sum(1 for i in items if i.retry_count == 3),
            }

        except Exception as e:
            logger.error(f"Failed to get retry stats: {e}")
            return {"pending_count": 0, "unique_users": 0}

    async def _add_item(self, item: KickRetryItem) -> None:
        """Add an item to the retry queue (deduplicates by user+channel)."""
        try:
            data = await self._cache.get(self.RETRY_KEY) or []

            data = [
                d
                for d in data
                if not (
                    d.get("telegram_id") == item.telegram_id
                    and d.get("channel_key") == item.channel_key
                )
            ]

            data.append(item.to_dict())

            await self._cache.set(self.RETRY_KEY, data, ttl=self.TTL_SECONDS)

        except Exception as e:
            logger.error(f"Failed to add to retry queue: {e}")

    async def _remove_item(self, item: KickRetryItem) -> None:
        """Remove an item from the retry queue."""
        try:
            data = await self._cache.get(self.RETRY_KEY) or []

            data = [
                d
                for d in data
                if not (
                    d.get("telegram_id") == item.telegram_id
                    and d.get("channel_key") == item.channel_key
                    and d.get("retry_count") == item.retry_count
                )
            ]

            if data:
                await self._cache.set(self.RETRY_KEY, data, ttl=self.TTL_SECONDS)
            else:
                await self._cache.delete(self.RETRY_KEY)

        except Exception as e:
            logger.error(f"Failed to remove from retry queue: {e}")

    async def clear_queue(self) -> None:
        """Clear the entire retry queue."""
        try:
            await self._cache.delete(self.RETRY_KEY)
            logger.info("Kick retry queue cleared")
        except Exception as e:
            logger.error(f"Failed to clear retry queue: {e}")


_kick_retry_manager: Optional[KickRetryManager] = None


def get_kick_retry_manager() -> KickRetryManager:
    """Return the KickRetryManager singleton."""
    global _kick_retry_manager
    if _kick_retry_manager is None:
        _kick_retry_manager = KickRetryManager()
    return _kick_retry_manager
