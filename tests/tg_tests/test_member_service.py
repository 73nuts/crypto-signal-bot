"""
MemberService unit tests.

TDD: write tests first, then implement.

Run:
    pytest tests/tg_tests/test_member_service.py -v
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from src.telegram.services.member_service import MemberService
from src.telegram.repositories.membership_repository import MembershipRepository


class TestMemberServiceCheckMembership(unittest.TestCase):
    """Membership validity check tests."""

    def setUp(self):
        """Reset before each test."""
        self.service = MemberService()
        # Clean test data
        self._cleanup_test_data()

    def _cleanup_test_data(self):
        """Clean test data."""
        test_ids = [100100100, 100100101, 100100102, 100100103]
        for tid in test_ids:
            self.service.repository._db.execute(
                "DELETE FROM memberships WHERE telegram_id = %s",
                (tid,), fetch=None
            )

    def test_check_membership_not_found(self):
        """Non-existent user returns inactive."""
        result = self.service.check_membership_valid(999999999)
        self.assertFalse(result['active'])
        self.assertIsNone(result['membership_type'])

    def test_check_membership_active(self):
        """Active member returns active."""
        # Create membership first
        self.service.activate_or_renew(
            telegram_id=100100100,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            order_id='TEST-001'
        )

        result = self.service.check_membership_valid(100100100)
        self.assertTrue(result['active'])
        self.assertEqual(result['membership_type'], 'BASIC_M')
        self.assertEqual(result['level'], 1)
        self.assertIsNotNone(result['days_remaining'])

    def test_check_membership_expired(self):
        """Expired member returns inactive."""
        # Create membership
        self.service.activate_or_renew(
            telegram_id=100100101,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            order_id='TEST-002'
        )
        # Manually set as expired
        self.service.expire_membership(100100101)

        result = self.service.check_membership_valid(100100101)
        self.assertFalse(result['active'])


class TestMemberServiceActivateRenew(unittest.TestCase):
    """Activate/renew tests."""

    def setUp(self):
        self.service = MemberService()
        self._cleanup_test_data()

    def _cleanup_test_data(self):
        test_ids = [200200200, 200200201, 200200202]
        for tid in test_ids:
            self.service.repository._db.execute(
                "DELETE FROM memberships WHERE telegram_id = %s",
                (tid,), fetch=None
            )

    def test_activate_new_member(self):
        """New user activation returns ID."""
        result = self.service.activate_or_renew(
            telegram_id=200200200,
            membership_type='PREMIUM_M',
            duration_days=30,
            level=2,
            order_id='TEST-003'
        )
        self.assertIsNotNone(result)
        self.assertIsInstance(result, int)

    def test_renew_existing_member(self):
        """Existing user renewal returns same ID."""
        # Activate first
        first_id = self.service.activate_or_renew(
            telegram_id=200200201,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            order_id='TEST-004'
        )

        # Renew
        second_id = self.service.activate_or_renew(
            telegram_id=200200201,
            membership_type='BASIC_Y',
            duration_days=365,
            level=1,
            order_id='TEST-005'
        )

        self.assertEqual(first_id, second_id)

        # Verify type updated
        member = self.service.repository.find_by_telegram_id(200200201)
        self.assertEqual(member['membership_type'], 'BASIC_Y')


class TestMemberServiceExpire(unittest.TestCase):
    """Expiration handling tests."""

    def setUp(self):
        self.service = MemberService()
        self._cleanup_test_data()

    def _cleanup_test_data(self):
        test_ids = [300300300, 300300301]
        for tid in test_ids:
            self.service.repository._db.execute(
                "DELETE FROM memberships WHERE telegram_id = %s",
                (tid,), fetch=None
            )

    def test_expire_membership(self):
        """Expired membership status becomes EXPIRED."""
        self.service.activate_or_renew(
            telegram_id=300300300,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            order_id='TEST-006'
        )

        success = self.service.expire_membership(300300300)
        self.assertTrue(success)

        member = self.service.repository.find_by_telegram_id(300300300)
        self.assertEqual(member['status'], 'EXPIRED')

    def test_expire_nonexistent_returns_false(self):
        """Expiring non-existent user returns False."""
        result = self.service.expire_membership(999999998)
        self.assertFalse(result)


class TestMemberServiceTraderProgram(unittest.TestCase):
    """Trader Program tests."""

    def setUp(self):
        self.service = MemberService()
        self._cleanup_test_data()

    def _cleanup_test_data(self):
        test_ids = [400400400, 400400401]
        for tid in test_ids:
            self.service.repository._db.execute(
                "DELETE FROM memberships WHERE telegram_id = %s",
                (tid,), fetch=None
            )

    def test_is_trader_verified_false(self):
        """Unverified user returns False."""
        self.service.activate_or_renew(
            telegram_id=400400400,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            order_id='TEST-007'
        )
        result = self.service.is_trader_verified(400400400)
        self.assertFalse(result)

    def test_submit_binance_uid(self):
        """Submit UID successfully."""
        self.service.activate_or_renew(
            telegram_id=400400401,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            order_id='TEST-008'
        )

        success = self.service.submit_binance_uid(400400401, '123456789')
        self.assertTrue(success)

        # Verify UID saved
        member = self.service.repository.find_by_telegram_id(400400401)
        self.assertEqual(member['binance_uid'], '123456789')


class TestMemberServiceLanguage(unittest.TestCase):
    """Language settings tests."""

    def setUp(self):
        self.service = MemberService()
        self._cleanup_test_data()

    def _cleanup_test_data(self):
        test_ids = [500500500]
        for tid in test_ids:
            self.service.repository._db.execute(
                "DELETE FROM memberships WHERE telegram_id = %s",
                (tid,), fetch=None
            )

    def test_update_language(self):
        """Update language successfully."""
        self.service.activate_or_renew(
            telegram_id=500500500,
            membership_type='BASIC_M',
            duration_days=30,
            level=1,
            order_id='TEST-009'
        )

        success = self.service.update_language(500500500, 'zh')
        self.assertTrue(success)

        member = self.service.repository.find_by_telegram_id(500500500)
        self.assertEqual(member['language'], 'zh')


class TestMemberServiceStatistics(unittest.TestCase):
    """Statistics tests."""

    def setUp(self):
        self.service = MemberService()

    def test_get_expiring_soon(self):
        """Get members expiring soon."""
        result = self.service.get_expiring_soon(days=7)
        self.assertIsInstance(result, list)

    def test_count_by_level(self):
        """Count members by level."""
        result = self.service.count_by_level()
        self.assertIsInstance(result, dict)
        self.assertIn('basic', result)
        self.assertIn('premium', result)


if __name__ == '__main__':
    unittest.main()
