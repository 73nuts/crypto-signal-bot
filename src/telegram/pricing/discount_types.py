"""
Discount type definitions.
"""

from enum import Enum


class DiscountType(str, Enum):
    """Discount type enum."""

    ALPHA = 'alpha'      # Alpha pricing (50% OFF)
    TRADER = 'trader'    # Trader Program (30% OFF)
    NONE = 'none'        # No discount

    @property
    def discount_percent(self) -> int:
        """Discount percentage for UI display."""
        if self == DiscountType.ALPHA:
            return 50
        elif self == DiscountType.TRADER:
            return 30
        return 0

    @property
    def has_discount(self) -> bool:
        """True if any discount applies."""
        return self != DiscountType.NONE
