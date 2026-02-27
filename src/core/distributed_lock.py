"""
Distributed lock module

Redis-based distributed lock to prevent concurrent duplicate operations such as duplicate order creation.

Design:
1. Use SET NX EX atomic operation to acquire lock
2. TTL auto-expiry prevents deadlocks
3. Supports both sync and async modes
4. Raises RedisUnavailableError on Redis failure; caller decides degradation strategy

Exceptions:
- LockAcquireError: Lock is already held (normal contention)
- RedisUnavailableError: Redis unavailable (needs degradation handling)
"""
import logging
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class LockAcquireError(Exception):
    """Cannot acquire lock (lock already held)"""
    pass


class RedisUnavailableError(Exception):
    """Redis unavailable (connection failure, timeout, etc.)"""
    pass


class DistributedLock:
    """
    Distributed lock

    Usage (sync):
        from src.core.distributed_lock import DistributedLock
        from src.core.cache import get_cache

        cache = get_cache().setup()
        lock = DistributedLock(cache, "order:create:123:BASIC_M")

        with lock:
            # critical section
            create_order(...)

    Usage (async):
        async with lock:
            await create_order(...)
    """

    def __init__(
        self,
        cache,
        key: str,
        ttl: int = 30,
        retry_times: int = 0,
        retry_delay: float = 0.1
    ):
        """
        Initialize distributed lock.

        Args:
            cache: CacheManager instance
            key: Unique lock identifier (automatically prefixed with 'lock:')
            ttl: Lock expiry in seconds (default 30)
            retry_times: Retry count on lock failure (default 0, no retry)
            retry_delay: Retry interval in seconds (default 0.1)
        """
        self.cache = cache
        self.key = f"lock:{key}"
        self.ttl = ttl
        self.retry_times = retry_times
        self.retry_delay = retry_delay
        self._locked = False

    def acquire(self) -> bool:
        """
        Acquire lock synchronously.

        Returns:
            True: acquired successfully
            False: acquire failed (lock already held)

        Raises:
            RedisUnavailableError: Redis unavailable; caller decides degradation strategy
        """
        for attempt in range(self.retry_times + 1):
            try:
                # Use setnx atomic operation
                success = self.cache.setnx_sync(self.key, "1", ttl=self.ttl)
                if success:
                    self._locked = True
                    logger.debug(f"Lock acquired: {self.key}")
                    return True

                if attempt < self.retry_times:
                    time.sleep(self.retry_delay)

            except Exception as e:
                # Redis unavailable; raise so caller can decide on degradation
                logger.error(f"Lock acquire error (Redis unavailable): {self.key}, error={e}")
                raise RedisUnavailableError(f"Redis unavailable: {self.key}") from e

        logger.warning(f"Lock acquire failed (already held): {self.key}")
        return False

    def release(self) -> None:
        """Release lock synchronously"""
        if self._locked:
            try:
                self.cache.delete_sync(self.key)
                logger.debug(f"Lock released: {self.key}")
            except Exception as e:
                logger.warning(f"Lock release error: {self.key}, error={e}")
            finally:
                self._locked = False

    async def acquire_async(self) -> bool:
        """
        Acquire lock asynchronously.

        Returns:
            True: acquired successfully
            False: acquire failed (lock already held)

        Raises:
            RedisUnavailableError: Redis unavailable; caller decides degradation strategy
        """
        import asyncio

        for attempt in range(self.retry_times + 1):
            try:
                success = await self.cache.setnx(self.key, "1", ttl=self.ttl)
                if success:
                    self._locked = True
                    logger.debug(f"Lock acquired: {self.key}")
                    return True

                if attempt < self.retry_times:
                    await asyncio.sleep(self.retry_delay)

            except Exception as e:
                # Redis unavailable; raise so caller can decide on degradation
                logger.error(f"Lock acquire error (Redis unavailable): {self.key}, error={e}")
                raise RedisUnavailableError(f"Redis unavailable: {self.key}") from e

        logger.warning(f"Lock acquire failed (already held): {self.key}")
        return False

    async def release_async(self) -> None:
        """Release lock asynchronously"""
        if self._locked:
            try:
                await self.cache.delete(self.key)
                logger.debug(f"Lock released: {self.key}")
            except Exception as e:
                logger.warning(f"Lock release error: {self.key}, error={e}")
            finally:
                self._locked = False

    def __enter__(self):
        """Sync context manager entry"""
        if not self.acquire():
            raise LockAcquireError(f"Cannot acquire lock: {self.key}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Sync context manager exit"""
        self.release()
        return False

    async def __aenter__(self):
        """Async context manager entry"""
        if not await self.acquire_async():
            raise LockAcquireError(f"Cannot acquire lock: {self.key}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.release_async()
        return False


@contextmanager
def order_creation_lock(cache, telegram_id: int, plan_code: str):
    """
    Convenience wrapper for order creation lock.

    Usage:
        from src.core.distributed_lock import order_creation_lock
        from src.core.cache import get_cache

        cache = get_cache().setup()

        with order_creation_lock(cache, telegram_id, plan_code):
            # create order
            ...

    Args:
        cache: CacheManager instance
        telegram_id: Telegram user ID
        plan_code: Plan code

    Raises:
        LockAcquireError: Cannot acquire lock
    """
    lock_key = f"order:create:{telegram_id}:{plan_code}"
    lock = DistributedLock(cache, lock_key, ttl=30)

    if not lock.acquire():
        raise LockAcquireError(
            f"Order creation lock failed, please retry: telegram_id={telegram_id}"
        )

    try:
        yield lock
    finally:
        lock.release()
