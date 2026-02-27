"""
Hedge Mode validation logic unit tests.

Test scenarios:
1. Mode matches - validation passes
2. Mode mismatch - auto-switch succeeds
3. Mode mismatch - switch fails (open positions) raises exception
4. API call fails - raises exception

How to run:
    python -m pytest tests/auto_trading/test_hedge_mode_validation.py -v

Version: v4.3.4
Created: 2025-12-01
"""

import os
import sys
import pytest
from unittest.mock import Mock, patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from binance.exceptions import BinanceAPIException


class TestHedgeModeValidation:
    """Hedge Mode validation test class."""

    @pytest.fixture
    def mock_binance_client(self):
        """Create a mock Binance client."""
        with patch('src.trading.binance_trading_client.Client') as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            yield mock_client

    def test_mode_match_hedge(self, mock_binance_client):
        """Scenario 1: Config hedge, API returns hedge - validation passes."""
        from src.trading.binance_trading_client import BinanceTradingClient

        # API returns hedge mode
        mock_binance_client.futures_get_position_mode.return_value = {
            'dualSidePosition': True
        }

        # Should not raise
        client = BinanceTradingClient(
            api_key='test_key',
            api_secret='test_secret',
            testnet=True,
            symbol='ETH',
            binance_config={'position_mode': 'hedge'}
        )

        assert client is not None
        mock_binance_client.futures_get_position_mode.assert_called_once()

    def test_mode_match_one_way(self, mock_binance_client):
        """Scenario 1b: Config one_way, API returns one_way - validation passes."""
        from src.trading.binance_trading_client import BinanceTradingClient

        # API returns one_way mode
        mock_binance_client.futures_get_position_mode.return_value = {
            'dualSidePosition': False
        }

        client = BinanceTradingClient(
            api_key='test_key',
            api_secret='test_secret',
            testnet=True,
            symbol='ETH',
            binance_config={'position_mode': 'one_way'}
        )

        assert client is not None

    def test_mode_mismatch_switch_success(self, mock_binance_client):
        """Scenario 2: Mode mismatch, auto-switch succeeds."""
        from src.trading.binance_trading_client import BinanceTradingClient

        # API returns one_way, config expects hedge
        mock_binance_client.futures_get_position_mode.return_value = {
            'dualSidePosition': False
        }
        # Switch succeeds
        mock_binance_client.futures_change_position_mode.return_value = {'code': 200}

        client = BinanceTradingClient(
            api_key='test_key',
            api_secret='test_secret',
            testnet=True,
            symbol='ETH',
            binance_config={'position_mode': 'hedge'}
        )

        assert client is not None
        mock_binance_client.futures_change_position_mode.assert_called_once()

    def test_mode_mismatch_switch_failed_has_position(self, mock_binance_client):
        """Scenario 3: Mode mismatch, open positions prevent switch - raises exception."""
        from src.trading.binance_trading_client import BinanceTradingClient

        # API returns one_way, config expects hedge
        mock_binance_client.futures_get_position_mode.return_value = {
            'dualSidePosition': False
        }
        # Switch fails (open positions)
        error = BinanceAPIException(
            response=Mock(status_code=400),
            status_code=400,
            text='{"code": -4068, "msg": "Position side cannot be changed if there exists open positions."}'
        )
        error.code = -4068
        error.message = "Position side cannot be changed if there exists open positions."
        mock_binance_client.futures_change_position_mode.side_effect = error

        # Should raise RuntimeError (mock notification modules to avoid real sends)
        with pytest.raises(RuntimeError) as excinfo:
            with patch('src.notifications.wechat_sender.WeChatSender.send'):
                with patch('src.notifications.email_sender.EmailSender.send'):
                    BinanceTradingClient(
                        api_key='test_key',
                        api_secret='test_secret',
                        testnet=True,
                        symbol='ETH',
                        binance_config={'position_mode': 'hedge'}
                    )

        assert "Hedge Mode Mismatch" in str(excinfo.value)

    def test_api_call_failed(self, mock_binance_client):
        """Scenario 4: API call fails - raises exception."""
        from src.trading.binance_trading_client import BinanceTradingClient

        # API call fails
        error = BinanceAPIException(
            response=Mock(status_code=500),
            status_code=500,
            text='{"code": -1000, "msg": "Unknown error"}'
        )
        error.code = -1000
        error.message = "Unknown error"
        mock_binance_client.futures_get_position_mode.side_effect = error

        with pytest.raises(RuntimeError) as excinfo:
            with patch('src.notifications.wechat_sender.WeChatSender.send'):
                with patch('src.notifications.email_sender.EmailSender.send'):
                    BinanceTradingClient(
                        api_key='test_key',
                        api_secret='test_secret',
                        testnet=True,
                        symbol='ETH',
                        binance_config={'position_mode': 'hedge'}
                    )

        assert "Cannot validate position mode" in str(excinfo.value)

    def test_default_config_is_one_way(self, mock_binance_client):
        """Scenario 5: No binance_config provided, defaults to one_way."""
        from src.trading.binance_trading_client import BinanceTradingClient

        # API returns one_way (matches default)
        mock_binance_client.futures_get_position_mode.return_value = {
            'dualSidePosition': False
        }

        # No binance_config passed
        client = BinanceTradingClient(
            api_key='test_key',
            api_secret='test_secret',
            testnet=True,
            symbol='ETH'
        )

        assert client is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
