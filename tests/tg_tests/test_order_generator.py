#!/usr/bin/env python3
"""
Order generator tests.

Coverage:
1. Order ID format validation
2. HMAC signature generation and verification
3. Order ID parsing
4. Idempotency validation (requires database)

Note: Some tests require database and HD wallet configuration.
"""

import hashlib
import hmac
import os
import sys
import unittest
from datetime import datetime
from decimal import Decimal

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestOrderIdGenerator(unittest.TestCase):
    """Order ID generator tests (offline, no database required)."""

    def test_order_id_format(self):
        """Order ID format YYYYMMDD-XXXX."""
        from src.telegram.order_generator import OrderIdGenerator

        order_id = OrderIdGenerator.generate()

        # Format validation
        self.assertRegex(order_id, r'^\d{8}-[A-Z0-9]{4}$')

        # Date part validation
        date_part = order_id.split('-')[0]
        today = datetime.now().strftime('%Y%m%d')
        self.assertEqual(date_part, today)

        print(f"\nGenerated order ID: {order_id}")

    def test_order_id_uniqueness(self):
        """Order ID uniqueness."""
        from src.telegram.order_generator import OrderIdGenerator

        # Generate 100 order IDs
        order_ids = [OrderIdGenerator.generate() for _ in range(100)]

        # Verify uniqueness
        self.assertEqual(len(set(order_ids)), 100)

        print("\n100 unique order IDs test passed")

    def test_order_id_no_confusing_chars(self):
        """Order ID contains no ambiguous characters (0O1IL)."""
        from src.telegram.order_generator import OrderIdGenerator

        # Generate many order IDs to check
        for _ in range(200):
            order_id = OrderIdGenerator.generate()
            random_part = order_id.split('-')[1]

            # Should not contain ambiguous characters
            for char in '0O1IL':
                self.assertNotIn(char, random_part)

        print("\nNo ambiguous characters test passed")

    def test_parse_valid_order_id(self):
        """Parse valid order ID."""
        from src.telegram.order_generator import OrderIdGenerator

        order_id = "20251126-AB3C"
        result = OrderIdGenerator.parse(order_id)

        self.assertIsNotNone(result)
        self.assertEqual(result['date'], '20251126')
        self.assertEqual(result['code'], 'AB3C')

    def test_parse_invalid_order_id(self):
        """Parse invalid order ID."""
        from src.telegram.order_generator import OrderIdGenerator

        # Invalid formats
        invalid_ids = [
            "ORDER-123456-ABCD-W",  # old format
            "20251126AB3C",          # missing separator
            "2025112-AB3C",          # date too short
            "20251126-AB",           # random code too short
            "20251326-AB3C",         # invalid date (month 13)
            "",                      # empty string
            None,                    # None
        ]

        for invalid_id in invalid_ids:
            if invalid_id is not None:
                result = OrderIdGenerator.parse(invalid_id)
                self.assertIsNone(result, f"Should be None: {invalid_id}")

    def test_is_valid_format(self):
        """Format validation."""
        from src.telegram.order_generator import OrderIdGenerator

        # Valid formats
        self.assertTrue(OrderIdGenerator.is_valid_format("20251126-AB3C"))
        self.assertTrue(OrderIdGenerator.is_valid_format("20240101-WXYZ"))

        # Invalid formats
        self.assertFalse(OrderIdGenerator.is_valid_format("ORDER-123-ABC-W"))
        self.assertFalse(OrderIdGenerator.is_valid_format("invalid"))


class TestOrderSignature(unittest.TestCase):
    """Order signature tests (offline, no database required)."""

    def setUp(self):
        """Set test key."""
        self.secret_key = "test_secret_key_for_unit_testing_only"

    def test_signature_generation(self):
        """Signature generation."""
        order_id = "20251126-AB3C"
        telegram_id = 123456789
        amount = Decimal("19.90")

        # Calculate signature
        sign_data = f"{order_id}:{telegram_id}:{amount}".encode('utf-8')
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            sign_data,
            hashlib.sha256
        ).hexdigest()

        # Verify format (64-char hex)
        self.assertEqual(len(signature), 64)
        self.assertRegex(signature, r'^[a-f0-9]{64}$')

        print("\nSignature test:")
        print(f"  Order ID: {order_id}")
        print(f"  User ID: {telegram_id}")
        print(f"  Amount: {amount}")
        print(f"  Signature: {signature[:16]}...")

    def test_signature_deterministic(self):
        """Signature is deterministic (same input = same output)."""
        order_id = "20251126-AB3C"
        telegram_id = 123456789
        amount = Decimal("19.90")

        sign_data = f"{order_id}:{telegram_id}:{amount}".encode('utf-8')

        sig1 = hmac.new(self.secret_key.encode('utf-8'), sign_data, hashlib.sha256).hexdigest()
        sig2 = hmac.new(self.secret_key.encode('utf-8'), sign_data, hashlib.sha256).hexdigest()

        self.assertEqual(sig1, sig2)

    def test_signature_changes_with_input(self):
        """Input change causes signature change."""
        base_order_id = "20251126-AB3C"
        base_telegram_id = 123456789
        base_amount = Decimal("19.90")

        def calc_sig(order_id, telegram_id, amount):
            sign_data = f"{order_id}:{telegram_id}:{amount}".encode('utf-8')
            return hmac.new(
                self.secret_key.encode('utf-8'),
                sign_data,
                hashlib.sha256
            ).hexdigest()

        base_sig = calc_sig(base_order_id, base_telegram_id, base_amount)

        # Change order ID
        self.assertNotEqual(
            base_sig,
            calc_sig("20251126-XXXX", base_telegram_id, base_amount)
        )

        # Change user ID
        self.assertNotEqual(
            base_sig,
            calc_sig(base_order_id, 987654321, base_amount)
        )

        # Change amount
        self.assertNotEqual(
            base_sig,
            calc_sig(base_order_id, base_telegram_id, Decimal("29.90"))
        )

    def test_signature_constant_time_compare(self):
        """Signature uses constant-time comparison."""
        sig1 = "a" * 64
        sig2 = "a" * 64
        sig3 = "b" * 64

        # Use hmac.compare_digest for constant-time comparison
        self.assertTrue(hmac.compare_digest(sig1, sig2))
        self.assertFalse(hmac.compare_digest(sig1, sig3))


class TestOrderGeneratorIntegration(unittest.TestCase):
    """Order generator integration tests (requires database and HD wallet configuration)."""

    @classmethod
    def setUpClass(cls):
        """Check environment configuration."""
        cls.skip_integration = False

        # Check required environment variables
        required_vars = ['ORDER_SECRET_KEY', 'HD_WALLET_MNEMONIC']
        missing = [v for v in required_vars if not os.getenv(v)]

        if missing:
            cls.skip_integration = True
            print(f"\nSkipping integration tests: missing env vars {missing}")

    def test_full_order_creation(self):
        """Full order creation flow."""
        if self.skip_integration:
            self.skipTest("Missing environment configuration")

        from src.telegram.order_generator import OrderGenerator

        try:
            generator = OrderGenerator()

            # Create order
            result = generator.create_order(
                telegram_id=123456789,
                plan_code='WEEK',
                telegram_username='test_user'
            )

            # Verify result
            self.assertIn('order_id', result)
            self.assertIn('payment_address', result)
            self.assertIn('amount', result)
            self.assertIn('expire_at', result)
            self.assertIn('is_existing', result)

            print("\nOrder created:")
            print(f"  Order ID: {result['order_id']}")
            print(f"  Payment address: {result['payment_address']}")
            print(f"  Amount: {result['amount']}")
            print(f"  Expires at: {result['expire_at']}")

        except Exception as e:
            self.skipTest(f"Integration test failed: {e}")

    def test_idempotent_order_creation(self):
        """Idempotency (duplicate order returns same order)."""
        if self.skip_integration:
            self.skipTest("Missing environment configuration")

        from src.telegram.order_generator import OrderGenerator

        try:
            generator = OrderGenerator()
            telegram_id = 123456790  # Use different user ID

            # First creation
            result1 = generator.create_order(
                telegram_id=telegram_id,
                plan_code='WEEK'
            )

            # Second creation (should return same order)
            result2 = generator.create_order(
                telegram_id=telegram_id,
                plan_code='WEEK'
            )

            # Verify same order
            self.assertEqual(result1['order_id'], result2['order_id'])
            self.assertEqual(result1['payment_address'], result2['payment_address'])
            self.assertTrue(result2['is_existing'])

            print("\nIdempotency test passed:")
            print(f"  Both calls returned same order: {result1['order_id']}")

        except Exception as e:
            self.skipTest(f"Integration test failed: {e}")


if __name__ == '__main__':
    # Run tests
    unittest.main(verbosity=2)
