"""
Audit log DAO.

Manages payment_audit_logs table, recording key financial operations.

Design principle: explicit calls only, no magic auto-recording.
Business layer calls log_event() explicitly after successful transactions.

Recorded events:
- Order create/confirm/expire/fail
- Membership activate/renew/expire
- Fund collection
"""

import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .base import BaseDAO


class AuditOperation(str, Enum):
    """Audit operation types."""
    # Order operations
    ORDER_CREATE = 'ORDER_CREATE'
    ORDER_CONFIRM = 'ORDER_CONFIRM'
    ORDER_EXPIRE = 'ORDER_EXPIRE'
    ORDER_FAIL = 'ORDER_FAIL'

    # Membership operations
    MEMBER_ACTIVATE = 'MEMBER_ACTIVATE'
    MEMBER_RENEW = 'MEMBER_RENEW'
    MEMBER_EXPIRE = 'MEMBER_EXPIRE'

    # Admin operations
    ADMIN_ADD_VIP = 'ADMIN_ADD_VIP'
    ADMIN_REMOVE_VIP = 'ADMIN_REMOVE_VIP'
    ADMIN_FIX_ORDER = 'ADMIN_FIX_ORDER'

    # Fund operations
    FUND_RECEIVE = 'FUND_RECEIVE'
    FUND_COLLECT = 'FUND_COLLECT'

    # System
    SYSTEM_ERROR = 'SYSTEM_ERROR'


class AuditDAO(BaseDAO):
    """Audit log data access object."""

    TABLE = 'payment_audit_logs'

    def log_event(
        self,
        operation: AuditOperation,
        order_id: Optional[str] = None,
        telegram_id: Optional[int] = None,
        operator: str = 'system',
        old_status: Optional[str] = None,
        new_status: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Record an audit event.

        Args:
            operation: Operation type
            order_id: Associated order ID (optional)
            telegram_id: Associated user ID (optional)
            operator: Actor (system/admin/user_id)
            old_status: Status before operation
            new_status: Status after operation
            details: JSON details

        Returns:
            True if recorded successfully
        """
        sql = f"""
            INSERT INTO {self.TABLE} (
                order_id, operation, operator,
                old_status, new_status, details, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, NOW(6)
            )
        """
        try:
            # Merge telegram_id into details
            if telegram_id and details:
                details['telegram_id'] = telegram_id
            elif telegram_id:
                details = {'telegram_id': telegram_id}

            self.db.execute_update(sql, (
                order_id,
                operation.value if isinstance(operation, AuditOperation) else operation,
                operator,
                old_status,
                new_status,
                json.dumps(details, ensure_ascii=False) if details else None
            ))
            return True

        except Exception as e:
            # Audit log failure must not block main flow
            self.logger.error(f"Audit log write failed: {e}")
            return False

    def log_order_create(
        self,
        order_id: str,
        telegram_id: int,
        membership_type: str,
        amount: float,
        payment_address: str
    ) -> bool:
        """Record order creation."""
        return self.log_event(
            operation=AuditOperation.ORDER_CREATE,
            order_id=order_id,
            telegram_id=telegram_id,
            new_status='PENDING',
            details={
                'membership_type': membership_type,
                'amount': str(amount),
                'payment_address': payment_address
            }
        )

    def log_order_confirm(
        self,
        order_id: str,
        telegram_id: int,
        tx_hash: str,
        actual_amount: float
    ) -> bool:
        """Record order confirmation."""
        return self.log_event(
            operation=AuditOperation.ORDER_CONFIRM,
            order_id=order_id,
            telegram_id=telegram_id,
            old_status='PENDING',
            new_status='CONFIRMED',
            details={
                'tx_hash': tx_hash,
                'actual_amount': str(actual_amount)
            }
        )

    def log_order_expire(self, order_id: str) -> bool:
        """Record order expiry."""
        return self.log_event(
            operation=AuditOperation.ORDER_EXPIRE,
            order_id=order_id,
            old_status='PENDING',
            new_status='EXPIRED'
        )

    def log_order_fail(
        self,
        order_id: str,
        reason: str
    ) -> bool:
        """Record order failure."""
        return self.log_event(
            operation=AuditOperation.ORDER_FAIL,
            order_id=order_id,
            old_status='PENDING',
            new_status='FAILED',
            details={'reason': reason}
        )

    def log_member_activate(
        self,
        order_id: str,
        telegram_id: int,
        membership_type: str,
        expire_date: datetime
    ) -> bool:
        """Record membership activation."""
        return self.log_event(
            operation=AuditOperation.MEMBER_ACTIVATE,
            order_id=order_id,
            telegram_id=telegram_id,
            new_status='ACTIVE',
            details={
                'membership_type': membership_type,
                'expire_date': expire_date.isoformat()
            }
        )

    def log_member_renew(
        self,
        order_id: str,
        telegram_id: int,
        new_expire_date: datetime
    ) -> bool:
        """Record membership renewal."""
        return self.log_event(
            operation=AuditOperation.MEMBER_RENEW,
            order_id=order_id,
            telegram_id=telegram_id,
            old_status='ACTIVE',
            new_status='ACTIVE',
            details={'new_expire_date': new_expire_date.isoformat()}
        )

    def log_fund_collect(
        self,
        address: str,
        amount: float,
        tx_hash: str
    ) -> bool:
        """Record fund collection."""
        return self.log_event(
            operation=AuditOperation.FUND_COLLECT,
            details={
                'address': address,
                'amount': str(amount),
                'tx_hash': tx_hash
            }
        )

    def get_order_history(
        self,
        order_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get complete operation history for an order.

        Args:
            order_id: Order ID

        Returns:
            List of audit records in ascending time order
        """
        sql = f"""
            SELECT id, order_id, operation, operator,
                   old_status, new_status, details, created_at
            FROM {self.TABLE}
            WHERE order_id = %s
            ORDER BY created_at ASC
        """
        return self.db.execute_query(sql, (order_id,)) or []

    def get_recent_events(
        self,
        limit: int = 100,
        operation: Optional[AuditOperation] = None
    ) -> List[Dict[str, Any]]:
        """
        Get recent audit events.

        Args:
            limit: Max number of events to return
            operation: Filter by operation type (optional)

        Returns:
            Event list in descending time order
        """
        if operation:
            sql = f"""
                SELECT id, order_id, operation, operator,
                       old_status, new_status, details, created_at
                FROM {self.TABLE}
                WHERE operation = %s
                ORDER BY created_at DESC
                LIMIT %s
            """
            params = (operation.value, limit)
        else:
            sql = f"""
                SELECT id, order_id, operation, operator,
                       old_status, new_status, details, created_at
                FROM {self.TABLE}
                ORDER BY created_at DESC
                LIMIT %s
            """
            params = (limit,)

        return self.db.execute_query(sql, params) or []
