"""
Phase 7: Tracing module tests

Tests for trace_id generation, context propagation, and decorator functionality
"""
import unittest
import asyncio
from unittest.mock import patch, MagicMock


class TestGenerateTraceId(unittest.TestCase):
    """trace_id generation tests"""

    def test_generate_trace_id_format(self):
        """trace_id should be a 16-character hex string"""
        from src.core.tracing import generate_trace_id

        trace_id = generate_trace_id()
        self.assertEqual(len(trace_id), 16)
        # Verify it is hexadecimal
        int(trace_id, 16)

    def test_generate_trace_id_unique(self):
        """Each generated trace_id should be unique"""
        from src.core.tracing import generate_trace_id

        ids = [generate_trace_id() for _ in range(100)]
        self.assertEqual(len(set(ids)), 100)

    def test_generate_span_id_format(self):
        """span_id should be an 8-character hex string"""
        from src.core.tracing import generate_span_id

        span_id = generate_span_id()
        self.assertEqual(len(span_id), 8)
        int(span_id, 16)


class TestContextVars(unittest.TestCase):
    """contextvars functionality tests"""

    def test_set_and_get_trace_id(self):
        """Set and get trace_id"""
        from src.core.tracing import set_trace_id, get_trace_id

        set_trace_id("abc123def456789")
        self.assertEqual(get_trace_id(), "abc123def456789")

    def test_set_and_get_user_id(self):
        """Set and get user_id"""
        from src.core.tracing import set_user_id, get_user_id

        set_user_id("12345")
        self.assertEqual(get_user_id(), "12345")

    def test_get_context_returns_all_fields(self):
        """get_context returns complete context"""
        from src.core.tracing import (
            set_trace_id, set_user_id, set_extra, get_context
        )

        set_trace_id("trace123")
        set_user_id("user456")
        set_extra("order_id", "order789")

        ctx = get_context()
        self.assertEqual(ctx.get('trace_id'), "trace123")
        self.assertEqual(ctx.get('user_id'), "user456")
        self.assertEqual(ctx.get('order_id'), "order789")


class TestTraceContext(unittest.TestCase):
    """TraceContext context manager tests"""

    def test_sync_context_manager(self):
        """Sync context manager"""
        from src.core.tracing import TraceContext, get_trace_id, get_user_id

        with TraceContext(trace_id="test123", user_id="456"):
            self.assertEqual(get_trace_id(), "test123")
            self.assertEqual(get_user_id(), "456")

    def test_auto_generate_trace_id(self):
        """Auto-generate trace_id when not provided"""
        from src.core.tracing import TraceContext, get_trace_id

        with TraceContext():
            trace_id = get_trace_id()
            self.assertEqual(len(trace_id), 16)

    def test_extra_context(self):
        """Extra context parameters"""
        from src.core.tracing import TraceContext, get_context

        with TraceContext(user_id="123", order_id="ord456", amount=99.9):
            ctx = get_context()
            self.assertEqual(ctx.get('order_id'), "ord456")
            self.assertEqual(ctx.get('amount'), 99.9)


class TestAsyncTraceContext(unittest.TestCase):
    """Async context manager tests"""

    def test_async_context_manager(self):
        """async with support"""
        async def run_test():
            from src.core.tracing import TraceContext, get_trace_id, get_user_id

            async with TraceContext(trace_id="async123", user_id="789"):
                self.assertEqual(get_trace_id(), "async123")
                self.assertEqual(get_user_id(), "789")

        asyncio.run(run_test())

    def test_context_propagation_across_async(self):
        """trace_id propagation across async boundaries"""
        async def inner_func():
            from src.core.tracing import get_trace_id
            return get_trace_id()

        async def outer_func():
            from src.core.tracing import TraceContext

            async with TraceContext(trace_id="propagate123"):
                # Get trace_id in inner coroutine
                return await inner_func()

        result = asyncio.run(outer_func())
        self.assertEqual(result, "propagate123")

    def test_nested_contexts(self):
        """Nested contexts"""
        async def run_test():
            from src.core.tracing import TraceContext, get_trace_id, get_user_id

            async with TraceContext(trace_id="outer", user_id="user1"):
                self.assertEqual(get_trace_id(), "outer")

                async with TraceContext(trace_id="inner", user_id="user2"):
                    self.assertEqual(get_trace_id(), "inner")
                    self.assertEqual(get_user_id(), "user2")

        asyncio.run(run_test())


class TestTraceContextDecorator(unittest.TestCase):
    """trace_context decorator tests"""

    def test_decorator_creates_context(self):
        """Decorator creates a trace context"""
        from src.core.tracing import trace_context, get_trace_id

        @trace_context()
        async def my_func():
            return get_trace_id()

        result = asyncio.run(my_func())
        self.assertEqual(len(result), 16)

    def test_decorator_extracts_user_id(self):
        """Decorator extracts user_id from arguments"""
        from src.core.tracing import trace_context, get_user_id

        @trace_context(user_id_arg='telegram_id')
        async def handle_message(telegram_id: int, text: str):
            return get_user_id()

        result = asyncio.run(handle_message(telegram_id=12345, text="hello"))
        self.assertEqual(result, "12345")


if __name__ == '__main__':
    unittest.main()
