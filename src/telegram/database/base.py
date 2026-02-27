"""
Database connection management base class.

Provides:
- Connection pool management (backed by DatabasePool)
- Transaction support (context manager)
- Unified exception handling
"""

import logging
from typing import Optional, Dict, List
from contextlib import contextmanager

import pymysql

from src.core.database import get_db


class DatabaseManager:
    """
    Database connection manager.

    Thin proxy over DatabasePool providing a unified database access interface.
    """

    _instance: Optional['DatabaseManager'] = None

    def __new__(cls, *args, **kwargs):
        """Singleton."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        host: Optional[str] = None,      # deprecated, kept for compatibility
        port: Optional[int] = None,      # deprecated, kept for compatibility
        user: Optional[str] = None,      # deprecated, kept for compatibility
        password: Optional[str] = None,  # deprecated, kept for compatibility
        database: Optional[str] = None   # deprecated, kept for compatibility
    ):
        """
        Initialize database manager.

        Note:
            All parameters are deprecated; kept for backwards compatibility.
            Internally uses DatabasePool; config is read from settings.
        """
        if self._initialized:
            return

        self.logger = logging.getLogger(__name__)
        self._db_pool = get_db()
        self._initialized = True
        self.logger.debug("DatabaseManager initialized (connection pool mode)")

    def get_connection(self) -> pymysql.Connection:
        """
        Acquire a connection from the pool.

        Returns:
            pymysql.Connection: connection; caller must call close() to return it
        """
        return self._db_pool.get_connection()

    @contextmanager
    def transaction(self):
        """
        Transaction context manager.

        Usage:
            with db.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(...)
                # auto-commit or rollback
        """
        conn = None
        try:
            conn = self.get_connection()
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Transaction rolled back: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def execute_query(
        self,
        sql: str,
        params: tuple = None,
        fetch_one: bool = False
    ) -> Optional[List[Dict] | Dict]:
        """
        Execute a SELECT query (auto-commit).

        Args:
            sql: SQL statement
            params: parameter tuple
            fetch_one: if True return one row, else return list

        Returns:
            Query result
        """
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, params)
            conn.commit()

            if fetch_one:
                return cursor.fetchone()
            return cursor.fetchall()

        except pymysql.Error as e:
            self.logger.error(f"Query failed: {e}, SQL: {sql}")
            raise
        finally:
            if conn:
                conn.close()

    def execute_update(
        self,
        sql: str,
        params: tuple = None
    ) -> int:
        """
        Execute an INSERT/UPDATE/DELETE statement.

        Args:
            sql: SQL statement
            params: parameter tuple

        Returns:
            affected_rows: number of rows affected
        """
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            affected = cursor.execute(sql, params)
            conn.commit()
            return affected

        except pymysql.Error as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Update failed: {e}, SQL: {sql}")
            raise
        finally:
            if conn:
                conn.close()

    def execute_insert(
        self,
        sql: str,
        params: tuple = None
    ) -> int:
        """
        Execute an INSERT and return the auto-increment ID.

        Args:
            sql: INSERT statement
            params: parameter tuple

        Returns:
            last_insert_id: auto-increment ID of the inserted row
        """
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, params)
            last_id = cursor.lastrowid
            conn.commit()
            return last_id

        except pymysql.Error as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Insert failed: {e}, SQL: {sql}")
            raise
        finally:
            if conn:
                conn.close()

    @classmethod
    def reset_instance(cls):
        """Reset singleton (for tests only)."""
        cls._instance = None


class BaseDAO:
    """DAO base class."""

    def __init__(self, db: Optional[DatabaseManager] = None):
        """
        Initialize DAO.

        Args:
            db: DatabaseManager instance; uses singleton if not provided
        """
        self.db = db or DatabaseManager()
        self.logger = logging.getLogger(self.__class__.__name__)
