"""
Mock database connection pool.

In-memory storage for testing.
"""
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager


class MockDatabasePool:
    """Mock database connection pool."""

    def __init__(self):
        self._data: Dict[str, List[Dict]] = {}
        self._healthy = True

    def get_connection(self):
        """Get a mock connection."""
        return MockConnection(self._data)

    async def execute(self, query: str, params: tuple = None) -> int:
        """Execute SQL (mock implementation)."""
        # Simple implementation: return affected row count
        return 1

    async def fetch_one(self, query: str, params: tuple = None) -> Optional[Dict[str, Any]]:
        """Fetch single record (mock implementation)."""
        return None

    async def fetch_all(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        """Fetch multiple records (mock implementation)."""
        return []

    @asynccontextmanager
    async def transaction(self):
        """Mock transaction."""
        yield self

    def health_check(self) -> bool:
        """Health check."""
        return self._healthy

    # Test helper methods
    def set_healthy(self, healthy: bool):
        """Set health state (for testing)."""
        self._healthy = healthy

    def set_data(self, table: str, data: List[Dict]):
        """Set table data (for testing)."""
        self._data[table] = data

    def get_data(self, table: str) -> List[Dict]:
        """Get table data (for testing)."""
        return self._data.get(table, [])

    def clear(self):
        """Clear all data (for testing)."""
        self._data.clear()


class MockConnection:
    """Mock database connection."""

    def __init__(self, data: Dict[str, List[Dict]]):
        self._data = data
        self._in_transaction = False

    def cursor(self):
        return MockCursor(self._data)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class MockCursor:
    """Mock database cursor."""

    def __init__(self, data: Dict[str, List[Dict]]):
        self._data = data
        self._result = []
        self._rowcount = 0

    def execute(self, query: str, params: tuple = None):
        self._rowcount = 1

    def fetchone(self) -> Optional[Dict]:
        return self._result[0] if self._result else None

    def fetchall(self) -> List[Dict]:
        return self._result

    @property
    def rowcount(self) -> int:
        return self._rowcount

    def close(self):
        pass
