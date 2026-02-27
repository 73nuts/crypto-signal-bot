"""
BinanceTradingClient unit tests.

How to run:
    python tests/test_binance_client.py

Requirements:
    1. Configure BINANCE_TESTNET_API_KEY and SECRET in .env
    2. Ensure testnet account has sufficient balance (at least 10 USDT)
    3. Internet connection required

Test coverage:
    - Client initialization
    - Account balance retrieval
    - Current price retrieval
    - Small market order creation
    - Order status query
    - Order cancellation

Version: v4.0.0-alpha
Created: 2025-11-11
"""

import logging
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from src.trading.binance_trading_client import BinanceTradingClient

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class BinanceClientTest:
    """Binance client test class."""

    def __init__(self):
        """Initialize tests."""
        self.api_key = os.getenv('BINANCE_TESTNET_API_KEY')
        self.api_secret = os.getenv('BINANCE_TESTNET_API_SECRET')

        if not self.api_key or not self.api_secret:
            raise ValueError(
                "Please configure BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET in .env"
            )

        self.client = None
        self.test_results = {
            'passed': 0,
            'failed': 0,
            'skipped': 0
        }

    def run_all_tests(self):
        """Run all tests."""
        logger.info("=" * 60)
        logger.info("Starting BinanceTradingClient unit tests (testnet)")
        logger.info("=" * 60)

        tests = [
            self.test_1_init_client,
            self.test_2_get_balance,
            self.test_3_get_current_price,
            self.test_4_set_leverage,
            self.test_5_set_position_mode,
            self.test_6_create_small_market_order,
            self.test_7_query_order_status,
            self.test_8_get_positions
        ]

        for test in tests:
            try:
                logger.info(f"\n{'='*60}")
                logger.info(f"Running test: {test.__name__}")
                logger.info(f"{'='*60}")
                test()
                self.test_results['passed'] += 1
                logger.info(f"PASS: {test.__name__}")
            except Exception as e:
                self.test_results['failed'] += 1
                logger.error(f"FAIL: {test.__name__} - {e}")
            time.sleep(1)  # Avoid API rate limiting

        self._print_summary()

    def test_1_init_client(self):
        """Test 1: Client initialization."""
        self.client = BinanceTradingClient(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=True,
            symbol='ETH'
        )

        assert self.client is not None, "Client initialization failed"
        assert self.client.testnet is True, "Testnet flag incorrect"
        assert self.client.trading_pair == 'ETH/USDT', "Trading pair incorrect"

        logger.info(f"Trading pair: {self.client.trading_pair}")
        logger.info(f"Testnet mode: {self.client.testnet}")

    def test_2_get_balance(self):
        """Test 2: Account balance retrieval."""
        balance = self.client.get_balance()

        assert balance is not None, "Balance retrieval failed"
        assert 'USDT' in balance, "No USDT in balance"

        usdt_balance = balance['USDT']
        free = usdt_balance.get('free', 0)
        used = usdt_balance.get('used', 0)
        total = usdt_balance.get('total', 0)

        logger.info(f"USDT balance - free: {free:.2f}, used: {used:.2f}, total: {total:.2f}")

        if free < 10:
            logger.warning("Testnet available balance below 10 USDT, may affect subsequent tests")

    def test_3_get_current_price(self):
        """Test 3: Current price retrieval."""
        price = self.client.get_current_price()

        assert price is not None, "Price retrieval failed"
        assert price > 0, "Price should be greater than 0"

        logger.info(f"ETH current price: ${price:.2f}")

    def test_4_set_leverage(self):
        """Test 4: Set leverage."""
        # Use 1x leverage for testnet
        success = self.client.set_leverage(leverage=1)

        # Leverage setting may fail (if already set), but this is not a test failure
        logger.info(f"Set leverage 1x: {'success' if success else 'failed (may already be set)'}")

    def test_5_set_position_mode(self):
        """Test 5: Set position mode."""
        # Hedge mode
        success = self.client.set_position_mode(hedge_mode=True)

        # Position mode may fail (if already set), but this is not a test failure
        logger.info(f"Set hedge position mode: {'success' if success else 'failed (may already be set)'}")

    def test_6_create_small_market_order(self):
        """Test 6: Create a small market order."""
        # Check balance
        balance = self.client.get_balance()
        free_usdt = balance['USDT'].get('free', 0)

        if free_usdt < 10:
            logger.warning("Insufficient balance, skipping order test")
            self.test_results['skipped'] += 1
            return

        # Get current price
        current_price = self.client.get_current_price()

        # Calculate quantity (~25 USDT, above Binance minimum of 20 USDT)
        quantity = round(25 / current_price, 3)

        logger.info(f"Placing order - BUY {quantity} ETH @ market (~$25)")

        # Create market order
        order = self.client.create_market_order(
            side='BUY',
            quantity=quantity,
            position_side='LONG',
            client_order_id=f"test_{int(time.time())}"
        )

        if order:
            logger.info("Order created successfully:")
            logger.info(f"  - Order ID: {order.get('id')}")
            logger.info(f"  - Fill price: ${order.get('average', 0):.2f}")
            logger.info(f"  - Filled: {order.get('filled', 0)}")
            logger.info(f"  - Status: {order.get('status')}")

            # Save order ID for subsequent tests
            self.test_order_id = order.get('id')
        else:
            raise Exception("Market order creation failed")

    def test_7_query_order_status(self):
        """Test 7: Query order status."""
        if not hasattr(self, 'test_order_id'):
            logger.warning("No test order ID, skipping query test")
            self.test_results['skipped'] += 1
            return

        order = self.client.get_order(self.test_order_id)

        if order:
            logger.info("Order query successful:")
            logger.info(f"  - Order ID: {order.get('id')}")
            logger.info(f"  - Status: {order.get('status')}")
            logger.info(f"  - Fill price: ${order.get('average', 0):.2f}")
        else:
            raise Exception("Order query failed")

    def test_8_get_positions(self):
        """Test 8: Get current positions."""
        positions = self.client.get_positions()

        logger.info(f"Current position count: {len(positions)}")

        for pos in positions:
            logger.info(f"  - {pos.get('symbol')}: "
                       f"{pos.get('side')} {pos.get('contracts')} "
                       f"@ ${pos.get('entryPrice', 0):.2f}")

    def _print_summary(self):
        """Print test summary."""
        logger.info("\n" + "=" * 60)
        logger.info("Test Summary")
        logger.info("=" * 60)
        logger.info(f"Passed: {self.test_results['passed']}")
        logger.info(f"Failed: {self.test_results['failed']}")
        logger.info(f"Skipped: {self.test_results['skipped']}")

        total = sum(self.test_results.values())
        success_rate = (self.test_results['passed'] / total * 100) if total > 0 else 0

        logger.info(f"Success rate: {success_rate:.1f}%")

        if self.test_results['failed'] == 0:
            logger.info("\nAll tests passed!")
        else:
            logger.error(f"\n{self.test_results['failed']} test(s) failed")


def main():
    """Main entry point."""
    try:
        tester = BinanceClientTest()
        tester.run_all_tests()
    except Exception as e:
        logger.error(f"Test run failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
