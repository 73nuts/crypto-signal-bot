"""
User feedback DAO.

Manages user_feedback table.

Features:
- Create feedback records
- Count user feedback (for rate limiting)
- Mark feedback as replied
- Query pending feedbacks
"""

from typing import Any, Dict, List, Optional

from .base import BaseDAO


class FeedbackDAO(BaseDAO):
    """User feedback data access object."""

    TABLE = 'user_feedback'

    def create_feedback(
        self,
        telegram_id: int,
        username: Optional[str],
        content: str
    ) -> int:
        """
        Create a feedback record.

        Args:
            telegram_id: User Telegram ID
            username: Username (optional)
            content: Feedback content

        Returns:
            feedback_id: ID of the new feedback record
        """
        sql = f"""
            INSERT INTO {self.TABLE} (
                telegram_id, username, content, created_at
            ) VALUES (
                %s, %s, %s, NOW()
            )
        """
        feedback_id = self.db.execute_insert(sql, (
            telegram_id,
            username,
            content
        ))
        self.logger.info(f"Feedback created: id={feedback_id}, user={telegram_id}")
        return feedback_id

    def count_recent_feedback(
        self,
        telegram_id: int,
        hours: int = 24
    ) -> int:
        """
        Count user's recent feedback submissions (for rate limiting).

        Args:
            telegram_id: User Telegram ID
            hours: Time window in hours

        Returns:
            Feedback count
        """
        sql = f"""
            SELECT COUNT(*) as count
            FROM {self.TABLE}
            WHERE telegram_id = %s
            AND created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR)
        """
        result = self.db.execute_query(sql, (telegram_id, hours))
        return result[0]['count'] if result else 0

    def get_feedback_by_id(self, feedback_id: int) -> Optional[Dict[str, Any]]:
        """
        Get feedback details by ID.

        Args:
            feedback_id: Feedback ID

        Returns:
            Feedback record dict, or None
        """
        sql = f"""
            SELECT id, telegram_id, username, content,
                   replied, reply_content, replied_at, replied_by,
                   created_at
            FROM {self.TABLE}
            WHERE id = %s
        """
        result = self.db.execute_query(sql, (feedback_id,))
        return result[0] if result else None

    def mark_as_replied(
        self,
        feedback_id: int,
        reply_content: str,
        replied_by: int
    ) -> bool:
        """
        Mark feedback as replied.

        Args:
            feedback_id: Feedback ID
            reply_content: Reply content
            replied_by: Replying admin ID

        Returns:
            True if updated successfully
        """
        sql = f"""
            UPDATE {self.TABLE}
            SET replied = 1,
                reply_content = %s,
                replied_at = NOW(),
                replied_by = %s
            WHERE id = %s
        """
        affected = self.db.execute_update(sql, (
            reply_content,
            replied_by,
            feedback_id
        ))
        if affected > 0:
            self.logger.info(
                f"Feedback replied: id={feedback_id}, by={replied_by}"
            )
        return affected > 0

    def get_pending_feedbacks(
        self,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get list of unreplied feedbacks.

        Args:
            limit: Max number to return

        Returns:
            Unreplied feedbacks in ascending time order (FIFO)
        """
        sql = f"""
            SELECT id, telegram_id, username, content, created_at
            FROM {self.TABLE}
            WHERE replied = 0
            ORDER BY created_at ASC
            LIMIT %s
        """
        return self.db.execute_query(sql, (limit,)) or []

    def get_user_feedbacks(
        self,
        telegram_id: int,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get a user's feedback history.

        Args:
            telegram_id: User Telegram ID
            limit: Max number to return

        Returns:
            User feedbacks in descending time order
        """
        sql = f"""
            SELECT id, content, replied, reply_content,
                   replied_at, created_at
            FROM {self.TABLE}
            WHERE telegram_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """
        return self.db.execute_query(sql, (telegram_id, limit)) or []

    def get_recent_feedbacks(
        self,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get all recent feedbacks (admin view).

        Args:
            limit: Max number to return

        Returns:
            Feedbacks in descending time order
        """
        sql = f"""
            SELECT id, telegram_id, username, content,
                   replied, reply_content, replied_at,
                   created_at
            FROM {self.TABLE}
            ORDER BY created_at DESC
            LIMIT %s
        """
        return self.db.execute_query(sql, (limit,)) or []
