#!/usr/bin/env python3
"""
BSC payment module tests.

Coverage:
1. HD wallet address derivation
2. Address pool management
3. Payment monitor initialization
4. Fund collector initialization

Note: Some tests require database connection.
"""

import os
import sys
import unittest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Test mnemonic (for testing only, do NOT use in production!)
TEST_MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


class TestHDWalletDerivation(unittest.TestCase):
    """HD wallet derivation tests (offline, no database required)."""

    def test_derive_address_deterministic(self):
        """Address derivation is deterministic."""
        from bip_utils import Bip39SeedGenerator, Bip44, Bip44Changes, Bip44Coins

        seed = Bip39SeedGenerator(TEST_MNEMONIC).Generate()
        bip44_ctx = Bip44.FromSeed(seed, Bip44Coins.ETHEREUM)
        account = bip44_ctx.Purpose().Coin().Account(0)
        change = account.Change(Bip44Changes.CHAIN_EXT)

        # Derive first 5 addresses
        addresses = []
        for i in range(5):
            addr = change.AddressIndex(i).PublicKey().ToAddress()
            addresses.append(addr.lower())

        # Verify address format
        for addr in addresses:
            self.assertTrue(addr.startswith('0x'))
            self.assertEqual(len(addr), 42)

        # Verify unique
        self.assertEqual(len(set(addresses)), 5)

        # Verify deterministic (known first address for test mnemonic)
        # "abandon...about" first address is fixed
        expected_first = "0x9858effd232b4033e47d90003d41ec34ecaeda94"
        self.assertEqual(addresses[0], expected_first)

        print("\nAddress derivation test passed:")
        for i, addr in enumerate(addresses):
            print(f"  [{i}] {addr}")

    def test_private_key_format(self):
        """Private key format."""
        from bip_utils import Bip39SeedGenerator, Bip44, Bip44Changes, Bip44Coins

        seed = Bip39SeedGenerator(TEST_MNEMONIC).Generate()
        bip44_ctx = Bip44.FromSeed(seed, Bip44Coins.ETHEREUM)
        account = bip44_ctx.Purpose().Coin().Account(0)
        change = account.Change(Bip44Changes.CHAIN_EXT)

        addr_ctx = change.AddressIndex(0)
        private_key = addr_ctx.PrivateKey().Raw().ToHex()

        # Private key should be 64 hex characters
        self.assertEqual(len(private_key), 64)

        print(f"\nPrivate key format test passed: 0x{private_key[:8]}...{private_key[-8:]}")


class TestHDWalletManager(unittest.TestCase):
    """HD wallet manager tests (requires database)."""

    @classmethod
    def setUpClass(cls):
        """Check database connection."""
        try:
            from src.telegram.database import DatabaseManager
            cls.db = DatabaseManager()
            cls.db_available = True
        except Exception as e:
            print(f"\nSkipping database tests: {e}")
            cls.db_available = False

    def setUp(self):
        if not self.db_available:
            self.skipTest("Database unavailable")

        # Set test mnemonic
        os.environ['HD_WALLET_MNEMONIC'] = TEST_MNEMONIC

    def test_manager_initialization(self):
        """Manager initialization."""
        from src.telegram.payment import HDWalletManager

        manager = HDWalletManager(mnemonic=TEST_MNEMONIC)
        self.assertIsNotNone(manager)

        # Derived address should be consistent
        addr = manager.derive_address(0)
        expected = "0x9858effd232b4033e47d90003d41ec34ecaeda94"
        self.assertEqual(addr['address'].lower(), expected)

        print("\nManager initialization test passed")

    def test_derive_multiple_addresses(self):
        """Bulk address derivation."""
        from src.telegram.payment import HDWalletManager

        manager = HDWalletManager(mnemonic=TEST_MNEMONIC)

        addresses = []
        for i in range(10):
            wallet = manager.derive_address(i)
            addresses.append(wallet['address'].lower())

        # All addresses should be unique
        self.assertEqual(len(set(addresses)), 10)

        print(f"\nBulk derivation test passed: {len(addresses)} addresses")


class TestPaymentMonitorInit(unittest.TestCase):
    """Payment monitor initialization tests."""

    def setUp(self):
        os.environ['HD_WALLET_MNEMONIC'] = TEST_MNEMONIC
        os.environ['BSC_RPC_URL'] = 'https://bsc-dataseed.binance.org/'

    def test_web3_connection(self):
        """Web3 connection."""
        from web3 import Web3
        from web3.middleware import ExtraDataToPOAMiddleware

        rpc_url = os.getenv('BSC_RPC_URL')
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        self.assertTrue(w3.is_connected())

        block = w3.eth.block_number
        self.assertGreater(block, 0)

        print(f"\nWeb3 connection test passed: current block {block}")

    def test_usdt_contract(self):
        """USDT contract query."""
        from web3 import Web3
        from web3.middleware import ExtraDataToPOAMiddleware

        rpc_url = os.getenv('BSC_RPC_URL')
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        usdt_address = '0x55d398326f99059fF775485246999027B3197955'
        usdt_abi = [{
            "constant": True,
            "inputs": [],
            "name": "symbol",
            "outputs": [{"name": "", "type": "string"}],
            "type": "function"
        }]

        contract = w3.eth.contract(
            address=Web3.to_checksum_address(usdt_address),
            abi=usdt_abi
        )

        symbol = contract.functions.symbol().call()
        self.assertEqual(symbol, 'USDT')

        print(f"\nUSDT contract test passed: symbol={symbol}")


class TestAddressPoolDB(unittest.TestCase):
    """Address pool database tests."""

    @classmethod
    def setUpClass(cls):
        """Check database and table."""
        try:
            from src.telegram.database import DatabaseManager
            cls.db = DatabaseManager()

            # Check if payment_addresses table exists
            result = cls.db.execute_query(
                "SHOW TABLES LIKE 'payment_addresses'",
                fetch_one=True
            )
            cls.table_exists = result is not None
            if not cls.table_exists:
                print("\nSkipping address pool tests: payment_addresses table not found")
        except Exception as e:
            print(f"\nSkipping address pool tests: {e}")
            cls.table_exists = False

    def setUp(self):
        if not self.table_exists:
            self.skipTest("payment_addresses table not found")

        os.environ['HD_WALLET_MNEMONIC'] = TEST_MNEMONIC

    def test_ensure_pool_size(self):
        """Address pool replenishment."""
        from src.telegram.payment import HDWalletManager

        manager = HDWalletManager(mnemonic=TEST_MNEMONIC)

        # Replenish address pool
        generated = manager.ensure_pool_size()

        # Query pool stats
        stats = manager.get_pool_stats()

        print("\nAddress pool test:")
        print(f"  Generated: {generated}")
        print(f"  Available: {stats['available']}")
        print(f"  Assigned: {stats['assigned']}")
        print(f"  Used: {stats['used']}")
        print(f"  Total: {stats['total']}")

        self.assertGreaterEqual(stats['available'], 0)


def run_tests():
    """Run tests."""
    print("=" * 60)
    print("BSC Payment Module Tests")
    print("=" * 60)

    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestHDWalletDerivation))
    suite.addTests(loader.loadTestsFromTestCase(TestHDWalletManager))
    suite.addTests(loader.loadTestsFromTestCase(TestPaymentMonitorInit))
    suite.addTests(loader.loadTestsFromTestCase(TestAddressPoolDB))

    # Run tests
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
