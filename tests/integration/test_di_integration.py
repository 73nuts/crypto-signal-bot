"""
DI container integration tests.

Validates:
1. Bootstrap correctly initializes dependencies
2. Mocks can replace production dependencies
3. Dependency resolution chain works correctly
4. Protocol implementations satisfy the interface

How to run:
    pytest tests/integration/test_di_integration.py -v
"""
import unittest


class TestBootstrapIntegration(unittest.TestCase):
    """Bootstrap integration tests."""

    def setUp(self):
        """Reset container before each test."""
        from src.core.container import _reset_container
        _reset_container()

    def tearDown(self):
        """Reset container after each test."""
        from src.core.container import _reset_container
        _reset_container()

    def test_bootstrap_test_environment(self):
        """Test environment Bootstrap should register all mock dependencies."""
        from src.core.bootstrap import bootstrap
        from src.core.protocols import (
            TradingClientProtocol,
            DatabasePoolProtocol,
            MembershipRepositoryProtocol,
            PositionRepositoryProtocol,
            MessageBusProtocol,
            MemberServiceProtocol,
            NotifierProtocol,
            BroadcasterProtocol,
        )
        from src.core.container import inject

        # Bootstrap test environment
        container = bootstrap('test')

        # Verify all dependencies can be resolved
        client = inject(TradingClientProtocol)
        self.assertIsNotNone(client)

        db = inject(DatabasePoolProtocol)
        self.assertIsNotNone(db)

        member_repo = inject(MembershipRepositoryProtocol)
        self.assertIsNotNone(member_repo)

        position_repo = inject(PositionRepositoryProtocol)
        self.assertIsNotNone(position_repo)

        bus = inject(MessageBusProtocol)
        self.assertIsNotNone(bus)

        member_service = inject(MemberServiceProtocol)
        self.assertIsNotNone(member_service)

        notifier = inject(NotifierProtocol)
        self.assertIsNotNone(notifier)

        broadcaster = inject(BroadcasterProtocol)
        self.assertIsNotNone(broadcaster)

    def test_mock_trading_client_satisfies_protocol(self):
        """MockTradingClient should satisfy TradingClientProtocol."""
        from src.core.protocols import TradingClientProtocol
        from tests.mocks.mock_trading_client import MockTradingClient

        client = MockTradingClient()

        # Verify protocol satisfaction
        self.assertTrue(isinstance(client, TradingClientProtocol))

        # Verify methods are callable
        balance = client.get_balance()
        self.assertIsInstance(balance, dict)

        order = client.create_market_order('BTCUSDT', 'BUY', 0.1)
        self.assertIn('orderId', order)

        stop_order = client.create_stop_order('BTCUSDT', 'SELL', 0.1, 45000.0)
        self.assertIn('orderId', stop_order)

        result = client.cancel_order('BTCUSDT', '123')
        self.assertTrue(result)

    def test_mock_database_pool_satisfies_protocol(self):
        """MockDatabasePool should satisfy DatabasePoolProtocol."""
        from src.core.protocols import DatabasePoolProtocol
        from tests.mocks.mock_database import MockDatabasePool

        pool = MockDatabasePool()

        # Verify protocol satisfaction
        self.assertTrue(isinstance(pool, DatabasePoolProtocol))

        # Verify methods are callable
        conn = pool.get_connection()
        self.assertIsNotNone(conn)

        self.assertTrue(pool.health_check())


class TestContainerOverrideIntegration(unittest.TestCase):
    """Container override integration tests."""

    def setUp(self):
        from src.core.container import _reset_container
        _reset_container()

    def tearDown(self):
        from src.core.container import _reset_container
        _reset_container()

    def test_override_replaces_dependency(self):
        """Override should replace a dependency."""
        from src.core.bootstrap import bootstrap
        from src.core.protocols import TradingClientProtocol
        from src.core.container import get_container, inject

        # Bootstrap first
        bootstrap('test')

        # Create custom mock
        class CustomMockClient:
            def get_balance(self):
                return {'USDT': 99999.0}

            def create_market_order(self, symbol, side, quantity):
                return {'orderId': 'custom'}

            def create_stop_order(self, symbol, side, quantity, stop_price):
                return {}

            def cancel_order(self, symbol, order_id):
                return True

            def get_position(self, symbol):
                return None

        custom_client = CustomMockClient()

        # Override
        get_container().override(TradingClientProtocol, custom_client)

        # Verify override takes effect
        resolved = inject(TradingClientProtocol)
        self.assertIs(resolved, custom_client)
        self.assertEqual(resolved.get_balance()['USDT'], 99999.0)


class TestMockFunctionality(unittest.TestCase):
    """Mock functionality integration tests."""

    def test_mock_trading_client_state_management(self):
        """MockTradingClient should correctly manage state."""
        from tests.mocks.mock_trading_client import MockTradingClient

        client = MockTradingClient()

        # Initial balance
        self.assertEqual(client.get_balance()['USDT'], 10000.0)

        # Position updated after order
        client.create_market_order('BTCUSDT', 'BUY', 0.5)
        position = client.get_position('BTCUSDT')
        self.assertEqual(position['quantity'], 0.5)

        # Another order
        client.create_market_order('BTCUSDT', 'BUY', 0.3)
        position = client.get_position('BTCUSDT')
        self.assertEqual(position['quantity'], 0.8)

        # Sell
        client.create_market_order('BTCUSDT', 'SELL', 0.2)
        position = client.get_position('BTCUSDT')
        self.assertAlmostEqual(position['quantity'], 0.6, places=5)

    def test_mock_message_bus_event_tracking(self):
        """MockMessageBus should track events."""
        import asyncio
        from tests.mocks.mock_message_bus import MockMessageBus

        bus = MockMessageBus()

        # Publish events
        async def test():
            await bus.publish('test_event', {'key': 'value'})
            await bus.publish('test_event', {'key': 'value2'})
            await bus.publish('other_event', {'data': 123})

        asyncio.run(test())

        # Verify event records
        self.assertEqual(bus.get_event_count(), 3)
        self.assertEqual(bus.get_event_count('test_event'), 2)
        self.assertEqual(bus.get_event_count('other_event'), 1)

    def test_mock_member_service_lifecycle(self):
        """MockMemberService should correctly handle membership lifecycle."""
        from tests.mocks.mock_services import MockMemberService

        service = MockMemberService()

        # Activate membership
        result = service.activate_or_renew(12345, 'PREMIUM_M', 30)
        self.assertTrue(result['success'])

        # Check validity
        self.assertTrue(service.check_valid(12345))

        # Force expire
        service.force_expire_membership(12345, 'test')
        self.assertFalse(service.check_valid(12345))

        # Get status
        status = service.get_member_status(12345)
        self.assertEqual(status['status'], 'EXPIRED')


class TestDependencyChain(unittest.TestCase):
    """Dependency chain integration tests."""

    def setUp(self):
        from src.core.container import _reset_container
        _reset_container()

    def tearDown(self):
        from src.core.container import _reset_container
        _reset_container()

    def test_singleton_returns_same_instance(self):
        """Singleton should return the same instance."""
        from src.core.bootstrap import bootstrap
        from src.core.protocols import TradingClientProtocol
        from src.core.container import inject

        bootstrap('test')

        client1 = inject(TradingClientProtocol)
        client2 = inject(TradingClientProtocol)

        self.assertIs(client1, client2)

    def test_reset_clears_singleton_cache(self):
        """Reset should clear singleton cache."""
        from src.core.bootstrap import bootstrap
        from src.core.protocols import TradingClientProtocol
        from src.core.container import inject, get_container

        bootstrap('test')

        client1 = inject(TradingClientProtocol)

        # Reset
        get_container().reset()

        # Re-bootstrap
        bootstrap('test')

        client2 = inject(TradingClientProtocol)

        self.assertIsNot(client1, client2)


if __name__ == '__main__':
    unittest.main()
