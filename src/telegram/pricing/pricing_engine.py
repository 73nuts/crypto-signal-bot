"""
Pricing engine.

Centralizes price calculation for UI display and order creation.
Priority: Alpha renewal > Alpha new user (50%) > Trader (30%) > list price.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from dataclasses import dataclass

from src.core.config import settings
from .discount_types import DiscountType

logger = logging.getLogger(__name__)


@dataclass
class PriceResult:
    """Pricing calculation result."""
    final_price: Decimal
    original_price: Decimal
    discount_type: DiscountType
    bonus_days: int

    @property
    def discount_percent(self) -> int:
        """Discount percentage for display."""
        return self.discount_type.discount_percent

    @property
    def has_discount(self) -> bool:
        """True if any discount applies."""
        return self.discount_type.has_discount


class PricingEngine:
    """Pricing engine. Handles Alpha and Trader discount calculation."""

    def __init__(self):
        self._member_service = None
        self._order_dao = None

    def _get_member_service(self):
        """Lazy-load MemberService to avoid circular imports."""
        if self._member_service is None:
            from src.telegram.services.member_service import MemberService
            self._member_service = MemberService()
        return self._member_service

    def _get_order_dao(self):
        """Lazy-load OrderDAO."""
        if self._order_dao is None:
            from src.telegram.database import DatabaseManager, OrderDAO
            db = DatabaseManager()
            self._order_dao = OrderDAO(db)
        return self._order_dao

    def calculate(
        self,
        original_price: Decimal,
        level: int,
        telegram_id: Optional[int] = None
    ) -> PriceResult:
        """
        Calculate the final price for a plan.

        Priority: Alpha renewal > Alpha new user > Trader > list price.
        """
        original = Decimal(str(original_price))

        # 1. Alpha renewal (Premium only, requires telegram_id)
        if level == 2 and telegram_id:
            renewal_result = self._check_alpha_renewal(original, telegram_id)
            if renewal_result:
                return renewal_result

        # 2. Alpha new-user discount (Premium only)
        if level == 2 and settings.ALPHA_PRICING_ENABLED:
            alpha_result = self._check_alpha_discount(original)
            if alpha_result:
                return alpha_result

        # 3. Trader discount
        if telegram_id:
            trader_result = self._check_trader_discount(original, telegram_id)
            if trader_result:
                return trader_result

        # 4. No discount
        return PriceResult(
            final_price=original,
            original_price=original,
            discount_type=DiscountType.NONE,
            bonus_days=0
        )

    def _check_alpha_renewal(
        self,
        original_price: Decimal,
        telegram_id: int
    ) -> Optional[PriceResult]:
        """
        Check Alpha renewal eligibility.

        Qualifies if the user's last confirmed order used Alpha pricing
        and membership is active or expired within the renewal window.
        No bonus days on renewal.
        """
        try:
            order_dao = self._get_order_dao()
            last_order = order_dao.get_last_confirmed_order(telegram_id)

            if not last_order:
                return None

            if last_order.get('discount_type') != 'ALPHA':
                return None

            member_service = self._get_member_service()
            member = member_service.check_membership_valid(telegram_id)

            if not member['active'] and member.get('expire_date'):
                from datetime import datetime
                days_since_expire = (datetime.now() - member['expire_date']).days
                window_days = settings.ALPHA_RENEWAL_WINDOW_DAYS

                if days_since_expire > window_days:
                    logger.info(
                        f"Alpha renewal window closed: telegram_id={telegram_id}, "
                        f"expired {days_since_expire} days ago (window={window_days} days)"
                    )
                    return None

            discount = Decimal(str(settings.ALPHA_DISCOUNT))
            final_price = (original_price * discount).quantize(
                Decimal('1'), rounding=ROUND_HALF_UP
            )

            logger.info(
                f"Alpha renewal eligible: telegram_id={telegram_id}, "
                f"last_order={last_order.get('order_id')}"
            )

            return PriceResult(
                final_price=final_price,
                original_price=original_price,
                discount_type=DiscountType.ALPHA,
                bonus_days=0
            )

        except Exception as e:
            logger.warning(f"Alpha renewal check failed: {e}")
            return None

    def _check_alpha_discount(self, original_price: Decimal) -> Optional[PriceResult]:
        """Check Alpha new-user discount (quota-based)."""
        try:
            service = self._get_member_service()
            premium_count = service.count_premium_users()

            if premium_count < settings.ALPHA_LIMIT:
                discount = Decimal(str(settings.ALPHA_DISCOUNT))
                final_price = (original_price * discount).quantize(
                    Decimal('1'), rounding=ROUND_HALF_UP
                )
                return PriceResult(
                    final_price=final_price,
                    original_price=original_price,
                    discount_type=DiscountType.ALPHA,
                    bonus_days=settings.ALPHA_BONUS_DAYS
                )
        except Exception as e:
            logger.warning(f"Alpha discount check failed: {e}")

        return None

    def _check_trader_discount(
        self,
        original_price: Decimal,
        telegram_id: int
    ) -> Optional[PriceResult]:
        """Check Trader Program discount."""
        try:
            service = self._get_member_service()
            if service.is_trader_verified(telegram_id):
                discount = Decimal(str(settings.TRADER_DISCOUNT))
                final_price = (original_price * discount).quantize(
                    Decimal('1'), rounding=ROUND_HALF_UP
                )
                return PriceResult(
                    final_price=final_price,
                    original_price=original_price,
                    discount_type=DiscountType.TRADER,
                    bonus_days=0
                )
        except Exception as e:
            logger.warning(f"Trader discount check failed: {e}")

        return None

    def get_alpha_remaining(self) -> int:
        """Return the number of remaining Alpha quota slots."""
        try:
            service = self._get_member_service()
            premium_count = service.count_premium_users()
            remaining = settings.ALPHA_LIMIT - premium_count
            return max(0, remaining)
        except Exception:
            return 0

    def is_alpha_active(self) -> bool:
        """Return True if Alpha pricing is enabled and quota remains."""
        if not settings.ALPHA_PRICING_ENABLED:
            return False
        return self.get_alpha_remaining() > 0
