"""
MySQL connection pool module

Singleton connection pool based on DBUtils.PooledDB, addressing:
1. "Too many connections" caused by frequent connection creation
2. Connection leak issues
3. Connection timeout without reconnect

Usage:
    from src.core.database import get_db

    # Option 1: connection context manager
    with get_db().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users")

    # Option 2: transaction context manager
    with get_db().transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users ...")
            cur.execute("UPDATE accounts ...")
        # auto-commit; rollback on exception

    # Option 3: shorthand execute
    rows = get_db().execute("SELECT * FROM users", fetch='all')
    row = get_db().execute("SELECT * FROM users WHERE id=%s", (1,), fetch='one')
    affected = get_db().execute("UPDATE users SET name=%s", ('test',), fetch=None)
"""

import logging
import threading
from contextlib import contextmanager
from typing import Any, Optional, Literal

import pymysql
from pymysql.cursors import DictCursor
from dbutils.pooled_db import PooledDB

from src.core.config import settings

logger = logging.getLogger(__name__)


class DatabasePool:
    """
    MySQL connection pool singleton

    Features:
    - Thread-safe singleton
    - Lazy pool initialization
    - Auto ping to detect disconnects
    - Automatic connection return to pool
    """

    _instance: Optional['DatabasePool'] = None
    _lock = threading.Lock()

    def __new__(cls) -> 'DatabasePool':
        """Thread-safe singleton"""
        if cls._instance is None:
            with cls._lock:
                # Double-check locking
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._pool = None
                    instance._config = None
                    cls._instance = instance
        return cls._instance

    @property
    def pool(self) -> PooledDB:
        """Lazy-load connection pool"""
        if self._pool is None:
            self._config = settings.get_mysql_config()
            self._pool = PooledDB(
                creator=pymysql,
                maxconnections=20,      # max connections
                mincached=2,            # min idle connections
                maxcached=10,           # max idle connections
                maxshared=0,            # disable shared connections (thread safety)
                blocking=True,          # block when pool is exhausted
                maxusage=None,          # max reuse per connection (None = unlimited)
                ping=1,                 # ping check on each acquire
                host=self._config['host'],
                port=self._config['port'],
                user=self._config['user'],
                password=self._config['password'],
                database=self._config['database'],
                charset='utf8mb4',
                cursorclass=DictCursor,
                autocommit=False,       # disabled by default; caller controls commits
            )
            logger.info(
                f"DatabasePool initialized: "
                f"host={self._config['host']}, "
                f"database={self._config['database']}, "
                f"max_connections=20"
            )
        return self._pool

    def get_connection(self) -> pymysql.Connection:
        """
        Get a connection from the pool.

        Note: Must call conn.close() after use to return to pool.
        Prefer the connection() context manager instead.
        """
        return self.pool.connection()

    @contextmanager
    def connection(self):
        """
        Connection context manager; auto-returns connection to pool.

        Usage:
            with db.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(...)
        """
        conn = self.get_connection()
        try:
            yield conn
        finally:
            conn.close()  # Return to pool

    @contextmanager
    def transaction(self):
        """
        Transaction context manager; commits on success, rolls back on exception.

        Usage:
            with db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT ...")
                    cur.execute("UPDATE ...")
                # auto-commit
        """
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(
        self,
        sql: str,
        params: Optional[tuple] = None,
        fetch: Optional[Literal['all', 'one']] = 'all'
    ) -> Any:
        """
        Convenience method to execute SQL.

        Args:
            sql: SQL statement
            params: Parameter tuple
            fetch: 'all' returns all rows, 'one' returns one row, None returns affected row count

        Returns:
            fetch='all': List[Dict]
            fetch='one': Dict or None
            fetch=None: int (affected rows)
        """
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if fetch == 'all':
                    return cur.fetchall()
                elif fetch == 'one':
                    return cur.fetchone()
                else:
                    conn.commit()  # Write operations need commit
                    return cur.rowcount

    def execute_insert(
        self,
        sql: str,
        params: Optional[tuple] = None
    ) -> int:
        """
        Execute INSERT and return the auto-increment ID.

        Args:
            sql: INSERT statement
            params: Parameter tuple

        Returns:
            lastrowid: Auto-increment ID of the inserted record
        """
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                last_id = cur.lastrowid
                conn.commit()
                return last_id

    def health_check(self) -> bool:
        """
        Health check.

        Returns:
            True: database is available
            False: database is unavailable
        """
        try:
            with self.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    result = cur.fetchone()
                    return result is not None
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False


def get_db() -> DatabasePool:
    """
    Get the database connection pool instance.

    Usage:
        from src.core.database import get_db

        db = get_db()
        with db.connection() as conn:
            ...
    """
    return DatabasePool()
