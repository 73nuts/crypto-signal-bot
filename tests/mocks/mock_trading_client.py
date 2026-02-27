"""
Mock trading client.

For testing only, does not call the Binance API.
"""
from typing import Any, Dict, List, Optional


class MockTradingClient:
    """Mock trading client."""

    def __init__(self):
        self.orders: List[Dict] = []
        self.positions: Dict[str, Dict] = {}
        self.balance = {'USDT': 10000.0}
        self._order_counter = 0

    def get_balance(self) -> Dict[str, float]:
        return self.balance.copy()

    def create_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float
    ) -> Dict[str, Any]:
        self._order_counter += 1
        order = {
            'orderId': str(self._order_counter),
            'symbol': symbol,
            'side': side,
            'quantity': quantity,
            'status': 'FILLED',
            'avgPrice': 50000.0  # Mock price
        }
        self.orders.append(order)

        # Update position
        if symbol not in self.positions:
            self.positions[symbol] = {'quantity': 0}
        if side == 'BUY':
            self.positions[symbol]['quantity'] += quantity
        else:
            self.positions[symbol]['quantity'] -= quantity

        return order

    def create_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float
    ) -> Dict[str, Any]:
        self._order_counter += 1
        return {
            'orderId': str(self._order_counter),
            'symbol': symbol,
            'side': side,
            'quantity': quantity,
            'stopPrice': stop_price,
            'status': 'NEW'
        }

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        return True

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self.positions.get(symbol)

    def invalidate_cache(self) -> None:
        """Invalidate cache (required by Protocol)."""
        pass

    # Test helper methods
    def set_balance(self, currency: str, amount: float):
        """Set balance (for testing)."""
        self.balance[currency] = amount

    def set_position(self, symbol: str, quantity: float, entry_price: float = 50000.0):
        """Set position (for testing)."""
        self.positions[symbol] = {
            'quantity': quantity,
            'entryPrice': entry_price
        }

    def clear(self):
        """Clear all state (for testing)."""
        self.orders.clear()
        self.positions.clear()
        self.balance = {'USDT': 10000.0}
        self._order_counter = 0


class MockTradingClientFactory:
    """Mock trading client factory (for testing)."""

    def __init__(self):
        self._clients: Dict[str, MockTradingClient] = {}

    def create(self, symbol: str, testnet: bool = True) -> MockTradingClient:
        """Create a mock client (reuses existing instances)."""
        if symbol not in self._clients:
            self._clients[symbol] = MockTradingClient()
        return self._clients[symbol]

    def get_client(self, symbol: str) -> Optional[MockTradingClient]:
        """Get an existing client (for test assertions)."""
        return self._clients.get(symbol)
