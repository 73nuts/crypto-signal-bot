"""
Telegram database access layer (DAO) tests.

Coverage:
1. DatabaseManager connection management
2. MembershipPlanDAO plan configuration
3. OrderDAO order operations (state machine + optimistic lock)
4. MembershipDAO membership management
5. SignalPushDAO push records

Run:
    python -m pytest tests/telegram/test_database_dao.py -v
    or
    python tests/telegram/test_database_dao.py
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.telegram.database.base import DatabaseManager, BaseDAO
from src.telegram.database.order_dao import OrderDAO, OrderStatus, MembershipType
from src.telegram.database.membership_dao import MembershipDAO, MembershipStatus
from src.telegram.database.membership_plan_dao import MembershipPlanDAO
from src.telegram.database.signal_push_dao import SignalPushDAO, SignalType


class TestDatabaseManager(unittest.TestCase):
    """Database manager tests."""

    @classmethod
    def setUpClass(cls):
        """Reset singleton before tests."""
        DatabaseManager.reset_instance()

    def test_singleton(self):
        """Singleton pattern."""
        db1 = DatabaseManager()
        db2 = DatabaseManager()
        self.assertIs(db1, db2, "DatabaseManager must be a singleton")

    def test_connection(self):
        """Database connection."""
        db = DatabaseManager()
        conn = db.get_connection()
        self.assertIsNotNone(conn)
        # Verify connection is valid by executing a query (compatible with connection pool)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            result = cur.fetchone()
            self.assertIsNotNone(result)
        conn.close()

    def test_execute_query(self):
        """Query execution."""
        db = DatabaseManager()
        result = db.execute_query("SELECT 1 as test", fetch_one=True)
        self.assertIsNotNone(result)
        self.assertEqual(result['test'], 1)

    def test_transaction_commit(self):
        """Transaction commit."""
        db = DatabaseManager()
        # Simple transaction test
        with db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            # Normal exit should auto-commit


class TestMembershipPlanDAO(unittest.TestCase):
    """Membership plan DAO tests."""

    def setUp(self):
        DatabaseManager.reset_instance()
        self.dao = MembershipPlanDAO()

    def test_get_all_enabled_plans(self):
        """Get all enabled plans."""
        plans = self.dao.get_all_enabled_plans()
        self.assertIsInstance(plans, list)
        print(f"\nEnabled plans: {len(plans)}")
        for plan in plans:
            print(f"  - {plan['plan_code']}: {plan['price_usdt']} USDT / {plan['duration_days']} days")

    def test_get_plan_by_code(self):
        """Query plan by code."""
        for code in ['WEEK', 'MONTH', 'SEASON']:
            plan = self.dao.get_plan_by_code(code)
            if plan:
                self.assertEqual(plan['plan_code'], code)
                self.assertIn('price_usdt', plan)
                self.assertIn('duration_days', plan)
                print(f"\n{code} plan: {plan['price_usdt']} USDT, {plan['duration_days']} days")

    def test_get_price_by_plan_code(self):
        """Get plan price."""
        price = self.dao.get_price_by_plan_code('WEEK')
        if price:
            self.assertIsInstance(price, Decimal)
            print(f"\nWEEK price: {price} USDT")

    def test_get_level_by_plan_code(self):
        """Get permission level."""
        # Basic plan level=1, Premium plan level=2
        for code in ['BASIC_M', 'BASIC_Y', 'PREMIUM_M', 'PREMIUM_Y']:
            level = self.dao.get_level_by_plan_code(code)
            if level:
                self.assertIsInstance(level, int)
                self.assertIn(level, [1, 2])
                print(f"\n{code} permission level: {level}")


class TestOrderDAO(unittest.TestCase):
    """Order DAO tests."""

    def setUp(self):
        DatabaseManager.reset_instance()
        self.dao = OrderDAO()
        # Generate unique test order ID
        self.test_order_id = f"TEST-ORDER-{datetime.now().strftime('%Y%m%d%H%M%S')}-001"
        self.test_telegram_id = 9999999999  # Test user ID

    def tearDown(self):
        """Clean up test data."""
        try:
            db = DatabaseManager()
            # Clean audit logs
            db.execute_update(
                "DELETE FROM payment_audit_logs WHERE order_id LIKE 'TEST-ORDER-%'"
            )
            # Clean test orders
            db.execute_update(
                "DELETE FROM payment_orders WHERE order_id LIKE 'TEST-ORDER-%'"
            )
        except Exception as e:
            print(f"Failed to clean test data: {e}")

    def test_create_order(self):
        """Create order."""
        result = self.dao.create_order(
            order_id=self.test_order_id,
            order_signature="test_signature_123",
            telegram_id=self.test_telegram_id,
            membership_type=MembershipType.WEEK.value,
            expected_amount=Decimal("19.90"),
            expire_at=datetime.now() + timedelta(hours=24),
            payment_address="0xtest_payment_address_001",
            address_index=1,
            duration_days=7,
            telegram_username="test_user"
        )
        self.assertTrue(result)
        print(f"\nOrder created: {self.test_order_id}")

    def test_get_order_by_id(self):
        """Query order by ID."""
        # Create order first
        self.dao.create_order(
            order_id=self.test_order_id,
            order_signature="test_signature_123",
            telegram_id=self.test_telegram_id,
            membership_type=MembershipType.WEEK.value,
            expected_amount=Decimal("19.90"),
            expire_at=datetime.now() + timedelta(hours=24),
            payment_address="0xtest_payment_address_002",
            address_index=2,
            duration_days=7
        )

        # Query order
        order = self.dao.get_order_by_id(self.test_order_id)
        self.assertIsNotNone(order)
        self.assertEqual(order['order_id'], self.test_order_id)
        self.assertEqual(order['status'], OrderStatus.PENDING.value)
        print(f"\nOrder queried: status={order['status']}")

    def test_order_status_machine(self):
        """Order state machine."""
        # Create order
        self.dao.create_order(
            order_id=self.test_order_id,
            order_signature="test_signature_123",
            telegram_id=self.test_telegram_id,
            membership_type=MembershipType.WEEK.value,
            expected_amount=Decimal("19.90"),
            expire_at=datetime.now() + timedelta(hours=24),
            payment_address="0xtest_payment_address_003",
            address_index=3,
            duration_days=7
        )

        # Get current version
        order = self.dao.get_order_by_id(self.test_order_id)
        version = order['version']

        # Confirm order
        result = self.dao.confirm_order(
            order_id=self.test_order_id,
            tx_hash="0xtest_tx_hash_123",
            actual_amount=Decimal("19.90"),
            from_address="0xtest_from_address",
            version=version
        )
        self.assertTrue(result)

        # Verify status change
        order = self.dao.get_order_by_id(self.test_order_id)
        self.assertEqual(order['status'], OrderStatus.CONFIRMED.value)
        print(f"\nState transition succeeded: PENDING -> CONFIRMED")

    def test_optimistic_lock(self):
        """Optimistic lock."""
        # Create order
        self.dao.create_order(
            order_id=self.test_order_id,
            order_signature="test_signature_123",
            telegram_id=self.test_telegram_id,
            membership_type=MembershipType.WEEK.value,
            expected_amount=Decimal("19.90"),
            expire_at=datetime.now() + timedelta(hours=24),
            payment_address="0xtest_payment_address_004",
            address_index=4,
            duration_days=7
        )

        # Try to update with wrong version number
        result = self.dao.confirm_order(
            order_id=self.test_order_id,
            tx_hash="0xtest_tx_hash_123",
            actual_amount=Decimal("19.90"),
            from_address="0xtest_from_address",
            version=999  # wrong version
        )
        self.assertFalse(result)
        print(f"\nOptimistic lock works: update rejected on version conflict")

    def test_get_pending_orders(self):
        """Get pending orders."""
        # Create test order
        self.dao.create_order(
            order_id=self.test_order_id,
            order_signature="test_signature_123",
            telegram_id=self.test_telegram_id,
            membership_type=MembershipType.WEEK.value,
            expected_amount=Decimal("19.90"),
            expire_at=datetime.now() + timedelta(hours=24),
            payment_address="0xtest_payment_address_005",
            address_index=5,
            duration_days=7
        )

        orders = self.dao.get_pending_orders()
        self.assertIsInstance(orders, list)
        # Should contain the just-created test order
        test_orders = [o for o in orders if o['order_id'] == self.test_order_id]
        self.assertEqual(len(test_orders), 1)
        print(f"\nPending orders: {len(orders)}")


class TestMembershipDAO(unittest.TestCase):
    """Membership DAO tests."""

    def setUp(self):
        DatabaseManager.reset_instance()
        self.dao = MembershipDAO()
        self.db = DatabaseManager()
        self.order_dao = OrderDAO()
        self.test_telegram_id = 8888888888  # Test user ID
        # Shorten order_id to fit field length limit
        self.test_order_id = f"TST-{datetime.now().strftime('%H%M%S')}-M"
        # Create associated order record first (satisfy foreign key constraint)
        self.order_dao.create_order(
            order_id=self.test_order_id,
            order_signature="test_sig_membership",
            telegram_id=self.test_telegram_id,
            membership_type='WEEK',
            expected_amount=Decimal("19.90"),
            expire_at=datetime.now() + timedelta(hours=24),
            payment_address="0xtest_payment_address_mem",
            address_index=100,
            duration_days=7
        )

    def tearDown(self):
        """Clean up test data."""
        try:
            db = DatabaseManager()
            # Delete membership first (foreign key points to order)
            db.execute_update(
                "DELETE FROM memberships WHERE telegram_id = %s",
                (self.test_telegram_id,)
            )
            # Then delete order
            db.execute_update(
                "DELETE FROM payment_orders WHERE order_id = %s",
                (self.test_order_id,)
            )
            # Clean renewal test orders
            db.execute_update(
                "DELETE FROM payment_orders WHERE order_id LIKE 'TST-%-R'"
            )
        except Exception as e:
            print(f"Failed to clean test data: {e}")

    def test_create_membership(self):
        """Create membership."""
        member_id = self.dao.create_membership(
            telegram_id=self.test_telegram_id,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,  # Basic level
            activated_by_order_id=self.test_order_id,
            telegram_username="test_member"
        )
        self.assertIsNotNone(member_id)
        print(f"\nMembership created: id={member_id}")

    def test_check_membership_valid(self):
        """Membership validity check."""
        # Create membership
        self.dao.create_membership(
            telegram_id=self.test_telegram_id,
            membership_type='PREMIUM_M',
            duration_days=30,
            level=2,  # Premium level
            activated_by_order_id=self.test_order_id
        )

        # Check validity
        status = self.dao.check_membership_valid(self.test_telegram_id)
        self.assertTrue(status['active'])
        self.assertEqual(status['membership_type'], 'PREMIUM_M')
        self.assertEqual(status['level'], 2)  # Premium = level 2
        self.assertIsNotNone(status['days_remaining'])
        print(f"\nMembership status: active={status['active']}, level={status['level']}, days_remaining={status['days_remaining']}")

    def test_check_nonexistent_membership(self):
        """Non-existent membership."""
        status = self.dao.check_membership_valid(1111111111)
        self.assertFalse(status['active'])
        self.assertIsNone(status['membership_type'])
        print(f"\nNon-member status: active={status['active']}")

    def test_activate_or_renew(self):
        """Activate or renew membership."""
        # First activation
        member_id = self.dao.activate_or_renew(
            telegram_id=self.test_telegram_id,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,  # Basic level
            activated_by_order_id=self.test_order_id,
            telegram_username="test_user"
        )
        self.assertIsNotNone(member_id)

        # Get expiry date
        member = self.dao.get_membership_by_telegram_id(self.test_telegram_id)
        first_expire = member['expire_date']

        # Renewal - create renewal order first (satisfy foreign key constraint)
        renew_order_id = f"TST-{datetime.now().strftime('%H%M%S')}-R"
        self.order_dao.create_order(
            order_id=renew_order_id,
            order_signature="test_sig_renew",
            telegram_id=self.test_telegram_id,
            membership_type='PREMIUM_M',
            expected_amount=Decimal("59.90"),
            expire_at=datetime.now() + timedelta(hours=24),
            payment_address="0xtest_payment_address_renew",
            address_index=101,
            duration_days=30
        )
        member_id2 = self.dao.activate_or_renew(
            telegram_id=self.test_telegram_id,
            membership_type='PREMIUM_M',
            duration_days=30,
            level=2,  # Upgrade to Premium
            activated_by_order_id=renew_order_id
        )
        self.assertIsNotNone(member_id2)

        # Verify expiry extended after renewal
        member = self.dao.get_membership_by_telegram_id(self.test_telegram_id)
        self.assertGreater(member['expire_date'], first_expire)
        self.assertEqual(member['renewal_count'], 1)
        print(f"\nRenewal succeeded: renewal_count={member['renewal_count']}")


class TestSignalPushDAO(unittest.TestCase):
    """Signal push DAO tests."""

    def setUp(self):
        DatabaseManager.reset_instance()
        self.dao = SignalPushDAO()
        self.db = DatabaseManager()
        self.order_dao = OrderDAO()
        self.membership_dao = MembershipDAO()
        self.test_telegram_id = 7777777777

        # signals table removed, use fixed value for testing
        self.test_signal_id = 1

        # Create associated order and membership records (satisfy foreign key constraints)
        # Shorten order_id to fit field length limit
        self.test_order_id = f"TST-{datetime.now().strftime('%H%M%S')}-P"
        self.order_dao.create_order(
            order_id=self.test_order_id,
            order_signature="test_sig_push",
            telegram_id=self.test_telegram_id,
            membership_type='WEEK',
            expected_amount=Decimal("19.90"),
            expire_at=datetime.now() + timedelta(hours=24),
            payment_address="0xtest_payment_address_push",
            address_index=200,
            duration_days=7
        )
        self.test_membership_id = self.membership_dao.create_membership(
            telegram_id=self.test_telegram_id,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,  # Basic level
            activated_by_order_id=self.test_order_id
        )

    def tearDown(self):
        """Clean up test data."""
        try:
            db = DatabaseManager()
            # Delete in foreign key dependency order
            db.execute_update(
                "DELETE FROM vip_signal_pushes WHERE telegram_id = %s",
                (self.test_telegram_id,)
            )
            db.execute_update(
                "DELETE FROM memberships WHERE telegram_id = %s",
                (self.test_telegram_id,)
            )
            db.execute_update(
                "DELETE FROM payment_orders WHERE order_id = %s",
                (self.test_order_id,)
            )
        except Exception as e:
            print(f"Failed to clean test data: {e}")

    def test_record_push(self):
        """Record push."""
        push_id = self.dao.record_push(
            signal_id=self.test_signal_id,
            telegram_id=self.test_telegram_id,
            membership_id=self.test_membership_id,
            signal_type=SignalType.SWING.value,
            symbol='ETH',
            success=True,
            membership_status_at_push='ACTIVE'
        )
        self.assertIsNotNone(push_id)
        print(f"\nPush record created: id={push_id}")

    def test_has_pushed_to_user(self):
        """Duplicate push detection."""
        # Record push
        self.dao.record_push(
            signal_id=self.test_signal_id,
            telegram_id=self.test_telegram_id,
            membership_id=self.test_membership_id,
            signal_type=SignalType.SWING.value,
            symbol='ETH',
            success=True
        )

        # Check if already pushed
        has_pushed = self.dao.has_pushed_to_user(
            self.test_signal_id, self.test_telegram_id
        )
        self.assertTrue(has_pushed)
        print(f"\nDuplicate push detection: has_pushed={has_pushed}")

    def test_get_push_history(self):
        """Get push history."""
        # signals table removed, use fixed values for testing
        signal_ids = [1, 2, 3]

        # Create push records (using real signal_ids)
        for i, sid in enumerate(signal_ids):
            self.dao.record_push(
                signal_id=sid,
                telegram_id=self.test_telegram_id,
                membership_id=self.test_membership_id,
                signal_type=SignalType.SWING.value if i % 2 == 0 else SignalType.INTRADAY.value,
                symbol='ETH',
                success=True
            )

        history = self.dao.get_push_history(self.test_telegram_id, limit=10)
        self.assertEqual(len(history), len(signal_ids))
        print(f"\nPush history count: {len(history)}")


def run_tests():
    """Run all tests."""
    print("=" * 60)
    print("Telegram DAO Unit Tests")
    print("=" * 60)

    # Check database connection
    try:
        db = DatabaseManager()
        print(f"Database connection: OK")
        print(f"  Host: {db.connection_params['host']}")
        print(f"  Database: {db.connection_params['database']}")
    except Exception as e:
        print(f"Database connection failed: {e}")
        print("Ensure MySQL is running and configured correctly")
        return

    # Check required tables exist
    tables = ['membership_plans', 'payment_orders', 'memberships',
              'payment_audit_logs', 'vip_signal_pushes']
    missing_tables = []

    for table in tables:
        try:
            db.execute_query(f"SELECT 1 FROM {table} LIMIT 1")
        except Exception:
            missing_tables.append(table)

    if missing_tables:
        print(f"\nMissing tables: {missing_tables}")
        print("Run database initialization SQL first")
        return

    print(f"Table check: OK ({len(tables)} tables)")
    print("=" * 60)

    # Run tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestDatabaseManager))
    suite.addTests(loader.loadTestsFromTestCase(TestMembershipPlanDAO))
    suite.addTests(loader.loadTestsFromTestCase(TestOrderDAO))
    suite.addTests(loader.loadTestsFromTestCase(TestMembershipDAO))
    suite.addTests(loader.loadTestsFromTestCase(TestSignalPushDAO))

    # Run
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print("\n" + "=" * 60)
    print(f"Tests complete: {result.testsRun} tests")
    print(f"  Passed: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  Failed: {len(result.failures)}")
    print(f"  Errors: {len(result.errors)}")
    print("=" * 60)

    return result


if __name__ == '__main__':
    run_tests()
