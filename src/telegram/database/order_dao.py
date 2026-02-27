"""
Payment order DAO.

Manages payment_orders table with order state machine and optimistic locking.

Schema:
- order_id: order number (HMAC-signed format)
- order_signature: HMAC-SHA256 signature
- telegram_id: Telegram user ID
- membership_type: membership type (WEEK/MONTH/SEASON)
- expected_amount: expected payment amount
- tx_hash: BSC transaction hash
- actual_amount: actual paid amount
- status: order status (PENDING/CONFIRMED/EXPIRED/FAILED)
- expire_at: order expiry time
- version: optimistic lock version

State machine:
PENDING → CONFIRMED (payment received)
PENDING → EXPIRED (timed out)
PENDING → FAILED (payment failed / amount mismatch)
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .base import BaseDAO


class OrderStatus(str, Enum):
    """Order status enum."""
    PENDING = 'PENDING'      # awaiting payment
    CONFIRMED = 'CONFIRMED'  # confirmed
    EXPIRED = 'EXPIRED'      # expired
    FAILED = 'FAILED'        # failed


class MembershipType(str, Enum):
    """Membership type enum."""
    WEEK = 'WEEK'
    MONTH = 'MONTH'
    SEASON = 'SEASON'


# State machine: allowed status transitions
VALID_TRANSITIONS = {
    OrderStatus.PENDING: [
        OrderStatus.CONFIRMED,
        OrderStatus.EXPIRED,
        OrderStatus.FAILED
    ],
    OrderStatus.CONFIRMED: [],  # terminal
    OrderStatus.EXPIRED: [],    # terminal
    OrderStatus.FAILED: [],     # terminal
}


class OrderDAO(BaseDAO):
    """Payment order data access object."""

    TABLE = 'payment_orders'
    AUDIT_TABLE = 'payment_audit_logs'

    def create_order(
        self,
        order_id: str,
        order_signature: str,
        telegram_id: int,
        membership_type: str,
        expected_amount: Decimal,
        expire_at: datetime,
        payment_address: str,
        address_index: int,
        duration_days: int,
        telegram_username: Optional[str] = None,
        discount_type: Optional[str] = None
    ) -> bool:
        """
        Create a payment order.

        Args:
            order_id: Order number (format: YYYYMMDD-XXXX)
            order_signature: HMAC-SHA256 signature
            telegram_id: Telegram user ID
            membership_type: Membership type (WEEK/MONTH/SEASON)
            expected_amount: Expected amount (price snapshot at order time)
            expire_at: Order expiry time
            payment_address: HD wallet receiving address
            address_index: HD wallet derivation index
            duration_days: Plan duration in days (snapshot)
            telegram_username: Telegram username (optional)
            discount_type: Discount type (ALPHA/TRADER/NONE)

        Returns:
            True: created successfully
            False: creation failed (e.g. duplicate order)
        """
        sql = f"""
            INSERT INTO {self.TABLE} (
                order_id, order_signature, telegram_id, telegram_username,
                membership_type, expected_amount, discount_type, duration_days,
                payment_address, address_index, status, expire_at,
                version, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, NOW(6)
            )
        """
        try:
            self.db.execute_update(sql, (
                order_id,
                order_signature,
                telegram_id,
                telegram_username,
                membership_type,
                expected_amount,
                discount_type or 'NONE',
                duration_days,
                payment_address,
                address_index,
                OrderStatus.PENDING.value,
                expire_at
            ))

            # Record audit log
            self._log_audit(
                order_id=order_id,
                operation='CREATE',
                operator='system',
                new_status=OrderStatus.PENDING.value,
                details={
                    'telegram_id': telegram_id,
                    'membership_type': membership_type,
                    'expected_amount': str(expected_amount),
                    'payment_address': payment_address,
                    'address_index': address_index,
                    'duration_days': duration_days
                }
            )

            self.logger.info(f"Order created: order_id={order_id}")
            return True

        except Exception as e:
            self.logger.error(f"Order creation failed: {e}")
            return False

    def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Look up an order by ID.

        Args:
            order_id: Order ID

        Returns:
            Order details dict, or None if not found
        """
        sql = f"""
            SELECT id, order_id, order_signature, telegram_id, telegram_username,
                   membership_type, expected_amount, duration_days,
                   payment_address, address_index, tx_hash, actual_amount,
                   from_address, status, expire_at, confirmed_at,
                   version, created_at
            FROM {self.TABLE}
            WHERE order_id = %s
        """
        return self.db.execute_query(sql, (order_id,), fetch_one=True)

    def get_order_by_tx_hash(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        """
        Look up an order by transaction hash (replay protection).

        Args:
            tx_hash: BSC transaction hash

        Returns:
            Order details, or None if not found
        """
        sql = f"""
            SELECT id, order_id, order_signature, telegram_id,
                   membership_type, expected_amount, tx_hash, actual_amount,
                   status, confirmed_at
            FROM {self.TABLE}
            WHERE tx_hash = %s
        """
        return self.db.execute_query(sql, (tx_hash,), fetch_one=True)

    def get_pending_orders(self) -> List[Dict[str, Any]]:
        """
        Query all pending, non-expired orders.

        Returns:
            List of pending orders
        """
        sql = f"""
            SELECT id, order_id, order_signature, telegram_id, telegram_username,
                   membership_type, expected_amount, expire_at,
                   version, created_at
            FROM {self.TABLE}
            WHERE status = %s AND expire_at > NOW()
            ORDER BY created_at ASC
        """
        return self.db.execute_query(sql, (OrderStatus.PENDING.value,)) or []

    def get_last_confirmed_order(
        self,
        telegram_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Get user's most recently confirmed order.

        Used for Alpha renewal logic to check if the user had an Alpha discount.

        Args:
            telegram_id: Telegram user ID

        Returns:
            Last confirmed order details, or None if not found
        """
        sql = f"""
            SELECT id, order_id, telegram_id, membership_type,
                   expected_amount, discount_type, confirmed_at
            FROM {self.TABLE}
            WHERE telegram_id = %s
              AND status = %s
            ORDER BY confirmed_at DESC
            LIMIT 1
        """
        return self.db.execute_query(
            sql,
            (telegram_id, OrderStatus.CONFIRMED.value),
            fetch_one=True
        )

    def get_expired_pending_orders(self) -> List[Dict[str, Any]]:
        """
        Query orders that have expired but are still in PENDING status.

        Returns:
            Orders to be marked expired (includes payment_address for release)
        """
        sql = f"""
            SELECT id, order_id, telegram_id, membership_type, version, payment_address
            FROM {self.TABLE}
            WHERE status = %s AND expire_at <= NOW()
        """
        return self.db.execute_query(sql, (OrderStatus.PENDING.value,)) or []

    def get_orders_by_telegram_id(
        self,
        telegram_id: int,
        status: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Query a user's order history.

        Args:
            telegram_id: Telegram user ID
            status: Optional status filter
            limit: Max number of results

        Returns:
            Orders in descending creation time order
        """
        if status:
            sql = f"""
                SELECT id, order_id, membership_type, expected_amount,
                       actual_amount, status, created_at, confirmed_at
                FROM {self.TABLE}
                WHERE telegram_id = %s AND status = %s
                ORDER BY created_at DESC
                LIMIT %s
            """
            params = (telegram_id, status, limit)
        else:
            sql = f"""
                SELECT id, order_id, membership_type, expected_amount,
                       actual_amount, status, created_at, confirmed_at
                FROM {self.TABLE}
                WHERE telegram_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """
            params = (telegram_id, limit)

        return self.db.execute_query(sql, params) or []

    def confirm_order(
        self,
        order_id: str,
        tx_hash: str,
        actual_amount: Decimal,
        from_address: str,
        version: int
    ) -> bool:
        """
        Confirm order payment (state: PENDING → CONFIRMED).

        Args:
            order_id: Order ID
            tx_hash: BSC transaction hash
            actual_amount: Actual paid amount
            from_address: Payer address
            version: Current version (optimistic lock)

        Returns:
            True: confirmed successfully
            False: invalid state transition or version conflict
        """
        # Validate current state
        current = self.get_order_by_id(order_id)
        if not current:
            self.logger.error(f"Order not found: {order_id}")
            return False

        current_status = OrderStatus(current['status'])
        if OrderStatus.CONFIRMED not in VALID_TRANSITIONS.get(current_status, []):
            self.logger.error(
                f"Invalid state transition: {current_status} → CONFIRMED, "
                f"order_id={order_id}"
            )
            return False

        sql = f"""
            UPDATE {self.TABLE}
            SET status = %s,
                tx_hash = %s,
                actual_amount = %s,
                from_address = %s,
                confirmed_at = NOW(6),
                version = version + 1
            WHERE order_id = %s AND version = %s AND status = %s
        """
        affected = self.db.execute_update(sql, (
            OrderStatus.CONFIRMED.value,
            tx_hash,
            actual_amount,
            from_address,
            order_id,
            version,
            OrderStatus.PENDING.value
        ))

        if affected == 0:
            self.logger.warning(
                f"Order confirmation failed (version conflict or status changed): order_id={order_id}"
            )
            return False

        # Record audit log
        self._log_audit(
            order_id=order_id,
            operation='CONFIRM',
            operator='system',
            old_status=OrderStatus.PENDING.value,
            new_status=OrderStatus.CONFIRMED.value,
            details={
                'tx_hash': tx_hash,
                'actual_amount': str(actual_amount),
                'from_address': from_address
            }
        )

        self.logger.info(f"Order confirmed: order_id={order_id}, tx_hash={tx_hash}")
        return True

    def expire_order(self, order_id: str, version: int) -> bool:
        """
        Mark order as expired (state: PENDING → EXPIRED).

        Args:
            order_id: Order ID
            version: Current version

        Returns:
            True: marked successfully
            False: invalid state transition or version conflict
        """
        sql = f"""
            UPDATE {self.TABLE}
            SET status = %s,
                version = version + 1
            WHERE order_id = %s AND version = %s AND status = %s
        """
        affected = self.db.execute_update(sql, (
            OrderStatus.EXPIRED.value,
            order_id,
            version,
            OrderStatus.PENDING.value
        ))

        if affected == 0:
            self.logger.warning(f"Order expiry mark failed: order_id={order_id}")
            return False

        # Record audit log
        self._log_audit(
            order_id=order_id,
            operation='EXPIRE',
            operator='system',
            old_status=OrderStatus.PENDING.value,
            new_status=OrderStatus.EXPIRED.value
        )

        self.logger.info(f"Order marked expired: order_id={order_id}")
        return True

    def fail_order(
        self,
        order_id: str,
        version: int,
        reason: str
    ) -> bool:
        """
        Mark order as failed (state: PENDING → FAILED).

        Args:
            order_id: Order ID
            version: Current version
            reason: Failure reason

        Returns:
            True: marked successfully
            False: invalid state transition or version conflict
        """
        sql = f"""
            UPDATE {self.TABLE}
            SET status = %s,
                version = version + 1
            WHERE order_id = %s AND version = %s AND status = %s
        """
        affected = self.db.execute_update(sql, (
            OrderStatus.FAILED.value,
            order_id,
            version,
            OrderStatus.PENDING.value
        ))

        if affected == 0:
            self.logger.warning(f"Order failure mark failed: order_id={order_id}")
            return False

        # Record audit log
        self._log_audit(
            order_id=order_id,
            operation='FAIL',
            operator='system',
            old_status=OrderStatus.PENDING.value,
            new_status=OrderStatus.FAILED.value,
            details={'reason': reason}
        )

        self.logger.info(f"Order marked failed: order_id={order_id}, reason={reason}")
        return True

    def verify_order_signature(
        self,
        order_id: str,
        signature: str
    ) -> bool:
        """
        Verify order signature matches.

        Args:
            order_id: Order ID
            signature: Signature to verify

        Returns:
            True: signature matches
            False: mismatch or order not found
        """
        sql = f"""
            SELECT order_signature
            FROM {self.TABLE}
            WHERE order_id = %s
        """
        result = self.db.execute_query(sql, (order_id,), fetch_one=True)
        if not result:
            return False

        return result['order_signature'] == signature

    def _log_audit(
        self,
        order_id: str,
        operation: str,
        operator: str,
        old_status: Optional[str] = None,
        new_status: Optional[str] = None,
        details: Optional[Dict] = None
    ):
        """
        Record audit log entry.

        Args:
            order_id: Order ID
            operation: Operation type (CREATE/VERIFY/CONFIRM/EXPIRE/FAIL)
            operator: Actor
            old_status: Status before operation
            new_status: Status after operation
            details: JSON details
        """
        import json

        sql = f"""
            INSERT INTO {self.AUDIT_TABLE} (
                order_id, operation, operator,
                old_status, new_status, details, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, NOW(6)
            )
        """
        try:
            self.db.execute_update(sql, (
                order_id,
                operation,
                operator,
                old_status,
                new_status,
                json.dumps(details) if details else None
            ))
        except Exception as e:
            # Audit log failure must not block main flow
            self.logger.error(f"Audit log write failed: {e}")

    def batch_expire_orders(self) -> int:
        """
        Bulk-expire timed-out orders.

        Returns:
            Number of orders expired
        """
        expired_orders = self.get_expired_pending_orders()
        count = 0

        for order in expired_orders:
            if self.expire_order(order['order_id'], order['version']):
                count += 1

        if count > 0:
            self.logger.info(f"Bulk order expiry complete: {count} orders")

        return count
