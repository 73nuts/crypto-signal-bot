"""
Order generator.

Core responsibilities:
1. Generate safe order IDs (format: YYYYMMDD-XXXX)
2. HMAC-SHA256 signature generation and verification
3. Coordinate HD wallet address allocation + order record creation
4. Idempotency guarantee (return existing PENDING order for same user + plan)

Design notes:
- Price fetched dynamically from DB and snapshotted in the order
- Order TTL: 30 minutes (configurable)
- Anti-duplicate: repeated requests return the existing PENDING order
- Short, human-readable order IDs for customer support queries
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional

from src.core.cache import CacheBackend, get_cache
from src.core.config import settings
from src.core.distributed_lock import DistributedLock, RedisUnavailableError
from src.core.structured_logger import get_logger

from .database import DatabaseManager, MembershipPlanDAO, OrderDAO
from .database.order_dao import OrderStatus
from .payment import HDWalletManager
from .pricing import PricingEngine

logger = get_logger(__name__)


class OrderGenerator:
    """Order generator."""

    def __init__(
        self,
        secret_key: Optional[str] = None,
        expire_minutes: Optional[int] = None
    ):
        """
        Initialize the order generator.

        Args:
            secret_key: Signing key (reads from env if not provided)
            expire_minutes: Order expiry in minutes (default 30)
        """
        self.db = DatabaseManager()
        self.order_dao = OrderDAO(self.db)
        self.plan_dao = MembershipPlanDAO(self.db)
        self.wallet_manager = HDWalletManager()

        # Cache manager (for distributed lock)
        self._cache = get_cache().setup(CacheBackend.REDIS)

        if secret_key:
            self.secret_key = secret_key
        elif settings.ORDER_SECRET_KEY:
            self.secret_key = settings.ORDER_SECRET_KEY.get_secret_value()
        else:
            raise ValueError("ORDER_SECRET_KEY not configured")

        self.expire_minutes = expire_minutes or settings.ORDER_EXPIRE_MINUTES

        logger.info(
            f"Order generator initialized: expire_minutes={self.expire_minutes}"
        )

    def create_order(
        self,
        telegram_id: int,
        plan_code: str,
        telegram_username: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create an order (idempotent).

        Core flow:
        1. Acquire distributed lock (prevent concurrent duplicate orders)
        2. Check for existing valid PENDING order (anti-duplicate)
        3. Fetch plan price (snapshot) + apply discount
        4. Allocate HD wallet address
        5. Generate order ID and signature
        6. Write to database

        Fallback:
        - Redis available: use Redis distributed lock
        - Redis unavailable: fall back to DB row-level lock (SELECT FOR UPDATE)

        Args:
            telegram_id: Telegram user ID
            plan_code: Plan code (BASIC_M/BASIC_Y/PREMIUM_M/PREMIUM_Y)
            telegram_username: Telegram username (optional)

        Returns:
            Order info dict:
            {
                'order_id': 'YYYYMMDD-XXXX',
                'payment_address': '0x...',
                'amount': Decimal('19.90'),
                'original_price': Decimal('29.90'),
                'discount_type': 'alpha' | 'trader' | None,
                'expire_at': datetime,
                'is_existing': bool,  # True = returned existing order
                'plan_name': 'Ignis Basic (Monthly)'
            }

        Raises:
            ValueError: Plan not found or disabled
            RuntimeError: Address allocation failed
            LockAcquireError: Cannot acquire distributed lock (too many concurrent requests)
        """
        # 0. Acquire distributed lock (prevent concurrent duplicate orders from multiple devices)
        lock_key = f"order:create:{telegram_id}:{plan_code}"
        lock = DistributedLock(self._cache, lock_key, ttl=30)

        try:
            with lock:
                return self._create_order_internal(
                    telegram_id, plan_code, telegram_username
                )
        except RedisUnavailableError:
            # Redis unavailable, fall back to DB row-level lock
            logger.warning(
                f"Redis unavailable, falling back to DB lock: telegram_id={telegram_id}, "
                f"plan_code={plan_code}"
            )
            return self._create_order_with_db_lock(
                telegram_id, plan_code, telegram_username
            )

    def _create_order_internal(
        self,
        telegram_id: int,
        plan_code: str,
        telegram_username: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Internal order creation (executed inside distributed lock).
        """
        # 1. Check for existing valid PENDING order (idempotency)
        existing_order = self._find_existing_pending_order(
            telegram_id, plan_code
        )
        if existing_order:
            logger.info(
                f"Returning existing PENDING order: telegram_id={telegram_id}, "
                f"order_id={existing_order['order_id']}"
            )
            plan = self.plan_dao.get_plan_by_code(plan_code)
            return {
                'order_id': existing_order['order_id'],
                'payment_address': existing_order['payment_address'],
                'amount': existing_order['expected_amount'],
                'original_price': existing_order['expected_amount'],  # already discounted
                'discount_type': None,
                'expire_at': existing_order['expire_at'],
                'is_existing': True,
                'plan_name': plan['plan_name'] if plan else plan_code
            }

        # 2. Fetch plan price (snapshot)
        plan = self.plan_dao.get_plan_by_code(plan_code)
        if not plan or not plan['enabled']:
            raise ValueError(f"Plan not found or disabled: {plan_code}")

        original_price = plan['price_usdt']

        # 3. Calculate dynamic pricing (Alpha > Trader > list price)
        price, discount_type, bonus_days = self._calculate_price(
            telegram_id, original_price, plan.get('level', 1)
        )

        # 4. Generate order ID and signature
        order_id = self._generate_order_id()
        signature = self._generate_signature(order_id, telegram_id, price)

        # 5. Allocate HD wallet address
        address = self.wallet_manager.allocate_address(order_id, telegram_id)
        if not address:
            from .config.constants import MSG_ADDRESS_ALLOCATION_FAILED
            raise RuntimeError(MSG_ADDRESS_ALLOCATION_FAILED)

        # Get address index
        address_info = self.wallet_manager.get_address_by_order(order_id)
        address_index: int = address_info['derive_index'] if address_info else 0

        # 6. Calculate expiry
        expire_at = datetime.now() + timedelta(minutes=self.expire_minutes)

        # 7. Snapshot plan duration + Alpha bonus days
        duration_days = plan['duration_days'] + bonus_days

        # 8. Write to database (OrderDAO ensures consistency)
        # Convert discount_type to DB format (uppercase)
        db_discount_type = discount_type.upper() if discount_type else 'NONE'

        try:
            success = self.order_dao.create_order(
                order_id=order_id,
                order_signature=signature,
                telegram_id=telegram_id,
                membership_type=plan_code,
                expected_amount=price,
                expire_at=expire_at,
                payment_address=address,
                address_index=address_index,
                duration_days=duration_days,
                telegram_username=telegram_username,
                discount_type=db_discount_type
            )

            if not success:
                raise RuntimeError("Failed to write order to database")

        except Exception as e:
            # Order creation failed: release the allocated address (compensation)
            logger.error(f"Order creation failed, releasing address: order_id={order_id}, error={e}")
            self.wallet_manager.release_address(address)
            from .config.constants import MSG_ORDER_CREATION_FAILED
            raise RuntimeError(MSG_ORDER_CREATION_FAILED) from e

        logger.info(
            f"Order created: order_id={order_id}, "
            f"telegram_id={telegram_id}, "
            f"amount={price}, "
            f"address={address}, "
            f"duration_days={duration_days}"
        )

        return {
            'order_id': order_id,
            'payment_address': address,
            'amount': price,
            'original_price': original_price,
            'discount_type': discount_type,
            'bonus_days': bonus_days,
            'duration_days': duration_days,
            'expire_at': expire_at,
            'is_existing': False,
            'plan_name': plan['plan_name']
        }

    def _create_order_with_db_lock(
        self,
        telegram_id: int,
        plan_code: str,
        telegram_username: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create an order using a DB row-level lock (Redis fallback).

        Flow:
        1. Start transaction
        2. SELECT ... FOR UPDATE to lock any existing PENDING order for this user
        3. Check for existing valid PENDING order
        4. If none, create a new order
        5. Commit transaction to release lock

        Notes:
        - Uses telegram_id+status index on payment_orders table
        - Lock granularity: per-user (serializes all order operations for a user)
        - Slightly slower than Redis lock but guarantees consistency

        Args:
            telegram_id: Telegram user ID
            plan_code: Plan code
            telegram_username: Telegram username (optional)

        Returns:
            Order info dict

        Raises:
            ValueError: Plan not found or disabled
            RuntimeError: Address allocation failed or DB error
        """
        conn = None
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()

            # 1. Lock user's PENDING order rows (prevent concurrency)
            cursor.execute(
                """
                SELECT order_id, order_signature, telegram_id, membership_type,
                       expected_amount, payment_address, address_index,
                       status, expire_at, version, created_at
                FROM payment_orders
                WHERE telegram_id = %s AND status = 'PENDING' AND expire_at > NOW()
                FOR UPDATE
                """,
                (telegram_id,)
            )
            existing_row = cursor.fetchone()

            # 2. Check if there is already a PENDING order for this plan
            if existing_row:
                columns = [desc[0] for desc in cursor.description]
                existing_order = dict(zip(columns, existing_row))

                if existing_order['membership_type'] == plan_code:
                    # Return existing order (idempotent)
                    conn.commit()
                    logger.info(
                        f"[DB lock] Returning existing PENDING order: telegram_id={telegram_id}, "
                        f"order_id={existing_order['order_id']}"
                    )
                    plan = self.plan_dao.get_plan_by_code(plan_code)
                    return {
                        'order_id': existing_order['order_id'],
                        'payment_address': existing_order['payment_address'],
                        'amount': existing_order['expected_amount'],
                        'original_price': existing_order['expected_amount'],
                        'discount_type': None,
                        'expire_at': existing_order['expire_at'],
                        'is_existing': True,
                        'plan_name': plan['plan_name'] if plan else plan_code
                    }

            # 3. Create new order (reuse internal logic)
            result = self._create_order_internal(
                telegram_id, plan_code, telegram_username
            )

            # 4. Commit transaction
            conn.commit()
            logger.info(
                f"[DB lock] Order created: telegram_id={telegram_id}, "
                f"order_id={result['order_id']}"
            )
            return result

        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"[DB lock] Order creation failed: telegram_id={telegram_id}, error={e}")
            raise

        finally:
            if conn:
                conn.close()

    def verify_signature(
        self,
        order_id: str,
        telegram_id: int,
        signature: str
    ) -> bool:
        """
        Verify order signature (anti-forgery).

        Args:
            order_id: Order ID
            telegram_id: Telegram user ID
            signature: Signature to verify

        Returns:
            True if signature is valid, False otherwise
        """
        order = self.order_dao.get_order_by_id(order_id)
        if not order:
            return False

        expected_signature = self._generate_signature(
            order_id, telegram_id, order['expected_amount']
        )

        # Constant-time comparison (prevents timing attacks)
        return hmac.compare_digest(signature, expected_signature)

    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Query order status.

        Args:
            order_id: Order ID

        Returns:
            Order info, or None if not found
        """
        return self.order_dao.get_order_by_id(order_id)

    def get_user_pending_order(
        self,
        telegram_id: int,
        plan_code: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Query the user's PENDING order.

        Args:
            telegram_id: Telegram user ID
            plan_code: Plan code (optional; if omitted, returns any plan type)

        Returns:
            PENDING order, or None if not found
        """
        return self._find_existing_pending_order(telegram_id, plan_code)

    def cancel_expired_orders(self) -> int:
        """
        Batch-cancel expired orders and release their addresses.

        Flow:
        1. Fetch expired order list
        2. Mark orders as EXPIRED
        3. Release allocated HD wallet addresses back to the pool

        Returns:
            Number of orders cancelled
        """
        expired_orders = self.order_dao.get_expired_pending_orders()
        count = 0

        for order in expired_orders:
            order_id = order['order_id']
            version = order['version']
            address = order.get('payment_address')

            # 1. Mark order as expired
            if self.order_dao.expire_order(order_id, version):
                count += 1

                # 2. Release address back to pool
                if address:
                    try:
                        self.wallet_manager.release_address(address)
                        logger.info(f"Expired order address released: order_id={order_id}, address={address}")
                    except Exception as e:
                        logger.warning(f"Failed to release expired order address: order_id={order_id}, error={e}")

        if count > 0:
            logger.info(f"Batch order expiry done: {count} orders")

        return count

    def _find_existing_pending_order(
        self,
        telegram_id: int,
        plan_code: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Find a valid PENDING order for the user.

        Args:
            telegram_id: Telegram user ID
            plan_code: Plan code (optional)

        Returns:
            Valid PENDING order, or None if not found
        """
        sql = """
            SELECT order_id, order_signature, telegram_id, membership_type,
                   expected_amount, payment_address, address_index,
                   status, expire_at, version, created_at
            FROM payment_orders
            WHERE telegram_id = %s
              AND status = %s
              AND expire_at > NOW()
        """
        params = [telegram_id, OrderStatus.PENDING.value]

        if plan_code:
            sql += " AND membership_type = %s"
            params.append(plan_code)

        sql += " ORDER BY created_at DESC LIMIT 1"

        result = self.db.execute_query(sql, tuple(params), fetch_one=True)
        return result if isinstance(result, dict) else None

    def _generate_order_id(self) -> str:
        """
        Generate an order ID.

        Format: YYYYMMDD-XXXX
        - YYYYMMDD: date
        - XXXX: 4-char random string (uppercase letters + digits, excluding ambiguous chars)

        Returns:
            Order ID string
        """
        date_part = datetime.now().strftime('%Y%m%d')
        # Exclude easily confused characters: 0O1IL
        chars = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
        random_part = ''.join(secrets.choice(chars) for _ in range(4))
        return f"{date_part}-{random_part}"

    def _generate_signature(
        self,
        order_id: str,
        telegram_id: int,
        amount: Decimal
    ) -> str:
        """
        Generate an HMAC-SHA256 signature.

        Signed payload: order_id:telegram_id:amount
        Uses HMAC-SHA256 to prevent tampering.

        Args:
            order_id: Order ID
            telegram_id: Telegram user ID
            amount: Order amount

        Returns:
            Signature string (64-char hex)
        """
        sign_data = f"{order_id}:{telegram_id}:{amount}".encode('utf-8')
        return hmac.new(
            self.secret_key.encode('utf-8'),
            sign_data,
            hashlib.sha256
        ).hexdigest()

    def _calculate_price(
        self,
        telegram_id: int,
        original_price: Decimal,
        level: int
    ) -> tuple:
        """
        Calculate dynamic pricing (delegates to PricingEngine).

        Priority: Alpha discount > Trader discount > list price

        Args:
            telegram_id: Telegram user ID
            original_price: List price
            level: Plan level (1=Basic, 2=Premium)

        Returns:
            (final_price, discount_type, bonus_days) tuple
            discount_type: 'alpha' | 'trader' | None
            bonus_days: Extra bonus days (Alpha early-bird Premium bonus)
        """
        engine = PricingEngine()
        result = engine.calculate(original_price, level, telegram_id)

        discount_type = None
        if result.has_discount:
            discount_type = result.discount_type.value  # 'alpha' | 'trader'

        logger.info(
            f"Pricing: telegram_id={telegram_id}, "
            f"original={original_price}, final={result.final_price}, "
            f"discount={discount_type}, bonus_days={result.bonus_days}"
        )

        return (result.final_price, discount_type, result.bonus_days)


class OrderIdGenerator:
    """
    Order ID generator (static utility class).

    Used where standalone order ID generation is needed.
    """

    # Exclude easily confused characters
    CHARS = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'

    @staticmethod
    def generate() -> str:
        """
        Generate an order ID.

        Format: YYYYMMDD-XXXX

        Returns:
            Order ID string
        """
        date_part = datetime.now().strftime('%Y%m%d')
        random_part = ''.join(
            secrets.choice(OrderIdGenerator.CHARS) for _ in range(4)
        )
        return f"{date_part}-{random_part}"

    @staticmethod
    def parse(order_id: str) -> Optional[Dict[str, str]]:
        """
        Parse an order ID.

        Args:
            order_id: Order ID

        Returns:
            {'date': 'YYYYMMDD', 'code': 'XXXX'} or None
        """
        try:
            parts = order_id.split('-')
            if len(parts) != 2:
                return None

            date_part, code_part = parts

            # Date must be 8 digits
            if len(date_part) != 8:
                return None

            # Validate date format
            datetime.strptime(date_part, '%Y%m%d')

            # Random code must be 4 chars
            if len(code_part) != 4:
                return None

            return {'date': date_part, 'code': code_part}

        except (ValueError, IndexError):
            return None

    @staticmethod
    def is_valid_format(order_id: str) -> bool:
        """
        Validate order ID format.

        Args:
            order_id: Order ID

        Returns:
            True if format is valid, False otherwise
        """
        return OrderIdGenerator.parse(order_id) is not None
