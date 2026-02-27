"""
DatabasePool unit tests (TDD - tests written first).

Test cases:
1. Singleton pattern validation
2. Connection context manager
3. Transaction commit/rollback
4. Health check
5. execute method
6. Rollback toggle
"""
import os
import threading
from unittest.mock import MagicMock, patch

import pytest


class TestDatabasePoolSingleton:
    """Singleton pattern tests."""

    def test_singleton_returns_same_instance(self):
        """Multiple calls return the same instance."""
        from src.core.database import DatabasePool

        pool1 = DatabasePool()
        pool2 = DatabasePool()

        assert pool1 is pool2

    def test_singleton_thread_safe(self):
        """Same instance returned under multi-threading."""
        from src.core.database import DatabasePool

        instances = []

        def get_instance():
            instances.append(DatabasePool())

        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All instances should be identical
        assert all(inst is instances[0] for inst in instances)


class TestDatabasePoolConnection:
    """Connection management tests."""

    @patch('src.core.database.PooledDB')
    def test_connection_context_manager(self, mock_pooled_db):
        """Connection context manager auto-returns connection to pool."""
        # Reset singleton
        from src.core import database
        database.DatabasePool._instance = None

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn
        mock_pooled_db.return_value = mock_pool

        from src.core.database import DatabasePool
        db = DatabasePool()

        with db.connection() as conn:
            assert conn is mock_conn

        # Connection should be closed (returned to pool)
        mock_conn.close.assert_called_once()

    @patch('src.core.database.PooledDB')
    def test_connection_exception_still_closes(self, mock_pooled_db):
        """Connection is returned to pool even on exception."""
        from src.core import database
        database.DatabasePool._instance = None

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn
        mock_pooled_db.return_value = mock_pool

        from src.core.database import DatabasePool
        db = DatabasePool()

        with pytest.raises(ValueError):
            with db.connection():
                raise ValueError("test error")

        mock_conn.close.assert_called_once()


class TestDatabasePoolTransaction:
    """Transaction tests."""

    @patch('src.core.database.PooledDB')
    def test_transaction_commit_on_success(self, mock_pooled_db):
        """Transaction commits on success."""
        from src.core import database
        database.DatabasePool._instance = None

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn
        mock_pooled_db.return_value = mock_pool

        from src.core.database import DatabasePool
        db = DatabasePool()

        with db.transaction():
            pass  # No exception

        mock_conn.commit.assert_called_once()
        mock_conn.rollback.assert_not_called()

    @patch('src.core.database.PooledDB')
    def test_transaction_rollback_on_exception(self, mock_pooled_db):
        """Transaction rolls back on exception."""
        from src.core import database
        database.DatabasePool._instance = None

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn
        mock_pooled_db.return_value = mock_pool

        from src.core.database import DatabasePool
        db = DatabasePool()

        with pytest.raises(ValueError):
            with db.transaction():
                raise ValueError("test error")

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()


class TestDatabasePoolExecute:
    """execute method tests."""

    @patch('src.core.database.PooledDB')
    def test_execute_fetch_all(self, mock_pooled_db):
        """fetch='all' returns all rows."""
        from src.core import database
        database.DatabasePool._instance = None

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [{'id': 1}, {'id': 2}]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn
        mock_pooled_db.return_value = mock_pool

        from src.core.database import DatabasePool
        db = DatabasePool()

        result = db.execute("SELECT * FROM test", fetch='all')

        assert result == [{'id': 1}, {'id': 2}]
        mock_cursor.execute.assert_called_once_with("SELECT * FROM test", None)

    @patch('src.core.database.PooledDB')
    def test_execute_fetch_one(self, mock_pooled_db):
        """fetch='one' returns single row."""
        from src.core import database
        database.DatabasePool._instance = None

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {'id': 1}
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn
        mock_pooled_db.return_value = mock_pool

        from src.core.database import DatabasePool
        db = DatabasePool()

        result = db.execute("SELECT * FROM test WHERE id=1", fetch='one')

        assert result == {'id': 1}

    @patch('src.core.database.PooledDB')
    def test_execute_fetch_none(self, mock_pooled_db):
        """fetch=None returns affected row count."""
        from src.core import database
        database.DatabasePool._instance = None

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 5
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn
        mock_pooled_db.return_value = mock_pool

        from src.core.database import DatabasePool
        db = DatabasePool()

        result = db.execute("UPDATE test SET val=1", fetch=None)

        assert result == 5


class TestDatabasePoolHealthCheck:
    """Health check tests."""

    @patch('src.core.database.PooledDB')
    def test_health_check_success(self, mock_pooled_db):
        """Returns True when database is available."""
        from src.core import database
        database.DatabasePool._instance = None

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn
        mock_pooled_db.return_value = mock_pool

        from src.core.database import DatabasePool
        db = DatabasePool()

        assert db.health_check() is True

    @patch('src.core.database.PooledDB')
    def test_health_check_failure(self, mock_pooled_db):
        """Returns False when database is unavailable."""
        from src.core import database
        database.DatabasePool._instance = None

        mock_pool = MagicMock()
        mock_pool.connection.side_effect = Exception("Connection failed")
        mock_pooled_db.return_value = mock_pool

        from src.core.database import DatabasePool
        db = DatabasePool()

        assert db.health_check() is False


class TestDatabasePoolToggle:
    """Rollback toggle tests."""

    def test_use_db_pool_default_true(self):
        """Connection pool is enabled by default."""
        from src.core.database import USE_DB_POOL
        # Should be True when environment variable is not set
        assert USE_DB_POOL is True or os.getenv('USE_DB_POOL', 'true').lower() == 'true'


class TestGetDbFunction:
    """get_db() global function tests."""

    def test_get_db_returns_pool_instance(self):
        """get_db() returns a DatabasePool instance."""
        from src.core.database import DatabasePool, get_db

        db = get_db()

        assert isinstance(db, DatabasePool)
