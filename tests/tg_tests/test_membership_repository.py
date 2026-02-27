"""
MembershipRepository unit tests.

TDD: write tests first, then implement.

Run:
    pytest tests/tg_tests/test_membership_repository.py -v
"""
import unittest
from datetime import datetime, timedelta

from src.telegram.repositories.membership_repository import MembershipRepository


class TestMembershipRepository(unittest.TestCase):
    """MembershipRepository unit tests."""

    def setUp(self):
        """Reset before each test."""
        self.repo = MembershipRepository()

    # ========================================
    # CREATE tests
    # ========================================

    def test_create_returns_id(self):
        """Create membership returns new ID."""
        result = self.repo.create(
            telegram_id=123456789,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            activated_by_order_id='ORD-001',
            telegram_username='testuser'
        )
        self.assertIsNotNone(result)
        self.assertIsInstance(result, int)

    def test_create_without_username(self):
        """Create membership (no username)."""
        result = self.repo.create(
            telegram_id=987654321,
            membership_type='PREMIUM_Y',
            duration_days=365,
            level=2,
            activated_by_order_id='ORD-002'
        )
        self.assertIsNotNone(result)

    # ========================================
    # READ tests
    # ========================================

    def test_find_by_telegram_id_returns_dict(self):
        """Query by telegram_id returns dict."""
        # Create first
        self.repo.create(
            telegram_id=111222333,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            activated_by_order_id='ORD-003'
        )

        # Query
        result = self.repo.find_by_telegram_id(111222333)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)
        self.assertEqual(result['telegram_id'], 111222333)
        self.assertEqual(result['membership_type'], 'BASIC_M')

    def test_find_by_telegram_id_not_found_returns_none(self):
        """Query non-existent user returns None."""
        result = self.repo.find_by_telegram_id(999999999)
        self.assertIsNone(result)

    def test_find_by_username(self):
        """Query by username."""
        self.repo.create(
            telegram_id=444555666,
            membership_type='PREMIUM_M',
            duration_days=30,
            level=2,
            activated_by_order_id='ORD-004',
            telegram_username='findme'
        )

        result = self.repo.find_by_username('findme')
        self.assertIsNotNone(result)
        self.assertEqual(result['telegram_username'], 'findme')

    def test_find_by_username_with_at_sign(self):
        """Username with @ prefix can also be queried."""
        self.repo.create(
            telegram_id=777888999,
            membership_type='BASIC_Y',
            duration_days=365,
            level=1,
            activated_by_order_id='ORD-005',
            telegram_username='testuser2'
        )

        result = self.repo.find_by_username('@testuser2')
        self.assertIsNotNone(result)

    def test_find_by_id(self):
        """Query by membership ID."""
        member_id = self.repo.create(
            telegram_id=123123123,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            activated_by_order_id='ORD-006'
        )

        result = self.repo.find_by_id(member_id)
        self.assertIsNotNone(result)
        self.assertEqual(result['id'], member_id)

    # ========================================
    # UPDATE tests
    # ========================================

    def test_update_status(self):
        """Update membership status."""
        self.repo.create(
            telegram_id=321321321,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            activated_by_order_id='ORD-007'
        )

        # Get version
        member = self.repo.find_by_telegram_id(321321321)
        version = member['version']

        # Update status
        success = self.repo.update_status(321321321, 'EXPIRED', version)
        self.assertTrue(success)

        # Verify
        updated = self.repo.find_by_telegram_id(321321321)
        self.assertEqual(updated['status'], 'EXPIRED')

    def test_update_with_wrong_version_returns_false(self):
        """Optimistic lock: wrong version update fails."""
        self.repo.create(
            telegram_id=654654654,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            activated_by_order_id='ORD-008'
        )

        # Use wrong version number
        success = self.repo.update_status(654654654, 'EXPIRED', version=999)
        self.assertFalse(success)

    def test_update_expiry(self):
        """Update expiry date (renewal)."""
        self.repo.create(
            telegram_id=789789789,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            activated_by_order_id='ORD-009'
        )

        member = self.repo.find_by_telegram_id(789789789)
        version = member['version']
        new_expire = datetime.now() + timedelta(days=60)

        success = self.repo.update_expiry(
            telegram_id=789789789,
            new_expire=new_expire,
            membership_type='BASIC_Y',
            level=1,
            order_id='ORD-010',
            version=version
        )
        self.assertTrue(success)

        updated = self.repo.find_by_telegram_id(789789789)
        self.assertEqual(updated['membership_type'], 'BASIC_Y')
        self.assertEqual(updated['version'], version + 1)

    def test_update_language(self):
        """Update language preference."""
        self.repo.create(
            telegram_id=456456456,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            activated_by_order_id='ORD-011'
        )

        success = self.repo.update_language(456456456, 'zh')
        self.assertTrue(success)

        member = self.repo.find_by_telegram_id(456456456)
        self.assertEqual(member['language'], 'zh')

    # ========================================
    # List query tests
    # ========================================

    def test_find_all_active(self):
        """Query all active members."""
        result = self.repo.find_all_active()
        self.assertIsInstance(result, list)

    def test_find_expiring(self):
        """Query members expiring soon."""
        result = self.repo.find_expiring(days=3)
        self.assertIsInstance(result, list)

    def test_count_by_level(self):
        """Count members by level."""
        count = self.repo.count_by_level(level=2)
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 0)


class TestMembershipRepositoryTableName(unittest.TestCase):
    """Table name attribute tests."""

    def test_table_name(self):
        """Verify table name."""
        repo = MembershipRepository()
        self.assertEqual(repo.table_name, 'memberships')


if __name__ == '__main__':
    unittest.main()
