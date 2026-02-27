"""
Idempotency decorator unit tests.

TDD: write tests first, then implement.

How to run:
    pytest tests/core_tests/test_idempotency.py -v
"""
import asyncio
import unittest
from unittest.mock import MagicMock, patch
import json


class TestIdempotentDecorator(unittest.TestCase):
    """Idempotency decorator tests."""

    def setUp(self):
        """Set up mock database."""
        self.db_patcher = patch('src.core.idempotency.get_db')
        self.mock_get_db = self.db_patcher.start()
        self.mock_db = MagicMock()
        self.mock_db.execute = MagicMock(return_value=None)
        self.mock_get_db.return_value = self.mock_db

    def tearDown(self):
        """Clean up."""
        self.db_patcher.stop()

    def test_first_call_executes_function(self):
        """First call executes the function."""
        from src.core.idempotency import idempotent

        execution_count = 0

        @idempotent(operation="test_op")
        async def my_function(order_id: str):
            nonlocal execution_count
            execution_count += 1
            return {"order_id": order_id, "processed": True}

        async def run_test():
            result = await my_function("order-123")
            return result

        result = asyncio.run(run_test())

        self.assertEqual(execution_count, 1)
        self.assertEqual(result["order_id"], "order-123")
        self.assertTrue(result["processed"])

    def test_cached_result_returned(self):
        """Cached result is returned on idempotency key hit."""
        from src.core.idempotency import idempotent

        execution_count = 0

        @idempotent(
            key_func=lambda order_id: f"process:{order_id}",
            operation="test_op"
        )
        async def my_function(order_id: str):
            nonlocal execution_count
            execution_count += 1
            return {"order_id": order_id, "count": execution_count}

        async def run_test():
            # First call: no cache
            self.mock_db.execute.return_value = None
            result1 = await my_function("order-123")

            # Second call: simulate cache hit
            self.mock_db.execute.return_value = {
                'response': json.dumps({"order_id": "order-123", "count": 1}),
                'status': 'COMPLETED'
            }
            result2 = await my_function("order-123")

            return result1, result2

        result1, result2 = asyncio.run(run_test())

        self.assertEqual(execution_count, 1)  # Only executed once
        self.assertEqual(result1["count"], 1)
        self.assertEqual(result2["count"], 1)

    def test_processing_status_raises(self):
        """PROCESSING status raises an exception."""
        from src.core.idempotency import idempotent

        @idempotent(
            key_func=lambda order_id: f"process:{order_id}",
            operation="test_op"
        )
        async def my_function(order_id: str):
            return {"processed": True}

        async def run_test():
            # Simulate in-progress
            self.mock_db.execute.return_value = {
                'status': 'PROCESSING'
            }
            await my_function("order-123")

        with self.assertRaises(RuntimeError) as cm:
            asyncio.run(run_test())

        self.assertIn("already in progress", str(cm.exception))

    def test_custom_key_func(self):
        """Custom idempotency key generation."""
        from src.core.idempotency import idempotent

        keys_used = []

        @idempotent(
            key_func=lambda order_id, user_id: f"order:{order_id}:user:{user_id}",
            operation="test_op"
        )
        async def my_function(order_id: str, user_id: int):
            return {"success": True}

        # Override execute to capture key
        original_execute = self.mock_db.execute

        def capture_key(sql, params, fetch=None):
            if 'idempotency_key' in sql and 'INSERT' in sql:
                keys_used.append(params[0])
            return None

        self.mock_db.execute.side_effect = capture_key

        async def run_test():
            await my_function("order-123", user_id=456)

        asyncio.run(run_test())

        self.assertEqual(len(keys_used), 1)
        self.assertEqual(keys_used[0], "order:order-123:user:456")

    def test_default_key_generation(self):
        """Default idempotency key generation (function name + args hash)."""
        from src.core.idempotency import idempotent

        @idempotent(operation="test_op")
        async def my_function(order_id: str):
            return {"success": True}

        keys_used = []

        def capture_key(sql, params, fetch=None):
            if 'idempotency_key' in sql and 'INSERT' in sql:
                keys_used.append(params[0])
            return None

        self.mock_db.execute.side_effect = capture_key

        async def run_test():
            await my_function("order-123")

        asyncio.run(run_test())

        self.assertEqual(len(keys_used), 1)
        self.assertTrue(keys_used[0].startswith("my_function:"))

    def test_failed_status_allows_retry(self):
        """FAILED status allows retry."""
        from src.core.idempotency import idempotent

        execution_count = 0

        @idempotent(
            key_func=lambda: "fixed-key",
            operation="test_op"
        )
        async def my_function():
            nonlocal execution_count
            execution_count += 1
            return {"count": execution_count}

        async def run_test():
            # Simulate previous failure
            self.mock_db.execute.return_value = {
                'status': 'FAILED'
            }
            result = await my_function()
            return result

        result = asyncio.run(run_test())

        # FAILED status should allow retry
        self.assertEqual(execution_count, 1)

    def test_ttl_hours_parameter(self):
        """TTL parameter is passed correctly."""
        from src.core.idempotency import idempotent

        sql_calls = []

        def capture_sql(sql, params, fetch=None):
            sql_calls.append((sql, params))
            return None

        self.mock_db.execute.side_effect = capture_sql

        @idempotent(operation="test_op", ttl_hours=48)
        async def my_function():
            return {"success": True}

        async def run_test():
            await my_function()

        asyncio.run(run_test())

        # Verify TTL parameter was passed
        update_calls = [c for c in sql_calls if 'INTERVAL' in c[0]]
        self.assertTrue(any(48 in c[1] for c in update_calls))


class TestIdempotencyKeyGeneration(unittest.TestCase):
    """Idempotency key generation tests."""

    def test_generate_key_from_args(self):
        """Generate idempotency key from arguments."""
        from src.core.idempotency import _generate_idempotency_key

        key1 = _generate_idempotency_key("my_func", ("arg1",), {"kwarg1": "value1"})
        key2 = _generate_idempotency_key("my_func", ("arg1",), {"kwarg1": "value1"})
        key3 = _generate_idempotency_key("my_func", ("arg2",), {"kwarg1": "value1"})

        # Same args should generate same key
        self.assertEqual(key1, key2)
        # Different args should generate different key
        self.assertNotEqual(key1, key3)

    def test_key_format(self):
        """Idempotency key format."""
        from src.core.idempotency import _generate_idempotency_key

        key = _generate_idempotency_key("my_func", (), {})

        # Format: {func_name}:{hash}
        self.assertTrue(key.startswith("my_func:"))
        self.assertEqual(len(key.split(":")), 2)


if __name__ == '__main__':
    unittest.main()
