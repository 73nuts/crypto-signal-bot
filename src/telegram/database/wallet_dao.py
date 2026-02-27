"""
HD wallet state DAO.

Manages wallet_state table, providing atomic address index allocation.

Core function:
- get_next_address_index(): atomically get the next available index
- Uses SELECT FOR UPDATE for concurrency safety

Concurrency: two simultaneous orders must not receive the same index.
Exclusive lock guarantees: first request acquires lock → updates index
→ releases lock → second request acquires lock.
"""

from .base import BaseDAO


class WalletDAO(BaseDAO):
    """HD wallet state data access object."""

    TABLE = 'wallet_state'

    def get_next_address_index(self) -> int:
        """
        Atomically get the next available address index.

        Locks the row with SELECT FOR UPDATE to prevent concurrent conflicts.
        Must be called within a transaction, otherwise the lock is released immediately.

        Flow:
        1. SELECT ... FOR UPDATE to lock counter row
        2. Compute next_index = current + 1
        3. UPDATE counter
        4. Commit transaction to release lock

        Returns:
            Next available address index (after increment)

        Raises:
            Exception: if fetch fails (table missing or initialization error)
        """
        conn = None
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()

            # 1. Lock row and get current value
            cursor.execute(
                f"SELECT current_index FROM {self.TABLE} WHERE id = 1 FOR UPDATE"
            )
            row = cursor.fetchone()

            if not row:
                # Table exists but no data — initialize
                cursor.execute(
                    f"INSERT INTO {self.TABLE} (id, current_index) VALUES (1, 0)"
                )
                current_index = 0
            else:
                current_index = row['current_index']

            # 2. Compute next index
            next_index = current_index + 1

            # 3. Update counter
            cursor.execute(
                f"UPDATE {self.TABLE} SET current_index = %s WHERE id = 1",
                (next_index,)
            )

            # 4. Commit (releases lock)
            conn.commit()

            self.logger.debug(f"Address index allocated: {next_index}")
            return next_index

        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Failed to get address index: {e}")
            raise

        finally:
            if conn:
                conn.close()

    def get_current_index(self) -> int:
        """
        Get current index value (read-only, no increment).

        Returns:
            Current index value
        """
        sql = f"SELECT current_index FROM {self.TABLE} WHERE id = 1"
        result = self.db.execute_query(sql, fetch_one=True)
        return result['current_index'] if result else 0

    def reset_index(self, new_index: int = 0) -> bool:
        """
        Reset index (for tests or disaster recovery only).

        WARNING: Do not call in production.

        Args:
            new_index: New index value

        Returns:
            True if successful
        """
        self.logger.warning(f"Resetting address index: {new_index}")
        sql = f"UPDATE {self.TABLE} SET current_index = %s WHERE id = 1"
        affected = self.db.execute_update(sql, (new_index,))
        return affected > 0

    def sync_from_addresses_table(self) -> int:
        """
        Sync index from payment_addresses table (for migration or recovery).

        Queries MAX(derive_index) from payment_addresses and updates wallet_state.

        Returns:
            Synced index value
        """
        sql = "SELECT MAX(derive_index) as max_idx FROM payment_addresses"
        result = self.db.execute_query(sql, fetch_one=True)
        max_index = result['max_idx'] if result and result['max_idx'] is not None else 0

        update_sql = f"UPDATE {self.TABLE} SET current_index = %s WHERE id = 1"
        self.db.execute_update(update_sql, (max_index,))

        self.logger.info(f"Index synced from payment_addresses: {max_index}")
        return max_index
