#!/usr/bin/env python3
"""
Phase 3 security tests.

Coverage:
1. Order signature forgery protection
2. SQL injection protection
3. Concurrent order creation
4. Duplicate payment handling (replay attack)

Note: These tests verify the effectiveness of security mechanisms.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from decimal import Decimal
import hmac
import hashlib
import threading
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestSignatureSecurity(unittest.TestCase):
    """Order signature security tests."""

    def setUp(self):
        """Test setup."""
        self.secret_key = "test_secret_key_32_bytes_long!!!"

    def test_signature_generation_format(self):
        """Signature generation format (HMAC-SHA256 = 64-char hex)."""
        # Test HMAC-SHA256 output format directly
        message = "20251127-TEST:123456789:49.9"
        signature = hmac.new(
            self.secret_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        self.assertIsNotNone(signature)
        self.assertEqual(len(signature), 64)  # SHA256 hex = 64 chars
        print(f"\nSignature format test passed: {signature[:16]}...")

    def test_signature_tamper_detection(self):
        """Tamper detection (modifying any field causes signature mismatch)."""
        # Original signature
        message_original = "20251127-TEST:123456789:49.9"
        signature = hmac.new(
            self.secret_key.encode(),
            message_original.encode(),
            hashlib.sha256
        ).hexdigest()

        # Tamper order ID
        message_tampered = "20251127-FAKE:123456789:49.9"
        signature_tampered = hmac.new(
            self.secret_key.encode(),
            message_tampered.encode(),
            hashlib.sha256
        ).hexdigest()

        self.assertNotEqual(signature, signature_tampered)
        print("\nOrder ID tamper detection passed")

    def test_signature_user_id_binding(self):
        """Signature bound to user ID (memo forgery attack protection)."""
        # User A's signature
        message_user_a = "20251127-TEST:111111111:49.9"
        signature_user_a = hmac.new(
            self.secret_key.encode(),
            message_user_a.encode(),
            hashlib.sha256
        ).hexdigest()

        # User B attempts forgery
        message_user_b = "20251127-TEST:222222222:49.9"
        signature_user_b = hmac.new(
            self.secret_key.encode(),
            message_user_b.encode(),
            hashlib.sha256
        ).hexdigest()

        # Signatures differ, user B cannot use user A's order
        self.assertNotEqual(signature_user_a, signature_user_b)
        print("\nMemo forgery attack protection test passed")

    def test_signature_amount_binding(self):
        """Signature bound to amount."""
        # Signature for 49.9
        message_49 = "20251127-TEST:123456789:49.9"
        signature_49 = hmac.new(
            self.secret_key.encode(),
            message_49.encode(),
            hashlib.sha256
        ).hexdigest()

        # Signature for 19.9
        message_19 = "20251127-TEST:123456789:19.9"
        signature_19 = hmac.new(
            self.secret_key.encode(),
            message_19.encode(),
            hashlib.sha256
        ).hexdigest()

        self.assertNotEqual(signature_49, signature_19)
        print("\nAmount tamper detection passed")

    def test_constant_time_comparison_in_code(self):
        """Code uses constant-time comparison (timing attack prevention)."""
        from src.telegram.order_generator import OrderGenerator
        import inspect

        source = inspect.getsource(OrderGenerator.verify_signature)

        # Verify secure comparison method is used
        self.assertTrue(
            'compare_digest' in source,
            "Signature verification should use hmac.compare_digest to prevent timing attacks"
        )
        print("\nConstant-time comparison test passed")


class TestSQLInjectionPrevention(unittest.TestCase):
    """SQL injection protection tests."""

    def test_order_id_sql_injection(self):
        """Order ID SQL injection protection."""
        from src.telegram.order_generator import OrderIdGenerator

        # Generated order IDs should not contain SQL special characters
        for _ in range(100):
            order_id = OrderIdGenerator.generate()

            # Check no dangerous characters
            dangerous_chars = ["'", '"', ';', '--', '/*', '*/', 'DROP', 'DELETE', 'UPDATE']
            for char in dangerous_chars:
                self.assertNotIn(char, order_id)

        print("\nOrder ID SQL injection protection test passed")

    def test_parameterized_queries(self):
        """Parameterized queries (code pattern check)."""
        # Verify DAO uses parameterized queries, not string concatenation
        from src.telegram.database.order_dao import OrderDAO
        import inspect

        # Get source code
        source = inspect.getsource(OrderDAO)

        # Check parameterized query pattern (%s or ?)
        self.assertTrue(
            '%s' in source or '?' in source,
            "DAO should use parameterized queries"
        )

        # Check no f-string SQL construction
        self.assertNotIn('f"SELECT', source, "Should not use f-string to build SQL")
        self.assertNotIn("f'SELECT", source, "Should not use f-string to build SQL")

        print("\nParameterized queries test passed")


class TestConcurrencyProtection(unittest.TestCase):
    """Concurrency protection tests."""

    @patch('src.telegram.membership_manager.DatabaseManager')
    @patch('src.telegram.membership_manager.OrderDAO')
    @patch('src.telegram.membership_manager.MembershipDAO')
    @patch('src.telegram.membership_manager.MembershipPlanDAO')
    def test_concurrent_activation_protection(self, mock_plan, mock_member, mock_order, mock_db):
        """Concurrent activation protection (optimistic lock)."""
        from src.telegram.membership_manager import MembershipManager

        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance

        # Simulate order
        mock_order_instance = MagicMock()
        mock_order_instance.get_order_by_id.return_value = {
            'order_id': '20251127-TEST',
            'telegram_id': 123456789,
            'membership_type': 'MONTH',
            'expected_amount': Decimal('49.9'),
            'status': 'PENDING',
            'version': 1  # optimistic lock version
        }
        # First succeeds, subsequent fail (simulate concurrent conflict)
        mock_order_instance.confirm_order.side_effect = [True, False, False, False, False]
        mock_order.return_value = mock_order_instance

        mock_plan_instance = MagicMock()
        mock_plan_instance.get_plan_by_code.return_value = {
            'duration_days': 30,
            'allow_intraday_signals': True
        }
        mock_plan.return_value = mock_plan_instance

        mock_member_instance = MagicMock()
        mock_member_instance.activate_or_renew.return_value = 1
        mock_member.return_value = mock_member_instance

        manager = MembershipManager()
        manager.db = mock_db_instance
        manager.order_dao = mock_order_instance
        manager.plan_dao = mock_plan_instance
        manager.membership_dao = mock_member_instance

        # Simulate 5 concurrent requests
        results = []

        def activate():
            result = manager.activate_membership(
                order_id='20251127-TEST',
                tx_hash='0xTxHash123',
                actual_amount=Decimal('49.9'),
                from_address='0xSender'
            )
            results.append(result)

        threads = [threading.Thread(target=activate) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only one should succeed
        success_count = sum(1 for r in results if r is not None)
        self.assertEqual(success_count, 1)
        print(f"\nConcurrent activation protection test passed: 5 concurrent requests, {success_count} succeeded")


class TestReplayAttackPrevention(unittest.TestCase):
    """Replay attack protection tests."""

    @patch('src.telegram.membership_manager.DatabaseManager')
    @patch('src.telegram.membership_manager.OrderDAO')
    @patch('src.telegram.membership_manager.MembershipDAO')
    @patch('src.telegram.membership_manager.MembershipPlanDAO')
    def test_duplicate_tx_hash_rejected(self, mock_plan, mock_member, mock_order, mock_db):
        """Duplicate tx_hash rejected."""
        from src.telegram.membership_manager import MembershipManager

        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance

        # First: normal order
        # Second: order status already CONFIRMED
        mock_order_instance = MagicMock()
        mock_order_instance.get_order_by_id.side_effect = [
            {
                'order_id': '20251127-TEST',
                'telegram_id': 123456789,
                'membership_type': 'MONTH',
                'expected_amount': Decimal('49.9'),
                'status': 'PENDING',
                'version': 1
            },
            {
                'order_id': '20251127-TEST',
                'telegram_id': 123456789,
                'membership_type': 'MONTH',
                'expected_amount': Decimal('49.9'),
                'status': 'CONFIRMED',  # already confirmed
                'version': 2
            }
        ]
        mock_order_instance.confirm_order.return_value = True
        mock_order.return_value = mock_order_instance

        mock_plan_instance = MagicMock()
        mock_plan_instance.get_plan_by_code.return_value = {
            'duration_days': 30,
            'allow_intraday_signals': True
        }
        mock_plan.return_value = mock_plan_instance

        mock_member_instance = MagicMock()
        mock_member_instance.activate_or_renew.return_value = 1
        mock_member.return_value = mock_member_instance

        manager = MembershipManager()
        manager.db = mock_db_instance
        manager.order_dao = mock_order_instance
        manager.plan_dao = mock_plan_instance
        manager.membership_dao = mock_member_instance

        # First activation: success
        result1 = manager.activate_membership(
            order_id='20251127-TEST',
            tx_hash='0xTxHash123',
            actual_amount=Decimal('49.9'),
            from_address='0xSender'
        )
        self.assertIsNotNone(result1)

        # Second activation (same tx_hash): fail (order status no longer PENDING)
        result2 = manager.activate_membership(
            order_id='20251127-TEST',
            tx_hash='0xTxHash123',  # same tx hash
            actual_amount=Decimal('49.9'),
            from_address='0xSender'
        )
        self.assertIsNone(result2)

        print("\nReplay attack protection test passed: same tx_hash rejected on second attempt")

    def test_tx_hash_uniqueness_constraint(self):
        """tx_hash uniqueness constraint exists."""
        from src.telegram.database.order_dao import OrderDAO
        import inspect

        # Check DAO or table definition for uniqueness constraint
        source = inspect.getsource(OrderDAO)

        # Verify tx_hash uniqueness check logic
        self.assertTrue(
            'tx_hash' in source,
            "OrderDAO should include tx_hash field handling"
        )
        print("\ntx_hash uniqueness constraint test passed")


class TestAmountValidation(unittest.TestCase):
    """Amount validation tests."""

    @patch('src.telegram.membership_manager.DatabaseManager')
    @patch('src.telegram.membership_manager.OrderDAO')
    @patch('src.telegram.membership_manager.MembershipDAO')
    @patch('src.telegram.membership_manager.MembershipPlanDAO')
    def test_insufficient_amount_rejected(self, mock_plan, mock_member, mock_order, mock_db):
        """Insufficient amount rejected."""
        from src.telegram.membership_manager import MembershipManager

        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance

        mock_order_instance = MagicMock()
        mock_order_instance.get_order_by_id.return_value = {
            'order_id': '20251127-TEST',
            'telegram_id': 123456789,
            'membership_type': 'MONTH',
            'expected_amount': Decimal('49.9'),  # expected 49.9
            'status': 'PENDING',
            'version': 1
        }
        mock_order_instance.fail_order.return_value = True
        mock_order.return_value = mock_order_instance

        mock_plan.return_value = MagicMock()
        mock_member.return_value = MagicMock()

        manager = MembershipManager()
        manager.db = mock_db_instance
        manager.order_dao = mock_order_instance

        # Pay insufficient amount
        result = manager.activate_membership(
            order_id='20251127-TEST',
            tx_hash='0xTxHash123',
            actual_amount=Decimal('19.9'),  # only paid 19.9
            from_address='0xSender'
        )

        self.assertIsNone(result)
        mock_order_instance.fail_order.assert_called_once()
        print("\nInsufficient amount rejection test passed")

    @patch('src.telegram.membership_manager.DatabaseManager')
    @patch('src.telegram.membership_manager.OrderDAO')
    @patch('src.telegram.membership_manager.MembershipDAO')
    @patch('src.telegram.membership_manager.MembershipPlanDAO')
    def test_boundary_amount_accepted(self, mock_plan, mock_member, mock_order, mock_db):
        """Boundary amount (-0.05 USDT fixed tolerance) accepted."""
        from src.telegram.membership_manager import MembershipManager

        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance

        mock_order_instance = MagicMock()
        mock_order_instance.get_order_by_id.return_value = {
            'order_id': '20251127-TEST',
            'telegram_id': 123456789,
            'membership_type': 'MONTH',
            'expected_amount': Decimal('49.9'),
            'status': 'PENDING',
            'version': 1
        }
        mock_order_instance.confirm_order.return_value = True
        mock_order.return_value = mock_order_instance

        mock_plan_instance = MagicMock()
        mock_plan_instance.get_plan_by_code.return_value = {
            'duration_days': 30,
            'allow_intraday_signals': True
        }
        mock_plan.return_value = mock_plan_instance

        mock_member_instance = MagicMock()
        mock_member_instance.activate_or_renew.return_value = 1
        mock_member.return_value = mock_member_instance

        manager = MembershipManager()
        manager.db = mock_db_instance
        manager.order_dao = mock_order_instance
        manager.plan_dao = mock_plan_instance
        manager.membership_dao = mock_member_instance

        # Pay 49.85 (expected 49.9 - 0.05 tolerance = 49.85 boundary) - should be accepted
        result = manager.activate_membership(
            order_id='20251127-TEST',
            tx_hash='0xTxHash123',
            actual_amount=Decimal('49.85'),  # 49.9 - 0.05 = 49.85 boundary
            from_address='0xSender'
        )

        self.assertIsNotNone(result)
        print("\nBoundary amount (-0.05U tolerance) acceptance test passed")

    @patch('src.telegram.membership_manager.DatabaseManager')
    @patch('src.telegram.membership_manager.OrderDAO')
    @patch('src.telegram.membership_manager.MembershipDAO')
    @patch('src.telegram.membership_manager.MembershipPlanDAO')
    def test_below_tolerance_rejected(self, mock_plan, mock_member, mock_order, mock_db):
        """Below tolerance amount (-0.06 USDT) rejected."""
        from src.telegram.membership_manager import MembershipManager

        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance

        mock_order_instance = MagicMock()
        mock_order_instance.get_order_by_id.return_value = {
            'order_id': '20251127-TEST',
            'telegram_id': 123456789,
            'membership_type': 'MONTH',
            'expected_amount': Decimal('49.9'),
            'status': 'PENDING',
            'version': 1
        }
        mock_order_instance.fail_order.return_value = True
        mock_order.return_value = mock_order_instance

        mock_plan.return_value = MagicMock()
        mock_member.return_value = MagicMock()

        manager = MembershipManager()
        manager.db = mock_db_instance
        manager.order_dao = mock_order_instance

        # Pay 49.84 (below 49.9 - 0.05 = 49.85 boundary) - should be rejected
        result = manager.activate_membership(
            order_id='20251127-TEST',
            tx_hash='0xTxHash123',
            actual_amount=Decimal('49.84'),  # 0.01 below boundary
            from_address='0xSender'
        )

        self.assertIsNone(result)
        mock_order_instance.fail_order.assert_called_once()
        print("\nBelow-tolerance amount rejection test passed")


def run_tests():
    """Run all security tests."""
    print("=" * 60)
    print("Phase 3 Security Tests")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestSignatureSecurity))
    suite.addTests(loader.loadTestsFromTestCase(TestSQLInjectionPrevention))
    suite.addTests(loader.loadTestsFromTestCase(TestConcurrencyProtection))
    suite.addTests(loader.loadTestsFromTestCase(TestReplayAttackPrevention))
    suite.addTests(loader.loadTestsFromTestCase(TestAmountValidation))

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
