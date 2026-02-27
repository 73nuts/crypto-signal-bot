"""
Mock repository implementations.

In-memory repositories for testing.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional


class MockMembershipRepository:
    """Mock membership repository."""

    def __init__(self):
        self._members: Dict[int, Dict[str, Any]] = {}
        self._id_counter = 0

    def find_by_telegram_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Find by Telegram ID."""
        return self._members.get(telegram_id)

    def find_by_binance_uid(self, binance_uid: str) -> Optional[Dict[str, Any]]:
        """Find by Binance UID."""
        for member in self._members.values():
            if member.get('binance_uid') == binance_uid:
                return member
        return None

    def create(self, member: Dict[str, Any]) -> int:
        """Create a member."""
        self._id_counter += 1
        member['id'] = self._id_counter
        telegram_id = member.get('telegram_id')
        if telegram_id:
            self._members[telegram_id] = member
        return self._id_counter

    def find_active_members(self) -> List[Dict[str, Any]]:
        """Get active members."""
        return [
            m for m in self._members.values()
            if m.get('status') == 'ACTIVE'
        ]

    # Test helper methods
    def add_member(
        self,
        telegram_id: int,
        status: str = 'ACTIVE',
        level: int = 1,
        expire_date: datetime = None
    ):
        """Add a member (for testing)."""
        self._id_counter += 1
        self._members[telegram_id] = {
            'id': self._id_counter,
            'telegram_id': telegram_id,
            'status': status,
            'level': level,
            'expire_date': expire_date or datetime.now(),
            'created_at': datetime.now()
        }

    def clear(self):
        """Clear all data (for testing)."""
        self._members.clear()
        self._id_counter = 0


class MockPositionRepository:
    """Mock position repository."""

    def __init__(self):
        self._positions: Dict[int, Dict[str, Any]] = {}
        self._id_counter = 0

    def find_by_id(self, position_id: int) -> Optional[Dict[str, Any]]:
        """Find by ID."""
        return self._positions.get(position_id)

    def find_open_positions(self, symbol: str = None) -> List[Dict[str, Any]]:
        """Find open positions."""
        positions = [
            p for p in self._positions.values()
            if p.get('status') == 'OPEN'
        ]
        if symbol:
            positions = [p for p in positions if p.get('symbol') == symbol]
        return positions

    def create(self, position: Dict[str, Any]) -> int:
        """Create a position."""
        self._id_counter += 1
        position['id'] = self._id_counter
        position['status'] = position.get('status', 'OPEN')
        self._positions[self._id_counter] = position
        return self._id_counter

    def update_status(self, position_id: int, status: str) -> bool:
        """Update status."""
        if position_id in self._positions:
            self._positions[position_id]['status'] = status
            return True
        return False

    # Test helper methods
    def add_position(
        self,
        symbol: str,
        side: str = 'LONG',
        entry_price: float = 50000.0,
        quantity: float = 0.1,
        status: str = 'OPEN'
    ) -> int:
        """Add a position (for testing)."""
        return self.create({
            'symbol': symbol,
            'side': side,
            'entry_price': entry_price,
            'quantity': quantity,
            'status': status,
            'created_at': datetime.now()
        })

    def clear(self):
        """Clear all data (for testing)."""
        self._positions.clear()
        self._id_counter = 0
