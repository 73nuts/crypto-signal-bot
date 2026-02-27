"""
Cache manager unit tests

TDD Red Phase: write tests first, verify they fail
"""
import asyncio
import unittest


class TestCacheManager(unittest.TestCase):
    """Tests for CacheManager core functionality"""

    def setUp(self):
        """Reset cache before each test"""
        from src.core.cache import _reset_cache
        _reset_cache()

    def tearDown(self):
        """Reset cache after each test"""
        from src.core.cache import _reset_cache
        _reset_cache()

    def test_singleton_pattern(self):
        """CacheManager should be a singleton"""
        from src.core.cache import get_cache

        cache1 = get_cache()
        cache2 = get_cache()
        self.assertIs(cache1, cache2)

    def test_make_key_full(self):
        """make_key should generate a complete normalized key"""
        from src.core.cache import get_cache

        cache = get_cache()
        key = cache.make_key("scanner", "price", "BTCUSDT")
        # Format: {env}:{service}:{entity}:{identifier}
        self.assertIn("scanner", key)
        self.assertIn("price", key)
        self.assertIn("BTCUSDT", key)
        # Verify correct format
        parts = key.split(":")
        self.assertEqual(len(parts), 4)

    def test_make_key_without_identifier(self):
        """make_key without identifier should generate a 3-part key"""
        from src.core.cache import get_cache

        cache = get_cache()
        key = cache.make_key("system", "health")
        parts = key.split(":")
        self.assertEqual(len(parts), 3)

    def test_setup_memory_backend(self):
        """Should support memory backend configuration"""
        from src.core.cache import get_cache, CacheBackend

        cache = get_cache()
        result = cache.setup(CacheBackend.MEMORY)
        self.assertIs(result, cache)  # chained call

    def test_get_set_operations(self):
        """get/set operations should work correctly"""
        from src.core.cache import get_cache, CacheBackend

        cache = get_cache()
        cache.setup(CacheBackend.MEMORY)

        async def test():
            key = cache.make_key("test", "data", "123")
            # Set value
            success = await cache.set(key, {"value": 42}, ttl=60)
            self.assertTrue(success)
            # Get value
            result = await cache.get(key)
            self.assertEqual(result["value"], 42)

        asyncio.run(test())

    def test_get_nonexistent_key(self):
        """Getting a nonexistent key should return None"""
        from src.core.cache import get_cache, CacheBackend

        cache = get_cache()
        cache.setup(CacheBackend.MEMORY)

        async def test():
            result = await cache.get("nonexistent:key")
            self.assertIsNone(result)

        asyncio.run(test())

    def test_delete_operation(self):
        """delete operation should remove cache entry"""
        from src.core.cache import get_cache, CacheBackend

        cache = get_cache()
        cache.setup(CacheBackend.MEMORY)

        async def test():
            key = cache.make_key("test", "delete", "456")
            await cache.set(key, "to_be_deleted", ttl=60)
            # Verify exists
            self.assertIsNotNone(await cache.get(key))
            # Delete
            success = await cache.delete(key)
            self.assertTrue(success)
            # Verify deleted
            self.assertIsNone(await cache.get(key))

        asyncio.run(test())

    def test_health_check(self):
        """Health check should return True (memory backend)"""
        from src.core.cache import get_cache, CacheBackend

        cache = get_cache()
        cache.setup(CacheBackend.MEMORY)

        async def test():
            result = await cache.health_check()
            self.assertTrue(result)

        asyncio.run(test())


class TestCacheDecorators(unittest.TestCase):
    """Tests for cache decorators"""

    def setUp(self):
        from src.core.cache import _reset_cache, get_cache, CacheBackend
        _reset_cache()
        cache = get_cache()
        cache.setup(CacheBackend.MEMORY)

    def tearDown(self):
        from src.core.cache import _reset_cache
        _reset_cache()

    def test_cached_decorator_caches_result(self):
        """@cached decorator should cache function results"""
        from src.core.cache import cached

        call_count = 0

        @cached("test", "func", ttl=60)
        async def expensive_function(item_id: str) -> dict:
            nonlocal call_count
            call_count += 1
            return {"id": item_id, "computed": True}

        async def test():
            nonlocal call_count
            # First call
            result1 = await expensive_function("abc")
            self.assertEqual(call_count, 1)
            self.assertEqual(result1["id"], "abc")

            # Second call, should return from cache
            result2 = await expensive_function("abc")
            self.assertEqual(call_count, 1)  # not called again
            self.assertEqual(result2["id"], "abc")

            # Different argument, should execute function
            result3 = await expensive_function("def")
            self.assertEqual(call_count, 2)

        asyncio.run(test())

    def test_cache_invalidate_decorator(self):
        """@cache_invalidate decorator should invalidate cache"""
        from src.core.cache import cached, cache_invalidate, get_cache

        cache = get_cache()

        @cached("test", "item", ttl=60)
        async def get_item(item_id: str) -> dict:
            return {"id": item_id, "data": "original"}

        @cache_invalidate("test", "item")
        async def update_item(item_id: str, **data) -> bool:
            return True

        async def test():
            # Cache data
            await get_item("xyz")
            key = cache.make_key("test", "item", "xyz")
            self.assertIsNotNone(await cache.get(key))

            # Update operation should invalidate cache
            await update_item("xyz", new_data="updated")
            self.assertIsNone(await cache.get(key))

        asyncio.run(test())


class TestCacheMetrics(unittest.TestCase):
    """Tests for cache monitoring metrics"""

    def test_metrics_initial_state(self):
        """Initial state should be 0"""
        from src.core.cache import CacheMetrics

        metrics = CacheMetrics()
        self.assertEqual(metrics.hits, 0)
        self.assertEqual(metrics.misses, 0)
        self.assertEqual(metrics.errors, 0)

    def test_metrics_hit_ratio(self):
        """Hit ratio calculation should be correct"""
        from src.core.cache import CacheMetrics

        metrics = CacheMetrics()
        metrics.record_hit()
        metrics.record_hit()
        metrics.record_hit()
        metrics.record_miss()

        self.assertEqual(metrics.hits, 3)
        self.assertEqual(metrics.misses, 1)
        self.assertAlmostEqual(metrics.hit_ratio, 0.75, places=2)

    def test_metrics_hit_ratio_empty(self):
        """Hit ratio should be 0 when no data"""
        from src.core.cache import CacheMetrics

        metrics = CacheMetrics()
        self.assertEqual(metrics.hit_ratio, 0.0)

    def test_metrics_to_dict(self):
        """to_dict should return a complete metrics dict"""
        from src.core.cache import CacheMetrics

        metrics = CacheMetrics()
        metrics.record_hit()
        metrics.record_error()

        result = metrics.to_dict()
        self.assertIn('hits', result)
        self.assertIn('misses', result)
        self.assertIn('errors', result)
        self.assertIn('hit_ratio', result)


class TestCacheProtocol(unittest.TestCase):
    """Tests for CacheProtocol definition"""

    def test_cache_protocol_exists(self):
        """CacheProtocol should exist in protocols module"""
        from src.core.protocols import CacheProtocol
        self.assertTrue(hasattr(CacheProtocol, 'make_key'))
        self.assertTrue(hasattr(CacheProtocol, 'get'))
        self.assertTrue(hasattr(CacheProtocol, 'set'))
        self.assertTrue(hasattr(CacheProtocol, 'delete'))
        self.assertTrue(hasattr(CacheProtocol, 'health_check'))

    def test_cache_manager_satisfies_protocol(self):
        """CacheManager should satisfy CacheProtocol"""
        from src.core.protocols import CacheProtocol
        from src.core.cache import CacheManager

        cache = CacheManager()
        self.assertIsInstance(cache, CacheProtocol)


if __name__ == '__main__':
    unittest.main()
