"""
Binance Futures WebSocket User Data Stream manager.

Responsibilities:
1. listenKey lifecycle management (create/renew/close)
2. WebSocket connection management (connect/reconnect)
3. Real-time in-memory cache updates (balance/positions)
4. Query interface compatible with REST API

Usage:
    manager = BinanceWebSocketManager(api_key, api_secret, testnet=True)
    await manager.start()
    positions = manager.get_positions()
    await manager.stop()
"""

import asyncio
import hashlib
import hmac
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp


@dataclass
class AccountCache:
    """Account cache data structure."""

    balances: Dict[str, Dict[str, float]] = field(default_factory=dict)
    positions: List[Dict[str, Any]] = field(default_factory=list)
    last_update: float = 0.0


class BinanceWebSocketManager:
    """
    Binance Futures WebSocket User Data Stream manager.

    Receives account updates via WebSocket in real time, avoiding
    frequent REST API calls that could trigger rate limits.
    """

    # Constants
    KEEPALIVE_INTERVAL = 25 * 60  # 25 min renewal (listenKey valid for 60 min)
    RECONNECT_DELAY_BASE = 1  # Base reconnect delay (seconds)
    RECONNECT_MAX_DELAY = 60  # Max reconnect delay
    MAX_RECONNECT_ATTEMPTS = 10  # Max reconnect attempts

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        on_position_update: Optional[Callable[[Dict], None]] = None,
        on_order_update: Optional[Callable[[Dict], None]] = None,
        on_balance_update: Optional[Callable[[Dict], None]] = None,
    ):
        """
        Initialize WebSocket manager.

        Args:
            api_key: Binance API Key
            api_secret: Binance API Secret
            testnet: Whether to use testnet
            on_position_update: Position update callback
            on_order_update: Order update callback
            on_balance_update: Balance update callback
        """
        self.logger = logging.getLogger(__name__)
        self._api_key = api_key
        self._api_secret = api_secret
        self.testnet = testnet

        # Callbacks
        self._on_position_update = on_position_update
        self._on_order_update = on_order_update
        self._on_balance_update = on_balance_update

        # API URLs
        if testnet:
            self._rest_base_url = "https://testnet.binancefuture.com"
            self._ws_base_url = "wss://stream.binancefuture.com"
        else:
            self._rest_base_url = "https://fapi.binance.com"
            self._ws_base_url = "wss://fstream.binance.com"

        # State
        self._listen_key: Optional[str] = None
        self._ws_session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._running = False
        self._reconnect_count = 0

        # Cache (thread-safe)
        self._cache = AccountCache()
        self._cache_lock = threading.Lock()

        # Background tasks
        self._keepalive_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None

        self.logger.info(f"BinanceWebSocketManager initialized - testnet={testnet}")

    # ========================================
    # listenKey management
    # ========================================

    def _sign_request(self, params: Dict) -> str:
        """Generate request signature."""
        query_string = urlencode(params)
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    async def _create_listen_key(self) -> Optional[str]:
        """
        Create listenKey.

        POST /fapi/v1/listenKey
        """
        url = f"{self._rest_base_url}/fapi/v1/listenKey"
        headers = {"X-MBX-APIKEY": self._api_key}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        listen_key = data.get("listenKey")
                        self.logger.info(f"listenKey created: {listen_key[:20]}...")
                        return listen_key
                    else:
                        error = await resp.text()
                        self.logger.error(f"Failed to create listenKey: {resp.status} - {error}")
                        return None
        except Exception as e:
            self.logger.error(f"Exception creating listenKey: {e}")
            return None

    async def _keepalive_listen_key(self) -> bool:
        """
        Renew listenKey.

        PUT /fapi/v1/listenKey
        """
        if not self._listen_key:
            return False

        url = f"{self._rest_base_url}/fapi/v1/listenKey"
        headers = {"X-MBX-APIKEY": self._api_key}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers) as resp:
                    if resp.status == 200:
                        self.logger.debug("listenKey renewed")
                        return True
                    else:
                        error = await resp.text()
                        self.logger.error(f"Failed to renew listenKey: {resp.status} - {error}")
                        return False
        except Exception as e:
            self.logger.error(f"Exception renewing listenKey: {e}")
            return False

    async def _close_listen_key(self) -> bool:
        """
        Close listenKey.

        DELETE /fapi/v1/listenKey
        """
        if not self._listen_key:
            return True

        url = f"{self._rest_base_url}/fapi/v1/listenKey"
        headers = {"X-MBX-APIKEY": self._api_key}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(url, headers=headers) as resp:
                    if resp.status == 200:
                        self.logger.info("listenKey closed")
                        return True
                    else:
                        error = await resp.text()
                        self.logger.warning(
                            f"Failed to close listenKey: {resp.status} - {error}"
                        )
                        return False
        except Exception as e:
            self.logger.warning(f"Exception closing listenKey: {e}")
            return False

    async def _keepalive_loop(self):
        """Background task: periodically renew listenKey."""
        while self._running:
            try:
                await asyncio.sleep(self.KEEPALIVE_INTERVAL)
                if self._running:
                    success = await self._keepalive_listen_key()
                    if not success:
                        self.logger.warning("listenKey renewal failed, attempting recreation")
                        self._listen_key = await self._create_listen_key()
                        if self._listen_key:
                            await self._reconnect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Keepalive loop exception: {e}")

    # ========================================
    # WebSocket connection management
    # ========================================

    async def start(self) -> bool:
        """
        Start WebSocket connection.

        Returns:
            Whether startup succeeded
        """
        if self._running:
            self.logger.warning("WebSocket already running")
            return True

        # 1. Create listenKey
        self._listen_key = await self._create_listen_key()
        if not self._listen_key:
            self.logger.error("Cannot create listenKey, startup failed")
            return False

        # 2. Initialize cache from REST API
        await self._init_cache_from_rest()

        # 3. Connect WebSocket
        success = await self._connect_websocket()
        if not success:
            return False

        self._running = True

        # 4. Start background tasks
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        self._receive_task = asyncio.create_task(self._receive_loop())

        self.logger.info("WebSocket started successfully")
        return True

    async def stop(self):
        """Stop WebSocket connection."""
        self._running = False

        # Cancel background tasks
        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        # Close WebSocket
        if self._ws and not self._ws.closed:
            await self._ws.close()

        if self._ws_session and not self._ws_session.closed:
            await self._ws_session.close()

        # Close listenKey
        await self._close_listen_key()

        self.logger.info("WebSocket stopped")

    async def _connect_websocket(self) -> bool:
        """Establish WebSocket connection."""
        if not self._listen_key:
            return False

        ws_url = f"{self._ws_base_url}/ws/{self._listen_key}"

        try:
            self._ws_session = aiohttp.ClientSession()
            self._ws = await self._ws_session.ws_connect(
                ws_url,
                heartbeat=30,  # 30s heartbeat
            )
            self._reconnect_count = 0
            self.logger.info(f"WebSocket connected: {ws_url[:50]}...")
            return True
        except Exception as e:
            self.logger.error(f"WebSocket connection failed: {e}")
            return False

    async def _reconnect(self):
        """Reconnect with exponential backoff."""
        if self._reconnect_count >= self.MAX_RECONNECT_ATTEMPTS:
            self.logger.error(f"Max reconnect attempts ({self.MAX_RECONNECT_ATTEMPTS}) exceeded, stopping")
            self._running = False
            return

        delay = min(
            self.RECONNECT_DELAY_BASE * (2**self._reconnect_count),
            self.RECONNECT_MAX_DELAY,
        )
        self._reconnect_count += 1

        self.logger.warning(
            f"WebSocket disconnected, reconnecting in {delay}s (attempt {self._reconnect_count})"
        )
        await asyncio.sleep(delay)

        # Close old connection
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._ws_session and not self._ws_session.closed:
            await self._ws_session.close()

        # Reconnect
        success = await self._connect_websocket()
        if not success:
            await self._reconnect()

    async def _receive_loop(self):
        """Loop to receive WebSocket messages."""
        while self._running:
            try:
                if not self._ws or self._ws.closed:
                    await self._reconnect()
                    continue

                msg = await self._ws.receive()

                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    self.logger.warning("WebSocket connection closed")
                    await self._reconnect()
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self.logger.error(f"WebSocket error: {self._ws.exception()}")
                    await self._reconnect()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Exception receiving message: {e}")
                await asyncio.sleep(1)

    # ========================================
    # Message handling
    # ========================================

    async def _handle_message(self, raw_message: str):
        """Handle WebSocket message."""
        try:
            data = json.loads(raw_message)
            event_type = data.get("e")

            if event_type == "ACCOUNT_UPDATE":
                await self._handle_account_update(data)
            elif event_type == "ORDER_TRADE_UPDATE":
                await self._handle_order_update(data)
            elif event_type == "ACCOUNT_CONFIG_UPDATE":
                self.logger.debug(f"Account config update: {data}")
            elif event_type == "listenKeyExpired":
                self.logger.warning("listenKey expired, recreating")
                self._listen_key = await self._create_listen_key()
                if self._listen_key:
                    await self._reconnect()
            else:
                self.logger.debug(f"Unknown event type: {event_type}")

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON parse failed: {e}")
        except Exception as e:
            self.logger.error(f"Message handling exception: {e}")

    async def _handle_account_update(self, data: Dict):
        """
        Handle ACCOUNT_UPDATE event.

        Event structure:
        {
            "e": "ACCOUNT_UPDATE",
            "T": 1564745798939,
            "a": {
                "B": [{"a": "USDT", "wb": "100", "cw": "100", "bc": "0"}],
                "P": [{"s": "BTCUSDT", "pa": "0.001", "ep": "50000", ...}]
            }
        }
        """
        account_data = data.get("a", {})

        with self._cache_lock:
            # Update balances
            for balance in account_data.get("B", []):
                asset = balance.get("a")  # asset name
                if asset:
                    self._cache.balances[asset] = {
                        "free": float(balance.get("cw", 0)),  # available balance
                        "total": float(balance.get("wb", 0)),  # total balance
                        "unrealized_pnl": float(balance.get("bc", 0)),  # unrealized PnL
                    }

            # Update positions
            for position in account_data.get("P", []):
                symbol = position.get("s")
                pos_amt = float(position.get("pa", 0))

                # Update or remove position
                self._update_position_cache(symbol, position, pos_amt)

            self._cache.last_update = time.time()

        self.logger.debug(
            f"Account update: {len(account_data.get('B', []))} balances, "
            f"{len(account_data.get('P', []))} positions"
        )

        # Trigger callbacks
        if self._on_balance_update:
            self._on_balance_update(self._cache.balances)
        if self._on_position_update:
            self._on_position_update(self._cache.positions)

    def _update_position_cache(self, symbol: str, position: Dict, pos_amt: float):
        """Update position cache."""
        # Find existing position
        existing_idx = None
        for i, p in enumerate(self._cache.positions):
            if p.get("symbol") == symbol:
                existing_idx = i
                break

        if pos_amt == 0:
            # Position closed, remove
            if existing_idx is not None:
                self._cache.positions.pop(existing_idx)
        else:
            # Update or add position
            new_position = {
                "symbol": symbol,
                "positionAmt": str(pos_amt),
                "entryPrice": position.get("ep", "0"),
                "unrealizedProfit": position.get("up", "0"),
                "marginType": position.get("mt", "cross"),
                "positionSide": position.get("ps", "BOTH"),
            }

            if existing_idx is not None:
                self._cache.positions[existing_idx] = new_position
            else:
                self._cache.positions.append(new_position)

    async def _handle_order_update(self, data: Dict):
        """
        Handle ORDER_TRADE_UPDATE event.

        Event structure:
        {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "X": "FILLED",
                ...
            }
        }
        """
        order_data = data.get("o", {})

        self.logger.info(
            f"Order update: {order_data.get('s')} {order_data.get('S')} "
            f"status={order_data.get('X')} fill_price={order_data.get('ap', 'N/A')}"
        )

        # Trigger callback
        if self._on_order_update:
            self._on_order_update(order_data)

    # ========================================
    # Cache initialization
    # ========================================

    async def _init_cache_from_rest(self):
        """Initialize cache from REST API."""
        try:
            # Get account info
            timestamp = int(time.time() * 1000)
            params = {"timestamp": timestamp}
            params["signature"] = self._sign_request(params)

            url = f"{self._rest_base_url}/fapi/v2/account"
            headers = {"X-MBX-APIKEY": self._api_key}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        with self._cache_lock:
                            # Initialize balances
                            for asset in data.get("assets", []):
                                asset_name = asset.get("asset")
                                if asset_name:
                                    self._cache.balances[asset_name] = {
                                        "free": float(asset.get("availableBalance", 0)),
                                        "total": float(asset.get("walletBalance", 0)),
                                        "unrealized_pnl": float(
                                            asset.get("unrealizedProfit", 0)
                                        ),
                                    }

                            # Initialize positions
                            for pos in data.get("positions", []):
                                pos_amt = float(pos.get("positionAmt", 0))
                                if pos_amt != 0:
                                    self._cache.positions.append(
                                        {
                                            "symbol": pos.get("symbol"),
                                            "positionAmt": pos.get("positionAmt"),
                                            "entryPrice": pos.get("entryPrice"),
                                            "unrealizedProfit": pos.get(
                                                "unrealizedProfit"
                                            ),
                                            "marginType": pos.get("marginType"),
                                            "positionSide": pos.get("positionSide"),
                                        }
                                    )

                            self._cache.last_update = time.time()

                        self.logger.info(
                            f"Cache initialized: {len(self._cache.balances)} assets, "
                            f"{len(self._cache.positions)} positions"
                        )
                    else:
                        error = await resp.text()
                        self.logger.error(f"Failed to get account info: {resp.status} - {error}")

        except Exception as e:
            self.logger.error(f"Cache initialization exception: {e}")

    # ========================================
    # Public query interface
    # ========================================

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """
        Get positions (from cache).

        Args:
            symbol: Optional filter for specific trading pair

        Returns:
            List of positions
        """
        with self._cache_lock:
            if symbol:
                return [p for p in self._cache.positions if p.get("symbol") == symbol]
            return list(self._cache.positions)

    def get_balance(self, asset: str = "USDT") -> Optional[Dict]:
        """
        Get balance (from cache).

        Args:
            asset: Asset name, default USDT

        Returns:
            Balance info
        """
        with self._cache_lock:
            return self._cache.balances.get(asset)

    def get_all_balances(self) -> Dict[str, Dict]:
        """Get all asset balances."""
        with self._cache_lock:
            return dict(self._cache.balances)

    def invalidate_cache(self):
        """
        Force cache refresh.

        Call after placing an order to ensure data consistency.
        """
        asyncio.create_task(self._init_cache_from_rest())

    @property
    def is_connected(self) -> bool:
        """Whether WebSocket is connected."""
        return self._running and self._ws is not None and not self._ws.closed

    @property
    def last_update_time(self) -> float:
        """Last update timestamp."""
        with self._cache_lock:
            return self._cache.last_update


# ========================================
# Command-line test
# ========================================


async def _test_connection():
    """Test WebSocket connection."""
    import os

    api_key = os.getenv("BINANCE_TESTNET_API_KEY")
    api_secret = os.getenv("BINANCE_TESTNET_API_SECRET")

    if not api_key or not api_secret:
        print("Please set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET env vars")
        return

    manager = BinanceWebSocketManager(
        api_key=api_key,
        api_secret=api_secret,
        testnet=True,
    )

    print("Starting WebSocket...")
    success = await manager.start()

    if success:
        print("Connected! Waiting for messages...")
        print(f"Initial positions: {manager.get_positions()}")
        print(f"USDT balance: {manager.get_balance('USDT')}")

        # Wait 30 seconds for messages
        await asyncio.sleep(30)

        print(f"Last update: {manager.last_update_time}")
        await manager.stop()
    else:
        print("Connection failed")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Binance WebSocket Manager")
    parser.add_argument("--test", action="store_true", help="Test connection")
    args = parser.parse_args()

    if args.test:
        from dotenv import load_dotenv

        load_dotenv()
        asyncio.run(_test_connection())
    else:
        parser.print_help()
