"""
Order book analysis module.
Identifies large order walls, calculates bid/ask strength ratio, and provides signal scoring input.
"""

import logging
from typing import List, Optional, TypedDict

from src.data.orderbook_client import OrderbookData


class WallInfo(TypedDict):
    """Large order wall info."""
    price: float
    quantity: float
    distance_percent: float  # Distance from current price as percentage


class OrderbookAnalysis(TypedDict):
    """Order book analysis result."""
    support_wall: Optional[WallInfo]      # Nearest support wall
    resistance_wall: Optional[WallInfo]   # Nearest resistance wall
    bid_ask_ratio: float                  # Bid/ask strength ratio (>1 = bids stronger)
    score: int                            # Score (-10 to +10)


class OrderbookAnalyzer:
    """Order book analyzer - identifies large order walls and bid/ask strength."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def analyze(
        self,
        orderbook: OrderbookData,
        current_price: float,
        action: str
    ) -> OrderbookAnalysis:
        """Analyze order book data and score it.

        Args:
            orderbook: Order book data
            current_price: Current price
            action: Signal type ('LONG', 'SELL', 'SHORT')

        Returns:
            OrderbookAnalysis containing:
                - support_wall: Support wall info (nearest large bid wall)
                - resistance_wall: Resistance wall info (nearest large ask wall)
                - bid_ask_ratio: Bid/ask strength ratio
                - score: Order book score (-10 to +10)
        """
        if not orderbook or not orderbook['bids'] or not orderbook['asks']:
            return self._empty_analysis()

        # 1. Identify large order walls
        support_walls = self._identify_walls(orderbook['bids'], current_price, is_bid=True)
        resistance_walls = self._identify_walls(orderbook['asks'], current_price, is_bid=False)

        # 2. Find nearest large wall
        support_wall = self._find_nearest_wall(support_walls, current_price, is_support=True)
        resistance_wall = self._find_nearest_wall(resistance_walls, current_price, is_support=False)

        # 3. Calculate bid/ask strength ratio
        bid_ask_ratio = self._calculate_strength_ratio(
            orderbook['bids'],
            orderbook['asks'],
            current_price
        )

        # 4. Score based on action
        score = self._score_orderbook(
            support_wall,
            resistance_wall,
            bid_ask_ratio,
            action
        )

        return {
            'support_wall': support_wall,
            'resistance_wall': resistance_wall,
            'bid_ask_ratio': bid_ask_ratio,
            'score': score
        }

    def _identify_walls(
        self,
        orders: List[List[float]],
        current_price: float,
        is_bid: bool,
        threshold: float = 3.0
    ) -> List[WallInfo]:
        """Identify large order walls.

        A wall is defined as: single-level quantity > average quantity × threshold.

        Args:
            orders: Order list [[price, qty], ...]
            current_price: Current price
            is_bid: Whether these are bids (True=bids, False=asks)
            threshold: Wall threshold (default 3× average)

        Returns:
            List[WallInfo]: List of identified walls
        """
        if not orders or len(orders) < 10:
            return []

        # Calculate average quantity
        quantities = [qty for _, qty in orders]
        avg_quantity = sum(quantities) / len(quantities)

        # Identify walls (quantity > average × threshold)
        walls: List[WallInfo] = []
        for price, qty in orders:
            if qty > avg_quantity * threshold:
                distance_percent = abs(price - current_price) / current_price * 100
                walls.append({
                    'price': price,
                    'quantity': qty,
                    'distance_percent': distance_percent
                })

        # Sort by distance from current price (nearest first)
        walls.sort(key=lambda w: w['distance_percent'])

        return walls

    def _find_nearest_wall(
        self,
        walls: List[WallInfo],
        current_price: float,
        is_support: bool
    ) -> Optional[WallInfo]:
        """Find the nearest large order wall.

        Args:
            walls: List of walls
            current_price: Current price
            is_support: Whether this is a support wall (True=support, False=resistance)

        Returns:
            Optional[WallInfo]: Nearest wall, or None if not found
        """
        if not walls:
            return None

        # Filter: only walls within 5% (distant walls are irrelevant)
        nearby_walls = [w for w in walls if w['distance_percent'] < 5.0]

        if not nearby_walls:
            return None

        # Return nearest wall
        return nearby_walls[0]

    def _calculate_strength_ratio(
        self,
        bids: List[List[float]],
        asks: List[List[float]],
        current_price: float
    ) -> float:
        """Calculate bid/ask strength ratio (weighted).

        Weight formula: orders closer to current price get higher weight.
        weight = 1 / (abs(price - current_price) + 1)

        Args:
            bids: Bid order list
            asks: Ask order list
            current_price: Current price

        Returns:
            float: Bid/ask ratio (>1 = bids stronger, <1 = asks stronger)
        """
        if not bids or not asks:
            return 1.0

        # Calculate weighted bid strength
        bid_strength = 0.0
        for price, qty in bids:
            distance = abs(price - current_price)
            weight = 1.0 / (distance + 1)
            bid_strength += qty * weight

        # Calculate weighted ask strength
        ask_strength = 0.0
        for price, qty in asks:
            distance = abs(price - current_price)
            weight = 1.0 / (distance + 1)
            ask_strength += qty * weight

        # Guard against division by zero
        if ask_strength == 0:
            return 10.0  # Edge case: no asks

        return bid_strength / ask_strength

    def _score_orderbook(
        self,
        support_wall: Optional[WallInfo],
        resistance_wall: Optional[WallInfo],
        bid_ask_ratio: float,
        action: str
    ) -> int:
        """Score order book analysis result.

        Scoring rules:
        LONG signal:
        - Support wall within 2% -> +5 points
        - Bid/ask ratio > 1.5 -> +5 points

        SHORT signal:
        - Resistance wall within 2% -> +5 points
        - Bid/ask ratio < 0.67 -> +5 points

        Args:
            support_wall: Support wall info
            resistance_wall: Resistance wall info
            bid_ask_ratio: Bid/ask strength ratio
            action: Signal type

        Returns:
            int: Score (-10 to +10)
        """
        score = 0

        if action in ['LONG', 'SELL']:
            # Long signal: check support wall and bid strength

            # Check support wall (within 2%)
            if support_wall and support_wall['distance_percent'] < 2.0:
                score += 5
                self.logger.info(
                    f"Orderbook +5: support wall within 2% "
                    f"(${support_wall['price']:.2f}, {support_wall['quantity']:.0f}, "
                    f"{support_wall['distance_percent']:.2f}%)"
                )

            # Check bid/ask ratio
            if bid_ask_ratio > 1.5:
                score += 5
                self.logger.info(f"Orderbook +5: bid/ask ratio {bid_ask_ratio:.2f} > 1.5 (strong bids)")
            elif bid_ask_ratio < 0.67:
                score -= 5
                self.logger.warning(f"Orderbook -5: bid/ask ratio {bid_ask_ratio:.2f} < 0.67 (strong asks, unfavorable for long)")

        elif action == 'SHORT':
            # Short signal: check resistance wall and ask strength

            # Check resistance wall (within 2%)
            if resistance_wall and resistance_wall['distance_percent'] < 2.0:
                score += 5
                self.logger.info(
                    f"Orderbook +5: resistance wall within 2% "
                    f"(${resistance_wall['price']:.2f}, {resistance_wall['quantity']:.0f}, "
                    f"{resistance_wall['distance_percent']:.2f}%)"
                )

            # Check bid/ask ratio
            if bid_ask_ratio < 0.67:
                score += 5
                self.logger.info(f"Orderbook +5: bid/ask ratio {bid_ask_ratio:.2f} < 0.67 (strong asks)")
            elif bid_ask_ratio > 1.5:
                score -= 5
                self.logger.warning(f"Orderbook -5: bid/ask ratio {bid_ask_ratio:.2f} > 1.5 (strong bids, unfavorable for short)")

        return max(-10, min(10, score))

    def _empty_analysis(self) -> OrderbookAnalysis:
        """Return empty analysis result (used when data fetch fails)."""
        return {
            'support_wall': None,
            'resistance_wall': None,
            'bid_ask_ratio': 1.0,
            'score': 0
        }
