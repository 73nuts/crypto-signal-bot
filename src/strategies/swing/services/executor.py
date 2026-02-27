"""
Swing strategy executor (daily trend following)

Responsibilities:
  1. Receive trading signals from the Swing scheduler
  2. Call the underlying trading modules to execute
  3. Manage position state (positions table is the single source of truth)

Architecture:
  SwingScheduler -> SwingExecutor -> BinanceTradingClient (orders)
                                  -> PositionManager (positions)
                                  -> TrailingStopManager (trailing stop)
                                  -> StopLossService (stop management)

Design principles:
  - Single source of truth: positions table
  - Lazy initialization: create components on demand
  - Precision safety: floor quantity to prevent insufficient balance
  - Risk checks first: validate before placing orders
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from src.strategies.swing.services.order_service import OrderService
    from src.strategies.swing.services.stop_loss_service import StopLossService
    from src.trading.position_manager import PositionManager
    from src.trading.trailing_stop_manager import TrailingStopManager
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from src.core.config import settings
from src.core.message_bus import get_message_bus
from src.core.protocols import (
    MessageBusProtocol,
    TradingClientFactoryProtocol,
    TradingClientProtocol,
)
from src.core.structured_logger import get_logger
from src.core.tracing import TraceContext
from src.notifications.wechat_sender import WeChatSender
from src.strategies.swing.config import (
    RISK_CONFIG,
    get_supported_symbols,
    get_symbol_config,
)


@dataclass
class SymbolExecutionConfig:
    """Symbol execution config (converted from centralized config)."""

    stop_type: str
    trailing_period: Optional[int] = None
    trailing_mult: Optional[float] = None
    quantity_precision: int = 2
    price_precision: int = 2

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "SymbolExecutionConfig":
        """Create from a config dict."""
        return cls(
            stop_type=config["stop_type"],
            trailing_period=config.get("trailing_period"),
            trailing_mult=config.get("trailing_mult"),
            quantity_precision=config.get("quantity_precision", 2),
            price_precision=config.get("price_precision", 2),
        )


class DefaultTradingClientFactory:
    """
    Default trading client factory

    Reads API keys from environment variables and creates BinanceTradingClient instances.
    Used in production; inject a mock factory for testing.
    """

    def __init__(self, use_websocket: bool = True):
        """
        Initialize the factory.

        Args:
            use_websocket: Whether to enable WebSocket real-time data (default True).
        """
        self._use_websocket = use_websocket

    def create(self, symbol: str, testnet: bool = True) -> TradingClientProtocol:
        """Create a trading client."""
        from src.trading.binance_trading_client import BinanceTradingClient

        api_key, api_secret = settings.get_binance_keys(testnet=testnet)
        if not api_key or not api_secret:
            env_name = (
                "BINANCE_TESTNET_API_KEY/SECRET"
                if testnet
                else "BINANCE_API_KEY/SECRET"
            )
            raise ValueError(f"Environment variable {env_name} not configured")

        return BinanceTradingClient(
            api_key=api_key,
            api_secret=api_secret,
            symbol=symbol,
            testnet=testnet,
            use_websocket=self._use_websocket,
        )


class SwingExecutor:
    """Swing strategy executor (daily trend following)"""

    def __init__(
        self,
        testnet: bool = True,
        dry_run: bool = False,
        risk_percent: float = None,
        max_position_value: float = None,
        use_websocket: bool = True,
        # DI parameters (optional, for test injection)
        trading_client_factory: TradingClientFactoryProtocol = None,
        position_manager: "PositionManager" = None,
        trailing_manager: "TrailingStopManager" = None,
        message_bus: MessageBusProtocol = None,
    ):
        """
        Initialize the executor.

        Args:
            testnet: Whether to use testnet.
            dry_run: Dry-run mode (log only, no orders; use for pre-launch validation).
            risk_percent: Per-trade risk percentage (default 2%).
            max_position_value: Maximum position value per trade (default $5000).
            use_websocket: Whether to enable WebSocket real-time data.
            trading_client_factory: Trading client factory (DI, for testing).
            position_manager: Position manager (DI, for testing).
            trailing_manager: Trailing stop manager (DI, for testing).
            message_bus: Message bus (DI, for testing).

        Note:
            Notifications are handled by handlers.py via the event bus; executor does not push directly.
        """
        self.testnet = testnet
        self.dry_run = dry_run
        self._use_websocket = use_websocket
        self.risk_percent = risk_percent or RISK_CONFIG["default_risk_percent"]
        self.max_position_value = (
            max_position_value or RISK_CONFIG["max_position_value"]
        )
        self.logger = get_logger(__name__)

        # DB config
        self._db_config = self._load_db_config()

        # DI: trading client factory
        self._trading_client_factory = (
            trading_client_factory
            or DefaultTradingClientFactory(use_websocket=use_websocket)
        )

        # Components (lazy initialized or injected)
        self._clients: Dict[str, TradingClientProtocol] = {}
        self._position_manager: Optional["PositionManager"] = position_manager
        self._trailing_manager: Optional["TrailingStopManager"] = trailing_manager

        # WeChat alerts (for exception notifications)
        self._wechat_sender = WeChatSender()

        # DI: message bus
        self._bus = message_bus or get_message_bus()

        # Stop-loss service (lazy initialized)
        self._stop_loss_service: Optional["StopLossService"] = None

        # Order service (lazy initialized)
        self._order_service: Optional["OrderService"] = None

        mode = "DRY RUN" if dry_run else ("TESTNET" if testnet else "MAINNET")
        self.logger.info(
            f"SwingExecutor initialized - mode: {mode}, risk: {self.risk_percent}%"
        )

    @staticmethod
    def _load_db_config() -> Dict[str, Any]:
        """Load database configuration from unified config manager."""
        return settings.get_mysql_config()

    def _get_symbol_config(self, symbol: str) -> SymbolExecutionConfig:
        """Get symbol config from centralized config."""
        config = get_symbol_config(symbol)  # raises ValueError if unsupported
        return SymbolExecutionConfig.from_dict(config)

    def _ensure_initialized(self, symbol: str) -> None:
        """
        Ensure trading components are initialized.

        Args:
            symbol: Asset symbol (BTC/ETH/BNB/SOL).

        Raises:
            ValueError: API keys not configured.
            RuntimeError: Component initialization failed.
        """
        if symbol in self._clients and self._position_manager:
            return

        try:
            self._init_components(symbol)
        except Exception as e:
            self.logger.error(f"[{symbol}] Component initialization failed: {e}", exc_info=True)
            raise RuntimeError(f"Component initialization failed: {e}") from e

    def _init_components(self, symbol: str) -> None:
        """Initialize trading components."""
        from src.trading.position_manager import PositionManager
        from src.trading.trailing_stop_manager import TrailingStopManager

        # 1. TradingClient (one per symbol, created via factory)
        if symbol not in self._clients:
            self._clients[symbol] = self._trading_client_factory.create(
                symbol=symbol, testnet=self.testnet
            )
            self.logger.debug(f"[{symbol}] TradingClient initialized")

        client = self._clients[symbol]

        # 2. PositionManager (shared, or injected)
        if not self._position_manager:
            self._position_manager = PositionManager(
                trading_client=client, **self._db_config
            )
            self.logger.debug("PositionManager initialized")

        # 3. TrailingStopManager (shared, or injected)
        if not self._trailing_manager:
            self._trailing_manager = TrailingStopManager(**self._db_config)
            self.logger.debug("TrailingStopManager initialized")

        # 4. StopLossService
        if not self._stop_loss_service:
            from src.strategies.swing.services.stop_loss_service import StopLossService

            self._stop_loss_service = StopLossService(
                position_manager=self._position_manager,
                trailing_manager=self._trailing_manager,
                message_bus=self._bus,
                alert_sender=self,  # Executor implements AlertSenderProtocol
                dry_run=self.dry_run,
            )
            self.logger.debug("StopLossService initialized")

        # 5. OrderService
        if not self._order_service:
            from src.strategies.swing.services.order_service import OrderService

            self._order_service = OrderService(
                position_manager=self._position_manager,
                stop_loss_service=self._stop_loss_service,
                message_bus=self._bus,
                alert_sender=self,
                risk_percent=self.risk_percent,
                max_position_value=self.max_position_value,
                testnet=self.testnet,
                dry_run=self.dry_run,
            )
            self.logger.debug("OrderService initialized")

        self.logger.info(f"[{symbol}] All trading components initialized")

    # ========================================
    # Initialization management
    # ========================================

    def is_initialized(self, symbol: str = None) -> bool:
        """
        Check initialization state.

        Args:
            symbol: Specific symbol; None checks global (position_manager).

        Returns:
            True if initialized.
        """
        if symbol:
            return symbol in self._clients and self._position_manager is not None
        return self._position_manager is not None

    def initialize_all(self) -> bool:
        """
        Initialize all supported symbols (called on startup).

        Returns:
            True if all succeeded, False if any failed.
        """
        all_success = True
        for symbol in get_supported_symbols():
            try:
                self._ensure_initialized(symbol)
                self.logger.debug(f"[{symbol}] Initialization succeeded")
            except Exception as e:
                self.logger.warning(f"[{symbol}] Initialization failed: {e}")
                all_success = False
        return all_success

    def _ensure_position_manager_ready(self) -> bool:
        """
        Ensure PositionManager is available for internal query methods.

        Returns:
            True if position_manager is available.
        """
        if self._position_manager:
            return True

        # Try initializing with the first supported symbol
        symbols = get_supported_symbols()
        if not symbols:
            return False

        try:
            self._ensure_initialized(symbols[0])
            return self._position_manager is not None
        except Exception as e:
            self.logger.debug(f"PositionManager initialization failed: {e}")
            return False

    # ========================================
    # Position queries
    # ========================================

    def has_position(self, symbol: str) -> bool:
        """
        Check whether a position exists.

        Args:
            symbol: Asset symbol.

        Returns:
            True if position exists.
        """
        return self.get_position(symbol) is not None

    def get_position(self, symbol: str) -> Optional[Dict]:
        """
        Get position details.

        Args:
            symbol: Asset symbol.

        Returns:
            Position dict, or None if no position.
        """
        self._ensure_initialized(symbol)
        positions = self._position_manager.get_open_positions(f"{symbol}USDT")
        return positions[0] if positions else None

    def get_all_positions(self) -> List[Dict]:
        """
        Get all open positions.

        Returns:
            List of position dicts.
        """
        if not self._ensure_position_manager_ready():
            return []
        return self._position_manager.get_open_positions()

    # ========================================
    # Entry execution
    # ========================================

    # Risk constants
    MIN_NOTIONAL_VALUE = 10.0      # minimum notional value (Binance requires $5, buffer to $10)
    LEVERAGE_BUFFER = 1.25         # leverage safety buffer (stop distance × 1.25 = liquidation price)
    MAX_LEVERAGE = 3               # maximum leverage (conservative strategy)
    ENTRY_SLIPPAGE_WARN = 0.005   # entry slippage warning threshold (0.5%)
    EXIT_SLIPPAGE_WARN = 0.01     # exit slippage warning threshold (1%, common on stop-loss)

    def execute_entry(
        self, symbol: str, price: float, atr: float, strategy_name: str
    ) -> Optional[Dict]:
        """
        Execute entry (production 5-step flow).

        Flow:
            1. Basic data prep and risk checks
            2. Dynamic leverage calculation (prevent liquidation before stop)
            3. Account setup (cancel orders -> isolated margin -> set leverage)
            4. Margin check
            5. Order placement and position recording

        Args:
            symbol: Asset symbol (BTC/ETH/BNB/SOL).
            price: Entry price.
            atr: ATR value (used to calculate stop and position size).
            strategy_name: Strategy name.

        Returns:
            Success: {'position_id': int, 'order_id': str, 'price': float, 'quantity': float, 'leverage': int}
            Failure: None
        """
        with TraceContext(
            operation="swing.execute_entry", symbol=symbol, strategy=strategy_name
        ):
            return self._do_execute_entry(symbol, price, atr, strategy_name)

    def _do_execute_entry(
        self, symbol: str, price: float, atr: float, strategy_name: str
    ) -> Optional[Dict]:
        """Actual entry execution, delegated to OrderService."""
        self._ensure_initialized(symbol)
        config = self._get_symbol_config(symbol)
        client = self._clients[symbol]

        from src.strategies.swing.services.order_service import SymbolConfig

        return self._order_service.execute_market_entry(
            client=client,
            symbol=symbol,
            price=price,
            atr=atr,
            strategy_name=strategy_name,
            config=SymbolConfig(
                stop_type=config.stop_type,
                trailing_period=config.trailing_period,
                trailing_mult=config.trailing_mult,
                quantity_precision=config.quantity_precision,
                price_precision=config.price_precision,
            ),
        )

    def _send_alert(self, title: str, message: str) -> None:
        """
        Send a WeChat alert.

        Args:
            title: Alert title.
            message: Alert body.
        """
        try:
            env_prefix = "[Testnet]" if self.testnet else "[Mainnet]"
            full_title = f"{env_prefix} {title}"
            self._wechat_sender.send(full_title, message)
        except Exception as e:
            self.logger.error(f"WeChat alert send failed: {e}")

    def send_alert(self, title: str, message: str) -> None:
        """AlertSenderProtocol implementation."""
        self._send_alert(title, message)

    # ========================================
    # Exit execution
    # ========================================

    def execute_exit(self, symbol: str, price: float, reason: str) -> Optional[Dict]:
        """
        Execute exit.

        Close position first, then cancel stop order (health ordering).
        Stop-order cancellation failure does not affect the result.

        Args:
            symbol: Asset symbol.
            price: Exit price (for logging; actual fill is at market).
            reason: Exit reason.

        Returns:
            Success: {'position_id': int, 'order_id': str, 'price': float, 'pnl': float}
            Failure: None
        """
        with TraceContext(operation="swing.execute_exit", symbol=symbol, reason=reason):
            return self._do_execute_exit(symbol, price, reason)

    def _do_execute_exit(
        self, symbol: str, price: float, reason: str
    ) -> Optional[Dict]:
        """Actual exit execution, delegated to OrderService."""
        self._ensure_initialized(symbol)

        position = self.get_position(symbol)
        if not position:
            self.logger.warning(f"[{symbol}] No position to close")
            return None

        client = self._clients[symbol]

        return self._order_service.execute_exit(
            client=client, symbol=symbol, position=position, price=price, reason=reason
        )

    # ========================================
    # Trailing stop
    # ========================================

    def update_trailing_stop(self, symbol: str, new_stop: float) -> Optional[Dict]:
        """
        Update trailing stop (can only be raised to prevent drawdown).

        Args:
            symbol: Asset symbol.
            new_stop: New stop-loss price.

        Returns:
            Dict: update details {'symbol', 'old_stop', 'new_stop', 'entry_price', 'strategy'}
            None: update failed or not needed.
        """
        self._ensure_initialized(symbol)

        position = self.get_position(symbol)
        if not position:
            self.logger.debug(f"[{symbol}] No position, skipping stop update")
            return None

        client = self._clients[symbol]

        return self._stop_loss_service.update_trailing_stop(
            client=client, symbol=symbol, position=position, new_stop=new_stop
        )

    def batch_update_trailing_stops(self, updates: List[Dict]) -> int:
        """
        Batch update trailing stops.

        Args:
            updates: [{'symbol': 'BTC', 'new_stop': 95000.0}, ...]

        Returns:
            Number of successful updates.
        """
        success_count = 0
        for update in updates:
            symbol = update.get("symbol")
            new_stop = update.get("new_stop")
            if symbol and new_stop:
                if self.update_trailing_stop(symbol, new_stop):
                    success_count += 1
        return success_count

    def _attach_sl_tp_after_fill(
        self,
        symbol: str,
        position_id: int,
        quantity: float,
        entry_price: float,
        stop_loss: float,
        entry_atr: float,
        strategy_name: str,
    ) -> None:
        """
        Place stop-loss and take-profit orders after a limit order fills.

        Args:
            symbol: Asset symbol.
            position_id: Position ID.
            quantity: Filled quantity.
            entry_price: Fill price.
            stop_loss: Stop-loss price.
            entry_atr: Entry ATR.
            strategy_name: Strategy name.
        """
        client = self._clients[symbol]
        config = self._get_symbol_config(symbol)

        self._stop_loss_service.attach_sl_tp_after_fill(
            client=client,
            symbol=symbol,
            position_id=position_id,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            entry_atr=entry_atr,
            strategy_name=strategy_name,
            stop_type=config.stop_type,
            max_leverage=self.MAX_LEVERAGE,
        )

    # ========================================
    # Helpers
    # ========================================

    def _calculate_initial_stop(
        self, price: float, atr: float, config: SymbolExecutionConfig
    ) -> float:
        """Calculate initial stop-loss price, delegated to StopLossService."""
        return self._stop_loss_service.calculate_initial_stop(
            price=price,
            atr=atr,
            stop_type=config.stop_type,
            trailing_mult=config.trailing_mult,
        )

    def _calculate_quantity(
        self, symbol: str, price: float, atr: float, config: SymbolExecutionConfig
    ) -> Optional[float]:
        """
        Calculate position size.

        Formula: quantity = (account balance * risk%) / stop distance
        Precision: floor to prevent insufficient balance.
        """
        client = self._clients[symbol]
        balances = client.get_balance()

        if not balances or "USDT" not in balances:
            self.logger.error(f"[{symbol}] Get balance failed")
            return None

        balance = float(balances["USDT"].get("free", 0))
        if balance <= 0:
            self.logger.error(f"[{symbol}] Balance is zero")
            return None

        risk_amount = balance * (self.risk_percent / 100)

        stop_distance = atr * RISK_CONFIG["atr_stop_mult"]
        if stop_distance <= 0:
            self.logger.error(f"[{symbol}] Invalid ATR: {atr}")
            return None

        quantity = risk_amount / stop_distance
        quantity = self._floor_to_precision(quantity, config.quantity_precision)

        self.logger.debug(
            f"[{symbol}] Position calculation - balance: ${balance:.2f}, "
            f"risk: {self.risk_percent}%, ATR: ${atr:.2f}, "
            f"quantity: {quantity}"
        )

        return quantity if quantity > 0 else None

    @staticmethod
    def _floor_to_precision(value: float, precision: int) -> float:
        """Floor a value to the specified decimal precision."""
        if precision <= 0:
            return float(int(value))
        decimal_value = Decimal(str(value))
        quantize_str = "0." + "0" * precision
        return float(decimal_value.quantize(Decimal(quantize_str), rounding=ROUND_DOWN))

    # ========================================
    # Status query
    # ========================================

    def get_status(self) -> Dict:
        """Return executor status."""
        positions = self.get_all_positions()
        return {
            "testnet": self.testnet,
            "dry_run": self.dry_run,
            "risk_percent": self.risk_percent,
            "max_position_value": self.max_position_value,
            "open_positions": len(positions),
            "initialized_symbols": list(self._clients.keys()),
        }

    # ========================================
    # WebSocket lifecycle management
    # ========================================

    async def start_websockets(self) -> int:
        """
        Start WebSocket connections for all initialized clients.

        Returns:
            Number of successfully started connections.
        """
        if not self._use_websocket:
            self.logger.debug("WebSocket not enabled, skipping startup")
            return 0

        success_count = 0
        for symbol, client in self._clients.items():
            if hasattr(client, "start_websocket"):
                try:
                    if await client.start_websocket():
                        success_count += 1
                        self.logger.info(f"[{symbol}] WebSocket started")
                except Exception as e:
                    self.logger.error(f"[{symbol}] WebSocket start failed: {e}")

        self.logger.info(f"WebSocket startup complete: {success_count}/{len(self._clients)}")
        return success_count

    async def stop_websockets(self) -> None:
        """Stop WebSocket connections for all clients."""
        for symbol, client in self._clients.items():
            if hasattr(client, "stop_websocket"):
                try:
                    await client.stop_websocket()
                    self.logger.info(f"[{symbol}] WebSocket stopped")
                except Exception as e:
                    self.logger.error(f"[{symbol}] WebSocket stop failed: {e}")

    # ========================================
    # Limit order entry
    # ========================================

    LIMIT_DISCOUNT = 0.01          # limit order discount (close * 0.99 = 1% discount)
    PRICE_RUNAWAY_THRESHOLD = 1.5  # abandon entry if risk expands beyond 1.5x

    def execute_limit_entry(
        self, symbol: str, price: float, atr: float, strategy_name: str
    ) -> Optional[Dict]:
        """
        Execute limit order entry.

        Args:
            symbol: Asset symbol (BTC/ETH/BNB/SOL).
            price: Current price (daily close).
            atr: ATR value.
            strategy_name: Strategy name.

        Returns:
            Success: {'position_id': int, 'order_id': str, 'limit_price': float}
            Failure: None
        """
        self._ensure_initialized(symbol)
        config = self._get_symbol_config(symbol)
        client = self._clients[symbol]

        from src.strategies.swing.services.order_service import SymbolConfig

        return self._order_service.execute_limit_entry(
            client=client,
            symbol=symbol,
            price=price,
            atr=atr,
            strategy_name=strategy_name,
            config=SymbolConfig(
                stop_type=config.stop_type,
                trailing_period=config.trailing_period,
                trailing_mult=config.trailing_mult,
                quantity_precision=config.quantity_precision,
                price_precision=config.price_precision,
            ),
        )

    def check_pending_orders(self) -> List[Dict]:
        """
        Check PENDING order status (called hourly).

        Returns:
            Status change list [{'symbol': 'BTC', 'action': 'FILLED/TIMEOUT', ...}]
        """
        if not self._ensure_position_manager_ready():
            return []

        return self._order_service.check_pending_orders(
            get_client_func=lambda s: self._clients[s],
            ensure_init_func=self._ensure_initialized,
        )

    def process_pending_timeout(self) -> List[Dict]:
        """
        Process timed-out PENDING orders (not filled within 24 hours).

        Returns:
            Processing result list.
        """
        if not self._ensure_position_manager_ready():
            return []

        return self._order_service.process_pending_timeout(
            get_client_func=lambda s: self._clients[s],
            ensure_init_func=self._ensure_initialized,
            execute_entry_func=self.execute_entry,
        )

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current market price."""
        try:
            self._ensure_initialized(symbol)
            client = self._clients[symbol]
            return client.get_current_price()
        except Exception as e:
            self.logger.error(f"[{symbol}] Get price failed: {e}")
        return None

    # ========================================
    # Public API (hides internal member access)
    # ========================================

    def get_telegram_message_id(self, symbol: str) -> Optional[int]:
        """Get the Telegram message ID associated with a position."""
        if not self._position_manager:
            return None
        trading_pair = f"{symbol}USDT"
        return self._position_manager.get_telegram_message_id(trading_pair)

    def update_telegram_message_id(self, symbol: str, message_id: int) -> bool:
        """Update the Telegram message ID associated with a position."""
        if not self._position_manager:
            return False
        position = self.get_position(symbol)
        if not position:
            return False
        return self._position_manager.update_telegram_message_id(
            position["id"], message_id
        )

    def get_pending_positions(self) -> List[Dict]:
        """Get all PENDING positions."""
        if not self._ensure_position_manager_ready():
            return []
        return self._position_manager.get_pending_positions()

    def close_position_by_id(
        self, position_id: int, exit_price: float, reason: str
    ) -> bool:
        """Close a position directly by ID (for reconciliation)."""
        if not self._position_manager:
            return False
        return self._position_manager.close_position(
            position_id=position_id,
            exit_order_id=None,
            exit_price=exit_price,
            exit_reason=reason,
        )

    def clear_sl_order_by_position_id(self, position_id: int) -> bool:
        """Clear the stop-loss order ID for a position."""
        if not self._stop_loss_service:
            if not self._position_manager:
                return False
            return self._position_manager.clear_sl_order(position_id)
        return self._stop_loss_service.clear_sl_order(position_id)

    def get_client_balance(self, symbol: str) -> Optional[float]:
        """Get trading account balance."""
        try:
            self._ensure_initialized(symbol)
            client = self._clients[symbol]
            return client.get_balance()
        except Exception as e:
            self.logger.debug(f"[{symbol}] Get balance failed: {e}")
        return None

    def ensure_initialized(self, symbol: str) -> bool:
        """Ensure the specified symbol is initialized (public method)."""
        try:
            self._ensure_initialized(symbol)
            return True
        except Exception as e:
            self.logger.debug(f"[{symbol}] Initialization failed: {e}")
            return False

    def get_exchange_positions(self, symbol: str) -> List[Dict]:
        """Get actual exchange positions (for reconciliation)."""
        try:
            self._ensure_initialized(symbol)
            client = self._clients[symbol]
            return client.get_positions()
        except Exception as e:
            self.logger.debug(f"[{symbol}] Get exchange positions failed: {e}")
            return []

    def query_order_status(self, symbol: str, order_id: str) -> Optional[str]:
        """
        Query order status (used by Scheduler for reconciliation).

        Args:
            symbol: Asset symbol (BTC/ETH/BNB/SOL).
            order_id: Order ID.

        Returns:
            Binance raw status: FILLED/CANCELED/EXPIRED/REJECTED/NEW/PARTIALLY_FILLED
            None on query failure.
        """
        try:
            self._ensure_initialized(symbol)
            client = self._clients[symbol]
            order = client.get_order(order_id)
            if order and "info" in order:
                return order["info"].get("status")
            return None
        except Exception as e:
            self.logger.warning(f"[{symbol}] Query order status failed: {order_id}, {e}")
            return None
