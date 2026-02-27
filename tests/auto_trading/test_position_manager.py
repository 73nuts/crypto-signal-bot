"""
PositionManager unit tests.

Test scope:
1. Position creation and query
2. Full close logic
3. Trailing stop update
4. Telegram message ID association

How to run:
    python -m pytest tests/auto_trading/test_position_manager.py -v

Version: v2.0 (Swing architecture)
Updated: 2025-12-17
"""

import logging
import os
import sys
import unittest

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.trading.position_manager import PositionManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TestPositionManager(unittest.TestCase):
    """PositionManager test class."""

    @classmethod
    def setUpClass(cls):
        """Test class setup."""
        from dotenv import load_dotenv
        load_dotenv()

        # MySQL config
        cls.mysql_host = os.getenv('MYSQL_HOST', 'localhost')
        cls.mysql_port = int(os.getenv('MYSQL_PORT', 3306))
        cls.mysql_user = os.getenv('MYSQL_USER', 'root')
        cls.mysql_password = os.getenv('MYSQL_PASSWORD', '')
        cls.mysql_database = os.getenv('MYSQL_DATABASE', 'crypto_signals')

        # Initialize PositionManager (no trading_client needed for pure DB tests)
        cls.manager = PositionManager(
            host=cls.mysql_host,
            port=cls.mysql_port,
            user=cls.mysql_user,
            password=cls.mysql_password,
            database=cls.mysql_database
        )

        # Test data
        cls.test_position_id = None

        logger.info(f"Test environment initialized: {cls.mysql_host}:{cls.mysql_port}")

    def test_01_create_position(self):
        """Test 1: Create position record."""
        test_price = 100000.0  # Simulated BTC price

        position_id = self.manager.open_position(
            symbol='BTCUSDT',
            side='LONG',
            entry_signal_id=99999,
            entry_order_id=1,
            entry_price=test_price,
            quantity=0.001,
            stop_loss=test_price * 0.95,
            take_profit_1=None,  # Swing strategy has no fixed take-profit
            take_profit_2=None,
            strategy_name='test-swing-ensemble',
            testnet=True,
            stop_type='TRAILING',
            trailing_period=25,
            trailing_mult=0.5
        )

        self.assertIsNotNone(position_id, "Position creation failed")
        TestPositionManager.test_position_id = position_id
        logger.info(f"Position created: ID={position_id}")

    def test_02_query_position(self):
        """Test 2: Query position."""
        self.assertIsNotNone(self.test_position_id, "No test position ID")

        # Query by ID
        position = self.manager.get_position_by_id(self.test_position_id)
        self.assertIsNotNone(position, "Position query failed")
        self.assertEqual(position['symbol'], 'BTCUSDT')
        self.assertEqual(position['side'], 'LONG')
        self.assertEqual(position['status'], 'OPEN')
        self.assertEqual(position['stop_type'], 'TRAILING')

        logger.info(f"Position queried: {position['symbol']} {position['side']}")

    def test_03_query_open_positions(self):
        """Test 3: Query all positions."""
        # Query all BTCUSDT positions
        positions = self.manager.get_open_positions('BTCUSDT')
        self.assertGreaterEqual(len(positions), 1, "Position list query failed")

        # Query all positions
        all_positions = self.manager.get_open_positions()
        self.assertGreaterEqual(len(all_positions), 1)

        logger.info(f"Position list queried: {len(all_positions)} positions")

    def test_04_update_trailing_stop(self):
        """Test 4: Update trailing stop."""
        self.assertIsNotNone(self.test_position_id, "No test position ID")

        position = self.manager.get_position_by_id(self.test_position_id)
        old_stop = float(position['current_stop'])
        new_stop = old_stop * 1.02  # Raise by 2%

        # Update stop loss - use correct method name
        success = self.manager.update_trailing_stop(
            position_id=self.test_position_id,
            new_stop=new_stop
        )
        self.assertTrue(success, "Stop loss update failed")

        # Verify update
        position = self.manager.get_position_by_id(self.test_position_id)
        self.assertAlmostEqual(float(position['current_stop']), new_stop, places=2)

        logger.info(f"Stop loss updated: {old_stop:.2f} -> {new_stop:.2f}")

    def test_05_telegram_message_id(self):
        """Test 5: Telegram message ID association."""
        self.assertIsNotNone(self.test_position_id, "No test position ID")

        test_msg_id = 123456789

        # Save message ID
        success = self.manager.update_telegram_message_id(
            self.test_position_id,
            test_msg_id
        )
        self.assertTrue(success, "Message ID save failed")

        # Read message ID - use symbol query (interface designed by symbol)
        msg_id = self.manager.get_telegram_message_id('BTCUSDT')
        self.assertEqual(msg_id, test_msg_id)

        logger.info(f"Telegram message ID associated: {test_msg_id}")

    def test_06_close_position(self):
        """Test 6: Full position close."""
        self.assertIsNotNone(self.test_position_id, "No test position ID")

        position = self.manager.get_position_by_id(self.test_position_id)
        entry_price = float(position['entry_price'])
        exit_price = entry_price * 1.10  # Simulate 10% profit

        success = self.manager.close_position(
            position_id=self.test_position_id,
            exit_order_id=99998,
            exit_price=exit_price,
            exit_reason='TEST_CLOSE'
        )
        self.assertTrue(success, "Position close failed")

        # Verify status
        position = self.manager.get_position_by_id(self.test_position_id)
        self.assertEqual(position['status'], 'CLOSED')
        self.assertIsNotNone(position['exit_price'])
        self.assertIsNotNone(position['closed_at'])

        logger.info(f"Position closed: PnL {position['realized_pnl_percent']:.2f}%")

    def test_07_cleanup(self):
        """Test 7: Clean up test data."""
        self.assertIsNotNone(self.test_position_id, "No test position ID")

        with self.manager._get_connection_ctx() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM positions WHERE id = %s",
                    (self.test_position_id,)
                )
                deleted = cursor.rowcount

        logger.info(f"Test data cleaned up: deleted {deleted} record(s)")


if __name__ == '__main__':
    unittest.main(verbosity=2)
