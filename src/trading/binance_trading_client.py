"""
Binance futures trading client.

Responsibilities:
1. Wrap Binance futures API (order placement, query, cancellation)
2. Support Testnet/Mainnet switching
3. Error handling and retry logic
4. API rate-limit protection
5. Position mode and leverage configuration
6. Hedge Mode validation on startup
7. Algo Order API support (migrated 2025-12-09)

Uses the python-binance official library (replaces ccxt, full testnet support).
"""

import logging
import time
import hmac
import hashlib
import requests
from typing import Dict, Optional, List

from binance.client import Client
from binance.exceptions import (
    BinanceAPIException,
    BinanceOrderException,
    BinanceRequestException,
)


class BinanceTradingClient:
    """Binance futures trading client (python-binance based)."""

    # Class-level: startup failure alert throttle (once per 60 seconds)
    _last_startup_alert_time: float = 0
    _STARTUP_ALERT_THROTTLE: int = 60  # seconds

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        symbol: str = "ETH",
        binance_config: Optional[Dict] = None,
        email_config: Optional[Dict] = None,
        use_websocket: bool = True,
    ):
        """
        Initialize Binance trading client.

        Args:
            api_key: Binance API Key
            api_secret: Binance API Secret
            testnet: Use testnet (default True, safety valve)
            symbol: Asset symbol (ETH/SOL/BNB/BTC)
            binance_config: Binance config dict (contains position_mode etc.)
            email_config: Email config (for startup failure alerts)
            use_websocket: Enable WebSocket real-time data (default enabled)
        """
        self.logger = logging.getLogger(f"{__name__}.{symbol}")
        self.symbol = symbol
        self.testnet = testnet
        self.binance_config = binance_config or {}
        self.email_config = email_config

        # Store API keys for direct Algo Order REST calls
        self._api_key = api_key
        self._api_secret = api_secret

        # Build trading pair symbol (Binance futures format: ETHUSDT)
        self.trading_symbol = f"{symbol}USDT"

        # Algo Order API base URL
        if testnet:
            self._algo_base_url = "https://testnet.binancefuture.com"
        else:
            self._algo_base_url = "https://fapi.binance.com"

        # Initialize Binance client
        self.client = self._init_client(api_key, api_secret, testnet)

        # API call counter (for rate-limit monitoring)
        self.api_call_count = 0
        self.last_reset_time = time.time()

        # Balance/position cache (60s TTL, reduces API calls)
        self._balance_cache = None
        self._balance_cache_time = 0
        self._positions_cache = None
        self._positions_cache_time = 0
        self._cache_ttl = 60

        # WebSocket manager (optional)
        self._use_websocket = use_websocket
        self._ws_manager = None

        self.logger.info(
            f"BinanceTradingClient initialized - "
            f"symbol: {self.trading_symbol}, "
            f"testnet: {testnet}, "
            f"websocket: {use_websocket}"
        )

        # Validate Hedge Mode on startup (runs once)
        self._validate_position_mode()

    def _init_client(self, api_key: str, api_secret: str, testnet: bool) -> Client:
        """
        Initialize Binance client.

        Args:
            api_key: API key
            api_secret: API secret
            testnet: Use testnet

        Returns:
            Binance Client instance
        """
        if testnet:
            client = Client(api_key=api_key, api_secret=api_secret, testnet=True)
            client.API_URL = "https://testnet.binancefuture.com"
            self.logger.info("Using testnet: https://testnet.binancefuture.com")
        else:
            client = Client(api_key=api_key, api_secret=api_secret)
            self.logger.info("Using mainnet")

        return client

    def _validate_position_mode(self):
        """
        Validate account position mode matches config.

        Runs once on startup to ensure Hedge Mode is configured correctly.
        If mismatched, attempts to switch; if switch fails, sends alert and raises.

        Raises:
            RuntimeError: When position mode mismatches and cannot be switched.
        """
        expected_mode = self.binance_config.get("position_mode", "one_way").lower()
        expected_hedge = expected_mode == "hedge"

        try:
            actual_hedge = self._get_position_mode_from_api()

            if actual_hedge == expected_hedge:
                mode_str = "Hedge" if actual_hedge else "One-Way"
                self.logger.info(f"Position mode validated: {mode_str}")
                return

            self.logger.warning(
                f"Position mode mismatch - config: {expected_mode}, "
                f"actual: {'hedge' if actual_hedge else 'one_way'}"
            )

            if self.set_position_mode(hedge_mode=expected_hedge):
                self.logger.info("Position mode switched successfully")
                return

            # Switch failed (usually due to open positions)
            self._send_startup_failure_alert()
            raise RuntimeError(
                f"Hedge Mode Mismatch: account has open positions, cannot switch. "
                f"Close all positions and restart. "
                f"Expected: {expected_mode}, actual: {'hedge' if actual_hedge else 'one_way'}"
            )

        except BinanceAPIException as e:
            self.logger.error(f"Failed to fetch position mode: {e}")
            self._send_startup_failure_alert(
                reason=f"API Error: {e.code} - {e.message}"
            )
            raise RuntimeError(f"Cannot validate position mode: {e}")

    def _send_startup_failure_alert(self, reason: str = None):
        """
        Send startup failure alert (email + WeChat).

        Args:
            reason: Failure reason (optional)

        Note:
            Uses class-level throttle; only one alert per 60 seconds across all symbols.
        """
        now = time.time()
        if (
            now - BinanceTradingClient._last_startup_alert_time
            < self._STARTUP_ALERT_THROTTLE
        ):
            self.logger.info("Startup failure alert throttled (already sent within 60s)")
            return

        BinanceTradingClient._last_startup_alert_time = now

        from src.core.config import settings

        title = f"{settings.ENVIRONMENT_PREFIX} Startup Failed - Position Mode Conflict"

        message = (
            f"Trading Client startup failed\n\n"
            f"Symbol: {self.symbol}\n"
            f"Reason: {reason or 'Account has open positions, cannot switch position mode'}\n\n"
            f"Steps:\n"
            f"1. Log in to Binance App/Web\n"
            f"2. Close all {self.trading_symbol} positions manually\n"
            f"3. Restart the service\n\n"
            f"Environment: {'testnet' if self.testnet else 'production'}"
        )

        # WeChat push (Server Chan, no config needed)
        try:
            from src.notifications.wechat_sender import WeChatSender

            wechat = WeChatSender()
            wechat.send(title, message)
            self.logger.info("Startup failure alert sent (WeChat)")
        except Exception as e:
            self.logger.error(f"WeChat alert failed: {e}")

        # Email push (requires email_config)
        if self.email_config:
            try:
                from src.notifications.email_sender import EmailSender

                email = EmailSender(self.email_config)
                email.send(title, message)
                self.logger.info("Startup failure alert sent (email)")
            except Exception as e:
                self.logger.error(f"Email alert failed: {e}")

    def set_leverage(self, leverage: int = 1) -> bool:
        """
        Set leverage multiplier.

        Args:
            leverage: Leverage multiplier (1-125; testnet: recommend 1-5)

        Returns:
            Whether succeeded
        """
        try:
            result = self.client.futures_change_leverage(
                symbol=self.trading_symbol, leverage=leverage
            )
            self._increment_api_call()
            self.logger.info(f"Leverage set: {leverage}x - {result}")
            return True
        except BinanceAPIException as e:
            # -4028: leverage already at target
            if e.code == -4028:
                self.logger.info(f"Leverage already {leverage}x, no change needed")
                return True
            self.logger.error(f"Failed to set leverage: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to set leverage: {e}")
            return False

    def set_margin_type(self, margin_type: str = "ISOLATED") -> bool:
        """
        Set margin type.

        Args:
            margin_type: 'ISOLATED' or 'CROSSED'

        Returns:
            Whether succeeded

        Note:
            - ISOLATED: each position's risk is isolated; max loss = that position's margin
            - CROSSED: all positions share margin; one liquidation can affect others
            - Strategy enforces ISOLATED mode for controlled risk management
        """
        try:
            result = self.client.futures_change_margin_type(
                symbol=self.trading_symbol, marginType=margin_type.upper()
            )
            self._increment_api_call()
            mode_name = "ISOLATED" if margin_type.upper() == "ISOLATED" else "CROSSED"
            self.logger.info(f"Margin type set: {mode_name} - {result}")
            return True
        except BinanceAPIException as e:
            # -4046: margin type already at target
            if e.code == -4046:
                mode_name = "ISOLATED" if margin_type.upper() == "ISOLATED" else "CROSSED"
                self.logger.info(f"Margin type already {mode_name}, no change needed")
                return True
            # -4047: cannot change with open positions
            elif e.code == -4047:
                self.logger.warning("Cannot change margin type with open positions, close first")
                return False
            self.logger.error(f"Failed to set margin type: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to set margin type: {e}")
            return False

    def set_position_mode(self, hedge_mode: bool = True) -> bool:
        """
        Set position mode.

        Args:
            hedge_mode: True=hedge mode (can hold both long and short simultaneously)
                       False=one-way mode (single direction only)

        Returns:
            Whether succeeded
        """
        try:
            result = self.client.futures_change_position_mode(
                dualSidePosition="true" if hedge_mode else "false"
            )
            self._increment_api_call()
            mode = "Hedge" if hedge_mode else "One-Way"
            self.logger.info(f"Position mode set: {mode} - {result}")
            return True
        except BinanceAPIException as e:
            # -4059: position mode already at target
            if e.code == -4059:
                self.logger.info("Position mode already at target, no change needed")
                return True
            # -4068: cannot change with open positions
            elif e.code == -4068:
                # Must get real mode from API, not from config
                try:
                    actual_mode = self._get_position_mode_from_api()
                except Exception as mode_err:
                    self.logger.warning(f"Failed to get position mode from API: {mode_err}")
                    actual_mode = not hedge_mode  # assume mismatch on API failure
                if actual_mode == hedge_mode:
                    self.logger.info("Cannot switch with open positions, but already at target mode")
                    return True
                else:
                    self.logger.error(
                        f"Cannot switch position mode with open positions! "
                        f"Current: {'One-Way' if not actual_mode else 'Hedge'}, "
                        f"Target: {'One-Way' if not hedge_mode else 'Hedge'}. "
                        f"Close positions first."
                    )
                    return False
            self.logger.warning(f"Failed to set position mode: {e}")
            return False
        except Exception as e:
            self.logger.warning(f"Failed to set position mode: {e}")
            return False

    def _get_position_mode_from_api(self) -> bool:
        """
        Get actual position mode from Binance API.

        Returns:
            True=hedge mode, False=one-way mode

        Raises:
            BinanceAPIException: On API call failure
        """
        result = self.client.futures_get_position_mode()
        self._increment_api_call()
        is_hedge = result.get("dualSidePosition", False)
        mode = "hedge" if is_hedge else "one_way"
        self.logger.info(f"API position mode: {mode}")
        return is_hedge

    def get_position_mode(self) -> bool:
        """
        Get current position mode (read from config).

        Returns:
            True=hedge mode, False=one-way mode
        """
        # Read from config (validated against API on init)
        position_mode = self.binance_config.get("position_mode", "one_way").lower()
        is_hedge = position_mode == "hedge"
        return is_hedge

    def create_market_order(
        self,
        side: str,
        quantity: float,
        position_side: str = "LONG",
        client_order_id: Optional[str] = None,
        max_retries: int = 3,
    ) -> Optional[Dict]:
        """
        Create market order.

        Args:
            side: BUY/SELL
            quantity: Quantity
            position_side: LONG/SHORT (hedge mode)
            client_order_id: Client order ID (idempotency)
            max_retries: Max retry count

        Returns:
            Order dict, None on failure
        """
        is_hedge_mode = self.get_position_mode()

        for attempt in range(1, max_retries + 1):
            try:
                self.logger.info(
                    f"Creating market order - {side} {quantity} {self.trading_symbol} "
                    f"({position_side}) [attempt {attempt}/{max_retries}]"
                )

                params = {
                    "symbol": self.trading_symbol,
                    "side": side.upper(),
                    "type": "MARKET",
                    "quantity": quantity,
                }

                # positionSide only needed in hedge mode
                if is_hedge_mode:
                    params["positionSide"] = position_side
                else:
                    # One-way mode: side determines direction, no positionSide needed
                    # BUY = open long or close short; SELL = close long or open short
                    self.logger.info("One-way mode, ignoring positionSide parameter")

                # Client order ID (idempotency)
                if client_order_id:
                    params["newClientOrderId"] = client_order_id

                order = self.client.futures_create_order(**params)
                self._increment_api_call()

                # Invalidate cache after order so next fetch gets fresh data
                self.invalidate_cache()

                self.logger.info(
                    f"Market order placed - orderId: {order.get('orderId')}, "
                    f"avgPrice: {order.get('avgPrice')}, "
                    f"status: {order.get('status')}"
                )

                # Poll for fill confirmation (max 2s)
                # Binance API is async; status may be NEW, need to wait for FILLED
                order_id = order.get("orderId")
                for poll_attempt in range(10):  # 10 * 0.2s = 2s max
                    if order.get("status") == "FILLED":
                        self.logger.info(
                            f"Market order filled - poll {poll_attempt + 1}/10, "
                            f"executedQty: {order.get('executedQty')}, "
                            f"avgPrice: {order.get('avgPrice')}"
                        )
                        break

                    time.sleep(0.2)
                    try:
                        refreshed = self.client.futures_get_order(
                            symbol=self.trading_symbol, orderId=order_id
                        )
                        self._increment_api_call()
                        if refreshed:
                            order = refreshed
                            self.logger.debug(
                                f"Polling order status - {poll_attempt + 1}/10, "
                                f"status: {order.get('status')}, "
                                f"executedQty: {order.get('executedQty')}"
                            )
                    except Exception as poll_err:
                        self.logger.warning(f"Poll query failed: {poll_err}")
                        continue

                final_status = order.get("status")
                final_filled = order.get("executedQty", 0)
                if final_status != "FILLED":
                    self.logger.warning(
                        f"Market order polling ended without full fill - "
                        f"status: {final_status}, executedQty: {final_filled}"
                    )

                # Convert to unified format (ccxt-compatible)
                return self._convert_order_format(order)

            except BinanceOrderException as e:
                self.logger.error(f"Order parameter error: {e}")
                return {
                    "status": "error",
                    "error_code": str(getattr(e, "code", "ORDER_ERROR")),
                    "error_message": str(e),
                }

            except BinanceAPIException as e:
                # -2010: insufficient funds
                if e.code == -2010:
                    self.logger.error(f"Insufficient funds: {e}")
                    return {
                        "status": "error",
                        "error_code": str(e.code),
                        "error_message": str(e),
                    }
                # -2021: duplicate order (idempotency already in effect)
                elif e.code == -2021:
                    self.logger.warning("Duplicate order detected, fetching existing order")
                    if client_order_id:
                        existing_order = self.get_order_by_client_id(client_order_id)
                        return (
                            existing_order
                            if existing_order
                            else {
                                "status": "error",
                                "error_code": str(e.code),
                                "error_message": str(e),
                            }
                        )
                    return {
                        "status": "error",
                        "error_code": str(e.code),
                        "error_message": str(e),
                    }
                else:
                    self.logger.error(f"Binance API error: {e}")
                    if attempt < max_retries:
                        time.sleep(2**attempt)
                        continue
                    return {
                        "status": "error",
                        "error_code": str(e.code),
                        "error_message": str(e),
                    }

            except BinanceRequestException as e:
                self.logger.warning(f"Network error (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(2**attempt)
                    continue
                return {
                    "status": "error",
                    "error_code": "NETWORK_ERROR",
                    "error_message": str(e),
                }

            except Exception as e:
                self.logger.error(f"Unknown error: {e}")
                return {
                    "status": "error",
                    "error_code": "UNKNOWN_ERROR",
                    "error_message": str(e),
                }

        self.logger.error(f"Market order failed after {max_retries} retries")
        return {
            "status": "error",
            "error_code": "MAX_RETRIES_EXCEEDED",
            "error_message": f"Market order failed after {max_retries} retries",
        }

    def _adjust_price_precision(self, price: float) -> float:
        """
        Adjust price precision per Binance trading rules.

        BTC: 1 decimal place; ETH/SOL/BNB: 2 decimal places.

        Args:
            price: Raw price

        Returns:
            Rounded price
        """
        precision_map = {
            "BTC": 1,
            "ETH": 2,
            "SOL": 2,
            "BNB": 2,
        }

        precision = precision_map.get(self.symbol, 2)  # default 2
        return round(price, precision)

    def create_limit_order(
        self,
        side: str,
        quantity: float,
        price: float,
        position_side: str = "LONG",
        client_order_id: Optional[str] = None,
        time_in_force: str = "GTC",
    ) -> Optional[Dict]:
        """
        Create limit order.

        Args:
            side: BUY/SELL
            quantity: Quantity
            price: Limit price
            position_side: LONG/SHORT
            client_order_id: Client order ID
            time_in_force: GTC/IOC/FOK

        Returns:
            Order dict, None on failure
        """
        try:
            is_hedge_mode = self.get_position_mode()
            price = self._adjust_price_precision(price)

            self.logger.info(
                f"Creating limit order - {side} {quantity} {self.trading_symbol} "
                f"@ ${price} ({position_side})"
            )

            params = {
                "symbol": self.trading_symbol,
                "side": side.upper(),
                "type": "LIMIT",
                "quantity": quantity,
                "price": price,
                "timeInForce": time_in_force,
            }

            # positionSide only needed in hedge mode
            if is_hedge_mode:
                params["positionSide"] = position_side
            else:
                self.logger.info("One-way mode, ignoring positionSide parameter")

            if client_order_id:
                params["newClientOrderId"] = client_order_id

            order = self.client.futures_create_order(**params)
            self._increment_api_call()
            self.invalidate_cache()

            self.logger.info(f"Limit order placed - orderId: {order.get('orderId')}")
            return self._convert_order_format(order)

        except BinanceOrderException as e:
            self.logger.error(f"Order parameter error: {e}")
            return {
                "status": "error",
                "error_code": str(getattr(e, "code", "ORDER_ERROR")),
                "error_message": str(e),
            }

        except BinanceAPIException as e:
            self.logger.error(f"Binance API error: {e}")
            return {
                "status": "error",
                "error_code": str(e.code),
                "error_message": str(e),
            }

        except Exception as e:
            self.logger.error(f"Limit order failed: {e}")
            return {
                "status": "error",
                "error_code": "UNKNOWN_ERROR",
                "error_message": str(e),
            }

    def _sign_request(self, params: Dict) -> str:
        """
        Sign Algo Order API request.

        Args:
            params: Request parameters dict

        Returns:
            Signed query string
        """
        query = "&".join([f"{k}={v}" for k, v in params.items()])
        signature = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return f"{query}&signature={signature}"

    def create_stop_loss_order(
        self,
        side: str,
        quantity: float,
        stop_price: float,
        position_side: str = "LONG",
        client_order_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Create stop-loss order (STOP_MARKET).

        Uses Algo Order API (migrated 2025-12-09).
        Endpoint: POST /fapi/v1/algoOrder

        Args:
            side: BUY/SELL (close long = SELL, close short = BUY)
            quantity: Quantity
            stop_price: Stop-loss trigger price
            position_side: LONG/SHORT
            client_order_id: Client order ID

        Returns:
            Order dict, None on failure
        """
        try:
            stop_price = self._adjust_price_precision(stop_price)
            is_hedge_mode = self.get_position_mode()

            self.logger.info(
                f"Creating stop-loss order (Algo) - {side} {quantity} {self.trading_symbol} "
                f"trigger ${stop_price} ({position_side}) "
                f"[{'hedge' if is_hedge_mode else 'one-way'}]"
            )

            params = {
                "algoType": "CONDITIONAL",
                "symbol": self.trading_symbol,
                "side": side.upper(),
                "type": "STOP_MARKET",
                "quantity": str(quantity),
                "triggerPrice": str(stop_price),  # Algo API uses triggerPrice
                "workingType": "CONTRACT_PRICE",
                "timestamp": int(time.time() * 1000),
            }

            # Add positionSide in hedge mode
            if is_hedge_mode:
                params["positionSide"] = position_side

            if client_order_id:
                params["clientAlgoId"] = client_order_id

            headers = {"X-MBX-APIKEY": self._api_key}
            url = (
                f"{self._algo_base_url}/fapi/v1/algoOrder?{self._sign_request(params)}"
            )

            resp = requests.post(url, headers=headers, timeout=10)
            self._increment_api_call()

            if resp.status_code == 200:
                result = resp.json()
                algo_id = result.get("algoId")
                self.logger.info(f"Stop-loss order (Algo) placed - algoId: {algo_id}")
                self.invalidate_cache()
                return self._convert_algo_order_format(result)
            else:
                error = resp.json()
                self.logger.error(f"Stop-loss order (Algo) failed: {error}")
                return {
                    "status": "error",
                    "error_code": str(error.get("code", "UNKNOWN")),
                    "error_message": error.get("msg", resp.text),
                }

        except requests.exceptions.Timeout:
            self.logger.error("Stop-loss order timed out")
            return {
                "status": "error",
                "error_code": "TIMEOUT",
                "error_message": "Request timed out",
            }

        except Exception as e:
            self.logger.error(f"Stop-loss order failed: {e}")
            return {
                "status": "error",
                "error_code": "UNKNOWN_ERROR",
                "error_message": str(e),
            }

    def _convert_algo_order_format(self, order: Dict) -> Dict:
        """
        Convert Algo Order format to unified format.

        Args:
            order: Algo Order API response

        Returns:
            Unified order dict
        """
        return {
            "id": str(order.get("algoId")),
            "clientOrderId": order.get("clientAlgoId"),
            "symbol": order.get("symbol"),
            "side": order.get("side", "").lower(),
            "type": order.get("orderType", "").lower(),
            "filled": float(order.get("actualQty", 0)),
            "average": float(order.get("avgPrice", 0)) if order.get("avgPrice") else 0,
            "status": self._convert_algo_status(order.get("algoStatus")),
            "triggerPrice": float(order.get("triggerPrice", 0)),
            "fee": {"cost": 0, "currency": "USDT"},
            "info": order,
        }

    def _convert_algo_status(self, algo_status: str) -> str:
        """
        Convert Algo Order status to unified format.

        Args:
            algo_status: Algo Order status

        Returns:
            Unified status string
        """
        status_map = {
            "NEW": "open",
            "TRIGGERED": "closed",
            "CANCELLED": "canceled",
            "FAILED": "rejected",
            "EXPIRED": "expired",
        }
        return status_map.get(algo_status, "open")

    def create_take_profit_order(
        self,
        side: str,
        quantity: float,
        stop_price: float,
        position_side: str = "LONG",
        client_order_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Create take-profit order (TAKE_PROFIT_MARKET).

        Args:
            side: BUY/SELL (close long = SELL, close short = BUY)
            quantity: Quantity
            stop_price: Take-profit trigger price
            position_side: LONG/SHORT
            client_order_id: Client order ID

        Returns:
            Order dict, None on failure
        """
        try:
            stop_price = self._adjust_price_precision(stop_price)
            is_hedge_mode = self.get_position_mode()

            self.logger.info(
                f"Creating take-profit order - {side} {quantity} {self.trading_symbol} "
                f"trigger ${stop_price} ({position_side}) "
                f"[{'hedge' if is_hedge_mode else 'one-way'}]"
            )

            params = {
                "symbol": self.trading_symbol,
                "side": side.upper(),
                "type": "TAKE_PROFIT_MARKET",
                "quantity": quantity,
                "stopPrice": stop_price,
                "workingType": "CONTRACT_PRICE",  # use contract price as trigger
                # closePosition removed: use quantity for partial take-profit
                # closePosition: true ignores quantity and closes entire position
            }

            # In hedge mode: add positionSide; STOP_MARKET doesn't need reduceOnly
            if is_hedge_mode:
                params["positionSide"] = position_side
            # One-way mode: no positionSide needed

            if client_order_id:
                params["newClientOrderId"] = client_order_id

            order = self.client.futures_create_order(**params)
            self._increment_api_call()
            self.invalidate_cache()

            self.logger.info(f"Take-profit order placed - orderId: {order.get('orderId')}")
            return self._convert_order_format(order)

        except BinanceOrderException as e:
            self.logger.error(f"Order parameter error: {e}")
            return {
                "status": "error",
                "error_code": str(getattr(e, "code", "ORDER_ERROR")),
                "error_message": str(e),
            }

        except BinanceAPIException as e:
            self.logger.error(f"Binance API error: {e}")
            return {
                "status": "error",
                "error_code": str(e.code),
                "error_message": str(e),
            }

        except Exception as e:
            self.logger.error(f"Take-profit order failed: {e}")
            return {
                "status": "error",
                "error_code": "UNKNOWN_ERROR",
                "error_message": str(e),
            }

    def get_order(self, order_id: str) -> Optional[Dict]:
        """
        Query order status.

        Args:
            order_id: Exchange order ID

        Returns:
            Order dict, None on failure
        """
        try:
            order = self.client.futures_get_order(
                symbol=self.trading_symbol, orderId=order_id
            )
            self._increment_api_call()
            return self._convert_order_format(order)
        except BinanceAPIException as e:
            if e.code == -2013:  # order does not exist
                self.logger.warning(f"Order not found: {order_id}")
                return None
            self.logger.error(f"Failed to query order {order_id}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to query order {order_id}: {e}")
            return None

    def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict]:
        """
        Query order by client order ID.

        Args:
            client_order_id: Client order ID

        Returns:
            Order dict, None on failure
        """
        try:
            order = self.client.futures_get_order(
                symbol=self.trading_symbol, origClientOrderId=client_order_id
            )
            self._increment_api_call()
            return self._convert_order_format(order)
        except Exception as e:
            self.logger.error(f"Failed to query order by client ID {client_order_id}: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel order.

        Args:
            order_id: Exchange order ID

        Returns:
            Whether cancellation succeeded
        """
        try:
            self.client.futures_cancel_order(
                symbol=self.trading_symbol, orderId=order_id
            )
            self._increment_api_call()
            self.logger.info(f"Order cancelled: {order_id}")
            return True
        except BinanceAPIException as e:
            if e.code == -2011:  # order does not exist or already cancelled
                self.logger.warning(f"Order not found or already cancelled: {order_id}")
                return False
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> int:
        """
        Cancel all open orders for the current trading pair.

        Returns:
            Number of orders cancelled

        Note:
            Call before setting leverage/margin mode to clear any leftover orders.
        """
        try:
            result = self.client.futures_cancel_all_open_orders(
                symbol=self.trading_symbol
            )
            self._increment_api_call()

            # code=200 means success
            if result.get("code") == 200:
                self.logger.info(f"All open orders cancelled: {self.trading_symbol}")
                return 1  # API doesn't return count; return 1 to indicate success
            else:
                self.logger.warning(f"Cancel all orders response: {result}")
                return 0
        except BinanceAPIException as e:
            # No open orders may return an error - not a real failure
            self.logger.info(
                f"Cancel all orders: {e.message if hasattr(e, 'message') else e}"
            )
            return 0
        except Exception as e:
            self.logger.error(f"Failed to cancel all orders: {e}")
            return 0

    def get_open_orders(self) -> List[Dict]:
        """
        Get all open orders.

        Returns:
            List of open orders, each containing orderId, type, side, stopPrice, etc.
        """
        try:
            orders = self.client.futures_get_open_orders(symbol=self.trading_symbol)
            self._increment_api_call()
            return orders
        except Exception as e:
            self.logger.error(f"Failed to get open orders: {e}")
            return []

    def cancel_stop_loss_orders(self, position_side: str) -> int:
        """
        Cancel all stop-loss orders for a given position side.

        Used after TP1 triggers to reset the stop-loss (cancel the full-size SL first).

        Args:
            position_side: Position side 'LONG' or 'SHORT'

        Returns:
            Number of orders cancelled
        """
        try:
            open_orders = self.get_open_orders()
            cancelled_count = 0

            # Stop-loss order types: STOP_MARKET (market stop) or STOP (limit stop)
            sl_types = ["STOP_MARKET", "STOP"]
            # LONG position stop-loss is SELL; SHORT position stop-loss is BUY
            sl_side = "SELL" if position_side == "LONG" else "BUY"

            for order in open_orders:
                order_type = order.get("type", "")
                order_side = order.get("side", "")
                order_position_side = order.get("positionSide", "")

                # Match stop orders: type + side + position side
                is_stop_order = order_type in sl_types
                is_correct_side = order_side == sl_side
                is_correct_position = order_position_side == position_side

                if is_stop_order and is_correct_side and is_correct_position:
                    order_id = order.get("orderId")
                    if self.cancel_order(order_id):
                        cancelled_count += 1
                        self.logger.info(
                            f"Stop-loss order cancelled - OrderID: {order_id}, "
                            f"Type: {order_type}, Side: {sl_side}"
                        )

            self.logger.info(f"Stop-loss cancellation done - total: {cancelled_count}")
            return cancelled_count

        except Exception as e:
            self.logger.error(f"Failed to cancel stop-loss orders: {e}")
            return 0

    # ========================================
    # Algo Order API (v4.3.11)
    # ========================================

    def get_open_algo_orders(self) -> List[Dict]:
        """
        Get all open Algo orders (conditional orders).

        Returns:
            List of Algo orders
        """
        try:
            params = {
                "symbol": self.trading_symbol,
                "timestamp": int(time.time() * 1000),
            }

            headers = {"X-MBX-APIKEY": self._api_key}
            url = f"{self._algo_base_url}/fapi/v1/openAlgoOrders?{self._sign_request(params)}"

            resp = requests.get(url, headers=headers, timeout=10)
            self._increment_api_call()

            if resp.status_code == 200:
                orders = resp.json()
                return [self._convert_algo_order_format(o) for o in orders]
            else:
                self.logger.error(f"Failed to get open Algo orders: {resp.text}")
                return []

        except Exception as e:
            self.logger.error(f"Get open Algo orders error: {e}")
            return []

    def cancel_algo_order(self, algo_id: str) -> bool:
        """
        Cancel Algo order.

        Endpoint: DELETE /fapi/v1/algoOrder

        Args:
            algo_id: Algo order ID

        Returns:
            Whether succeeded
        """
        try:
            params = {"algoId": algo_id, "timestamp": int(time.time() * 1000)}

            headers = {"X-MBX-APIKEY": self._api_key}
            url = (
                f"{self._algo_base_url}/fapi/v1/algoOrder?{self._sign_request(params)}"
            )

            resp = requests.delete(url, headers=headers, timeout=10)
            self._increment_api_call()

            if resp.status_code == 200:
                resp.json()
                self.logger.info(f"Algo order cancelled - algoId: {algo_id}")
                self.invalidate_cache()
                return True
            else:
                error = resp.json()
                self.logger.error(f"Algo order cancellation failed: {error}")
                return False

        except Exception as e:
            self.logger.error(f"Cancel Algo order error: {e}")
            return False

    def cancel_all_algo_orders(self) -> int:
        """
        Cancel all open Algo orders.

        Endpoint: DELETE /fapi/v1/algoOpenOrders

        Returns:
            Number of orders cancelled
        """
        try:
            params = {
                "symbol": self.trading_symbol,
                "timestamp": int(time.time() * 1000),
            }

            headers = {"X-MBX-APIKEY": self._api_key}
            url = f"{self._algo_base_url}/fapi/v1/algoOpenOrders?{self._sign_request(params)}"

            resp = requests.delete(url, headers=headers, timeout=10)
            self._increment_api_call()

            if resp.status_code == 200:
                result = resp.json()
                count = (
                    result.get("count", 0)
                    if isinstance(result, dict)
                    else len(result)
                    if isinstance(result, list)
                    else 0
                )
                self.logger.info(f"All Algo orders cancelled - count: {count}")
                self.invalidate_cache()
                return count
            else:
                self.logger.error(f"Failed to cancel all Algo orders: {resp.text}")
                return 0

        except Exception as e:
            self.logger.error(f"Cancel all Algo orders error: {e}")
            return 0

    def get_positions(self, force_refresh: bool = False) -> List[Dict]:
        """
        Get current positions (WebSocket first, REST fallback).

        Args:
            force_refresh: Force cache refresh

        Returns:
            List of positions
        """
        # WebSocket first
        if self._ws_manager and self._ws_manager.is_connected and not force_refresh:
            ws_positions = self._ws_manager.get_positions(self.trading_symbol)
            if ws_positions is not None:
                return ws_positions

        current_time = time.time()

        # Check cache validity
        if not force_refresh and self._positions_cache is not None:
            if current_time - self._positions_cache_time < self._cache_ttl:
                return self._positions_cache

        try:
            positions = self.client.futures_position_information(
                symbol=self.trading_symbol
            )
            self._increment_api_call()

            # Filter zero-quantity positions and convert format
            active_positions = []
            for pos in positions:
                qty = float(pos.get("positionAmt", 0))
                if qty != 0:
                    active_positions.append(
                        {
                            "symbol": pos["symbol"],
                            "side": "long" if qty > 0 else "short",
                            "contracts": abs(qty),
                            "entryPrice": float(pos.get("entryPrice", 0)),
                            "unrealizedPnl": float(pos.get("unRealizedProfit", 0)),
                            "leverage": int(pos.get("leverage", 1)),
                            "positionSide": pos.get("positionSide"),
                        }
                    )

            # Update cache
            self._positions_cache = active_positions
            self._positions_cache_time = current_time

            return active_positions

        except Exception as e:
            self.logger.error(f"Failed to get positions: {e}")
            return self._positions_cache if self._positions_cache else []

    def get_balance(self, force_refresh: bool = False) -> Optional[Dict]:
        """
        Get account balance (WebSocket first, REST fallback).

        Args:
            force_refresh: Force cache refresh

        Returns:
            Balance info: {'USDT': {'free': 1000.0, 'used': 100.0, 'total': 1100.0}}
        """
        # WebSocket first
        if self._ws_manager and self._ws_manager.is_connected and not force_refresh:
            ws_balance = self._ws_manager.get_balance("USDT")
            if ws_balance:
                return {"USDT": ws_balance}

        current_time = time.time()

        # Check cache validity
        if not force_refresh and self._balance_cache is not None:
            if current_time - self._balance_cache_time < self._cache_ttl:
                return self._balance_cache

        try:
            account = self.client.futures_account()
            self._increment_api_call()

            # Extract USDT balance
            balances = {}
            for asset in account.get("assets", []):
                if asset["asset"] == "USDT":
                    balances["USDT"] = {
                        "free": float(asset["availableBalance"]),
                        "used": float(asset["initialMargin"]),
                        "total": float(asset["walletBalance"]),
                    }
                    break

            # Update cache
            self._balance_cache = balances
            self._balance_cache_time = current_time

            return balances

        except Exception as e:
            self.logger.error(f"Failed to get balance: {e}")
            return self._balance_cache  # return cached data on failure

    def get_current_price(self) -> Optional[float]:
        """
        Get current market price.

        Returns:
            Current price, None on failure
        """
        try:
            ticker = self.client.futures_symbol_ticker(symbol=self.trading_symbol)
            self._increment_api_call()
            return float(ticker.get("price", 0))
        except Exception as e:
            self.logger.error(f"Failed to get current price: {e}")
            return None

    def get_funding_rate(self) -> Optional[float]:
        """
        Get current funding rate.

        Binance API: GET /fapi/v1/fundingRate?symbol=BTCUSDT&limit=1

        Returns:
            Current funding rate as decimal (e.g. 0.0001 = 0.01%/8h).
            None on failure (fail-open: API failure does not block entry).
        """
        try:
            result = self.client.futures_funding_rate(
                symbol=self.trading_symbol, limit=1
            )
            self._increment_api_call()

            if result and len(result) > 0:
                funding_rate = float(result[0].get("fundingRate", 0))
                self.logger.debug(
                    f"[{self.symbol}] funding rate: {funding_rate:.4%} "
                    f"(annualized: {funding_rate * 3 * 365:.1%})"
                )
                return funding_rate

            return None

        except Exception as e:
            # fail-open: return None on API failure, do not block entry
            self.logger.warning(f"Failed to get funding rate (will not block entry): {e}")
            return None

    def _convert_order_format(self, order: Dict) -> Dict:
        """
        Convert Binance order format to unified format (ccxt-compatible).

        Args:
            order: Raw Binance order

        Returns:
            Unified order dict
        """
        return {
            "id": str(order.get("orderId")),
            "clientOrderId": order.get("clientOrderId"),
            "symbol": order.get("symbol"),
            "side": order.get("side", "").lower(),
            "type": order.get("type", "").lower(),
            "filled": float(order.get("executedQty", 0)),
            "average": float(order.get("avgPrice", 0)) if order.get("avgPrice") else 0,
            "status": self._convert_status(order.get("status")),
            "fee": {
                "cost": 0,  # futures fee requires separate query
                "currency": "USDT",
            },
            "info": order,  # preserve raw data
        }

    def _convert_status(self, binance_status: str) -> str:
        """
        Convert Binance order status to ccxt format.

        Args:
            binance_status: Binance status string

        Returns:
            ccxt-format status string
        """
        status_map = {
            "NEW": "open",
            "PARTIALLY_FILLED": "open",
            "FILLED": "closed",
            "CANCELED": "canceled",
            "REJECTED": "rejected",
            "EXPIRED": "expired",
        }
        return status_map.get(binance_status, "open")

    def _increment_api_call(self):
        """Increment API call counter (for rate-limit monitoring)."""
        current_time = time.time()

        # Reset counter every minute
        if current_time - self.last_reset_time >= 60:
            self.api_call_count = 0
            self.last_reset_time = current_time

        self.api_call_count += 1

        # Alert when approaching rate limit
        if self.api_call_count >= 50:
            self.logger.warning(
                f"High API call rate: {self.api_call_count}/min, approaching limit"
            )

    def invalidate_cache(self):
        """Invalidate cache (call after placing orders to ensure fresh data on next fetch)."""
        self._balance_cache = None
        self._balance_cache_time = 0
        self._positions_cache = None
        self._positions_cache_time = 0

    async def start_websocket(self) -> bool:
        """
        Start WebSocket connection.

        Returns:
            Whether started successfully
        """
        if not self._use_websocket:
            self.logger.debug("WebSocket not enabled, skipping")
            return False

        if self._ws_manager and self._ws_manager.is_connected:
            self.logger.debug("WebSocket already connected, skipping")
            return True

        try:
            from src.trading.binance_ws_manager import BinanceWebSocketManager

            self._ws_manager = BinanceWebSocketManager(
                api_key=self._api_key,
                api_secret=self._api_secret,
                testnet=self.testnet,
            )
            success = await self._ws_manager.start()
            if success:
                self.logger.info("WebSocket connection started")
            else:
                self.logger.warning("WebSocket connection failed, falling back to REST API")
            return success
        except Exception as e:
            self.logger.error(f"WebSocket start error: {e}")
            return False

    async def stop_websocket(self):
        """Stop WebSocket connection."""
        if self._ws_manager:
            await self._ws_manager.stop()
            self._ws_manager = None
            self.logger.info("WebSocket connection stopped")

    def close(self):
        """Close connection."""
        # Note: if in async context, call await stop_websocket() first
        if self._ws_manager:
            self.logger.warning(
                "WebSocket not properly closed; call stop_websocket() in async context"
            )
        self.logger.info("BinanceTradingClient closed")

    # ccxt-compatible property
    @property
    def trading_pair(self) -> str:
        """ccxt-compatible trading_pair attribute."""
        return f"{self.symbol}/USDT"
