"""
Trailing stop manager.

Responsibilities:
1. Trailing stop price updates (single/batch)
2. Query positions using trailing stop
3. Drawdown protection (stop can only be raised)
4. Stop update audit log
"""

import logging
from contextlib import contextmanager
from typing import Dict, List, Optional

from src.core.database import get_db


class TrailingStopManager:
    """Trailing stop manager."""

    def __init__(
        self,
        host: str = None,      # deprecated, kept for backward compatibility
        port: int = None,      # deprecated, kept for backward compatibility
        user: str = None,      # deprecated, kept for backward compatibility
        password: str = None,  # deprecated, kept for backward compatibility
        database: str = None,  # deprecated, kept for backward compatibility
        notifier=None,         # optional: Telegram notification
        **_                    # absorb other deprecated params
    ):
        """
        Initialize trailing stop manager.

        Args:
            notifier: Notifier instance (optional, for stop update notifications)

        Note:
            host/port/user/password/database params are deprecated, kept for backward compatibility.
            Internally uses DatabasePool connection pool.
        """
        self.logger = logging.getLogger(__name__)
        self.notifier = notifier
        self._db_pool = get_db()
        self.logger.debug("TrailingStopManager initialized (connection pool mode)")

    @contextmanager
    def _get_connection_ctx(self):
        """
        Context manager for database connection.

        Usage:
            with self._get_connection_ctx() as conn:
                with conn.cursor() as cur:
                    cur.execute(...)
        """
        conn = self._db_pool.get_connection()
        try:
            yield conn
        finally:
            conn.close()  # return to pool

    def update_trailing_stop(
        self,
        position_id: int,
        new_stop: float,
        highest_price: float = None,
        force: bool = False
    ) -> bool:
        """
        Update trailing stop price.

        Stop can only be raised, not lowered (drawdown protection).

        Args:
            position_id: Position ID
            new_stop: New stop price
            highest_price: Highest price since entry (optional)
            force: Force update (skip drawdown check, only for initialization)

        Returns:
            Whether update succeeded
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    # 1. Get current stop price (drawdown check)
                    cursor.execute(
                        "SELECT symbol, side, current_stop FROM positions WHERE id = %s",
                        (position_id,)
                    )
                    row = cursor.fetchone()

                    if not row:
                        self.logger.warning(f"Position not found: {position_id}")
                        return False

                    symbol = row['symbol']
                    side = row['side']
                    old_stop = float(row['current_stop']) if row['current_stop'] else 0

                    # 2. Drawdown protection check
                    if not force and old_stop > 0:
                        if side == 'LONG' and new_stop < old_stop:
                            self.logger.warning(
                                f"[{symbol}] Rejected stop downgrade: {old_stop:.2f} -> {new_stop:.2f} "
                                f"(drawdown protection)"
                            )
                            return False
                        elif side == 'SHORT' and new_stop > old_stop:
                            self.logger.warning(
                                f"[{symbol}] Rejected stop upgrade for short: {old_stop:.2f} -> {new_stop:.2f} "
                                f"(short drawdown protection)"
                            )
                            return False

                    # 3. Update stop
                    if highest_price:
                        sql = """
                            UPDATE positions SET
                                current_stop = %s,
                                highest_since_entry = %s,
                                updated_at = NOW()
                            WHERE id = %s AND status IN ('OPEN', 'PARTIAL_CLOSED')
                        """
                        cursor.execute(sql, (new_stop, highest_price, position_id))
                    else:
                        sql = """
                            UPDATE positions SET
                                current_stop = %s,
                                updated_at = NOW()
                            WHERE id = %s AND status IN ('OPEN', 'PARTIAL_CLOSED')
                        """
                        cursor.execute(sql, (new_stop, position_id))

                    affected = cursor.rowcount

                    # 4. Record audit log
                    if affected > 0 and old_stop != new_stop:
                        self._log_stop_update(
                            cursor, position_id, symbol, old_stop, new_stop
                        )

            # connection auto-returned to pool

            if affected > 0:
                change_pct = ((new_stop - old_stop) / old_stop * 100) if old_stop > 0 else 0
                self.logger.info(
                    f"[{symbol}] Trailing stop updated: ${old_stop:.2f} -> ${new_stop:.2f} "
                    f"({change_pct:+.2f}%)"
                )

                # 5. Send notification on significant change (>1%)
                if self.notifier and abs(change_pct) > 1.0:
                    self._send_stop_update_notification(
                        symbol, old_stop, new_stop, change_pct
                    )

                return True
            return False

        except Exception as e:
            self.logger.error(f"Failed to update trailing stop: {e}")
            return False

    def _log_stop_update(
        self,
        cursor,
        position_id: int,
        symbol: str,
        old_stop: float,
        new_stop: float
    ):
        """
        Record stop update audit log.

        Args:
            cursor: Database cursor
            position_id: Position ID
            symbol: Token symbol
            old_stop: Previous stop price
            new_stop: New stop price
        """
        try:
            # Write to positions table notes field
            sql = """
                UPDATE positions SET
                    notes = CONCAT(
                        COALESCE(notes, ''),
                        '\n[', NOW(), '] Stop updated: $',
                        %s, ' -> $', %s
                    )
                WHERE id = %s
            """
            cursor.execute(sql, (old_stop, new_stop, position_id))

        except Exception as e:
            # Audit log failure does not affect main flow
            self.logger.warning(f"Failed to record stop audit log: {e}")

    def _send_stop_update_notification(
        self,
        symbol: str,
        old_stop: float,
        new_stop: float,
        change_pct: float
    ):
        """
        Send stop update notification on significant changes.

        Args:
            symbol: Token symbol
            old_stop: Previous stop price
            new_stop: New stop price
            change_pct: Change percentage
        """
        try:
            # Use silent notification (disable_notification=True)
            message = (
                f"[{symbol}] Trailing stop updated\n"
                f"${old_stop:.2f} -> ${new_stop:.2f} ({change_pct:+.2f}%)"
            )
            self.logger.info(f"Stop update notification: {message}")

        except Exception as e:
            self.logger.warning(f"Failed to send stop update notification: {e}")

    def get_trailing_stop_positions(self) -> List[Dict]:
        """
        Get all positions using trailing stop.

        Returns:
            List of positions
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        SELECT * FROM positions
                        WHERE status IN ('OPEN', 'PARTIAL_CLOSED')
                        AND stop_type != 'FIXED'
                        ORDER BY opened_at DESC
                    """
                    cursor.execute(sql)
                    return cursor.fetchall()

        except Exception as e:
            self.logger.error(f"Failed to query trailing stop positions: {e}")
            return []

    def batch_update_trailing_stops(
        self,
        updates: List[Dict]
    ) -> int:
        """
        Batch update trailing stops (used by swing scheduler).

        Args:
            updates: [{'position_id': 1, 'new_stop': 95000.0, 'highest_price': 100000.0}, ...]

        Returns:
            Number of successful updates
        """
        if not updates:
            return 0

        success_count = 0
        for update in updates:
            if self.update_trailing_stop(
                position_id=update['position_id'],
                new_stop=update['new_stop'],
                highest_price=update.get('highest_price')
            ):
                success_count += 1

        self.logger.info(f"Batch trailing stop update complete: {success_count}/{len(updates)}")
        return success_count

    def calculate_new_trailing_stop(
        self,
        position: Dict,
        current_high: float,
        lowest_n_days: float = None
    ) -> Optional[float]:
        """
        Calculate new trailing stop price.

        Args:
            position: Position record
            current_high: Current highest price
            lowest_n_days: N-day lowest price (for TRAILING_LOWEST)

        Returns:
            New stop price, or None if no update needed
        """
        stop_type = position.get('stop_type', 'FIXED')

        if stop_type == 'FIXED':
            return None

        current_stop = float(position.get('current_stop', 0))
        float(position.get('entry_price', 0))
        position.get('side', 'LONG')

        if stop_type == 'TRAILING_LOWEST':
            # Swing ensemble: N-day low trailing stop
            if lowest_n_days and lowest_n_days > current_stop:
                return lowest_n_days
            return None

        elif stop_type == 'TRAILING_ATR':
            # Swing breakout: ATR multiplier stop (fixed, does not trail)
            # Still records highest_since_entry for analysis
            return None

        return None
