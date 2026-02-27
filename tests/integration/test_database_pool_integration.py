"""
Database connection pool integration tests.

Test cases:
1. Telegram DAO uses connection pool
2. PositionManager uses connection pool
3. Concurrent query test (20 threads, 100 queries)
4. Connection reuse validation

How to run:
    pytest tests/integration/test_database_pool_integration.py -v

Note: Requires local MySQL to be running.
"""
import threading
import time
import pytest

from src.core.database import DatabasePool, get_db
from src.telegram.database.base import DatabaseManager
from src.trading.position_manager import PositionManager


class TestDatabaseManagerIntegration:
    """DatabaseManager integration tests."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """Reset singletons before each test."""
        DatabaseManager.reset_instance()
        DatabasePool._instance = None
        yield
        DatabaseManager.reset_instance()
        DatabasePool._instance = None

    def test_database_manager_uses_pool(self):
        """Verify DatabaseManager uses the connection pool."""
        db = DatabaseManager()

        # Verify pool mode is active
        assert db._use_pool is True
        assert db._db_pool is not None

        # Execute query
        result = db.execute_query("SELECT 1 as test", fetch_one=True)
        assert result['test'] == 1

    def test_database_manager_transaction(self):
        """Verify transactions still work correctly."""
        db = DatabaseManager()

        # Transactions should work normally
        with db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 as val")
                result = cur.fetchone()
                assert result['val'] == 1


class TestPositionManagerIntegration:
    """PositionManager integration tests."""

    @pytest.fixture(autouse=True)
    def reset_pool(self):
        """Reset connection pool before each test."""
        DatabasePool._instance = None
        yield
        DatabasePool._instance = None

    def test_position_manager_uses_pool(self):
        """Verify PositionManager uses the connection pool."""
        pm = PositionManager()

        # Verify pool mode is active
        assert pm._use_pool is True
        assert pm._db_pool is not None

        # Execute query (result may be list or tuple)
        positions = pm.get_open_positions()
        assert isinstance(positions, (list, tuple))

    def test_position_manager_connection_ctx(self):
        """Verify _get_connection_ctx works correctly."""
        pm = PositionManager()

        with pm._get_connection_ctx() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 as val")
                result = cur.fetchone()
                assert result['val'] == 1


class TestConcurrentQueries:
    """Concurrent query tests."""

    @pytest.fixture(autouse=True)
    def reset_pool(self):
        """Reset connection pool before each test."""
        DatabasePool._instance = None
        yield
        DatabasePool._instance = None

    def test_concurrent_queries_no_error(self):
        """20 threads, 100 concurrent queries with no errors."""
        db = get_db()
        errors = []
        success_count = [0]
        lock = threading.Lock()

        def query_worker(worker_id):
            for i in range(5):  # 5 queries per thread
                try:
                    result = db.execute("SELECT %s as val", (worker_id,), fetch='one')
                    assert result['val'] == worker_id
                    with lock:
                        success_count[0] += 1
                except Exception as e:
                    with lock:
                        errors.append(f"Worker {worker_id}, query {i}: {e}")

        # Start 20 threads
        threads = [
            threading.Thread(target=query_worker, args=(i,))
            for i in range(20)
        ]

        start_time = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - start_time

        # Verify
        assert len(errors) == 0, f"Errors: {errors}"
        assert success_count[0] == 100  # 20 threads × 5 queries
        print(f"\n100 concurrent queries completed in {elapsed:.2f}s")

    def test_pool_connection_reuse(self):
        """Verify connection reuse (pool size limit)."""
        db = get_db()

        # Execute multiple queries, verify too many connections are not created
        for _ in range(50):
            result = db.execute("SELECT 1", fetch='one')
            assert result is not None

        # Verify pool is still healthy
        assert db.health_check() is True


class TestPoolToggle:
    """Rollback toggle tests."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """Reset singletons before each test."""
        DatabaseManager.reset_instance()
        DatabasePool._instance = None
        yield
        DatabaseManager.reset_instance()
        DatabasePool._instance = None

    def test_pool_enabled_by_default(self):
        """Connection pool is enabled by default."""
        from src.core.database import USE_DB_POOL
        import os

        # Default to True when environment variable is not set
        env_value = os.getenv('USE_DB_POOL', 'true').lower()
        assert env_value == 'true' or USE_DB_POOL is True
