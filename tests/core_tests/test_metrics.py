"""
Phase 7: Metrics module tests

Tests for Counter/Histogram/Gauge metric collection and decorator functionality
"""
import asyncio
import threading
import time
import unittest


class TestMetricsCollector(unittest.TestCase):
    """MetricsCollector basic functionality tests"""

    def setUp(self):
        """Reset metrics before each test"""
        from src.core.metrics import get_metrics
        get_metrics().reset()

    def test_counter_increment(self):
        """Counter increment"""
        from src.core.metrics import get_metrics

        metrics = get_metrics()
        metrics.increment("requests_total")
        metrics.increment("requests_total")
        metrics.increment("requests_total", value=3)

        result = metrics.get_metrics()
        self.assertEqual(result['counters']['requests_total'], 5)

    def test_counter_with_labels(self):
        """Counter with labels"""
        from src.core.metrics import get_metrics

        metrics = get_metrics()
        metrics.increment("http_requests", labels={"method": "GET", "status": "200"})
        metrics.increment("http_requests", labels={"method": "POST", "status": "201"})
        metrics.increment("http_requests", labels={"method": "GET", "status": "200"})

        result = metrics.get_metrics()
        self.assertEqual(result['counters']['http_requests{method=GET,status=200}'], 2)
        self.assertEqual(result['counters']['http_requests{method=POST,status=201}'], 1)

    def test_histogram_observe(self):
        """Histogram observation"""
        from src.core.metrics import get_metrics

        metrics = get_metrics()
        metrics.observe("request_duration_ms", 10.5)
        metrics.observe("request_duration_ms", 20.3)
        metrics.observe("request_duration_ms", 15.0)

        result = metrics.get_metrics()
        hist = result['histograms']['request_duration_ms']
        self.assertEqual(hist['count'], 3)
        self.assertAlmostEqual(hist['avg'], 15.27, places=1)
        self.assertEqual(hist['min'], 10.5)
        self.assertEqual(hist['max'], 20.3)

    def test_gauge_set(self):
        """Gauge set value"""
        from src.core.metrics import get_metrics

        metrics = get_metrics()
        metrics.set_gauge("active_connections", 10)
        metrics.set_gauge("active_connections", 15)

        result = metrics.get_metrics()
        self.assertEqual(result['gauges']['active_connections'], 15)

    def test_reset_clears_all(self):
        """reset clears all metrics"""
        from src.core.metrics import get_metrics

        metrics = get_metrics()
        metrics.increment("counter1")
        metrics.observe("histogram1", 10.0)
        metrics.set_gauge("gauge1", 5)

        metrics.reset()
        result = metrics.get_metrics()

        self.assertEqual(result['counters'], {})
        self.assertEqual(result['histograms'], {})
        self.assertEqual(result['gauges'], {})


class TestMetricsSingleton(unittest.TestCase):
    """Singleton pattern tests"""

    def test_singleton_instance(self):
        """get_metrics returns the same instance"""
        from src.core.metrics import get_metrics

        m1 = get_metrics()
        m2 = get_metrics()
        self.assertIs(m1, m2)


class TestMetricsThreadSafety(unittest.TestCase):
    """Thread safety tests"""

    def setUp(self):
        from src.core.metrics import get_metrics
        get_metrics().reset()

    def test_concurrent_increments(self):
        """Concurrent counter increments"""
        from src.core.metrics import get_metrics

        metrics = get_metrics()
        threads = []

        def increment_100_times():
            for _ in range(100):
                metrics.increment("concurrent_counter")

        for _ in range(10):
            t = threading.Thread(target=increment_100_times)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        result = metrics.get_metrics()
        self.assertEqual(result['counters']['concurrent_counter'], 1000)


class TestTimedDecorator(unittest.TestCase):
    """@timed decorator tests"""

    def setUp(self):
        from src.core.metrics import get_metrics
        get_metrics().reset()

    def test_timed_async_function(self):
        """Async function timing"""
        from src.core.metrics import get_metrics, timed

        @timed("async_operation_ms")
        async def slow_operation():
            await asyncio.sleep(0.01)
            return "done"

        result = asyncio.run(slow_operation())
        self.assertEqual(result, "done")

        metrics = get_metrics().get_metrics()
        hist = metrics['histograms']['async_operation_ms']
        self.assertEqual(hist['count'], 1)
        self.assertGreater(hist['avg'], 5)  # at least 5ms

    def test_timed_sync_function(self):
        """Sync function timing"""
        from src.core.metrics import get_metrics, timed

        @timed("sync_operation_ms")
        def fast_operation():
            time.sleep(0.01)
            return 42

        result = fast_operation()
        self.assertEqual(result, 42)

        metrics = get_metrics().get_metrics()
        hist = metrics['histograms']['sync_operation_ms']
        self.assertEqual(hist['count'], 1)
        self.assertGreater(hist['avg'], 5)

    def test_timed_with_labels(self):
        """Timing with labels"""
        from src.core.metrics import get_metrics, timed

        @timed("db_query_ms", labels={"table": "users"})
        def query_users():
            return ["user1", "user2"]

        query_users()
        metrics = get_metrics().get_metrics()
        self.assertIn('db_query_ms{table=users}', metrics['histograms'])


class TestCountedDecorator(unittest.TestCase):
    """@counted decorator tests"""

    def setUp(self):
        from src.core.metrics import get_metrics
        get_metrics().reset()

    def test_counted_async_function(self):
        """Async function call counting"""
        from src.core.metrics import counted, get_metrics

        @counted("api_calls")
        async def api_handler():
            return {"status": "ok"}

        for _ in range(5):
            asyncio.run(api_handler())

        metrics = get_metrics().get_metrics()
        self.assertEqual(metrics['counters']['api_calls'], 5)

    def test_counted_sync_function(self):
        """Sync function call counting"""
        from src.core.metrics import counted, get_metrics

        @counted("function_calls")
        def my_function():
            return True

        for _ in range(3):
            my_function()

        metrics = get_metrics().get_metrics()
        self.assertEqual(metrics['counters']['function_calls'], 3)

    def test_counted_with_labels(self):
        """Call counting with labels"""
        from src.core.metrics import counted, get_metrics

        @counted("endpoint_calls", labels={"endpoint": "/subscribe"})
        def subscribe_handler():
            pass

        subscribe_handler()
        subscribe_handler()

        metrics = get_metrics().get_metrics()
        self.assertEqual(metrics['counters']['endpoint_calls{endpoint=/subscribe}'], 2)


if __name__ == '__main__':
    unittest.main()
