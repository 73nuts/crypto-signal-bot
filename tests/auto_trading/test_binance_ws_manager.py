"""
BinanceWebSocketManager unit tests.

How to run:
    pytest tests/auto_trading/test_binance_ws_manager.py -v

Test coverage:
    - WebSocket connection management
    - listenKey lifecycle
    - Event handling (ACCOUNT_UPDATE/ORDER_TRADE_UPDATE)
    - Cache read interface

Version: v5.0.0
Created: 2026-01-16
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

# Configure pytest-asyncio
pytestmark = pytest.mark.anyio


class TestBinanceWebSocketManager:
    """WebSocket manager tests."""

    @pytest.fixture
    def manager(self):
        """Create a WebSocket manager for testing."""
        from src.trading.binance_ws_manager import BinanceWebSocketManager

        return BinanceWebSocketManager(
            api_key="test_api_key",
            api_secret="test_api_secret",
            testnet=True,
        )

    def test_init(self, manager):
        """Test initialization."""
        assert manager._api_key == "test_api_key"
        assert manager.testnet is True
        assert manager._listen_key is None
        assert manager._ws is None
        assert manager.is_connected is False

    def test_get_positions_empty(self, manager):
        """Test get_positions with empty cache."""
        positions = manager.get_positions()
        assert positions == []

    def test_get_balance_empty(self, manager):
        """Test get_balance with empty cache."""
        balance = manager.get_balance("USDT")
        assert balance is None

    @pytest.mark.asyncio
    async def test_handle_account_update_positions(self, manager):
        """Test handling ACCOUNT_UPDATE event (position update)."""
        event_data = {
            "e": "ACCOUNT_UPDATE",
            "T": 1234567890123,
            "a": {
                "B": [{"a": "USDT", "wb": "1000.0", "cw": "950.0", "bc": "0"}],
                "P": [
                    {
                        "s": "ETHUSDT",
                        "pa": "1.5",
                        "ep": "3200.50",
                        "up": "25.75",
                        "ps": "LONG",
                    },
                    {
                        "s": "BTCUSDT",
                        "pa": "-0.1",
                        "ep": "96000.00",
                        "up": "-50.00",
                        "ps": "SHORT",
                    },
                ],
            },
        }

        # Call async handler
        await manager._handle_account_update(event_data)

        # Verify position cache (raw format)
        eth_positions = manager.get_positions("ETHUSDT")
        assert len(eth_positions) == 1
        assert float(eth_positions[0]["positionAmt"]) == 1.5
        assert eth_positions[0]["entryPrice"] == "3200.50"

        btc_positions = manager.get_positions("BTCUSDT")
        assert len(btc_positions) == 1
        assert float(btc_positions[0]["positionAmt"]) == -0.1

        # Verify balance cache
        usdt_balance = manager.get_balance("USDT")
        assert usdt_balance is not None
        assert usdt_balance["total"] == 1000.0
        assert usdt_balance["free"] == 950.0

    def test_get_positions_filter_by_symbol(self, manager):
        """Test filtering positions by symbol."""
        # Simulate multiple positions (direct cache manipulation)
        manager._cache.positions = [
            {"symbol": "ETHUSDT", "positionAmt": "1.0", "entryPrice": "3200"},
            {"symbol": "BTCUSDT", "positionAmt": "-0.5", "entryPrice": "96000"},
        ]

        # Filter ETH
        eth_positions = manager.get_positions("ETHUSDT")
        assert len(eth_positions) == 1
        assert eth_positions[0]["symbol"] == "ETHUSDT"

        # Get all
        all_positions = manager.get_positions()
        assert len(all_positions) == 2

    def test_keepalive_without_listen_key(self, manager):
        """Test keepalive returns False when no listenKey."""

        # Should return False directly when no listenKey
        result = asyncio.get_event_loop().run_until_complete(
            manager._keepalive_listen_key()
        )
        assert result is False

    def test_close_without_listen_key(self, manager):
        """Test close returns True when no listenKey."""

        # Should return True directly when no listenKey
        result = asyncio.get_event_loop().run_until_complete(
            manager._close_listen_key()
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_handle_order_update(self, manager):
        """Test handling ORDER_TRADE_UPDATE event."""
        event_data = {
            "e": "ORDER_TRADE_UPDATE",
            "T": 1234567890123,
            "o": {
                "s": "ETHUSDT",
                "c": "client_order_123",
                "S": "BUY",
                "o": "MARKET",
                "q": "1.0",
                "p": "0",
                "ap": "3200.50",
                "X": "FILLED",
                "i": 123456789,
            },
        }

        # Order callback does not affect cache, only triggers callback
        # Verify no exception is raised
        await manager._handle_order_update(event_data)

    def test_thread_safety(self, manager):
        """Test thread safety (lock mechanism)."""
        import threading

        results = []

        def update_cache():
            for i in range(100):
                with manager._cache_lock:
                    manager._cache.positions.append(
                        {"symbol": f"TEST{i}USDT", "positionAmt": "1.0"}
                    )
                results.append(len(manager.get_positions()))

        threads = [threading.Thread(target=update_cache) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify no concurrency conflicts
        assert len(results) == 500


class TestBinanceTradingClientWebSocket:
    """BinanceTradingClient WebSocket integration tests."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Binance client."""
        with (
            patch("binance.client.Client"),
            patch(
                "src.trading.binance_trading_client.BinanceTradingClient._validate_position_mode"
            ),
        ):
            from src.trading.binance_trading_client import BinanceTradingClient

            client = BinanceTradingClient(
                api_key="test_key",
                api_secret="test_secret",
                testnet=True,
                symbol="ETH",
                use_websocket=True,
            )
            return client

    def test_init_with_websocket(self, mock_client):
        """Test initialization with WebSocket enabled."""
        assert mock_client._use_websocket is True
        assert mock_client._ws_manager is None  # Lazy initialization

    def test_get_positions_falls_back_to_rest(self, mock_client):
        """Test fallback to REST when WebSocket is not connected."""
        # WebSocket not started, should use REST
        mock_client._positions_cache = [
            {"symbol": "ETHUSDT", "side": "long", "contracts": 1.0}
        ]
        mock_client._positions_cache_time = 9999999999  # Not expired

        positions = mock_client.get_positions()
        assert len(positions) == 1

    @pytest.mark.asyncio
    async def test_start_websocket(self, mock_client):
        """Test WebSocket startup."""
        with patch(
            "src.trading.binance_ws_manager.BinanceWebSocketManager"
        ) as MockWSManager:
            mock_ws = AsyncMock()
            mock_ws.start.return_value = True
            MockWSManager.return_value = mock_ws

            result = await mock_client.start_websocket()
            assert result is True
            assert mock_client._ws_manager is not None

    @pytest.mark.asyncio
    async def test_stop_websocket(self, mock_client):
        """Test WebSocket shutdown."""
        mock_ws = AsyncMock()
        mock_client._ws_manager = mock_ws

        await mock_client.stop_websocket()
        mock_ws.stop.assert_called_once()
        assert mock_client._ws_manager is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
