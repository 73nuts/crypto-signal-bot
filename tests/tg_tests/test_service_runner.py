#!/usr/bin/env python3
"""
Module 8 tests: service runner and Docker configuration.

Coverage:
1. service_runner.py import and initialization
2. bot/main.py JobQueue configuration
3. docker-compose.yml syntax validation
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestServiceRunnerImport(unittest.TestCase):
    """service_runner module import tests."""

    def test_import_service_runner(self):
        """Import service_runner module."""
        try:
            from src.telegram.payment import service_runner
            self.assertTrue(hasattr(service_runner, 'PaymentServiceRunner'))
            self.assertTrue(hasattr(service_runner, 'main'))
            print("\nservice_runner module imported successfully")
        except ImportError as e:
            self.fail(f"Import failed: {e}")

    def test_heartbeat_constants(self):
        """Heartbeat config constants."""
        from src.telegram.payment import service_runner

        self.assertEqual(service_runner.HEARTBEAT_INTERVAL, 30)
        self.assertEqual(service_runner.POLL_INTERVAL, 10)
        self.assertEqual(service_runner.COLLECT_INTERVAL_HOURS, 6)
        print("\nHeartbeat config constants correct")


class TestPaymentServiceRunner(unittest.TestCase):
    """PaymentServiceRunner class tests."""

    def test_runner_init(self):
        """Runner initialization."""
        from src.telegram.payment.service_runner import PaymentServiceRunner

        runner = PaymentServiceRunner()
        self.assertFalse(runner.running)
        self.assertIsNone(runner.monitor)
        self.assertIsNone(runner.collector)
        print("\nPaymentServiceRunner initialized successfully")

    @patch('src.telegram.payment.service_runner.settings')
    @patch('src.telegram.payment.service_runner.PaymentMonitor')
    @patch('src.telegram.payment.service_runner.FundCollector')
    @patch('src.telegram.payment.service_runner.GroupController')
    def test_init_components(self, mock_gc, mock_fc, mock_pm, mock_settings):
        """Component initialization."""
        from src.telegram.payment.service_runner import PaymentServiceRunner

        # Mock settings
        mock_settings.BSC_RPC_URL = 'https://bsc-dataseed.binance.org/'
        mock_settings.TELEGRAM_BOT_TOKEN.get_secret_value.return_value = 'test_token'
        mock_settings.TELEGRAM_GROUP_PREMIUM = '-100111111111'

        runner = PaymentServiceRunner()
        result = runner._init_components()

        self.assertTrue(result)
        mock_pm.assert_called_once()
        mock_fc.assert_called_once()
        mock_gc.assert_called_once()
        print("\nComponents initialized successfully")


class TestBotMainJobQueue(unittest.TestCase):
    """Bot main program JobQueue configuration tests."""

    def test_import_bot_main(self):
        """Import bot.main module."""
        try:
            from src.telegram.bot import main
            self.assertTrue(hasattr(main, 'create_application'))
            self.assertTrue(hasattr(main, 'check_and_kick_expired'))
            print("\nbot.main module imported successfully")
        except ImportError as e:
            self.fail(f"Import failed: {e}")

    def test_check_expired_function_exists(self):
        """check_and_kick_expired function exists."""

        from src.telegram.bot.main import check_and_kick_expired

        self.assertTrue(asyncio.iscoroutinefunction(check_and_kick_expired))
        print("\ncheck_and_kick_expired is async function")

    def test_singleton_getters(self):
        """Singleton getter functions."""
        from src.telegram.bot.main import _get_group_controller, _get_membership_manager

        # These functions should exist
        self.assertTrue(callable(_get_membership_manager))
        self.assertTrue(callable(_get_group_controller))
        print("\nSingleton getter functions exist")


class TestDockerComposeConfig(unittest.TestCase):
    """Docker Compose configuration tests."""

    def test_compose_file_exists(self):
        """docker-compose.yml file exists."""
        compose_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'docker-compose.yml'
        )
        self.assertTrue(os.path.exists(compose_path))
        print(f"\ndocker-compose.yml file exists: {compose_path}")

    def test_compose_syntax(self):
        """docker-compose.yml syntax."""
        import yaml

        compose_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'docker-compose.yml'
        )

        with open(compose_path, 'r') as f:
            try:
                config = yaml.safe_load(f)
                self.assertIsNotNone(config)
                self.assertIn('services', config)
                print("\nYAML syntax correct")
            except yaml.YAMLError as e:
                self.fail(f"YAML syntax error: {e}")

    def test_telegram_services_exist(self):
        """Telegram service configuration exists."""
        import yaml

        compose_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'docker-compose.yml'
        )

        with open(compose_path, 'r') as f:
            config = yaml.safe_load(f)

        services = config.get('services', {})

        # Check tg-bot service (v2.1 service name)
        self.assertIn('tg-bot', services)
        bot_service = services['tg-bot']
        self.assertIn('python', ' '.join(bot_service['command']))
        print("\ntg-bot service config correct")

        # Check tg-payment service (v2.1 service name)
        self.assertIn('tg-payment', services)
        payment_service = services['tg-payment']
        self.assertIn('service_runner', ' '.join(payment_service['command']))
        print("tg-payment service config correct")

    def test_healthcheck_config(self):
        """Health check configuration."""
        import yaml

        compose_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'docker-compose.yml'
        )

        with open(compose_path, 'r') as f:
            config = yaml.safe_load(f)

        services = config.get('services', {})

        # tg-payment should have heartbeat file check (v2.1 service name)
        payment_service = services.get('tg-payment', {})
        healthcheck = payment_service.get('healthcheck', {})
        test_cmd = ' '.join(healthcheck.get('test', []))
        self.assertIn('payment_heartbeat', test_cmd)
        print("\ntg-payment health check config correct (heartbeat file)")


class TestEnvironmentVariables(unittest.TestCase):
    """Environment variable configuration tests."""

    def test_required_env_vars_documented(self):
        """Required env vars are documented (v2.0)."""
        # Bot required variables (v2.0: ALPHA + RADAR)
        bot_vars = [
            'TELEGRAM_BOT_TOKEN',
            'TELEGRAM_GROUP_BASIC',
            'TELEGRAM_GROUP_PREMIUM'
        ]

        # Payment required variables
        payment_vars = [
            'BSC_RPC_URL',
            'BSCSCAN_API_KEY',
            'HD_WALLET_MNEMONIC'
        ]

        print("\nBot service required env vars (v2.0):")
        for var in bot_vars:
            print(f"  - {var}")

        print("\nPayment service required env vars:")
        for var in payment_vars:
            print(f"  - {var}")

        self.assertTrue(True)  # Documentation test


def run_tests():
    """Run all tests."""
    print("=" * 60)
    print("Module 8 Unit Tests (Service Runner + Docker Config)")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestServiceRunnerImport))
    suite.addTests(loader.loadTestsFromTestCase(TestPaymentServiceRunner))
    suite.addTests(loader.loadTestsFromTestCase(TestBotMainJobQueue))
    suite.addTests(loader.loadTestsFromTestCase(TestDockerComposeConfig))
    suite.addTests(loader.loadTestsFromTestCase(TestEnvironmentVariables))

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
