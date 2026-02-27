"""
Mock trading executor

Used for unit testing: simulates trading behavior without making real API calls.

Usage:
    from src.strategies.swing.mock_executor import MockTradingPort

    mock = MockTradingPort()
    scheduler = SwingScheduler(execute_mode=True, executor=mock)

    # Run signal check
    signals = scheduler.check_signals()

    # Verify mock records
    assert len(mock.orders) > 0
    assert mock.has_position('BTC')
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime


class MockTradingPort:
    """
    Mock trading port

    Implements the TradingPort protocol for unit testing.
    """

    def __init__(self):
        """Initialize the mock executor."""
        self._positions: Dict[str, Dict[str, Any]] = {}
        self._orders: List[Dict[str, Any]] = []
        self._position_id_counter = 0
        self.logger = logging.getLogger(__name__)

    @property
    def positions(self) -> Dict[str, Dict[str, Any]]:
        """Return all positions."""
        return self._positions

    @property
    def orders(self) -> List[Dict[str, Any]]:
        """Return all order records."""
        return self._orders

    def has_position(self, symbol: str) -> bool:
        """Check whether a position exists."""
        return symbol in self._positions

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return position details."""
        return self._positions.get(symbol)

    def get_all_positions(self) -> List[Dict[str, Any]]:
        """Return all positions."""
        return list(self._positions.values())

    def execute_entry(
        self,
        symbol: str,
        price: float,
        atr: float,
        strategy_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Simulate entry execution.

        Args:
            symbol: Asset symbol.
            price: Entry price.
            atr: ATR value.
            strategy_name: Strategy name.

        Returns:
            Simulated execution result.
        """
        if self.has_position(symbol):
            self.logger.warning(f"[{symbol}] Position already exists, rejecting entry")
            return None

        self._position_id_counter += 1
        position_id = self._position_id_counter

        # Simulate stop-loss calculation
        stop_loss = price - atr * 2

        position = {
            'id': position_id,
            'symbol': f'{symbol}USDT',
            'side': 'LONG',
            'entry_price': price,
            'quantity': 0.1,  # simulated quantity
            'stop_loss': stop_loss,
            'current_stop': stop_loss,
            'strategy_name': strategy_name,
            'entry_atr': atr,
            'status': 'OPEN',
            'opened_at': datetime.now(),
        }

        self._positions[symbol] = position

        order = {
            'type': 'ENTRY',
            'symbol': symbol,
            'price': price,
            'quantity': 0.1,
            'strategy_name': strategy_name,
            'timestamp': datetime.now(),
        }
        self._orders.append(order)

        self.logger.info(f"[Mock] {symbol} entry @ {price}, stop @ {stop_loss}")

        return {
            'position_id': position_id,
            'order_id': f'mock_order_{len(self._orders)}',
            'price': price,
            'quantity': 0.1,
            'leverage': 3,
        }

    def execute_exit(
        self,
        symbol: str,
        price: float,
        reason: str
    ) -> Optional[Dict[str, Any]]:
        """
        Simulate exit execution.

        Args:
            symbol: Asset symbol.
            price: Exit price.
            reason: Exit reason.

        Returns:
            Simulated execution result.
        """
        if not self.has_position(symbol):
            self.logger.warning(f"[{symbol}] No position, cannot exit")
            return None

        position = self._positions.pop(symbol)
        entry_price = position['entry_price']
        quantity = position['quantity']

        # Calculate PnL
        pnl = (price - entry_price) * quantity
        pnl_percent = (price - entry_price) / entry_price * 100

        order = {
            'type': 'EXIT',
            'symbol': symbol,
            'price': price,
            'quantity': quantity,
            'reason': reason,
            'pnl': pnl,
            'pnl_percent': pnl_percent,
            'timestamp': datetime.now(),
        }
        self._orders.append(order)

        self.logger.info(
            f"[Mock] {symbol} exit @ {price}, "
            f"PnL: {pnl_percent:.2f}%, reason: {reason}"
        )

        return {
            'position_id': position['id'],
            'exit_price': price,
            'pnl': pnl,
            'pnl_percent': pnl_percent,
            'reason': reason,
        }

    def update_trailing_stop(
        self,
        symbol: str,
        new_stop: float
    ) -> Optional[Dict[str, Any]]:
        """
        Simulate trailing stop update.

        Args:
            symbol: Asset symbol.
            new_stop: New stop-loss price.

        Returns:
            Update result.
        """
        if not self.has_position(symbol):
            self.logger.warning(f"[{symbol}] No position, cannot update stop")
            return None

        position = self._positions[symbol]
        old_stop = position['current_stop']

        # Stop can only be raised
        if new_stop <= old_stop:
            self.logger.debug(f"[{symbol}] New stop {new_stop} <= old stop {old_stop}, skipping")
            return None

        position['current_stop'] = new_stop

        self.logger.info(f"[Mock] {symbol} stop raised: {old_stop} -> {new_stop}")

        return {
            'position_id': position['id'],
            'old_stop': old_stop,
            'new_stop': new_stop,
            'symbol': symbol,
        }

    def initialize_all(self):
        """Simulate initialization (no-op)."""
        self.logger.info("[Mock] initialize_all called")

    def reset(self):
        """Reset mock state (use between tests)."""
        self._positions.clear()
        self._orders.clear()
        self._position_id_counter = 0
        self.logger.info("[Mock] State reset")
