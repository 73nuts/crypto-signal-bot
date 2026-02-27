"""
Protocol definition unit tests.

TDD: write tests first, then implement.

How to run:
    pytest tests/core_tests/test_protocols.py -v
"""
import unittest
from typing import Dict, Any, Optional, List


class TestTradingClientProtocol(unittest.TestCase):
    """TradingClientProtocol tests."""

    def test_protocol_is_runtime_checkable(self):
        """Protocol should support runtime checking."""
        from src.core.protocols import TradingClientProtocol

        # Verify it is runtime_checkable
        self.assertTrue(hasattr(TradingClientProtocol, '__protocol_attrs__'))

    def test_implementation_satisfies_protocol(self):
        """Implementation class should satisfy Protocol."""
        from src.core.protocols import TradingClientProtocol

        class MockClient:
            def get_balance(self) -> Dict[str, float]:
                return {}

            def create_market_order(self, symbol: str, side: str, quantity: float) -> Dict[str, Any]:
                return {}

            def create_stop_order(self, symbol: str, side: str, quantity: float, stop_price: float) -> Dict[str, Any]:
                return {}

            def cancel_order(self, symbol: str, order_id: str) -> bool:
                return True

            def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
                return None

        # Verify implementation satisfies Protocol
        self.assertTrue(isinstance(MockClient(), TradingClientProtocol))

    def test_incomplete_implementation_fails(self):
        """Incomplete implementation does not satisfy Protocol."""
        from src.core.protocols import TradingClientProtocol

        class IncompleteClient:
            def get_balance(self) -> Dict[str, float]:
                return {}
            # Missing other methods

        # Incomplete implementation does not satisfy Protocol
        self.assertFalse(isinstance(IncompleteClient(), TradingClientProtocol))


class TestPositionRepositoryProtocol(unittest.TestCase):
    """PositionRepositoryProtocol tests."""

    def test_protocol_defined(self):
        """Protocol should be correctly defined."""
        from src.core.protocols import PositionRepositoryProtocol

        # Verify core methods exist
        self.assertTrue(hasattr(PositionRepositoryProtocol, 'find_by_id'))
        self.assertTrue(hasattr(PositionRepositoryProtocol, 'find_open_positions'))
        self.assertTrue(hasattr(PositionRepositoryProtocol, 'create'))


class TestMembershipProtocols(unittest.TestCase):
    """Membership-related protocol tests."""

    def test_membership_repository_protocol(self):
        """MembershipRepositoryProtocol should be correctly defined."""
        from src.core.protocols import MembershipRepositoryProtocol

        self.assertTrue(hasattr(MembershipRepositoryProtocol, 'find_by_telegram_id'))
        self.assertTrue(hasattr(MembershipRepositoryProtocol, 'create'))

    def test_member_service_protocol(self):
        """MemberServiceProtocol should be correctly defined."""
        from src.core.protocols import MemberServiceProtocol

        self.assertTrue(hasattr(MemberServiceProtocol, 'check_valid'))
        self.assertTrue(hasattr(MemberServiceProtocol, 'activate_or_renew'))


class TestNotifierProtocol(unittest.TestCase):
    """Notification-related protocol tests."""

    def test_notifier_protocol(self):
        """NotifierProtocol should be correctly defined."""
        from src.core.protocols import NotifierProtocol

        self.assertTrue(hasattr(NotifierProtocol, 'send'))

    def test_broadcaster_protocol(self):
        """BroadcasterProtocol should be correctly defined."""
        from src.core.protocols import BroadcasterProtocol

        self.assertTrue(hasattr(BroadcasterProtocol, 'broadcast'))


class TestMessageBusProtocol(unittest.TestCase):
    """Message bus protocol tests."""

    def test_message_bus_protocol(self):
        """MessageBusProtocol should be correctly defined."""
        from src.core.protocols import MessageBusProtocol

        self.assertTrue(hasattr(MessageBusProtocol, 'subscribe'))
        self.assertTrue(hasattr(MessageBusProtocol, 'publish'))


class TestDatabasePoolProtocol(unittest.TestCase):
    """Database connection pool protocol tests."""

    def test_database_pool_protocol(self):
        """DatabasePoolProtocol should be correctly defined."""
        from src.core.protocols import DatabasePoolProtocol

        self.assertTrue(hasattr(DatabasePoolProtocol, 'get_connection'))
        self.assertTrue(hasattr(DatabasePoolProtocol, 'execute'))
        self.assertTrue(hasattr(DatabasePoolProtocol, 'health_check'))


if __name__ == '__main__':
    unittest.main()
