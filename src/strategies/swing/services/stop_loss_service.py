"""
Swing stop-loss service (Strangler Fig Pattern)

Responsibilities:
  1. Stop-loss order creation/update/cancellation
  2. Take-profit order creation
  3. Trailing stop calculation and update

Split from SwingExecutor; Executor calls this service as a facade.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, List, TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from src.trading.position_manager import PositionManager
    from src.trading.trailing_stop_manager import TrailingStopManager

from src.core.protocols import TradingClientProtocol, MessageBusProtocol
from src.core.events import StopLossMovedEvent, PositionOpenedEvent
from src.notifications.telegram_app import run_async
from src.strategies.swing.config import RISK_CONFIG


class AlertSenderProtocol(Protocol):
    """Alert sender protocol."""
    def send_alert(self, title: str, message: str) -> None: ...


class StopLossService:
    """
    Stop-loss service

    Handles all stop-loss/take-profit operations:
    - Stop-loss order creation, update, cancellation
    - Take-profit order creation
    - Trailing stop calculation
    """

    def __init__(
        self,
        position_manager: 'PositionManager',
        trailing_manager: 'TrailingStopManager',
        message_bus: MessageBusProtocol,
        alert_sender: Optional[AlertSenderProtocol] = None,
        dry_run: bool = False,
    ):
        """
        Initialize the stop-loss service.

        Args:
            position_manager: Position manager.
            trailing_manager: Trailing stop manager.
            message_bus: Message bus.
            alert_sender: Alert sender (optional).
            dry_run: Dry-run mode.
        """
        self._position_manager = position_manager
        self._trailing_manager = trailing_manager
        self._bus = message_bus
        self._alert_sender = alert_sender
        self.dry_run = dry_run
        self.logger = logging.getLogger(__name__)

    def _send_alert(self, title: str, message: str) -> None:
        """Send an alert."""
        if self._alert_sender:
            self._alert_sender.send_alert(title, message)
        else:
            self.logger.warning(f"[ALERT] {title}: {message}")

    # ========================================
    # Stop price calculation
    # ========================================

    def calculate_initial_stop(
        self,
        price: float,
        atr: float,
        stop_type: str,
        trailing_mult: Optional[float] = None
    ) -> float:
        """
        Calculate initial stop-loss price.

        Args:
            price: Entry price.
            atr: ATR value.
            stop_type: Stop type (TRAILING_ATR/TRAILING_LOWEST).
            trailing_mult: Trailing stop multiplier.

        Returns:
            Initial stop-loss price.
        """
        if stop_type == 'TRAILING_ATR':
            mult = trailing_mult or RISK_CONFIG['atr_stop_mult']
            return price - atr * mult
        else:
            # TRAILING_LOWEST: use ATR estimate as initial stop
            return price - atr * RISK_CONFIG['atr_stop_mult']

    # ========================================
    # Stop-loss order operations
    # ========================================

    def create_stop_loss_order(
        self,
        client: TradingClientProtocol,
        symbol: str,
        quantity: float,
        stop_price: float,
        position_id: int
    ) -> Optional[str]:
        """
        Create a stop-loss order.

        Args:
            client: Trading client.
            symbol: Asset symbol.
            quantity: Position quantity.
            stop_price: Stop-loss trigger price.
            position_id: Position ID.

        Returns:
            Stop-loss order ID or None.
        """
        try:
            sl_order = client.create_stop_loss_order(
                side='SELL',
                quantity=quantity,
                stop_price=stop_price,
                position_side='LONG'
            )

            if sl_order and sl_order.get('status') != 'error':
                sl_order_id = sl_order.get('id')
                # Update DB
                self._position_manager.update_sl_order(
                    position_id, sl_order_id, stop_price
                )
                self.logger.info(
                    f"[{symbol}] Stop-loss order created - "
                    f"OrderID: {sl_order_id}, trigger: ${stop_price:.2f}"
                )
                return sl_order_id
            else:
                error_msg = sl_order.get('error_message') if sl_order else 'API returned None'
                raise Exception(error_msg)

        except Exception as e:
            self.logger.error(f"[{symbol}] Stop-loss order creation failed: {e}")
            return None

    def create_take_profit_order(
        self,
        client: TradingClientProtocol,
        symbol: str,
        quantity: float,
        take_profit_price: float
    ) -> Optional[str]:
        """
        Create a take-profit order.

        Args:
            client: Trading client.
            symbol: Asset symbol.
            quantity: Position quantity.
            take_profit_price: Take-profit trigger price.

        Returns:
            Take-profit order ID or None.
        """
        try:
            tp_order = client.create_take_profit_order(
                side='SELL',
                quantity=quantity,
                stop_price=take_profit_price,
                position_side='LONG'
            )

            if tp_order and tp_order.get('status') != 'error':
                tp_order_id = tp_order.get('id')
                self.logger.info(
                    f"[{symbol}] Take-profit order created - "
                    f"OrderID: {tp_order_id}, trigger: ${take_profit_price:.2f}"
                )
                return tp_order_id
            else:
                error_msg = tp_order.get('error_message') if tp_order else 'API returned None'
                raise Exception(error_msg)

        except Exception as e:
            self.logger.warning(f"[{symbol}] Take-profit order creation failed: {e}")
            return None

    def cancel_stop_loss_order(
        self,
        client: TradingClientProtocol,
        symbol: str,
        order_id: str
    ) -> bool:
        """
        Cancel a stop-loss order.

        Args:
            client: Trading client.
            symbol: Asset symbol.
            order_id: Order ID.

        Returns:
            True on success.
        """
        try:
            client.cancel_algo_order(order_id)
            self.logger.info(f"[{symbol}] Stop-loss order cancelled: {order_id}")
            return True
        except Exception as e:
            # Old order may already be gone (triggered or manually cancelled)
            self.logger.warning(
                f"[{symbol}] Cancel stop-loss failed (ignorable): {order_id}, {e}"
            )
            return False

    # ========================================
    # Trailing stop updates
    # ========================================

    def update_trailing_stop(
        self,
        client: TradingClientProtocol,
        symbol: str,
        position: Dict,
        new_stop: float
    ) -> Optional[Dict]:
        """
        Update trailing stop (can only be raised to prevent drawdown).

        Flow: cancel old order (fault-tolerant) -> place new order -> update DB

        Args:
            client: Trading client.
            symbol: Asset symbol.
            position: Position info.
            new_stop: New stop-loss price.

        Returns:
            Dict: update details {'symbol', 'old_stop', 'new_stop', 'entry_price', 'strategy'}
            None: update failed or not needed.
        """
        old_stop = float(position.get('current_stop') or position.get('stop_loss') or 0)
        position_id = position['id']
        quantity = float(position['quantity'])
        old_sl_order_id = position.get('sl_order_id')

        # Stop can only be raised
        if new_stop <= old_stop:
            self.logger.debug(
                f"[{symbol}] New stop ${new_stop:.2f} <= current ${old_stop:.2f}, skipping"
            )
            return None

        # Dry-run mode: update DB only
        if self.dry_run:
            success = self._trailing_manager.update_trailing_stop(
                position_id=position_id,
                new_stop=new_stop,
                highest_price=None
            )
            if success:
                self.logger.info(
                    f"[DRY RUN][{symbol}] Trailing stop update: ${old_stop:.2f} -> ${new_stop:.2f}"
                )
                return {
                    'symbol': symbol,
                    'old_stop': old_stop,
                    'new_stop': new_stop,
                    'entry_price': float(position['entry_price']),
                    'strategy': position.get('strategy_name', 'v9')
                }
            return None

        # Step 1: cancel old stop-loss order (fault-tolerant, failure does not block)
        if old_sl_order_id:
            self.cancel_stop_loss_order(client, symbol, old_sl_order_id)

        # Step 2: place new stop-loss order
        new_sl_order_id = None
        try:
            sl_order = client.create_stop_loss_order(
                side='SELL',
                quantity=quantity,
                stop_price=new_stop,
                position_side='LONG'
            )

            if sl_order and sl_order.get('status') != 'error':
                new_sl_order_id = sl_order.get('id')
                self.logger.info(
                    f"[{symbol}] New stop-loss order created - "
                    f"OrderID: {new_sl_order_id}, trigger: ${new_stop:.2f}"
                )
            else:
                error_msg = sl_order.get('error_message') if sl_order else 'API returned None'
                raise Exception(error_msg)

        except Exception as e:
            self.logger.error(f"[{symbol}] New stop-loss order creation failed: {e}")
            self._send_alert(
                f"[{symbol}] Trailing stop order failed",
                f"Old stop cancelled but new stop order failed!\n"
                f"Old stop: ${old_stop:.2f}\n"
                f"New stop: ${new_stop:.2f}\n"
                f"Error: {e}\n"
                f"Please place a stop-loss order manually immediately!"
            )
            # Continue to update DB (at least preserve soft stop)

        # Step 3: update DB (stop price + stop order ID)
        if new_sl_order_id:
            self._position_manager.update_sl_order(
                position_id, new_sl_order_id, new_stop
            )
        else:
            # No new stop order, update stop price only (soft stop as fallback)
            self._trailing_manager.update_trailing_stop(
                position_id=position_id,
                new_stop=new_stop,
                highest_price=None
            )

        self.logger.info(
            f"[{symbol}] Trailing stop updated: ${old_stop:.2f} -> ${new_stop:.2f}"
            f"{', SL order: ' + new_sl_order_id if new_sl_order_id else ' (DB only)'}"
        )

        # Publish stop moved event
        run_async(self._bus.publish(StopLossMovedEvent(
            position_id=position_id,
            symbol=symbol,
            old_stop=old_stop,
            new_stop=new_stop
        )))

        return {
            'symbol': symbol,
            'old_stop': old_stop,
            'new_stop': new_stop,
            'entry_price': float(position['entry_price']),
            'strategy': position.get('strategy_name', 'v9'),
            'sl_order_id': new_sl_order_id
        }

    def batch_update_trailing_stops(
        self,
        updates: List[Dict],
        get_client_func,
        get_position_func
    ) -> int:
        """
        Batch update trailing stops.

        Args:
            updates: [{'symbol': 'BTC', 'new_stop': 95000.0}, ...]
            get_client_func: Function to get client by symbol.
            get_position_func: Function to get position by symbol.

        Returns:
            Number of successful updates.
        """
        success_count = 0
        for update in updates:
            symbol = update.get('symbol')
            new_stop = update.get('new_stop')
            if symbol and new_stop:
                client = get_client_func(symbol)
                position = get_position_func(symbol)
                if client and position:
                    if self.update_trailing_stop(client, symbol, position, new_stop):
                        success_count += 1
        return success_count

    # ========================================
    # Post-fill handling
    # ========================================

    def attach_sl_tp_after_fill(
        self,
        client: TradingClientProtocol,
        symbol: str,
        position_id: int,
        quantity: float,
        entry_price: float,
        stop_loss: float,
        entry_atr: float,
        strategy_name: str,
        stop_type: str,
        max_leverage: int = 3
    ) -> Dict:
        """
        Place stop-loss and take-profit orders after a limit order fills.

        Args:
            client: Trading client.
            symbol: Asset symbol.
            position_id: Position ID.
            quantity: Filled quantity.
            entry_price: Fill price.
            stop_loss: Stop-loss price.
            entry_atr: Entry ATR.
            strategy_name: Strategy name.
            stop_type: Stop type.
            max_leverage: Maximum leverage.

        Returns:
            Dict: {'sl_order_id': ..., 'tp_order_id': ...}
        """
        sl_order_id = None
        tp_order_id = None

        # Step 1: place stop-loss order
        if stop_loss > 0:
            sl_order_id = self.create_stop_loss_order(
                client, symbol, quantity, stop_loss, position_id
            )
            if not sl_order_id:
                self._send_alert(
                    f"[{symbol}] Stop-loss order failed after fill - URGENT",
                    f"Limit order filled but stop-loss order placement failed!\n"
                    f"Position ID: {position_id}\n"
                    f"Fill price: ${entry_price:.2f}\n"
                    f"Stop price: ${stop_loss:.2f}\n"
                    f"Please place a stop-loss order manually immediately!"
                )

        # Step 2: place take-profit for SOL strategy (TRAILING_ATR = fixed TP/SL)
        if stop_type == 'TRAILING_ATR' and entry_atr > 0:
            take_profit_atr = 6.0  # SOL take-profit multiplier
            take_profit = entry_price + take_profit_atr * entry_atr
            tp_order_id = self.create_take_profit_order(
                client, symbol, quantity, take_profit
            )

        # Step 3: publish event
        run_async(self._bus.publish(PositionOpenedEvent(
            position_id=position_id,
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            strategy_name=strategy_name,
            stop_loss=stop_loss,
            leverage=max_leverage
        )))

        return {
            'sl_order_id': sl_order_id,
            'tp_order_id': tp_order_id
        }

    def clear_sl_order(self, position_id: int) -> bool:
        """
        Clear the stop-loss order ID for a position.

        Args:
            position_id: Position ID.

        Returns:
            True on success.
        """
        try:
            self._position_manager.clear_sl_order(position_id)
            return True
        except Exception as e:
            self.logger.warning(f"Clear stop-loss order ID failed: {e}")
            return False
