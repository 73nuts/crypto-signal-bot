"""
Performance image cache service.

Event-driven cache strategy:
- Caches Telegram file_id to avoid re-generating and re-uploading images
- Refreshes only when new CLOSED records appear in the positions table
- Staleness detected by comparing last_trade_id
"""

import logging
import threading
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PerformanceCache:
    """
    Performance image cache manager.

    Singleton; event-driven invalidation (not TTL); thread-safe.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._cache: Dict[str, Any] = {
            "file_id": None,
            "last_trade_id": 0,
            "trade_count": 0,
            "generated_at": None,
        }
        self._cache_lock = threading.Lock()
        self._initialized = True
        logger.info("PerformanceCache initialized")

    def get_cached_file_id(self) -> Optional[str]:
        """Return the cached Telegram file_id, or None if not cached."""
        with self._cache_lock:
            return self._cache.get("file_id")

    def is_cache_valid(self, current_last_trade_id: int, current_trade_count: int) -> bool:
        """
        Check cache validity by comparing latest trade ID and count.

        Returns True if cache is still valid, False if a refresh is needed.
        """
        with self._cache_lock:
            if not self._cache.get("file_id"):
                logger.debug("Cache invalid: file_id is empty")
                return False

            cached_id = self._cache.get("last_trade_id", 0)
            cached_count = self._cache.get("trade_count", 0)

            if current_last_trade_id > cached_id:
                logger.info(f"Cache stale: new trade {cached_id} -> {current_last_trade_id}")
                return False

            if current_trade_count != cached_count:
                logger.info(f"Cache stale: trade count {cached_count} -> {current_trade_count}")
                return False

            return True

    def update_cache(
        self,
        file_id: str,
        last_trade_id: int,
        trade_count: int
    ) -> None:
        """Update the cache with a new file_id, trade ID, and trade count."""
        with self._cache_lock:
            self._cache["file_id"] = file_id
            self._cache["last_trade_id"] = last_trade_id
            self._cache["trade_count"] = trade_count
            self._cache["generated_at"] = datetime.now().isoformat()

            logger.info(
                f"Cache updated: file_id={file_id[:20]}..., "
                f"last_trade_id={last_trade_id}, count={trade_count}"
            )

    def invalidate(self) -> None:
        """Manually invalidate the cache (e.g. forced refresh via admin command)."""
        with self._cache_lock:
            self._cache["file_id"] = None
            logger.info("Cache manually invalidated")

    def get_cache_info(self) -> Dict[str, Any]:
        """Return cache status dict (for debugging)."""
        with self._cache_lock:
            return {
                "has_cache": bool(self._cache.get("file_id")),
                "last_trade_id": self._cache.get("last_trade_id", 0),
                "trade_count": self._cache.get("trade_count", 0),
                "generated_at": self._cache.get("generated_at"),
            }


_performance_cache: Optional[PerformanceCache] = None


def get_performance_cache() -> PerformanceCache:
    """Return the PerformanceCache singleton."""
    global _performance_cache
    if _performance_cache is None:
        _performance_cache = PerformanceCache()
    return _performance_cache
