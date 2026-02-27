#!/usr/bin/env python3
"""
VIP signal sender tests (v2.0).

Coverage:
1. Group config loading (PREMIUM group)
2. Signal formatting (main signal + analysis)
3. Hash calculation and deduplication logic
4. Routing logic

Note: Actual Telegram API calls require mocking.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestVipSignalSenderConfig(unittest.TestCase):
    """Config tests (v2.0)."""

    def test_group_env_mapping(self):
        """Group env variable mapping (v2.1: PREMIUM group)."""
        from src.telegram.vip_signal_sender import VipSignalSender

        expected = {
            'PREMIUM': 'TELEGRAM_GROUP_PREMIUM',
        }
        self.assertEqual(VipSignalSender.GROUP_ENV_MAP, expected)
        print("\nGroup env variable mapping correct (v2.0)")

    def test_type_labels(self):
        """Signal type labels (bilingual)."""
        from src.telegram.vip_signal_sender import VipSignalSender

        self.assertIn('SWING', VipSignalSender.TYPE_LABELS)
        self.assertIn('波段', VipSignalSender.TYPE_LABELS['SWING'])
        print("\nSignal type labels (bilingual) correct")

    def test_action_config(self):
        """Direction configuration."""
        from src.telegram.vip_signal_sender import VipSignalSender

        self.assertIn('LONG', VipSignalSender.ACTION_CONFIG)
        self.assertIn('SHORT', VipSignalSender.ACTION_CONFIG)
        self.assertIn('emoji', VipSignalSender.ACTION_CONFIG['LONG'])
        self.assertIn('label', VipSignalSender.ACTION_CONFIG['LONG'])
        print("\nDirection config correct")


class TestSignalFormatting(unittest.TestCase):
    """Signal formatting tests."""

    def setUp(self):
        """Initialize test data."""
        self.sample_signal = {
            'id': 1,
            'symbol': 'BTC',
            'action': 'LONG',
            'price': 96500,
            'long_range': {'min': 96500, 'max': 96800},
            'take_profit': [98000, 99500],
            'stop_loss': 95500,
            'position_size': 20,
            'confidence': 85,
            'rsi': 35.5,
            'technical_reason': '价格测试MA25支撑位，MACD金叉确认',
            'created_at': datetime(2024, 11, 27, 10, 0, 0)
        }

    @patch.dict(os.environ, {
        'TELEGRAM_GROUP_PREMIUM': '-100111111111',
    })
    def test_format_main_signal_long(self):
        """Long main signal formatting."""
        from src.telegram.vip_signal_sender import VipSignalSender

        mock_bot = MagicMock()
        mock_db = MagicMock()
        sender = VipSignalSender(mock_bot, mock_db)

        result = sender._format_main_signal(self.sample_signal, 'SWING')

        # Verify key content
        self.assertIn('LONG (做多)', result)
        self.assertIn('#BTCUSDT', result)
        self.assertIn('Swing / 波段', result)
        self.assertIn('<code>96500 - 96800</code>', result)
        self.assertIn('<code>98000</code>', result)
        self.assertIn('<code>95500</code>', result)
        self.assertIn('Cross 20x', result)
        print("\nLong main signal formatting correct")
        print(f"Message length: {len(result)} chars")

    @patch.dict(os.environ, {
        'TELEGRAM_GROUP_PREMIUM': '-100111111111',
    })
    def test_format_main_signal_short(self):
        """Short main signal formatting."""
        from src.telegram.vip_signal_sender import VipSignalSender

        mock_bot = MagicMock()
        mock_db = MagicMock()
        sender = VipSignalSender(mock_bot, mock_db)

        short_signal = self.sample_signal.copy()
        short_signal['action'] = 'SHORT'
        short_signal['short_entry_price'] = 97000

        result = sender._format_main_signal(short_signal, 'SWING')

        self.assertIn('SHORT (做空)', result)
        print("\nShort main signal formatting correct")

    @patch.dict(os.environ, {
        'TELEGRAM_GROUP_PREMIUM': '-100111111111',
    })
    def test_format_analysis(self):
        """Analysis report formatting."""
        from src.telegram.vip_signal_sender import VipSignalSender

        mock_bot = MagicMock()
        mock_db = MagicMock()
        sender = VipSignalSender(mock_bot, mock_db)

        result = sender._format_analysis(self.sample_signal)

        # Verify key content
        self.assertIn('Analysis / 策略分析', result)
        self.assertIn('#BTCUSDT', result)
        self.assertIn('RSI:', result)
        self.assertIn('85%', result)  # confidence
        print("\nAnalysis report formatting correct")
        print(f"Message length: {len(result)} chars")


class TestHashAndDedup(unittest.TestCase):
    """Hash and deduplication tests."""

    @patch.dict(os.environ, {
        'TELEGRAM_GROUP_PREMIUM': '-100111111111'
    })
    def test_calculate_hash(self):
        """Signal hash calculation."""
        from src.telegram.vip_signal_sender import VipSignalSender

        mock_bot = MagicMock()
        mock_db = MagicMock()
        sender = VipSignalSender(mock_bot, mock_db)

        signal1 = {'symbol': 'BTC', 'action': 'LONG', 'price': 96500, 'stop_loss': 95500}
        signal2 = {'symbol': 'BTC', 'action': 'LONG', 'price': 96500, 'stop_loss': 95500}
        signal3 = {'symbol': 'ETH', 'action': 'LONG', 'price': 96500, 'stop_loss': 95500}

        hash1 = sender._calculate_hash(signal1)
        hash2 = sender._calculate_hash(signal2)
        hash3 = sender._calculate_hash(signal3)

        # Same signal should have same hash
        self.assertEqual(hash1, hash2)
        # Different signals should have different hash
        self.assertNotEqual(hash1, hash3)

        # Hash length should be 64 (SHA256)
        self.assertEqual(len(hash1), 64)
        print(f"\nHash calculation correct: {hash1[:16]}...")

    @patch.dict(os.environ, {
        'TELEGRAM_GROUP_PREMIUM': '-100111111111'
    })
    def test_has_indicators(self):
        """Indicator data detection."""
        from src.telegram.vip_signal_sender import VipSignalSender

        mock_bot = MagicMock()
        mock_db = MagicMock()
        sender = VipSignalSender(mock_bot, mock_db)

        # Has indicator data
        signal_with_indicators = {'rsi': 35, 'macd': 0.001}
        self.assertTrue(sender._has_indicators(signal_with_indicators))

        # No indicator data
        signal_without = {'symbol': 'BTC', 'action': 'LONG'}
        self.assertFalse(sender._has_indicators(signal_without))

        print("\nIndicator data detection correct")


class TestRouting(unittest.TestCase):
    """Routing logic tests (v2.0)."""

    def test_load_group_config(self):
        """Group config loading (v2.1: PREMIUM group)."""
        from src.telegram.vip_signal_sender import VipSignalSender

        mock_bot = MagicMock()
        mock_db = MagicMock()

        # Mock settings
        with patch('src.telegram.vip_signal_sender.settings') as mock_settings:
            mock_settings.TELEGRAM_GROUP_PREMIUM = '-100111111111'

            sender = VipSignalSender(mock_bot, mock_db)

            self.assertEqual(sender.groups['PREMIUM'], -100111111111)
            self.assertTrue(sender.is_fully_configured())
            print("\nGroup config loaded correctly (v2.0)")

    @patch('src.telegram.vip_signal_sender.settings')
    def test_no_config(self, mock_settings):
        """No configuration."""
        from src.telegram.vip_signal_sender import VipSignalSender

        # Mock settings returns empty config
        mock_settings.TELEGRAM_GROUP_PREMIUM = None

        mock_bot = MagicMock()
        mock_db = MagicMock()
        sender = VipSignalSender(mock_bot, mock_db)

        self.assertNotIn('PREMIUM', sender.groups)
        self.assertFalse(sender.is_fully_configured())
        print("\nNo-config detection correct")


class TestBilingualFormat(unittest.TestCase):
    """Bilingual format tests."""

    @patch.dict(os.environ, {
        'TELEGRAM_GROUP_PREMIUM': '-100111111111'
    })
    def test_bilingual_labels(self):
        """Bilingual labels."""
        from src.telegram.vip_signal_sender import VipSignalSender

        mock_bot = MagicMock()
        mock_db = MagicMock()
        sender = VipSignalSender(mock_bot, mock_db)

        signal = {
            'symbol': 'BTC',
            'action': 'LONG',
            'price': 96500,
            'take_profit': [98000],
            'stop_loss': 95500,
        }

        result = sender._format_main_signal(signal, 'SWING')

        # Verify bilingual format
        self.assertIn('Entry / 进场', result)
        self.assertIn('Targets / 止盈', result)
        self.assertIn('Stop / 止损', result)
        self.assertIn('Rec. Lev. / 推荐杠杆', result)
        print("\nBilingual label format correct")


def run_tests():
    """Run all tests."""
    print("=" * 60)
    print("VipSignalSender Unit Tests (v2.0)")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestVipSignalSenderConfig))
    suite.addTests(loader.loadTestsFromTestCase(TestSignalFormatting))
    suite.addTests(loader.loadTestsFromTestCase(TestHashAndDedup))
    suite.addTests(loader.loadTestsFromTestCase(TestRouting))
    suite.addTests(loader.loadTestsFromTestCase(TestBilingualFormat))

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
