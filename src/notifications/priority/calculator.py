"""
Priority calculator.

Calculates message priority based on event type and thresholds.

Priority definitions:
- P0 Pinned: VIP signals / Basic extreme events (spread>=10%, orderbook>=87%) -> BASIC+PREMIUM
- P1 Important: Premium early warning (spread>=3%, orderbook>=74%, alert score>=90) -> PREMIUM only
- P2 Normal: Others (routed to Digest queue)
"""

import logging
from enum import IntEnum
from typing import Optional, Union

from src.core.events import (
    AlertDetectedEvent,
    BaseEvent,
    OrderbookImbalanceEvent,
    SpreadDetectedEvent,
)

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """Priority enum (lower value = higher priority)."""
    P0 = 0  # Pinned: VIP signals, extreme events
    P1 = 1  # Important: high-threshold events
    P2 = 2  # Normal: routed to Digest queue


class PriorityCalculator:
    """Priority calculator.

    Calculates message priority based on event type and numeric thresholds.
    """

    # Threshold config
    # Alert events
    ALERT_P0_SCORE = 95.0   # score >= 95 -> P0
    ALERT_P1_SCORE = 90.0   # score >= 90 -> P1

    # Spread events
    SPREAD_P0_PCT = 10.0    # abs(spread) >= 10% -> P0 (Basic extreme)
    SPREAD_P1_PCT = 3.0     # abs(spread) >= 3% -> P1 (Premium early warning)

    # Orderbook events
    ORDERBOOK_P0_PCT = 87.0  # imbalance >= 87% -> P0 (Basic extreme)
    ORDERBOOK_P1_PCT = 74.0  # imbalance >= 74% -> P1 (Premium early warning)

    def __init__(self):
        logger.info("PriorityCalculator initialized")

    def calculate(
        self,
        event: Union[AlertDetectedEvent, SpreadDetectedEvent, OrderbookImbalanceEvent, BaseEvent]
    ) -> Priority:
        """Calculate event priority.

        Args:
            event: Event object

        Returns:
            Priority enum value
        """
        if isinstance(event, AlertDetectedEvent):
            return self._calculate_alert_priority(event)
        elif isinstance(event, SpreadDetectedEvent):
            return self._calculate_spread_priority(event)
        elif isinstance(event, OrderbookImbalanceEvent):
            return self._calculate_orderbook_priority(event)
        else:
            # Unknown event type defaults to P2
            logger.debug(f"Unknown event type: {type(event).__name__}, default P2")
            return Priority.P2

    def _calculate_alert_priority(self, event: AlertDetectedEvent) -> Priority:
        """Calculate alert event priority."""
        score = event.score

        if score >= self.ALERT_P0_SCORE:
            logger.debug(f"Alert {event.symbol}: score={score:.1f} -> P0")
            return Priority.P0
        elif score >= self.ALERT_P1_SCORE:
            logger.debug(f"Alert {event.symbol}: score={score:.1f} -> P1")
            return Priority.P1
        else:
            logger.debug(f"Alert {event.symbol}: score={score:.1f} -> P2")
            return Priority.P2

    def _calculate_spread_priority(self, event: SpreadDetectedEvent) -> Priority:
        """Calculate spread event priority."""
        abs_spread = abs(event.spread_pct)

        if abs_spread >= self.SPREAD_P0_PCT:
            logger.debug(f"Spread {event.symbol}: {abs_spread:.2f}% -> P0")
            return Priority.P0
        elif abs_spread >= self.SPREAD_P1_PCT:
            logger.debug(f"Spread {event.symbol}: {abs_spread:.2f}% -> P1")
            return Priority.P1
        else:
            logger.debug(f"Spread {event.symbol}: {abs_spread:.2f}% -> P2")
            return Priority.P2

    def _calculate_orderbook_priority(self, event: OrderbookImbalanceEvent) -> Priority:
        """Calculate orderbook event priority."""
        imbalance_pct = event.imbalance_pct

        if imbalance_pct >= self.ORDERBOOK_P0_PCT:
            logger.debug(f"Orderbook {event.symbol}: {imbalance_pct:.0f}% -> P0")
            return Priority.P0
        elif imbalance_pct >= self.ORDERBOOK_P1_PCT:
            logger.debug(f"Orderbook {event.symbol}: {imbalance_pct:.0f}% -> P1")
            return Priority.P1
        else:
            logger.debug(f"Orderbook {event.symbol}: {imbalance_pct:.0f}% -> P2")
            return Priority.P2


# ==========================================
# Global singleton
# ==========================================

_priority_calculator: Optional[PriorityCalculator] = None


def get_priority_calculator() -> PriorityCalculator:
    """Get global PriorityCalculator instance."""
    global _priority_calculator
    if _priority_calculator is None:
        _priority_calculator = PriorityCalculator()
    return _priority_calculator
