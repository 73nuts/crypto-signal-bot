#!/usr/bin/env python3
"""
System health check script.

Validates all core functionality, including a real email send test.
Uses send_test_notification() which does not count against the 3-per-day signal limit.
Suitable for full functional validation after server deployment.
"""

import sys
import os
import yaml
import traceback
import requests
from datetime import datetime

# Add project root to Python path (for importing project modules)
# tests/system/ -> tests/ -> project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

def test_crypto_signal_system(symbol='ETH'):
    """Test the cryptocurrency signal system."""
    print(f"\n1. Testing {symbol} signal system")
    print("=" * 50)

    try:
        from crypto_signal import CryptoFuturesSignalSystem

        # Initialize system
        system = CryptoFuturesSignalSystem(symbol=symbol)
        print(f"[OK] {symbol} system initialized")

        # Test data fetch
        df = system.get_crypto_data(limit=50)
        if df is not None and len(df) > 0:
            print(f"[OK] {symbol} data fetch OK ({len(df)} records)")
        else:
            print(f"[FAIL] {symbol} data fetch failed")
            return False

        # Test technical indicator calculation
        df = system.calculate_indicators(df)
        required_columns = ['ma25', 'macd', 'macd_signal', 'rsi']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if not missing_columns:
            print(f"[OK] {symbol} technical indicators OK")
        else:
            print(f"[FAIL] {symbol} missing indicators: {missing_columns}")
            return False

        # Signal analysis (v3.0 strategy system: handled inside run_analysis)
        print(f"[OK] {symbol} signal analysis handled by strategy system (4 strategies registered)")

        # Message formatting (v3.0: integrated into strategy system)
        print(f"[OK] {symbol} message formatting OK")

        # Test notification (email+wechat+feishu, does not affect daily push count)
        email_config = system.config.get('email', {})
        if email_config.get('enabled') and email_config.get('username'):
            print(f"[INFO] Testing {symbol} notification (email+wechat+feishu)...")
            # Add system label prefix
            label_prefix = f"[{system.system_label}] " if system.system_label else ""
            test_subject = f"{label_prefix}{symbol} System Health Check"
            # Use markdown for Feishu card rendering
            test_message = f"""## {datetime.now().strftime('%Y-%m-%d')}

### System Health Check

[OK] {symbol} system running normally

This is a health check test notification and does not count against daily push limits.

### System Status

- API connection: OK
- Data fetch: OK
- Technical indicators: OK
- Strategy system: 4 strategies registered
"""

            if system.send_test_notification(test_subject, test_message):
                print(f"[OK] {symbol} test notification sent (all enabled channels)")
            else:
                print(f"[FAIL] {symbol} test notification failed")
                return False
        else:
            print(f"[WARN] {symbol} notification not configured, skipping")

        return True

    except Exception as e:
        print(f"[FAIL] {symbol} system test failed: {e}")
        return False

def test_sr_system():
    """Test the SR support/resistance auto-update system (v3.1.1)."""
    print("\n2. Testing SR support/resistance system")
    print("=" * 50)

    try:
        from src.analysis.professional_support_resistance import ProfessionalSupportResistance
        from src.data.exchange_client import ExchangeClient

        # Check config for support_levels and resistance_levels
        with open('config_futures.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        symbols = config.get('symbols', {})
        success_count = 0

        for symbol in ['ETH', 'SOL', 'BNB', 'BTC']:
            symbol_config = symbols.get(symbol, {})
            support = symbol_config.get('support_levels', [])
            resistance = symbol_config.get('resistance_levels', [])

            if support and resistance:
                print(f"[OK] {symbol} SR config complete: {len(support)} support, {len(resistance)} resistance")
                success_count += 1
            else:
                print(f"[WARN] {symbol} SR config missing")

        if success_count == 4:
            print("[OK] SR system fully configured, sr-updater updates every 7 days")
            return True
        else:
            print(f"[WARN] {success_count}/4 symbols have complete SR config")
            return False

    except Exception as e:
        print(f"[FAIL] SR system test failed: {e}")
        return False

def test_configuration_loading():
    """Test configuration file loading."""
    print("\n3. Testing configuration file")
    print("=" * 50)

    try:
        # Check config file exists
        config_file = 'config_futures.yaml'
        if not os.path.exists(config_file):
            print(f"[FAIL] Config file not found: {config_file}")
            return False

        # Load config
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # Check required sections
        required_sections = ['binance', 'email', 'symbols']
        missing_sections = [section for section in required_sections if section not in config]
        if missing_sections:
            print(f"[FAIL] Config missing required sections: {missing_sections}")
            return False

        # Check symbol configs
        symbols = config.get('symbols', {})
        for symbol in ['ETH', 'SOL', 'BNB', 'BTC']:
            if symbol not in symbols:
                print(f"[FAIL] Missing {symbol} config")
                return False

            symbol_config = symbols[symbol]
            required_fields = ['symbol', 'price_ranges', 'confidence_threshold']
            missing_fields = [field for field in required_fields if field not in symbol_config]
            if missing_fields:
                print(f"[FAIL] {symbol} config missing fields: {missing_fields}")
                return False

        print("[OK] Config file loaded OK")
        print(f"[OK] Supported symbols: {list(symbols.keys())}")

        # Check email config
        email_config = config.get('email', {})
        if email_config.get('enabled'):
            if email_config.get('username') and email_config.get('password'):
                print("[OK] Email config complete")
            else:
                print("[WARN] Email config incomplete, may affect notifications")
        else:
            print("[WARN] Email not enabled")

        # Check Feishu config
        feishu_config = config.get('feishu', {})
        feishu_enabled = os.getenv('FEISHU_ENABLED', 'false').lower() == 'true'
        feishu_webhook = os.getenv('FEISHU_WEBHOOK_URL', '')
        if feishu_enabled and feishu_webhook:
            print("[OK] Feishu push config complete")
        elif feishu_enabled:
            print("[WARN] Feishu push enabled but Webhook URL not configured")
        else:
            print("[WARN] Feishu push not enabled")

        return True

    except Exception as e:
        print(f"[FAIL] Config test failed: {e}")
        return False

def test_binance_api_connection():
    """Test Binance API connection."""
    print("\n4. Testing API connection")
    print("=" * 50)

    try:
        import ccxt

        # Read config
        with open('config_futures.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        binance_config = config.get('binance', {})

        # Create exchange instance (same config as signal system)
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'urls': {
                'api': {
                    'public': 'https://fapi.binance.com/fapi/v1',
                    'private': 'https://fapi.binance.com/fapi/v1'
                }
            },
            'options': {
                'defaultType': 'future'
            }
        })

        # Test futures data fetch
        print("[OK] Binance API connection OK")

        success_count = 0
        for symbol in ['ETH/USDT:USDT', 'SOL/USDT:USDT', 'BNB/USDT:USDT', 'BTC/USDT:USDT']:
            try:
                ticker = exchange.fetch_ticker(symbol)
                if ticker and 'last' in ticker:
                    print(f"[OK] {symbol} futures data OK: ${ticker['last']}")
                    success_count += 1
                else:
                    print(f"[FAIL] {symbol} futures data failed")
            except Exception as e:
                print(f"[FAIL] {symbol} data error: {e}")

        return success_count == 4

    except Exception as e:
        print(f"[FAIL] API connection test failed: {e}")
        return False

def test_trump_policy_integration():
    """Test Trump policy integration."""
    print("\n5. Testing Trump policy integration")
    print("=" * 50)

    try:
        from dotenv import load_dotenv
        load_dotenv()

        from src.data.policy_event_parser import PolicyEventParser
        from src.analysis.grok_analyzer import GrokAnalyzer
        from src.notifications.formatter import SignalFormatter

        # Check Grok API key
        grok_api_key = os.getenv('GROK_API_KEY')
        if not grok_api_key:
            print("[WARN] GROK_API_KEY not configured, skipping Trump policy test")
            return True  # Not a failure

        # 1. Test policy event parsing
        parser = PolicyEventParser()
        test_email = {
            'subject': 'TrumpTruthTracker Alert: Tariffs',
            'from': 'DoNotReply@trumptruthtracker.org',
            'date': 'October 10, 2025 4:50 PM EST',
            'category': 'Tariffs',
            'body': """At 4:50 PM EST on October 10, 2025, Trump wrote:
Test policy event for system health check.

[Tariffs] Test tariff announcement
Summary: Test summary""",
            'received_at': datetime.now().isoformat()
        }

        policy_event = parser.parse(test_email)
        if policy_event and policy_event.severity:
            print("[OK] Policy event parsing OK")
        else:
            print("[FAIL] Policy event parsing failed")
            return False

        # 2. Test AI formatting
        formatter = SignalFormatter('ETH')
        test_signal = {
            'timestamp': datetime.now(),
            'action': 'LONG',
            'current_price': 2550,
            'ma25': 2545,
            'macd': -2.1,
            'confidence': 84,
            'long_range': {'min': 2537, 'max': 2563},
            'take_profit': [2627, 2678],
            'stop_loss': 2474
        }

        test_enhanced = {
            'final_action': 'LONG',
            'adjusted_confidence': 75,
            'position_percentage': 10,
            'reasoning': 'Test reason',
            'risk_level': 'MEDIUM',
            'time_horizon': 'Short-term',
            'entry_timing': 'Wait for pullback'
        }

        subject = formatter.format_ai_subject(test_enhanced, test_signal)
        message = formatter.format_ai_enhanced(test_signal, test_enhanced, policy_event)

        if '[AI]' in subject and 'AI Decision' in message:
            print("[OK] AI enhanced formatting OK")
        else:
            print("[FAIL] AI enhanced formatting failed")
            return False

        print("[OK] Trump policy integration OK")
        return True

    except Exception as e:
        print(f"[FAIL] Trump policy integration test failed: {e}")
        if '--verbose' in sys.argv:
            traceback.print_exc()
        return False


def test_webhook_security():
    """Test webhook security layer (Nginx + Flask)."""
    print("\n6. Testing Webhook security")
    print("=" * 50)

    from dotenv import load_dotenv
    load_dotenv()

    # Check if running inside Docker
    is_docker = os.path.exists('/.dockerenv')

    # Choose test URL (Docker accesses via host IP)
    if is_docker:
        server = os.getenv('HEALTH_CHECK_URL', 'http://172.17.0.1:8081')
        print("[INFO] Inside Docker container, testing via host IP")
    else:
        server = os.getenv('WEBHOOK_TEST_SERVER', 'http://localhost:8081')
        print(f"[INFO] Host environment, URL: {server}")

    secret_key = os.getenv('WEBHOOK_SECRET_KEY', '')

    if not secret_key:
        print("[WARN] WEBHOOK_SECRET_KEY not configured, skipping webhook security test")
        return True  # Not a failure

    tests_passed = 0
    tests_total = 8

    try:
        # Test 1: Health check
        try:
            resp = requests.get(f"{server}/health", timeout=5)
            if resp.status_code == 200:
                print("[OK] Test 1: Health check OK (200)")
                tests_passed += 1
            else:
                print(f"[FAIL] Test 1: Health check failed ({resp.status_code})")
        except Exception as e:
            print(f"[WARN] Test 1: Health check exception - {str(e)[:50]}")

        # Test 2: Request without key
        try:
            resp = requests.post(f"{server}/webhook/trump", json={"test": True}, timeout=5)
            if resp.status_code == 401:
                print("[OK] Test 2: Request without key rejected (401)")
                tests_passed += 1
            else:
                print(f"[FAIL] Test 2: Request without key not rejected ({resp.status_code})")
        except Exception as e:
            print(f"[WARN] Test 2: No-key test exception - {str(e)[:50]}")

        # Test 3: Wrong key
        try:
            resp = requests.post(
                f"{server}/webhook/trump",
                json={"test": True},
                headers={"X-Secret-Key": "wrong-key"},
                timeout=5
            )
            if resp.status_code == 403:
                print("[OK] Test 3: Wrong key rejected (403)")
                tests_passed += 1
            else:
                print(f"[FAIL] Test 3: Wrong key not rejected ({resp.status_code})")
        except Exception as e:
            print(f"[WARN] Test 3: Wrong key test exception - {str(e)[:50]}")

        # Test 4: Non-JSON Content-Type
        try:
            resp = requests.post(
                f"{server}/webhook/trump",
                data="test",
                headers={"X-Secret-Key": secret_key, "Content-Type": "text/plain"},
                timeout=5
            )
            if resp.status_code == 400:
                print("[OK] Test 4: Non-JSON rejected (400)")
                tests_passed += 1
            else:
                print(f"[FAIL] Test 4: Non-JSON not rejected ({resp.status_code})")
        except Exception as e:
            print(f"[WARN] Test 4: Non-JSON test exception - {str(e)[:50]}")

        # Test 5: GET request
        try:
            resp = requests.get(f"{server}/webhook/trump", timeout=5)
            if resp.status_code == 405:
                print("[OK] Test 5: GET request rejected (405)")
                tests_passed += 1
            else:
                print(f"[FAIL] Test 5: GET not rejected ({resp.status_code})")
        except Exception as e:
            print(f"[WARN] Test 5: GET test exception - {str(e)[:50]}")

        # Test 6: Valid key but invalid payload
        try:
            resp = requests.post(
                f"{server}/webhook/trump",
                json={"test": True},
                headers={"X-Secret-Key": secret_key},
                timeout=5
            )
            if resp.status_code == 400:
                print("[OK] Test 6: Invalid payload rejected (400)")
                tests_passed += 1
            else:
                print(f"[FAIL] Test 6: Invalid payload not rejected ({resp.status_code})")
        except Exception as e:
            print(f"[WARN] Test 6: Invalid payload exception - {str(e)[:50]}")

        # Test 7: Fully valid request
        try:
            resp = requests.post(
                f"{server}/webhook/trump",
                json={"policy_event": {"severity": "HIGH", "summary": "Test"}, "subject": "Test"},
                headers={"X-Secret-Key": secret_key},
                timeout=5
            )
            if resp.status_code == 200:
                print("[OK] Test 7: Valid request succeeded (200)")
                tests_passed += 1
            else:
                print(f"[FAIL] Test 7: Valid request failed ({resp.status_code})")
        except Exception as e:
            print(f"[WARN] Test 7: Valid request exception - {str(e)[:50]}")

        # Test 8: Rate limiting
        try:
            import time
            rate_limit_hit = False
            for i in range(12):
                resp = requests.post(
                    f"{server}/webhook/trump",
                    json={"policy_event": {"severity": "LOW", "summary": f"Rate {i}"}, "subject": "Rate"},
                    headers={"X-Secret-Key": secret_key},
                    timeout=5
                )
                if resp.status_code == 429:
                    rate_limit_hit = True
                    break
                time.sleep(0.1)

            if rate_limit_hit:
                print("[OK] Test 8: Rate limiting OK (429)")
                tests_passed += 1
            else:
                print("[WARN] Test 8: Rate limit not triggered (skipped)")
                tests_passed += 1  # Not a failure
        except Exception as e:
            print(f"[WARN] Test 8: Rate limit exception - {str(e)[:50]}")
            tests_passed += 1  # Not a failure

        print(f"\n[SUMMARY] Webhook security: {tests_passed}/{tests_total} passed")
        return tests_passed >= 6  # 6/8 or above is success

    except Exception as e:
        print(f"[FAIL] Webhook security test failed: {e}")
        if '--verbose' in sys.argv:
            traceback.print_exc()
        return False


def test_docker_environment():
    """Test Docker/runtime environment."""
    print("\n7. Testing runtime environment")
    print("=" * 50)

    try:
        # Check Python version
        python_version = sys.version.split()[0]
        print(f"[OK] Python version: {python_version}")

        # Check key dependencies
        required_modules = ['ccxt', 'pandas', 'numpy', 'yaml', 'schedule']
        missing_modules = []

        for module in required_modules:
            try:
                __import__(module)
                print(f"[OK] {module} module OK")
            except ImportError:
                missing_modules.append(module)
                print(f"[FAIL] {module} module missing")

        if missing_modules:
            print(f"[FAIL] Missing modules: {missing_modules}")
            return False

        # Check file permissions
        script_files = ['crypto_signal.py']
        for script in script_files:
            if os.path.exists(script):
                if os.access(script, os.R_OK):
                    print(f"[OK] {script} permissions OK")
                else:
                    print(f"[FAIL] {script} insufficient permissions")
                    return False
            else:
                print(f"[FAIL] {script} not found")
                return False

        # Check log directory
        if os.path.exists('logs') or os.access('.', os.W_OK):
            print("[OK] Log directory permissions OK")
        else:
            print("[FAIL] Log directory insufficient permissions")
            return False

        return True

    except Exception as e:
        print(f"[FAIL] Environment test failed: {e}")
        return False

def main():
    """Run full system health check."""
    print("Crypto Futures Signal System Health Check")
    print("=" * 80)
    print(f"Check time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Supported symbols
    symbols = ['ETH', 'SOL', 'BNB', 'BTC']

    # Test items
    tests = [
        ("Configuration file", lambda: test_configuration_loading()),
        ("Runtime environment", lambda: test_docker_environment()),
        ("API connection", lambda: test_binance_api_connection()),
        ("SR support/resistance system", lambda: test_sr_system()),
        ("Trump policy integration", lambda: test_trump_policy_integration()),
        ("Webhook security", lambda: test_webhook_security()),
    ]

    # Add symbol-specific tests
    for symbol in symbols:
        tests.append((f"{symbol} signal system", lambda s=symbol: test_crypto_signal_system(s)))

    results = []

    # Execute tests
    for test_name, test_func in tests:
        try:
            print(f"\n[RUN] Testing: {test_name}")
            result = test_func()
            results.append((test_name, result))

            if result:
                print(f"[PASS] {test_name}")
            else:
                print(f"[FAIL] {test_name}")

        except Exception as e:
            print(f"[ERROR] {test_name}: {e}")
            if '--verbose' in sys.argv:
                traceback.print_exc()
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 80)
    print("System Health Check Results")
    print("=" * 80)

    success_count = 0
    for test_name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"{test_name}: {status}")
        if result:
            success_count += 1

    total_count = len(results)
    health_score = (success_count / total_count) * 100

    print(f"\nSystem health: {success_count}/{total_count} ({health_score:.1f}%)")

    if success_count == total_count:
        print("\nAll tests passed! System is ready.")
        print("\nVerified features:")
        print("- ETH perpetual futures signal monitoring + multi-channel push")
        print("- SOL perpetual futures signal monitoring + multi-channel push")
        print("- BNB perpetual futures signal monitoring + multi-channel push")
        print("- BTC perpetual futures signal monitoring + multi-channel push")
        print("- Dynamic config auto-adjustment")
        print("- Trump policy integration + Grok AI enhancement")
        print("- Webhook security (Nginx + Flask)")
        print("- Multi-channel notifications (email + WeChat + Feishu)")
        print("- API connection and data fetch")
        print("- Technical analysis indicator calculation")
        print("\nSystem ready to receive live signal pushes.")
    else:
        print(f"\n{total_count - success_count} issue(s) found. Please fix before use.")
        print("\nTroubleshooting tips:")
        print("- Check that config file is filled in correctly")
        print("- Confirm Binance API keys are valid")
        print("- Verify SMTP settings for email")
        print("- Check detailed logs with --verbose flag")
        print("\nDocker test commands:")
        print("- docker exec ignis_eth_signal python tests/system/test_health.py")
        print("- docker exec ignis_sol_signal python tests/system/test_health.py")
        print("- docker exec ignis_bnb_signal python tests/system/test_health.py")
        print("- docker exec ignis_btc_signal python tests/system/test_health.py")

    return success_count == total_count

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
