"""
VIP signal push record DAO.

Manages vip_signal_pushes table, recording details of each signal push.

Schema:
- signal_id: associated signal ID
- telegram_id: receiving user ID
- membership_id: associated membership ID
- signal_type: signal type (SWING)
- symbol: trading symbol
- push_time: push timestamp
- success: whether push succeeded
- error_message: failure reason
- membership_status_at_push: membership status snapshot at push time
"""

from typing import Optional, List, Dict, Any
from enum import Enum

from .base import BaseDAO


class SignalType(str, Enum):
    """Signal type enum."""
    SWING = 'SWING'        # swing signal
    INTRADAY = 'INTRADAY'  # [deprecated] kept for data compatibility


class SignalPushDAO(BaseDAO):
    """VIP signal push record data access object."""

    TABLE = 'vip_signal_pushes'

    def record_push(
        self,
        signal_id: int,
        telegram_id: int,
        membership_id: int,
        signal_type: str,
        symbol: str,
        success: bool,
        error_message: Optional[str] = None,
        membership_status_at_push: Optional[str] = None
    ) -> Optional[int]:
        """
        Record a signal push.

        Args:
            signal_id: Signal ID
            telegram_id: Telegram user ID
            membership_id: Membership record ID
            signal_type: Signal type (SWING)
            symbol: Trading symbol
            success: Whether push succeeded
            error_message: Failure reason
            membership_status_at_push: Membership status at push time

        Returns:
            Record ID, or None on failure
        """
        sql = f"""
            INSERT INTO {self.TABLE} (
                signal_id, telegram_id, membership_id, signal_type,
                symbol, push_time, success, error_message,
                membership_status_at_push, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, NOW(6), %s, %s, %s, NOW(6)
            )
        """
        try:
            push_id = self.db.execute_insert(sql, (
                signal_id,
                telegram_id,
                membership_id,
                signal_type,
                symbol,
                success,
                error_message,
                membership_status_at_push
            ))

            self.logger.debug(
                f"Push record saved: signal_id={signal_id}, "
                f"telegram_id={telegram_id}, success={success}"
            )
            return push_id

        except Exception as e:
            self.logger.error(f"Push record save failed: {e}")
            return None

    def get_push_history(
        self,
        telegram_id: int,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Query a user's push history.

        Args:
            telegram_id: Telegram user ID
            limit: Max number to return

        Returns:
            Push records in descending time order
        """
        sql = f"""
            SELECT id, signal_id, signal_type, symbol,
                   push_time, success, error_message
            FROM {self.TABLE}
            WHERE telegram_id = %s
            ORDER BY push_time DESC
            LIMIT %s
        """
        return self.db.execute_query(sql, (telegram_id, limit)) or []

    def get_push_by_signal_and_user(
        self,
        signal_id: int,
        telegram_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Look up push record for a specific signal and user (duplicate push guard).

        Args:
            signal_id: Signal ID
            telegram_id: Telegram user ID

        Returns:
            Push record, or None if not found
        """
        sql = f"""
            SELECT id, push_time, success, error_message
            FROM {self.TABLE}
            WHERE signal_id = %s AND telegram_id = %s
        """
        return self.db.execute_query(
            sql, (signal_id, telegram_id), fetch_one=True
        )

    def has_pushed_to_user(self, signal_id: int, telegram_id: int) -> bool:
        """
        Check whether a signal has already been pushed to a user.

        Args:
            signal_id: Signal ID
            telegram_id: Telegram user ID

        Returns:
            True if already pushed, False otherwise
        """
        return self.get_push_by_signal_and_user(
            signal_id, telegram_id
        ) is not None

    def get_push_stats_by_signal(self, signal_id: int) -> Dict[str, Any]:
        """
        Get push statistics for a single signal.

        Args:
            signal_id: Signal ID

        Returns:
            {'total': 100, 'success': 98, 'failed': 2, 'success_rate': 0.98}
        """
        sql = f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN success = TRUE THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END) as failed_count
            FROM {self.TABLE}
            WHERE signal_id = %s
        """
        result = self.db.execute_query(sql, (signal_id,), fetch_one=True)

        if not result or result['total'] == 0:
            return {'total': 0, 'success': 0, 'failed': 0, 'success_rate': 0}

        total = result['total']
        success = result['success_count'] or 0
        failed = result['failed_count'] or 0

        return {
            'total': total,
            'success': success,
            'failed': failed,
            'success_rate': round(success / total, 4) if total > 0 else 0
        }

    def get_daily_push_stats(
        self,
        days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Get daily push statistics.

        Args:
            days: Number of days to include

        Returns:
            Daily stats list
        """
        sql = f"""
            SELECT
                DATE(push_time) as date,
                signal_type,
                COUNT(*) as total,
                SUM(CASE WHEN success = TRUE THEN 1 ELSE 0 END) as success_count
            FROM {self.TABLE}
            WHERE push_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
            GROUP BY DATE(push_time), signal_type
            ORDER BY date DESC, signal_type
        """
        return self.db.execute_query(sql, (days,)) or []

    def get_failed_pushes(
        self,
        hours: int = 24
    ) -> List[Dict[str, Any]]:
        """
        Get recent failed pushes.

        Args:
            hours: Time window in hours

        Returns:
            Failed push list
        """
        sql = f"""
            SELECT id, signal_id, telegram_id, signal_type,
                   symbol, push_time, error_message
            FROM {self.TABLE}
            WHERE success = FALSE
              AND push_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
            ORDER BY push_time DESC
        """
        return self.db.execute_query(sql, (hours,)) or []

    def get_user_push_count_today(
        self,
        telegram_id: int,
        signal_type: Optional[str] = None
    ) -> int:
        """
        Get number of pushes to a user today.

        Args:
            telegram_id: Telegram user ID
            signal_type: Optional signal type filter

        Returns:
            Push count
        """
        if signal_type:
            sql = f"""
                SELECT COUNT(*) as count
                FROM {self.TABLE}
                WHERE telegram_id = %s
                  AND signal_type = %s
                  AND DATE(push_time) = CURDATE()
                  AND success = TRUE
            """
            params = (telegram_id, signal_type)
        else:
            sql = f"""
                SELECT COUNT(*) as count
                FROM {self.TABLE}
                WHERE telegram_id = %s
                  AND DATE(push_time) = CURDATE()
                  AND success = TRUE
            """
            params = (telegram_id,)

        result = self.db.execute_query(sql, params, fetch_one=True)
        return result['count'] if result else 0

    def cleanup_old_records(self, days: int = 90) -> int:
        """
        Delete push records older than the retention period.

        Args:
            days: Retention days

        Returns:
            Number of records deleted
        """
        sql = f"""
            DELETE FROM {self.TABLE}
            WHERE push_time < DATE_SUB(NOW(), INTERVAL %s DAY)
        """
        affected = self.db.execute_update(sql, (days,))

        if affected > 0:
            self.logger.info(f"Cleaned up old push records: {affected}")

        return affected
