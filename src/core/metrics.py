"""
Performance metrics collector

Lightweight implementation with interface reserved for future Prometheus/OpenTelemetry integration.
"""
import asyncio
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Dict, Optional


@dataclass
class MetricPoint:
    """Metric data point"""
    count: int = 0
    total: float = 0.0
    min_value: float = float('inf')
    max_value: float = float('-inf')

    def record(self, value: float):
        self.count += 1
        self.total += value
        self.min_value = min(self.min_value, value)
        self.max_value = max(self.max_value, value)

    @property
    def avg(self) -> float:
        return self.total / self.count if self.count > 0 else 0.0


class MetricsCollector:
    """
    Metrics collector

    Supports:
    - Counter: monotonically increasing count
    - Histogram: latency distribution
    - Gauge: current value
    """

    _instance: Optional['MetricsCollector'] = None
    _lock = threading.Lock()

    def __new__(cls) -> 'MetricsCollector':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._counters: Dict[str, int] = defaultdict(int)
        self._histograms: Dict[str, MetricPoint] = {}
        self._gauges: Dict[str, float] = {}
        self._data_lock = threading.Lock()
        self._initialized = True

    def increment(self, name: str, value: int = 1, labels: Dict[str, str] = None):
        """Increment a counter"""
        key = self._make_key(name, labels)
        with self._data_lock:
            self._counters[key] += value

    def observe(self, name: str, value: float, labels: Dict[str, str] = None):
        """Record a histogram value"""
        key = self._make_key(name, labels)
        with self._data_lock:
            if key not in self._histograms:
                self._histograms[key] = MetricPoint()
            self._histograms[key].record(value)

    def set_gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        """Set a gauge value"""
        key = self._make_key(name, labels)
        with self._data_lock:
            self._gauges[key] = value

    def _make_key(self, name: str, labels: Dict[str, str] = None) -> str:
        """Generate metric key"""
        if not labels:
            return name
        label_str = ','.join(f'{k}={v}' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def get_metrics(self) -> Dict[str, Any]:
        """Get all metrics"""
        with self._data_lock:
            return {
                'counters': dict(self._counters),
                'histograms': {
                    k: {
                        'count': v.count,
                        'avg': v.avg,
                        'min': v.min_value if v.count > 0 else None,
                        'max': v.max_value if v.count > 0 else None,
                    }
                    for k, v in self._histograms.items()
                },
                'gauges': dict(self._gauges),
            }

    def reset(self):
        """Reset all metrics"""
        with self._data_lock:
            self._counters.clear()
            self._histograms.clear()
            self._gauges.clear()


# Global singleton
_metrics: Optional[MetricsCollector] = None
_metrics_lock = threading.Lock()


def get_metrics() -> MetricsCollector:
    """Get the metrics collector"""
    global _metrics
    if _metrics is None:
        with _metrics_lock:
            if _metrics is None:
                _metrics = MetricsCollector()
    return _metrics


def timed(name: str, labels: Dict[str, str] = None):
    """
    Timing decorator

    Usage:
        @timed("db_query_ms", labels={"table": "users"})
        async def get_user(user_id: int):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                elapsed = (time.perf_counter() - start) * 1000  # ms
                get_metrics().observe(name, elapsed, labels)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed = (time.perf_counter() - start) * 1000  # ms
                get_metrics().observe(name, elapsed, labels)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def counted(name: str, labels: Dict[str, str] = None):
    """
    Count decorator

    Usage:
        @counted("api_calls", labels={"endpoint": "/subscribe"})
        async def handle_subscribe():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            get_metrics().increment(name, labels=labels)
            return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            get_metrics().increment(name, labels=labels)
            return func(*args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
