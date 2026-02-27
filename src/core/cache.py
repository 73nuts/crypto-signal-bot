"""
Unified cache manager

Responsibilities:
1. Wrap cashews with a unified API
2. Normalize key naming
3. Collect monitoring metrics
4. Environment isolation
"""
import logging
from typing import Optional, Any, Callable, TypeVar, List
from functools import wraps
from enum import Enum

from cashews import cache

from src.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CacheBackend(Enum):
    """Cache backend type"""
    REDIS = "redis"
    MEMORY = "memory"


class CacheMetrics:
    """Cache monitoring metrics"""

    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.errors = 0

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def record_hit(self):
        self.hits += 1

    def record_miss(self):
        self.misses += 1

    def record_error(self):
        self.errors += 1

    def to_dict(self) -> dict:
        return {
            'hits': self.hits,
            'misses': self.misses,
            'errors': self.errors,
            'hit_ratio': f"{self.hit_ratio:.2%}",
        }


class CacheManager:
    """Unified cache manager"""

    _instance: Optional['CacheManager'] = None
    _main_loop: Optional[Any] = None  # Reference to the main event loop

    def __new__(cls) -> 'CacheManager':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self._env = os.getenv('ENVIRONMENT', 'dev')
        self._initialized = True
        self._setup_done = False
        self._metrics = CacheMetrics()
        logger.info(f"CacheManager singleton created, env={self._env}")

    def _capture_main_loop(self) -> None:
        """Capture the main event loop (called on first async operation)"""
        import asyncio
        if CacheManager._main_loop is None:
            try:
                CacheManager._main_loop = asyncio.get_running_loop()
                logger.debug("Captured main event loop for sync operations")
            except RuntimeError:
                pass  # Not in async context, capture later

    def _get_main_loop(self) -> Optional[Any]:
        """Get the main event loop"""
        import asyncio
        if CacheManager._main_loop is not None:
            # Verify the loop is still running
            try:
                if CacheManager._main_loop.is_running():
                    return CacheManager._main_loop
            except Exception:
                pass
        return None

    def setup(self, backend: CacheBackend = CacheBackend.REDIS) -> 'CacheManager':
        """
        Configure the cache backend

        Args:
            backend: Cache backend type
        """
        if self._setup_done:
            return self

        if backend == CacheBackend.REDIS:
            redis_host = settings.REDIS_HOST
            redis_port = settings.REDIS_PORT
            redis_password = settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else ''

            auth = f":{redis_password}@" if redis_password else ""
            redis_url = f"redis://{auth}{redis_host}:{redis_port}"

            cache.setup(
                redis_url,
                socket_timeout=5,
            )
            logger.info(f"Redis cache configured: {redis_host}:{redis_port}")
        else:
            cache.setup("mem://")
            logger.info("Memory cache configured")

        self._setup_done = True
        return self

    def make_key(self, service: str, entity: str, identifier: str = "") -> str:
        """
        Generate a normalized cache key

        Format: {env}:{service}:{entity}:{identifier}
        """
        parts = [self._env, service, entity]
        if identifier:
            parts.append(str(identifier))
        return ":".join(parts)

    async def get(self, key: str) -> Optional[Any]:
        """Get a cached value"""
        self._capture_main_loop()
        try:
            value = await cache.get(key)
            if value is not None:
                self._metrics.record_hit()
            else:
                self._metrics.record_miss()
            return value
        except Exception as e:
            self._metrics.record_error()
            logger.warning(f"Cache read failed key={key}: {e}")
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int = 300,
        tags: List[str] = None
    ) -> bool:
        """
        Set a cached value

        Args:
            key: Cache key
            value: Value to cache
            ttl: Expiry in seconds
            tags: Tag list (for bulk invalidation)
        """
        self._capture_main_loop()
        try:
            await cache.set(key, value, expire=ttl)
            return True
        except Exception as e:
            self._metrics.record_error()
            logger.warning(f"Cache write failed key={key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Delete a cached value"""
        try:
            await cache.delete(key)
            return True
        except Exception as e:
            self._metrics.record_error()
            logger.warning(f"Cache delete failed key={key}: {e}")
            return False

    async def setnx(self, key: str, value: Any, ttl: int = 30) -> bool:
        """
        Atomically set a value only if the key does not exist.

        Used for distributed locks and similar scenarios.
        Note: Simulated via exist+set using cashews, not a true atomic operation.
        Sufficient for low-concurrency cases; for high concurrency use Redis SETNX directly.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Expiry in seconds

        Returns:
            True: set successfully (key did not exist)
            False: set failed (key already exists)
        """
        self._capture_main_loop()
        try:
            # Check if key exists
            existing = await cache.get(key)
            if existing is not None:
                return False

            # Set the value
            await cache.set(key, value, expire=ttl)
            return True
        except Exception as e:
            self._metrics.record_error()
            logger.warning(f"Cache setnx failed key={key}: {e}")
            return False

    async def invalidate_by_tag(self, tag: str) -> int:
        """Bulk invalidate by tag"""
        try:
            return await cache.delete_tags(tag)
        except Exception as e:
            self._metrics.record_error()
            logger.warning(f"Tag invalidation failed tag={tag}: {e}")
            return 0

    async def invalidate_by_pattern(self, pattern: str) -> int:
        """Bulk invalidate by pattern (use with caution)"""
        try:
            return await cache.delete_match(pattern)
        except Exception as e:
            self._metrics.record_error()
            logger.warning(f"Pattern invalidation failed pattern={pattern}: {e}")
            return 0

    async def health_check(self) -> bool:
        """Health check"""
        try:
            test_key = self.make_key("system", "health", "check")
            await cache.set(test_key, "ok", expire=10)
            value = await cache.get(test_key)
            await cache.delete(test_key)
            return value == "ok"
        except Exception as e:
            logger.error(f"Cache health check failed: {e}")
            return False

    async def close(self):
        """Close cache connection"""
        await cache.close()
        logger.info("Cache connection closed")

    @property
    def metrics(self) -> CacheMetrics:
        """Get monitoring metrics"""
        return self._metrics

    # ==========================================
    # Sync adapters (for sync callers like AlertDetector)
    # ==========================================
    #
    # Design notes:
    # 1. Sync methods may be called from thread pools (e.g. aiogram run_sync/to_thread)
    # 2. Thread pool threads get a new event loop, not the main loop
    # 3. Redis connections are bound to the main loop; must schedule via run_coroutine_threadsafe
    # 4. _main_loop is captured on first async operation
    # ==========================================

    def _run_in_main_loop(self, coro) -> Any:
        """
        Execute a coroutine in the main event loop.

        Used when sync methods are called from thread pools.

        Args:
            coro: Coroutine object

        Returns:
            Coroutine result

        Raises:
            RuntimeError: Main loop not captured or not running
        """
        import asyncio

        main_loop = self._get_main_loop()
        if main_loop is not None:
            # Schedule on the main loop
            future = asyncio.run_coroutine_threadsafe(coro, main_loop)
            return future.result(timeout=5)

        # Main loop not captured, try the current thread's loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Current loop is running; cannot use run_until_complete
                # This should not happen (sync methods should not be called in async context)
                raise RuntimeError(
                    "Cannot run sync cache operation in async context. "
                    "Use async methods instead."
                )
            return loop.run_until_complete(coro)
        except RuntimeError:
            # No event loop; create a new one as last resort (may have connection issues)
            logger.warning("Creating new event loop for cache operation - may have connection issues")
            return asyncio.run(coro)

    def get_sync(self, key: str) -> Optional[Any]:
        """Synchronously get a cached value"""
        try:
            return self._run_in_main_loop(self.get(key))
        except Exception as e:
            self._metrics.record_error()
            logger.warning(f"Cache sync read failed key={key}: {e}")
            return None

    def set_sync(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Synchronously set a cached value"""
        try:
            return self._run_in_main_loop(self.set(key, value, ttl))
        except Exception as e:
            self._metrics.record_error()
            logger.warning(f"Cache sync write failed key={key}: {e}")
            return False

    def delete_sync(self, key: str) -> bool:
        """Synchronously delete a cached value"""
        try:
            return self._run_in_main_loop(self.delete(key))
        except Exception as e:
            self._metrics.record_error()
            logger.warning(f"Cache sync delete failed key={key}: {e}")
            return False

    def setnx_sync(self, key: str, value: Any, ttl: int = 30) -> bool:
        """
        Synchronously set a value only if the key does not exist.

        Used for distributed locks and similar scenarios.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Expiry in seconds

        Returns:
            True: set successfully (key did not exist)
            False: set failed (key already exists)

        Raises:
            Exception: Cache operation failed
        """
        try:
            return self._run_in_main_loop(self.setnx(key, value, ttl))
        except Exception as e:
            self._metrics.record_error()
            logger.warning(f"Cache sync setnx failed key={key}: {e}")
            raise

    @classmethod
    def set_main_loop(cls, loop) -> None:
        """
        Explicitly set the main event loop.

        Call this at application startup to ensure sync methods can schedule
        onto the main loop correctly.

        Usage:
            import asyncio
            from src.core.cache import CacheManager

            async def main():
                CacheManager.set_main_loop(asyncio.get_running_loop())
                # ... application code ...
        """
        cls._main_loop = loop
        logger.debug(f"Main event loop set explicitly: {loop}")

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton (for testing only)"""
        cls._instance = None
        cls._main_loop = None


# Global singleton
_cache_manager: Optional[CacheManager] = None


def get_cache() -> CacheManager:
    """Get the cache manager singleton"""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager


def _reset_cache():
    """Reset cache manager (for testing only)"""
    global _cache_manager
    CacheManager._reset_singleton()
    _cache_manager = None


def cached(
    service: str,
    entity: str,
    ttl: int = 300,
    key_builder: Callable[..., str] = None,
    tags: List[str] = None,
):
    """
    Cache decorator

    Usage:
        @cached("scanner", "price", ttl=600)
        async def get_price(symbol: str) -> float:
            ...

        @cached("telegram", "member", ttl=3600, key_builder=lambda uid: str(uid))
        async def get_member(user_id: int) -> dict:
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            cm = get_cache()

            # Generate cache key
            if key_builder:
                identifier = key_builder(*args, **kwargs)
            elif args:
                identifier = str(args[0])
            else:
                identifier = ""

            cache_key = cm.make_key(service, entity, identifier)

            # Try to get from cache
            cached_value = await cm.get(cache_key)
            if cached_value is not None:
                return cached_value

            # Cache miss: execute the original function
            result = await func(*args, **kwargs)

            # Write to cache
            if result is not None:
                await cm.set(cache_key, result, ttl=ttl, tags=tags)

            return result

        return wrapper
    return decorator


def cache_invalidate(
    service: str,
    entity: str,
    key_builder: Callable[..., str] = None,
):
    """
    Cache invalidation decorator

    Usage:
        @cache_invalidate("telegram", "member", key_builder=lambda uid, **kw: str(uid))
        async def update_member(user_id: int, **data) -> bool:
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            # Execute the original function first
            result = await func(*args, **kwargs)

            # Invalidate cache
            cm = get_cache()
            if key_builder:
                identifier = key_builder(*args, **kwargs)
            elif args:
                identifier = str(args[0])
            else:
                identifier = ""

            cache_key = cm.make_key(service, entity, identifier)
            await cm.delete(cache_key)

            return result

        return wrapper
    return decorator
