"""
Swing order service (Strangler Fig Pattern)

Responsibilities:
  1. Market entry execution
  2. Limit entry execution
  3. Exit execution
  4. PENDING order management

Split from SwingExecutor; Executor calls this service as a facade.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING, Dict, List, Optional, Protocol

if TYPE_CHECKING:
    from src.strategies.swing.services.stop_loss_service import StopLossService
    from src.trading.position_manager import PositionManager

from src.core.events import PositionClosedEvent, PositionOpenedEvent
from src.core.protocols import MessageBusProtocol, TradingClientProtocol
from src.core.structured_logger import get_logger
from src.notifications.telegram_app import run_async
from src.strategies.swing.config import RISK_CONFIG


class AlertSenderProtocol(Protocol):
    """Alert sender protocol."""

    def send_alert(self, title: str, message: str) -> None: ...


@dataclass
class SymbolConfig:
    """Symbol configuration."""

    stop_type: str
    trailing_period: Optional[int] = None
    trailing_mult: Optional[float] = None
    quantity_precision: int = 2
    price_precision: int = 2


class OrderService:
    """
    Order service

    Handles all order-related operations:
    - Market entry
    - Limit entry
    - Exit
    - PENDING order management
    """

    # Risk constants
    MIN_NOTIONAL_VALUE = 10.0     # minimum notional value
    LEVERAGE_BUFFER = 1.25        # leverage safety buffer
    MAX_LEVERAGE = 3              # maximum leverage
    ENTRY_SLIPPAGE_WARN = 0.005  # entry slippage warning threshold (0.5%)
    EXIT_SLIPPAGE_WARN = 0.01    # exit slippage warning threshold (1%)
    LIMIT_DISCOUNT = 0.01        # limit order discount (1%)
    PRICE_RUNAWAY_THRESHOLD = 1.5  # abandon entry if risk expands beyond this

    def __init__(
        self,
        position_manager: "PositionManager",
        stop_loss_service: "StopLossService",
        message_bus: MessageBusProtocol,
        alert_sender: Optional[AlertSenderProtocol] = None,
        risk_percent: float = 2.0,
        max_position_value: float = 5000.0,
        testnet: bool = True,
        dry_run: bool = False,
    ):
        """
        Initialize the order service.

        Args:
            position_manager: Position manager.
            stop_loss_service: Stop-loss service.
            message_bus: Message bus.
            alert_sender: Alert sender (optional).
            risk_percent: Per-trade risk percentage.
            max_position_value: Maximum position value per trade.
            testnet: Whether using testnet.
            dry_run: Dry-run mode.
        """
        self._position_manager = position_manager
        self._stop_loss_service = stop_loss_service
        self._bus = message_bus
        self._alert_sender = alert_sender
        self.risk_percent = risk_percent
        self.max_position_value = max_position_value
        self.testnet = testnet
        self.dry_run = dry_run
        self.logger = get_logger(__name__)

    def _send_alert(self, title: str, message: str) -> None:
        """Send an alert."""
        if self._alert_sender:
            self._alert_sender.send_alert(title, message)
        else:
            self.logger.warning(f"[ALERT] {title}: {message}")

    # ========================================
    # Position size calculation
    # ========================================

    def calculate_quantity(
        self,
        client: TradingClientProtocol,
        symbol: str,
        price: float,
        atr: float,
        config: SymbolConfig,
    ) -> Optional[float]:
        """
        Calculate position size.

        Formula: quantity = (account balance * risk%) / stop distance
        Precision: floor to prevent insufficient balance.
        """
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
    # Market entry
    # ========================================

    def execute_market_entry(
        self,
        client: TradingClientProtocol,
        symbol: str,
        price: float,
        atr: float,
        strategy_name: str,
        config: SymbolConfig,
    ) -> Optional[Dict]:
        """
        Execute a market entry.

        Args:
            client: Trading client.
            symbol: Asset symbol.
            price: Entry price.
            atr: ATR value.
            strategy_name: Strategy name.
            config: Symbol config.

        Returns:
            Success: {'position_id': int, 'order_id': str, 'price': float, ...}
            Failure: None
        """
        # Step 1: risk checks
        if self._position_manager.get_open_positions(f"{symbol}USDT"):
            self.logger.warning(f"[{symbol}] Position already exists, rejecting entry")
            return None

        balances = client.get_balance()
        if not balances or "USDT" not in balances:
            self.logger.error(f"[{symbol}] Get balance failed")
            return None

        available_balance = float(balances["USDT"].get("free", 0))
        if available_balance <= 0:
            self.logger.error(f"[{symbol}] Available balance is zero")
            return None

        # Calculate stop distance and initial stop
        stop_loss_distance = atr * RISK_CONFIG["atr_stop_mult"]
        initial_stop = price - stop_loss_distance

        # Calculate position size
        quantity = self.calculate_quantity(client, symbol, price, atr, config)
        if not quantity or quantity <= 0:
            self.logger.error(f"[{symbol}] Position calculation failed or zero")
            return None

        notional_value = quantity * price

        # Risk check: minimum notional value
        if notional_value < self.MIN_NOTIONAL_VALUE:
            self.logger.warning(
                f"[{symbol}] Position value ${notional_value:.2f} too small, aborting"
            )
            return None

        # Risk check: maximum value - auto-reduce if exceeded
        if notional_value > self.max_position_value:
            self.logger.warning(
                f"[{symbol}] Position reduced: ${notional_value:.2f} -> ${self.max_position_value:.2f}"
            )
            notional_value = self.max_position_value
            quantity = notional_value / price
            quantity = self._floor_to_precision(quantity, config.quantity_precision)
            if quantity <= 0:
                self.logger.error(f"[{symbol}] Reduced position is zero, aborting")
                return None

        # Step 2: dynamic leverage calculation
        stop_distance_pct = stop_loss_distance / price
        raw_leverage = 1.0 / (stop_distance_pct * self.LEVERAGE_BUFFER)
        safe_leverage = int(max(1, min(self.MAX_LEVERAGE, raw_leverage)))
        est_liquidation_price = price * (1 - 1 / safe_leverage)

        self.logger.info(
            f"[{symbol}] Dynamic leverage - "
            f"stop distance: {stop_distance_pct:.1%}, "
            f"safe leverage: {safe_leverage}x, "
            f"stop: ${initial_stop:.2f}, "
            f"est. liquidation: ${est_liquidation_price:.2f}"
        )

        # Dry-run mode
        if self.dry_run:
            self.logger.info(
                f"[DRY RUN][{symbol}] Entry - "
                f"price: ${price:.2f}, quantity: {quantity}, "
                f"stop: ${initial_stop:.2f}, leverage: {safe_leverage}x"
            )
            return {
                "dry_run": True,
                "symbol": symbol,
                "price": price,
                "quantity": quantity,
                "stop_loss": initial_stop,
                "leverage": safe_leverage,
            }

        # Step 3: account setup
        try:
            client.cancel_all_orders()
            if not client.set_margin_type("ISOLATED"):
                self.logger.warning(f"[{symbol}] Set isolated margin failed, continuing")
            if not client.set_leverage(safe_leverage):
                self.logger.error(f"[{symbol}] Set leverage failed")
                self._send_alert(
                    f"[{symbol}] Set leverage failed", f"Target leverage: {safe_leverage}x"
                )
                return None
        except Exception as e:
            self.logger.error(f"[{symbol}] Account setup failed: {e}")
            self._send_alert(f"[{symbol}] Account setup failed", str(e))
            return None

        # Step 4: margin check
        required_margin = notional_value / safe_leverage
        if required_margin > available_balance:
            self.logger.error(
                f"[{symbol}] Insufficient margin! Required: ${required_margin:.2f}, available: ${available_balance:.2f}"
            )
            self._send_alert(
                f"[{symbol}] Insufficient margin",
                f"Required: ${required_margin:.2f}\nAvailable: ${available_balance:.2f}",
            )
            return None

        # Step 5: place order
        order = client.create_market_order(
            side="BUY", quantity=quantity, position_side="LONG"
        )

        if not order:
            self.logger.error(f"[{symbol}] Order placement failed: API returned None")
            self._send_alert(f"[{symbol}] Order placement failed", "API returned None")
            return None

        executed_qty = float(order.get("filled", 0))
        order_status = order.get("status")
        order_id = order.get("id")

        # Handle unfilled order
        if executed_qty <= 0:
            self.logger.warning(f"[{symbol}] Order not filled, attempting cancel")
            try:
                client.cancel_order(order_id)
            except Exception as cancel_err:
                self.logger.warning(f"[{symbol}] Cancel failed: {cancel_err}")

            time.sleep(0.3)
            try:
                final_order = client.get_order(order_id)
                if final_order:
                    final_qty = float(final_order.get("filled", 0))
                    if final_qty > 0:
                        executed_qty = final_qty
                        order = final_order
                        order_status = final_order.get("status")
            except Exception as query_err:
                self.logger.warning(f"[{symbol}] Final status query failed: {query_err}")

            if executed_qty <= 0:
                self.logger.error(f"[{symbol}] Order failed or timed out unfilled")
                self._send_alert(
                    f"[{symbol}] Order timeout unfilled",
                    f"Order ID: {order_id}\nStatus: {order_status}",
                )
                return None

        # Partial fill alert
        if order_status != "closed":
            self.logger.warning(
                f"[{symbol}] Partial fill - planned: {quantity}, actual: {executed_qty}"
            )
            self._send_alert(
                f"[{symbol}] Partial fill", f"Planned: {quantity}\nActual: {executed_qty}"
            )

        actual_quantity = executed_qty
        fill_price = float(order.get("average", price))

        # Slippage monitoring
        slippage = abs(fill_price - price) / price
        if slippage > self.ENTRY_SLIPPAGE_WARN:
            self.logger.warning(f"[{symbol}] Entry slippage warning: {slippage:.2%}")

        # Step 6: create position record
        position_id = self._position_manager.open_position(
            symbol=f"{symbol}USDT",
            side="LONG",
            entry_signal_id=0,
            entry_order_id=order.get("id", 0),
            entry_price=fill_price,
            quantity=actual_quantity,
            stop_loss=initial_stop,
            take_profit_1=None,
            take_profit_2=None,
            strategy_name=strategy_name,
            testnet=self.testnet,
            stop_type=config.stop_type,
            trailing_period=config.trailing_period,
            trailing_mult=config.trailing_mult,
            entry_atr=atr,
        )

        if not position_id:
            self.logger.error(f"[{symbol}] Position record creation failed")
            self._send_alert(
                f"[{symbol}] Position record creation failed",
                f"Order filled but DB write failed!\nFilled quantity: {actual_quantity}",
            )
            return None

        # Step 6.5: record implementation shortfall
        self._position_manager.record_implementation_shortfall(
            position_id=position_id,
            symbol=f"{symbol}USDT",
            side="LONG",
            signal_price=price,
            fill_price=fill_price,
            quantity=actual_quantity,
        )

        # Step 7: place stop-loss order
        sl_order_id = self._stop_loss_service.create_stop_loss_order(
            client, symbol, actual_quantity, initial_stop, position_id
        )

        if not sl_order_id:
            self._send_alert(
                f"[{symbol}] Stop-loss order failed - URGENT",
                f"Position opened but stop-loss order placement failed!\nPosition ID: {position_id}\nPlease place a stop-loss manually immediately!",
            )

        # Step 8: place take-profit for SOL strategy
        tp_order_id = None
        if config.stop_type == "TRAILING_ATR" and atr > 0:
            take_profit_atr = 6.0
            take_profit = fill_price + take_profit_atr * atr
            tp_order_id = self._stop_loss_service.create_take_profit_order(
                client, symbol, actual_quantity, take_profit
            )

        self.logger.info(
            f"[{symbol}] Entry succeeded - PositionID: {position_id}, "
            f"price: ${fill_price:.2f}, quantity: {actual_quantity}, "
            f"leverage: {safe_leverage}x, stop: ${initial_stop:.2f}"
        )

        # Publish event
        run_async(
            self._bus.publish(
                PositionOpenedEvent(
                    position_id=position_id,
                    symbol=symbol,
                    entry_price=fill_price,
                    quantity=actual_quantity,
                    strategy_name=strategy_name,
                    stop_loss=initial_stop,
                    leverage=safe_leverage,
                )
            )
        )

        return {
            "position_id": position_id,
            "order_id": order.get("id"),
            "price": fill_price,
            "quantity": actual_quantity,
            "stop_loss": initial_stop,
            "leverage": safe_leverage,
            "sl_order_id": sl_order_id,
        }

    # ========================================
    # Limit entry
    # ========================================

    def execute_limit_entry(
        self,
        client: TradingClientProtocol,
        symbol: str,
        price: float,
        atr: float,
        strategy_name: str,
        config: SymbolConfig,
    ) -> Optional[Dict]:
        """
        Execute a limit order entry.

        Args:
            client: Trading client.
            symbol: Asset symbol.
            price: Current price (daily close).
            atr: ATR value.
            strategy_name: Strategy name.
            config: Symbol config.

        Returns:
            Success: {'position_id': int, 'order_id': str, 'limit_price': float}
            Failure: None
        """
        # Step 1: check for existing position
        if self._position_manager.has_pending_or_open_position(f"{symbol}USDT"):
            self.logger.warning(f"[{symbol}] PENDING or OPEN position exists, skipping")
            return None

        # Step 2: calculate limit price
        limit_price = price * (1 - self.LIMIT_DISCOUNT)
        limit_price = round(limit_price, config.price_precision)

        # Step 3: calculate size and stop
        quantity = self.calculate_quantity(client, symbol, price, atr, config)
        if not quantity or quantity <= 0:
            self.logger.error(f"[{symbol}] Position calculation failed")
            return None

        notional_value = quantity * limit_price

        # Risk checks
        if notional_value < self.MIN_NOTIONAL_VALUE:
            self.logger.warning(f"[{symbol}] Position value too small, aborting")
            return None

        if notional_value > self.max_position_value:
            self.logger.warning(
                f"[{symbol}] Position reduced: ${notional_value:.2f} -> ${self.max_position_value:.2f}"
            )
            notional_value = self.max_position_value
            quantity = notional_value / limit_price
            quantity = self._floor_to_precision(quantity, config.quantity_precision)
            if quantity <= 0:
                self.logger.error(f"[{symbol}] Reduced position is zero, aborting")
                return None

        # Stop price (based on limit price)
        initial_stop = self._stop_loss_service.calculate_initial_stop(
            limit_price, atr, config.stop_type, config.trailing_mult
        )

        # Dry-run mode
        if self.dry_run:
            self.logger.info(
                f"[DRY RUN][{symbol}] Limit entry - "
                f"limit: ${limit_price:.2f}, quantity: {quantity}, stop: ${initial_stop:.2f}"
            )
            return {
                "dry_run": True,
                "symbol": symbol,
                "limit_price": limit_price,
                "quantity": quantity,
                "stop_loss": initial_stop,
            }

        # Step 4: dynamic leverage calculation
        stop_distance_pct = (limit_price - initial_stop) / limit_price
        raw_leverage = 1.0 / (stop_distance_pct * self.LEVERAGE_BUFFER)
        safe_leverage = int(max(1, min(self.MAX_LEVERAGE, raw_leverage)))

        # Step 5: account setup
        try:
            client.cancel_all_orders()
            client.set_margin_type("ISOLATED")
            if not client.set_leverage(safe_leverage):
                self.logger.error(f"[{symbol}] Set leverage failed")
                return None
        except Exception as e:
            self.logger.error(f"[{symbol}] Account setup failed: {e}")
            return None

        # Step 6: place limit order
        order = client.create_limit_order(
            side="BUY",
            quantity=quantity,
            price=limit_price,
            position_side="LONG",
            time_in_force="GTC",
        )

        if not order or order.get("status") == "error":
            self.logger.error(f"[{symbol}] Limit order creation failed: {order}")
            self._send_alert(f"[{symbol}] Limit order creation failed", f"Limit: ${limit_price:.2f}")
            return None

        order_id = order.get("id")

        # Step 7: create PENDING position record
        position_id = self._position_manager.create_pending_position(
            symbol=f"{symbol}USDT",
            side="LONG",
            pending_order_id=order_id,
            pending_limit_price=limit_price,
            target_quantity=quantity,
            stop_loss=initial_stop,
            entry_atr=atr,
            strategy_name=strategy_name,
            testnet=self.testnet,
            stop_type=config.stop_type,
            trailing_period=config.trailing_period,
            trailing_mult=config.trailing_mult,
            take_profit_1=None,
            take_profit_2=None,
        )

        if not position_id:
            self.logger.error(f"[{symbol}] PENDING position record creation failed, cancelling order")
            client.cancel_order(order_id)
            return None

        self.logger.info(
            f"[{symbol}] Limit order placed - PositionID: {position_id}, "
            f"limit: ${limit_price:.2f}, quantity: {quantity}"
        )

        return {
            "position_id": position_id,
            "order_id": order_id,
            "limit_price": limit_price,
            "quantity": quantity,
            "stop_loss": initial_stop,
            "leverage": safe_leverage,
        }

    # ========================================
    # Exit execution
    # ========================================

    def execute_exit(
        self,
        client: TradingClientProtocol,
        symbol: str,
        position: Dict,
        price: float,
        reason: str,
    ) -> Optional[Dict]:
        """
        Execute an exit.

        Args:
            client: Trading client.
            symbol: Asset symbol.
            position: Position info.
            price: Exit price (for logging; actual fill is at market).
            reason: Exit reason.

        Returns:
            Success: {'position_id': int, 'order_id': str, 'price': float, 'pnl': float}
            Failure: None
        """
        quantity = float(position["quantity"])
        position_id = position["id"]
        entry_price = float(position["entry_price"])
        sl_order_id = position.get("sl_order_id")

        # Dry-run mode
        if self.dry_run:
            pnl = (price - entry_price) * quantity
            pnl_pct = (price - entry_price) / entry_price * 100
            self.logger.info(
                f"[DRY RUN][{symbol}] Exit - "
                f"price: ${price:.2f}, pnl: ${pnl:.2f} ({pnl_pct:+.2f}%), "
                f"reason: {reason}"
            )
            return {
                "dry_run": True,
                "symbol": symbol,
                "price": price,
                "pnl": pnl,
                "reason": reason,
            }

        # Step 1: close position
        order = client.create_market_order(
            side="SELL", quantity=quantity, position_side="LONG"
        )

        if not order or order.get("status") != "closed":
            self.logger.error(f"[{symbol}] Position close failed: {order}")
            return None

        fill_price = float(order.get("average", price))

        # Exit slippage monitoring
        slippage = abs(fill_price - price) / price
        if slippage > self.EXIT_SLIPPAGE_WARN:
            self.logger.warning(f"[{symbol}] Exit slippage warning: {slippage:.2%}")
            self._send_alert(
                f"[{symbol}] Exit slippage too large", f"Slippage: {slippage:.2%}\nReason: {reason}"
            )

        # Step 2: cancel stop-loss order
        if sl_order_id:
            try:
                client.cancel_algo_order(sl_order_id)
                self.logger.info(f"[{symbol}] Stop-loss order cancelled: {sl_order_id}")
            except Exception as e:
                self.logger.warning(f"[{symbol}] Cancel stop-loss failed (ignorable): {e}")

        # Step 3: update position record
        success = self._position_manager.close_position(
            position_id=position_id,
            exit_order_id=order.get("id", 0),
            exit_price=fill_price,
            exit_reason=reason,
        )

        if not success:
            self.logger.error(f"[{symbol}] Position record update failed")

        self._position_manager.clear_sl_order(position_id)

        # Calculate P/L
        pnl = (fill_price - entry_price) * quantity
        pnl_pct = (fill_price - entry_price) / entry_price * 100

        self.logger.info(
            f"[{symbol}] Exit succeeded - price: ${fill_price:.2f}, "
            f"pnl: ${pnl:.2f} ({pnl_pct:+.2f}%), reason: {reason}"
        )

        # Publish event
        telegram_msg_id = position.get("telegram_message_id")
        run_async(
            self._bus.publish(
                PositionClosedEvent(
                    position_id=position_id,
                    symbol=symbol,
                    exit_price=fill_price,
                    pnl_percent=pnl_pct,
                    entry_price=entry_price,
                    reason=reason,
                    telegram_message_id=telegram_msg_id,
                )
            )
        )

        return {
            "position_id": position_id,
            "order_id": order.get("id"),
            "price": fill_price,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason,
        }

    # ========================================
    # PENDING order management
    # ========================================

    def check_pending_orders(self, get_client_func, ensure_init_func) -> List[Dict]:
        """
        Check PENDING order status (called hourly).

        Args:
            get_client_func: Function to get client by symbol.
            ensure_init_func: Function to ensure initialization.

        Returns:
            Status change list [{'symbol': 'BTC', 'action': 'FILLED/TIMEOUT', ...}]
        """
        results = []

        pending_list = self._position_manager.get_pending_positions()
        if not pending_list:
            self.logger.debug("No PENDING positions")
            return results

        for pos in pending_list:
            symbol = pos["symbol"].replace("USDT", "")
            order_id = pos["pending_order_id"]

            try:
                ensure_init_func(symbol)
                client = get_client_func(symbol)

                order = client.get_order(order_id)
                if not order:
                    self.logger.warning(f"[{symbol}] Order query failed: {order_id}")
                    continue

                status = order.get("status")
                filled_qty = float(order.get("filled", 0))
                avg_price = float(order.get("average", 0))

                # Filled -> promote to OPEN
                if status == "closed" and filled_qty > 0:
                    success = self._position_manager.promote_pending_to_open(
                        position_id=pos["id"],
                        entry_price=avg_price,
                        quantity=filled_qty,
                        entry_order_id=order_id,
                    )
                    if success:
                        self.logger.info(
                            f"[{symbol}] Limit order filled - price: ${avg_price:.2f}, quantity: {filled_qty}"
                        )

                        # Place stop-loss and take-profit orders
                        self._stop_loss_service.attach_sl_tp_after_fill(
                            client=client,
                            symbol=symbol,
                            position_id=pos["id"],
                            quantity=filled_qty,
                            entry_price=avg_price,
                            stop_loss=float(pos.get("stop_loss", 0)),
                            entry_atr=float(pos.get("entry_atr", 0)),
                            strategy_name=pos.get("strategy_name", "unknown"),
                            stop_type=pos.get("stop_type", "TRAILING_LOWEST"),
                            max_leverage=self.MAX_LEVERAGE,
                        )

                        results.append(
                            {
                                "symbol": symbol,
                                "action": "FILLED",
                                "position_id": pos["id"],
                                "price": avg_price,
                                "quantity": filled_qty,
                            }
                        )

            except Exception as e:
                self.logger.error(f"[{symbol}] Check PENDING order failed: {e}")

        return results

    def process_pending_timeout(
        self, get_client_func, ensure_init_func, execute_entry_func
    ) -> List[Dict]:
        """
        Process timed-out PENDING orders (not filled within 24 hours).

        Args:
            get_client_func: Function to get client by symbol.
            ensure_init_func: Function to ensure initialization.
            execute_entry_func: Function to execute market entry.

        Returns:
            Processing result list.
        """
        results = []

        timeout_list = self._position_manager.get_pending_positions(timeout_hours=24)
        if not timeout_list:
            self.logger.debug("No timed-out PENDING positions")
            return results

        for pos in timeout_list:
            symbol = pos["symbol"].replace("USDT", "")
            order_id = pos["pending_order_id"]
            limit_price = float(pos["pending_limit_price"])
            stop_loss = float(pos["stop_loss"])
            atr = float(pos.get("entry_atr", 0))
            strategy_name = pos.get("strategy_name", "unknown")

            try:
                ensure_init_func(symbol)
                client = get_client_func(symbol)

                # Step 1: cancel limit order
                client.cancel_order(order_id)
                self.logger.info(f"[{symbol}] Timeout: cancelled limit order {order_id}")

                # Step 2: get current price
                current_price = client.get_current_price()
                if not current_price:
                    self.logger.error(f"[{symbol}] Get current price failed")
                    self._position_manager.cancel_pending_position(
                        pos["id"], "TIMEOUT_NO_PRICE"
                    )
                    continue

                # Step 3: price runaway check
                original_risk = limit_price - stop_loss
                new_risk = current_price - stop_loss

                if (
                    original_risk > 0
                    and new_risk / original_risk > self.PRICE_RUNAWAY_THRESHOLD
                ):
                    self.logger.warning(
                        f"[{symbol}] Price ran away, aborting market order - "
                        f"original risk: ${original_risk:.2f}, new risk: ${new_risk:.2f}"
                    )
                    self._position_manager.cancel_pending_position(
                        pos["id"], "PRICE_RUNAWAY"
                    )
                    results.append(
                        {
                            "symbol": symbol,
                            "action": "CANCELLED",
                            "reason": "PRICE_RUNAWAY",
                            "position_id": pos["id"],
                        }
                    )
                    continue

                # Step 4: market order fallback
                self.logger.info(
                    f"[{symbol}] Timeout: placing market order at ${current_price:.2f}"
                )
                self._position_manager.cancel_pending_position(
                    pos["id"], "TIMEOUT_MARKET_FILL"
                )

                result = execute_entry_func(
                    symbol=symbol,
                    price=current_price,
                    atr=atr,
                    strategy_name=strategy_name,
                )

                if result:
                    results.append(
                        {
                            "symbol": symbol,
                            "action": "MARKET_FILLED",
                            "position_id": result["position_id"],
                            "price": result["price"],
                        }
                    )
                else:
                    results.append(
                        {
                            "symbol": symbol,
                            "action": "MARKET_FAILED",
                            "position_id": pos["id"],
                        }
                    )

            except Exception as e:
                self.logger.error(f"[{symbol}] Process timeout PENDING failed: {e}")
                results.append({"symbol": symbol, "action": "ERROR", "error": str(e)})

        return results
