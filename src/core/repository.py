"""
Base repository class

Provides:
1. Unified database access interface
2. Connection management via DatabasePool
3. Common CRUD template methods
"""
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

_VALID_TABLE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")

from src.core.database import DatabasePool, get_db


class BaseRepository(ABC):
    """
    Base repository class

    Base class for all data repositories; provides unified database access patterns.
    Subclasses must implement the table_name property.
    """

    def __init__(self, db: DatabasePool = None):
        """
        Initialize the repository.

        Args:
            db: DatabasePool instance; uses the singleton if not provided
        """
        self._db = db or get_db()
        self.logger = logging.getLogger(self.__class__.__name__)
        if not _VALID_TABLE_NAME.match(self.table_name):
            raise ValueError(f"Invalid table name: {self.table_name}")

    @property
    @abstractmethod
    def table_name(self) -> str:
        """Table name (subclasses must implement)"""
        pass

    # ========================================
    # Common CRUD templates
    # ========================================

    def find_by_id(self, id: int) -> Optional[Dict[str, Any]]:
        """Find by ID"""
        sql = f"SELECT * FROM {self.table_name} WHERE id = %s"
        return self._db.execute(sql, (id,), fetch='one')

    def exists(self, id: int) -> bool:
        """Check if a record exists"""
        sql = f"SELECT 1 FROM {self.table_name} WHERE id = %s LIMIT 1"
        result = self._db.execute(sql, (id,), fetch='one')
        return result is not None

    def count(self) -> int:
        """Count total records"""
        sql = f"SELECT COUNT(*) as cnt FROM {self.table_name}"
        result = self._db.execute(sql, fetch='one')
        return result['cnt'] if result else 0

    def delete_by_id(self, id: int) -> bool:
        """Delete by ID"""
        sql = f"DELETE FROM {self.table_name} WHERE id = %s"
        affected = self._db.execute(sql, (id,), fetch=None)
        return affected > 0
