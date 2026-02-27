"""
Swing strategy scheduler (daily trend following)

Responsibilities:
  1. Run Swing signal check daily at UTC 00:01
  2. Heartbeat mechanism (log every hour)
  3. Integrate Telegram push notifications
  4. Exception handling and alerting

Architecture:
  - Async Native: main loop is asyncio-driven, non-blocking
  - schedule library is used only as a cron trigger (marks tasks for execution)
  - SwingExecutor is the single source of truth (positions table)

Usage:
  python -m src.strategies.swing.scheduler
  python -m src.strategies.swing.scheduler --run-now
  python -m src.strategies.swing.scheduler --status
  python -m src.strategies.swing.scheduler --execute --mainnet
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import schedule

# Add project root to path
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ),
)

from dotenv import load_dotenv

load_dotenv()

# Import strategy modules to trigger registration (must be before create_all_strategies)
from src.core.saga import get_orchestrator

# Saga support
from src.sagas.trading_saga import execute_trade, register_trading_saga
from src.strategies.swing.config import (
    FEATURE_FLAGS,
    FUNDING_RATE_CONFIG,
    get_supported_symbols,
    get_symbol_config,
)
from src.strategies.swing.providers.data_provider import (
    BinanceDataProvider,
    DataProvider,
)
from src.strategies.swing.services.executor import SwingExecutor
from src.strategies.swing.services.notification_manager import SwingNotificationManager
from src.strategies.swing.strategies import (
    breakout,  # noqa: F401
    ensemble,  # noqa: F401
)
from src.strategies.swing.strategies.registry import create_all_strategies


class SwingScheduler:
    """Swing strategy scheduler (daily trend following) - Async Native"""

    # Task interval constants
    HOURLY_INTERVAL_MIN = 60
    RECONCILE_INTERVAL_MIN = 240  # 4 hours

    def __init__(
        self,
        execute_mode: bool = False,
        testnet: bool = True,
        dry_run: bool = False,
        use_websocket: bool = True,
        data_provider: DataProvider = None,
        executor=None,
        notification_manager=None,
    ):
        """
        Initialize the scheduler.

        Args:
            execute_mode: Whether to enable execute mode (live trading).
            testnet: Whether to use testnet.
            dry_run: Dry-run mode (log only, no orders).
            use_websocket: Whether to enable WebSocket real-time data.
            data_provider: Data provider (default BinanceDataProvider).
            executor: Executor (default SwingExecutor; supports mock injection).
            notification_manager: Notification manager (default SwingNotificationManager; supports mock injection).
        """
        self.execute_mode = execute_mode
        self.testnet = testnet
        self.dry_run = dry_run
        self._use_websocket = use_websocket
        self.logger = logging.getLogger(__name__)

        # Async Native: task scheduling state
        self._running = False
        self._pending_cron_tasks: List[str] = []
        self._last_run_times: Dict[str, datetime] = {}

        # Initialize data provider
        self.data_provider = data_provider or BinanceDataProvider()

        # Initialize strategy instances
        self.strategies: Dict[str, Any] = {}
        self._init_strategies()

        # Initialize notification manager (must be before executor)
        self.notification_manager = notification_manager or SwingNotificationManager()

        # Initialize executor (single source of truth)
        self.executor = executor or SwingExecutor(
            testnet=testnet, dry_run=dry_run, use_websocket=use_websocket
        )

        # Pre-initialize all symbols in execute mode
        if execute_mode and hasattr(self.executor, "initialize_all"):
            self.executor.initialize_all()

        mode_str = self._get_mode_string()
        self.logger.info(f"SwingScheduler initialized - {mode_str}")

    def _get_mode_string(self) -> str:
        """Return a human-readable mode description."""
        if self.dry_run:
            return "DRY RUN"
        elif self.execute_mode:
            net = "testnet" if self.testnet else "mainnet"
            return f"execute ({net})"
        else:
            return "signal only"

    def _init_strategies(self) -> None:
        """Initialize strategy instances using the strategy registry."""
        self.strategies = create_all_strategies()
        self.logger.info(f"Strategies initialized: {list(self.strategies.keys())}")

    # ========================================
    # Data loading
    # ========================================

    def load_daily_data(self, symbol: str, days: int = 100):
        """
        Fetch daily data via DataProvider.

        Args:
            symbol: Asset symbol (BTC, ETH, etc.).
            days: Number of days to load.

        Returns:
            Daily DataFrame with unclosed candle removed.
        """
        return self.data_provider.get_daily_data(symbol, days)

    # ========================================
    # Signal checking
    # ========================================

    async def check_signals(self) -> List[Dict]:
        """
        Check signals for all symbols (Async Native).

        Returns:
            List of signal dicts.
        """
        signals = []
        now = datetime.now()

        for symbol, strategy in self.strategies.items():
            try:
                signal = await self._check_symbol_signal(symbol, strategy, now)
                if signal:
                    signals.append(signal)
            except Exception as e:
                self.logger.error(f"[{symbol}] Signal check failed: {e}", exc_info=True)

        return signals

    async def _check_symbol_signal(
        self, symbol: str, strategy: Any, now: datetime
    ) -> Optional[Dict]:
        """
        Check signal for a single symbol (Async Native).

        Args:
            symbol: Asset symbol.
            strategy: Strategy instance.
            now: Current time.

        Returns:
            Signal dict or None.
        """
        # Load data
        df = self.load_daily_data(symbol)
        if df is None or len(df) < 50:
            self.logger.warning(f"[{symbol}] Insufficient data, skipping")
            return None

        # Prepare data
        df = strategy.prepare_data(df)
        current = df.iloc[-1]
        strategy_name = get_symbol_config(symbol)["strategy"]

        # Check position state (single source of truth: positions table)
        position = self.executor.get_position(symbol)

        if position:
            return await self._check_exit_signal(
                symbol, strategy, df, position, current, now
            )
        else:
            return await self._check_entry_signal(
                symbol, strategy, df, current, strategy_name, now
            )

    async def _check_exit_signal(
        self, symbol: str, strategy: Any, df, position: Dict, current, now: datetime
    ) -> Optional[Dict]:
        """Check exit signal (Async Native)."""
        entry_price = float(position["entry_price"])
        entry_atr = float(position.get("entry_atr", 0))
        entry_time = position.get("opened_at")

        exit_signal = strategy.check_exit(df, entry_price, entry_atr, entry_time)

        if exit_signal:
            pnl_pct = (exit_signal.price - entry_price) / entry_price * 100

            # Execute mode: call executor to close position
            if self.execute_mode:
                self.executor.execute_exit(
                    symbol=symbol, price=exit_signal.price, reason=exit_signal.reason
                )

            self.logger.info(
                f"[{symbol}] Exit signal: {exit_signal.reason}, PnL={pnl_pct:+.2f}%"
            )

            return {
                "type": "EXIT",
                "symbol": symbol,
                "strategy": get_symbol_config(symbol)["strategy"],
                "price": exit_signal.price,
                "entry_price": entry_price,
                "pnl_pct": pnl_pct,
                "reason": exit_signal.reason,
                "timestamp": now,
                "action": "SELL",
            }

        # No exit signal: update trailing stop
        await self._update_trailing_stop(symbol, strategy, df, position, current)
        return None

    async def _update_trailing_stop(
        self, symbol: str, strategy: Any, df, position: Dict, current
    ) -> None:
        """Update trailing stop (Async Native)."""
        if not strategy.supports_trailing_stop():
            return

        new_stop = strategy.get_trailing_stop(df)
        if not new_stop:
            return

        current_stop = float(
            position.get("current_stop") or position.get("stop_loss") or 0
        )

        if new_stop > current_stop:
            update_result = self.executor.update_trailing_stop(symbol, new_stop)

            if update_result:
                self.logger.info(
                    f"[{symbol}] Trailing stop updated: ${update_result['old_stop']:.2f} -> ${update_result['new_stop']:.2f}"
                )

                # Send trailing stop notification
                current_price = current["close"]
                await self._send_trailing_stop_notification(
                    update_result, current_price
                )

    async def _check_entry_signal(
        self, symbol: str, strategy: Any, df, current, strategy_name: str, now: datetime
    ) -> Optional[Dict]:
        """
        Check entry signal (Saga pattern).

        In execute_mode, entry is executed via Saga, providing:
        - Automatic retry (exponential backoff)
        - State persistence (recoverable)
        - Automatic compensation on failure
        """
        entry_signal = strategy.check_entry(df)

        if not entry_signal:
            return None

        entry_price = float(current["close"])
        entry_atr = float(current["atr"])

        # Funding rate filter (only in execute_mode)
        if self.execute_mode and FEATURE_FLAGS.get("enable_funding_filter", True):
            funding_filter_result = await self._check_funding_rate(symbol)
            if funding_filter_result == "SKIP":
                return None  # funding rate too high, skip entry

        # IV regime filter
        if FEATURE_FLAGS.get("enable_iv_filter", True):
            iv_result = await self._check_iv_regime(symbol)
            if iv_result == "SKIP" and FEATURE_FLAGS.get(
                "enable_iv_filter_blocking", False
            ):
                return None  # IV extreme, skip entry

        # Execute mode: run entry via Saga (limit order)
        if self.execute_mode:
            # Idempotent signal ID: {symbol}:{date}
            signal_id = f"{symbol}:{now.strftime('%Y%m%d')}"

            try:
                result = await execute_trade(
                    symbol=symbol,
                    signal_id=signal_id,
                    side="LONG",
                    signal_type="ENTRY",
                    entry_price=entry_price,
                    atr=entry_atr,
                    strategy_name=strategy_name,
                    use_limit_order=True,
                    testnet=self.testnet,
                )

                if not result:
                    self.logger.error(f"[{symbol}] Saga entry returned empty result")
                    return None

                self.logger.info(
                    f"[{symbol}] Saga entry completed: position_id={result.get('create_order', {}).get('position_id')}"
                )

            except Exception as e:
                self.logger.error(f"[{symbol}] Saga entry failed: {e}", exc_info=True)
                return None

        self.logger.info(
            f"[{symbol}] Entry signal: {entry_signal.reason}, price=${entry_price:.2f}"
        )

        # Calculate stop/TP for Telegram display
        stop_loss = entry_price - entry_atr * 2
        take_profit = strategy.get_fixed_take_profit(entry_price, entry_atr)

        return {
            "type": "ENTRY",
            "symbol": symbol,
            "strategy": strategy_name,
            "price": entry_price,
            "reason": entry_signal.reason,
            "timestamp": now,
            "action": "LONG",
            "stop_loss": stop_loss,
            "take_profit": [take_profit] if take_profit else None,
            "current_price": entry_price,
        }

    # ========================================
    # Funding rate filter
    # ========================================

    async def _check_funding_rate(self, symbol: str) -> str:
        """
        Check funding rate (called before entry).

        Args:
            symbol: Asset symbol (BTC/ETH/BNB/SOL).

        Returns:
            "PASS"   - rate normal, proceed with entry
            "REDUCE" - rate elevated, suggest reducing size (log only for now)
            "SKIP"   - rate too high, skip entry
        """
        try:
            client = self.executor._get_client(symbol)
            if not client:
                self.logger.warning(f"[{symbol}] Cannot get client, skipping funding rate check")
                return "PASS"  # fail-open

            funding_rate = client.get_funding_rate()
            if funding_rate is None:
                self.logger.warning(f"[{symbol}] Get funding rate failed, skipping check")
                return "PASS"  # fail-open

            # Annualized rate (for logging)
            annual_rate = abs(funding_rate) * 3 * 365 * 100

            # For LONG positions, positive funding rate is unfavorable (longs pay shorts)
            # Current strategy is LONG only, so only positive rates matter
            if funding_rate <= 0:
                self.logger.debug(
                    f"[{symbol}] Funding rate: {funding_rate:.4%} (negative, favorable for longs)"
                )
                return "PASS"

            skip_threshold = FUNDING_RATE_CONFIG.get("skip_threshold", 0.001)
            reduce_threshold = FUNDING_RATE_CONFIG.get("reduce_threshold", 0.0007)
            warn_threshold = FUNDING_RATE_CONFIG.get("warn_threshold", 0.0005)

            if funding_rate >= skip_threshold:
                self.logger.warning(
                    f"[{symbol}] Funding rate too high: {funding_rate:.4%} "
                    f"(annualized {annual_rate:.1f}%) >= {skip_threshold:.4%}, skipping entry"
                )
                return "SKIP"

            if funding_rate >= reduce_threshold:
                self.logger.warning(
                    f"[{symbol}] Funding rate elevated: {funding_rate:.4%} "
                    f"(annualized {annual_rate:.1f}%) >= {reduce_threshold:.4%}, suggest reducing size"
                )
                return "REDUCE"

            if funding_rate >= warn_threshold:
                self.logger.info(
                    f"[{symbol}] Funding rate warning: {funding_rate:.4%} "
                    f"(annualized {annual_rate:.1f}%)"
                )

            return "PASS"

        except Exception as e:
            self.logger.error(f"[{symbol}] Funding rate check error: {e}")
            return "PASS"  # fail-open

    # ========================================
    # IV regime filter
    # ========================================

    async def _check_iv_regime(self, symbol: str) -> str:
        """
        Check IV regime (called before entry).

        Args:
            symbol: Asset symbol (BTC/ETH/BNB/SOL).

        Returns:
            "PASS"    - IV normal
            "CAUTION" - IV elevated (log only)
            "SKIP"    - IV extreme (observation mode: log only; blocking mode currently disabled)
        """
        try:
            from src.core.cache import get_cache
            from src.data.deribit_client import DeribitClient
            from src.strategies.swing.iv_filter import IVFilter

            if not hasattr(self, "_iv_filter"):
                client = DeribitClient()
                cache = get_cache()
                self._iv_filter = IVFilter(client, cache)

            return await self._iv_filter.check_iv_regime(symbol)

        except Exception as e:
            self.logger.error(f"[{symbol}] IV regime check error: {e}")
            return "PASS"  # fail-open

    # ========================================
    # Signal push (multi-channel + reply thread) - Async Native
    # ========================================

    async def send_signal_notification(self, signal: Dict) -> None:
        """
        Send signal to all channels (Async Native).

        Entry signal: push to all channels, record telegram_message_id.
        Exit signal: push to all channels, reply to entry message.
        """
        try:
            signal_type = signal.get("type", "ENTRY")
            symbol = signal.get("symbol", "UNKNOWN")

            # Exit signal: get entry message ID for reply thread
            reply_to_message_id = None
            if signal_type == "EXIT" and self.execute_mode:
                reply_to_message_id = self._get_telegram_message_id(symbol)

            result = await self.notification_manager.send_signal(
                signal, reply_to_message_id
            )

            # Entry signal: save telegram_message_id to position
            if signal_type == "ENTRY" and self.execute_mode:
                telegram_msg_id = result.get("telegram_message_id")
                if telegram_msg_id:
                    self._save_telegram_message_id(symbol, telegram_msg_id)

        except Exception as e:
            self.logger.error(f"Signal push failed: {e}", exc_info=True)

    async def _send_trailing_stop_notification(
        self, update_data: Dict, current_price: float
    ) -> None:
        """Send trailing stop update notification (Telegram VIP only, reply to entry message)."""
        try:
            symbol = update_data.get("symbol", "UNKNOWN")

            reply_to_message_id = None
            if self.execute_mode:
                reply_to_message_id = self._get_telegram_message_id(symbol)

            await self.notification_manager.send_trailing_stop_update(
                update_data, current_price, reply_to_message_id
            )
        except Exception as e:
            self.logger.error(f"Trailing stop notification failed: {e}", exc_info=True)

    def _get_telegram_message_id(self, symbol: str) -> Optional[int]:
        """Get the Telegram entry message ID for a position."""
        try:
            return self.executor.get_telegram_message_id(symbol)
        except Exception as e:
            self.logger.warning(f"Get Telegram message ID failed: {e}")
            return None

    def _save_telegram_message_id(self, symbol: str, telegram_msg_id: int) -> None:
        """Save Telegram message ID to position."""
        try:
            if self.executor.update_telegram_message_id(symbol, telegram_msg_id):
                self.logger.info(f"[{symbol}] Telegram message ID saved: {telegram_msg_id}")
        except Exception as e:
            self.logger.warning(f"Save Telegram message ID failed: {e}")

    # ========================================
    # Scheduled tasks
    # ========================================

    async def _daily_check(self) -> None:
        """Daily signal check task (Async Native)."""
        self.logger.info("=" * 60)
        self.logger.info("Swing daily signal check starting")
        self.logger.info("=" * 60)

        try:
            # Step 1: process timed-out PENDING orders (not filled in 24h)
            if self.execute_mode:
                timeout_results = self.executor.process_pending_timeout()
                if timeout_results:
                    for r in timeout_results:
                        self.logger.info(f"  Timeout handled: {r['symbol']} -> {r['action']}")

            # Step 1.5: state reconciliation
            await self._reconcile_positions()

            # Step 2: check signals
            signals = await self.check_signals()

            # Step 3: log status
            self._log_current_status()

            if signals:
                self.logger.info(f"Generated {len(signals)} signal(s)")
                for sig in signals:
                    price = sig.get("price", 0)
                    self.logger.info(f"  {sig['type']}: {sig['symbol']} @ ${price:.2f}")

                    await self.send_signal_notification(sig)

            self.logger.info("Swing daily check complete")

        except Exception as e:
            self.logger.error(f"Daily check failed: {e}", exc_info=True)

    def _hourly_check(self) -> None:
        """
        Hourly check task (synchronous, no notifications).

        PENDING order check and position status query run independently of execute_mode:
        - PENDING order check: must run regardless of mode once orders are placed
        - Position status query: basic function, always runs
        """
        now = datetime.now()

        # Step 1: check PENDING order fill status (independent of execute_mode)
        try:
            results = self.executor.check_pending_orders()
            if results:
                for r in results:
                    self.logger.info(
                        f"[PENDING] {r['symbol']} -> {r['action']}, "
                        f"price: ${r.get('price', 0):.2f}"
                    )
        except Exception as e:
            self.logger.error(f"Check PENDING orders failed: {e}")

        # Step 2: heartbeat log
        positions = self.executor.get_all_positions()
        pending_positions = []
        try:
            pending_positions = self.executor.get_pending_positions()
        except Exception as e:
            self.logger.debug(f"Get PENDING positions failed: {e}")

        position_info = ""
        if positions:
            symbols = [p["symbol"].replace("USDT", "") for p in positions]
            position_info = f" OPEN: {', '.join(symbols)}"

        pending_info = ""
        if pending_positions:
            symbols = [p["symbol"].replace("USDT", "") for p in pending_positions]
            pending_info = f" PENDING: {', '.join(symbols)}"

        self.logger.info(
            f"[HEARTBEAT] {now.strftime('%Y-%m-%d %H:%M')} |{position_info}{pending_info}"
        )

    def _log_current_status(self) -> None:
        """Log current position status."""
        positions = self.executor.get_all_positions()
        if positions:
            for p in positions:
                symbol = p["symbol"].replace("USDT", "")
                entry = float(p["entry_price"])
                stop = float(p.get("current_stop") or p.get("stop_loss") or 0)
                self.logger.info(f"  Position: {symbol} @ ${entry:.2f}, stop: ${stop:.2f}")
        else:
            self.logger.info("  No open positions")

    # ========================================
    # State reconciliation - Async Native
    # ========================================

    def _query_sl_order_status(self, symbol: str, sl_order_id: str) -> Optional[str]:
        """
        Query stop-loss order status (lazy evaluation).

        Args:
            symbol: Asset symbol (BTC/ETH/BNB/SOL).
            sl_order_id: Stop-loss order ID.

        Returns:
            Binance raw status: FILLED/CANCELED/EXPIRED/REJECTED/NEW/PARTIALLY_FILLED
            None on query failure.
        """
        return self.executor.query_order_status(symbol, sl_order_id)

    def _close_db_position(
        self,
        symbol: str,
        position: Dict,
        reason: str,
        exit_price: Optional[float] = None,
    ) -> None:
        """
        Close a DB position and clean up.

        Args:
            symbol: Asset symbol.
            position: Position info.
            reason: Close reason.
            exit_price: Exit price (defaults to stop price).
        """
        position_id = position["id"]
        if exit_price is None:
            exit_price = float(
                position.get("current_stop") or position.get("stop_loss") or 0
            )

        # Close position
        self.executor.close_position_by_id(
            position_id=position_id, exit_price=exit_price, reason=reason
        )

        # Clear stop-loss order ID
        self.executor.clear_sl_order_by_position_id(position_id)

        # Calculate P/L
        entry_price = float(position["entry_price"])
        pnl_pct = (exit_price - entry_price) / entry_price * 100

        self.logger.info(
            f"[{symbol}] Reconciliation close - reason: {reason}, "
            f"exit: ${exit_price:.2f}, pnl: {pnl_pct:+.2f}%"
        )

    async def _send_critical_alert(self, message: str) -> None:
        """
        Send a critical alert (for situations requiring human intervention).

        Args:
            message: Alert message.
        """
        self.logger.error(f"[CRITICAL ALERT] {message}")
        try:
            await self.notification_manager.send_signal(
                {
                    "type": "ALERT",
                    "symbol": "SYSTEM",
                    "reason": message,
                    "strategy": "reconcile",
                }
            )
        except Exception as e:
            self.logger.error(f"Send critical alert failed: {e}")

    async def _reconcile_positions(self) -> None:
        """
        State reconciliation: check consistency between DB and exchange positions.

        Order-State-Resolution-Logic:
        - Query real sl_order_id status before closing DB position
        - Decide whether to close based on order status to prevent API glitch false positives

        Decision tree:
        - FILLED           -> close DB (stop triggered)
        - CANCELED         -> close DB (manually closed)
        - EXPIRED/REJECTED -> close DB (abnormal) + critical alert
        - PARTIALLY_FILLED -> keep DB OPEN (waiting for full fill)
        - NEW/ACTIVE       -> keep DB OPEN (API glitch)
        - Other/query fail -> keep DB OPEN + critical alert
        """
        self.logger.info("Starting state reconciliation...")

        try:
            for symbol in self.strategies.keys():
                # Check if DB has an OPEN position
                position = self.executor.get_position(symbol)
                if not position:
                    continue

                position_id = position["id"]
                db_symbol = position["symbol"]  # BTCUSDT

                # Query Binance actual positions
                binance_positions = self.executor.get_exchange_positions(symbol)

                # Find position for this symbol
                # BinanceTradingClient.get_positions() returns normalized format
                # Use 'contracts' rather than raw 'positionAmt'
                actual_qty = 0.0
                for bp in binance_positions:
                    if bp.get("symbol") == db_symbol:
                        actual_qty = abs(
                            float(bp.get("contracts") or bp.get("positionAmt") or 0)
                        )
                        break

                # DB has position but Binance shows empty -> needs verification
                if actual_qty < 0.0001:  # essentially zero
                    self.logger.warning(
                        f"[{symbol}] Reconciliation discrepancy - "
                        f"DB: OPEN (id={position_id}), Binance: empty"
                    )

                    sl_order_id = position.get("sl_order_id")

                    # Case 0: no stop-loss order ID -> assume manual close
                    if not sl_order_id:
                        self.logger.warning(f"[{symbol}] No SL order ID, assuming manual close")
                        self._close_db_position(symbol, position, "Manual/Unknown")
                        continue

                    # Query order status (lazy evaluation)
                    status = self._query_sl_order_status(symbol, sl_order_id)
                    self.logger.info(
                        f"[{symbol}] SL order status query: {sl_order_id} -> {status}"
                    )

                    if status == "FILLED":
                        # Normal stop-loss triggered
                        self._close_db_position(symbol, position, "StopLoss")
                        # Send notification
                        entry_price = float(position["entry_price"])
                        stop_price = float(
                            position.get("current_stop")
                            or position.get("stop_loss")
                            or 0
                        )
                        pnl_pct = (stop_price - entry_price) / entry_price * 100
                        await self.notification_manager.send_signal(
                            {
                                "type": "EXIT",
                                "symbol": symbol,
                                "price": stop_price,
                                "reason": "STOP_LOSS_TRIGGERED",
                                "pnl_pct": pnl_pct,
                                "strategy": position.get("strategy_name", "v9"),
                            }
                        )

                    elif status == "CANCELED":
                        # User manually cancelled order and closed position
                        self.logger.info(f"[{symbol}] SL cancelled, assuming manual close")
                        self._close_db_position(symbol, position, "Manual_SL_Cancel")

                    elif status in ["EXPIRED", "REJECTED"]:
                        # Abnormal close
                        self._close_db_position(symbol, position, f"Order_{status}")
                        await self._send_critical_alert(
                            f"[{symbol}] Stop-loss order status abnormal: {status}, DB closed, please verify assets!"
                        )

                    elif status == "PARTIALLY_FILLED":
                        # Stop in progress, waiting for full fill
                        self.logger.info(
                            f"[{symbol}] SL executing (PARTIALLY_FILLED), waiting for full fill"
                        )
                        # Keep DB OPEN

                    elif status in ["NEW", "ACTIVE"]:
                        # API glitch: exchange position shows 0 but order still active
                        self.logger.warning(
                            f"[{symbol}] API Glitch detected: Pos=0 but Order={status}. "
                            f"Keeping DB OPEN, ignoring this reconciliation."
                        )
                        # Keep DB OPEN

                    else:
                        # Unknown status or query failed
                        self.logger.error(f"[{symbol}] Reconciliation: unknown order status: {status}")
                        await self._send_critical_alert(
                            f"[{symbol}] Reconciliation: unknown order status: {status}, please investigate manually!"
                        )
                        # Keep DB OPEN (conservative)

            self.logger.info("State reconciliation complete")

        except Exception as e:
            self.logger.error(f"State reconciliation failed: {e}", exc_info=True)

    # ========================================
    # Task scheduling (Async Native)
    # ========================================

    def _schedule_cron_task(self, task_type: str) -> None:
        """
        Mark a cron task for execution (called by schedule library).

        The schedule library calls this method only to mark the task; actual
        execution happens in the main loop's _process_cron_tasks().
        """
        self._pending_cron_tasks.append(task_type)
        self.logger.debug(f"Cron task scheduled: {task_type}")

    async def _process_cron_tasks(self) -> None:
        """Process pending cron tasks (Async Native)."""
        while self._pending_cron_tasks:
            task = self._pending_cron_tasks.pop(0)
            try:
                if task == "daily_check":
                    await self._daily_check()
                self.logger.info(f"Cron task completed: {task}")
            except Exception as e:
                self.logger.error(f"Cron task {task} failed: {e}", exc_info=True)

    def _should_run(self, task_name: str, interval_min: int) -> bool:
        """Check whether an interval task should run."""
        last_run = self._last_run_times.get(task_name)
        if last_run is None:
            return True
        elapsed = (datetime.now() - last_run).total_seconds() / 60
        return elapsed >= interval_min

    def _mark_run(self, task_name: str) -> None:
        """Record the last run time for a task."""
        self._last_run_times[task_name] = datetime.now()

    async def _execute_interval_tasks(self) -> None:
        """Execute all interval tasks (with independent cooldown)."""
        # Hourly check (PENDING orders + heartbeat)
        if self._should_run("hourly", interval_min=self.HOURLY_INTERVAL_MIN):
            try:
                self._hourly_check()
                self._mark_run("hourly")
            except Exception as e:
                self.logger.error(f"Hourly task failed: {e}", exc_info=True)

        # 4-hour reconciliation
        if self._should_run("reconcile", interval_min=self.RECONCILE_INTERVAL_MIN):
            try:
                await self._reconcile_positions()
                self._mark_run("reconcile")
            except Exception as e:
                self.logger.error(f"Reconcile task failed: {e}", exc_info=True)

    # ========================================
    # Saga recovery
    # ========================================

    async def _recover_pending_sagas(self) -> None:
        """
        Recover incomplete Saga instances on startup.

        Scenarios:
        - Process crash and restart
        - Container rescheduled
        - Manual kill and recovery
        """
        try:
            # Register trading saga (ensure definition exists for recovery)
            register_trading_saga()

            orchestrator = get_orchestrator()
            results = await orchestrator.recover_pending_sagas(max_age_hours=24)

            if results:
                recovered = sum(1 for r in results if r["status"] == "recovered")
                failed = sum(1 for r in results if r["status"] == "failed")
                self.logger.warning(f"[Saga recovery] Recovered: {recovered}, failed: {failed}")

                for r in results:
                    if r["status"] == "recovered":
                        self.logger.info(
                            f"  [Recovered] {r['saga_type']}: {r['saga_id']}"
                        )
                    else:
                        self.logger.error(
                            f"  [Failed] {r['saga_type']}: {r['saga_id']} - {r.get('error')}"
                        )
            else:
                self.logger.info("[Saga recovery] No pending instances")

        except Exception as e:
            self.logger.error(f"[Saga recovery] Recovery error: {e}", exc_info=True)
            # Recovery failure does not block startup

    # ========================================
    # Run entry points (Async Native)
    # ========================================

    def run(self) -> None:
        """Start the scheduler (synchronous entry point)."""
        asyncio.run(self.start())

    async def start(self) -> None:
        """Start the scheduler (asynchronous entry point)."""
        if self._running:
            self.logger.warning("Scheduler already running")
            return

        self._running = True
        self.logger.info("Swing Scheduler starting (Async Native)...")

        # Start WebSocket connections if enabled
        if self._use_websocket and hasattr(self.executor, "start_websockets"):
            await self.executor.start_websockets()

        try:
            await self._run_scheduler_loop()
        except asyncio.CancelledError:
            self.logger.info("Scheduler cancelled")
        finally:
            # Stop WebSocket connections
            if self._use_websocket and hasattr(self.executor, "stop_websockets"):
                await self.executor.stop_websockets()
            self._running = False
            self.logger.info("Swing Scheduler stopped")

    async def stop(self) -> None:
        """Stop the scheduler."""
        if self._use_websocket and hasattr(self.executor, "stop_websockets"):
            await self.executor.stop_websockets()
        self._running = False

    async def _run_scheduler_loop(self) -> None:
        """
        Main scheduler loop (Async Native).

        Architecture:
        - schedule library used only as cron trigger
        - Main loop is asyncio-driven, non-blocking
        - Exception containment prevents boot-loop
        """
        self.logger.info("=" * 60)
        self.logger.info("Swing Strategy Scheduler starting")
        self.logger.info(f"Mode: {self._get_mode_string()}")
        self.logger.info("=" * 60)

        # 0. Saga recovery (in execute_mode)
        if self.execute_mode:
            await self._recover_pending_sagas()

        # 1. Register cron triggers (lambda just marks tasks, does not execute)
        # UTC 00:01 = CST 08:01
        schedule.every().day.at("08:01").do(
            lambda: self._schedule_cron_task("daily_check")
        )
        self.logger.info("Registered: daily signal check at 08:01 CST (00:01 UTC)")

        # 2. Warm-up
        await asyncio.sleep(2)
        self.logger.info("Warm-up complete, starting main loop...")

        # 3. Initial heartbeat
        self._hourly_check()
        self._mark_run("hourly")

        # 4. Main loop
        while self._running:
            try:
                # A. Trigger cron checks (sync, very fast, only appends to list)
                schedule.run_pending()

                # B. Consume and execute cron tasks
                if self._pending_cron_tasks:
                    await self._process_cron_tasks()

                # C. Interval tasks
                await self._execute_interval_tasks()

            except asyncio.CancelledError:
                self.logger.info("Scheduler loop cancelled")
                break
            except Exception as e:
                self.logger.error(f"Critical Scheduler Loop Error: {e}", exc_info=True)
                await asyncio.sleep(30)  # cooldown
                continue

            # Non-blocking interval (check every minute)
            await asyncio.sleep(60)

    async def run_once(self) -> List[Dict]:
        """Run one signal check (for testing) - Async Native."""
        return await self.check_signals()

    def force_entry(self, symbol: str) -> Optional[Dict]:
        """
        Force entry execution (for end-to-end testing).

        Reuses production logic: load data -> compute ATR -> execute entry.
        Skips signal detection, goes directly to order placement.

        Args:
            symbol: Asset symbol (BTC/ETH/BNB/SOL).

        Returns:
            Success: executor.execute_entry result.
            Failure: None.
        """
        strategy = self.strategies.get(symbol)
        if not strategy:
            self.logger.error(f"[{symbol}] Strategy not found")
            return None

        # Step 1: load data
        df = self.load_daily_data(symbol)
        if df is None or len(df) < 50:
            self.logger.error(f"[{symbol}] Data load failed or insufficient")
            return None

        # Step 2: compute ATR via strategy
        df = strategy.prepare_data(df)
        current = df.iloc[-1]

        close_price = float(current["close"])
        atr = float(current["atr"])
        strategy_name = get_symbol_config(symbol)["strategy"]

        self.logger.info(
            f"[{symbol}] Force entry - price: ${close_price:,.2f}, "
            f"ATR: ${atr:,.2f} ({atr / close_price * 100:.2f}%)"
        )

        # Step 3: execute entry
        result = self.executor.execute_entry(
            symbol=symbol, price=close_price, atr=atr, strategy_name=strategy_name
        )

        return result

    def get_status(self) -> Dict:
        """Return current status."""
        positions = self.executor.get_all_positions()
        return {
            "mode": self._get_mode_string(),
            "testnet": self.testnet,
            "dry_run": self.dry_run,
            "execute_mode": self.execute_mode,
            "strategies": list(self.strategies.keys()),
            "positions": [
                {
                    "symbol": p["symbol"].replace("USDT", ""),
                    "entry_price": float(p["entry_price"]),
                    "current_stop": float(
                        p.get("current_stop") or p.get("stop_loss") or 0
                    ),
                    "opened_at": str(p.get("opened_at", "")),
                }
                for p in positions
            ],
        }


async def _cleanup_bot_session():
    """Clean up Telegram Bot session to avoid unclosed client session warnings."""
    from src.notifications.telegram_app import close_bot

    await close_bot()


def main():
    """Main entry point."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Swing strategy scheduler")
    parser.add_argument("--run-now", action="store_true", help="Run one check immediately")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument(
        "--execute", action="store_true", help="Enable execute mode (live trading)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Dry-run mode (log only, no orders)"
    )
    parser.add_argument(
        "--testnet", action="store_true", default=True, help="Use testnet (default)"
    )
    parser.add_argument("--mainnet", action="store_true", help="Use mainnet (production)")
    parser.add_argument(
        "--no-websocket",
        action="store_true",
        help="Disable WebSocket (enabled by default, for debugging only)",
    )
    parser.add_argument("--test-notify", action="store_true", help="Test all notification channels")
    parser.add_argument(
        "--test-entry",
        type=str,
        metavar="SYMBOL",
        help="Force entry test (BTC/ETH/BNB/SOL), for end-to-end testing",
    )
    args = parser.parse_args()

    # Configure logging
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/swing_scheduler.log", encoding="utf-8"),
        ],
    )

    testnet = not args.mainnet

    scheduler = SwingScheduler(
        execute_mode=args.execute,
        testnet=testnet,
        dry_run=args.dry_run,
        use_websocket=not args.no_websocket,
    )

    if args.status:
        status = scheduler.get_status()
        status["notifications"] = scheduler.notification_manager.get_status()
        print(json.dumps(status, indent=2, default=str, ensure_ascii=False))
        return

    if args.test_notify:
        print("Testing all notification channels...")

        async def _test_and_cleanup():
            try:
                return await scheduler.notification_manager.test_all_channels()
            finally:
                await _cleanup_bot_session()

        results = asyncio.run(_test_and_cleanup())
        print("\nTest results:")
        for channel, success in results.items():
            status_str = "success" if success else "failed/not configured"
            print(f"  {channel}: {status_str}")
        return

    if args.test_entry:
        symbol = args.test_entry.upper()
        supported = get_supported_symbols()
        if symbol not in supported:
            print(f"[ERROR] Unsupported symbol: {symbol}, supported: {supported}")
            return

        if not args.execute:
            print("[ERROR] --test-entry requires --execute")
            print(
                "Example: python -m src.strategies.swing.scheduler --test-entry BTC --execute --testnet"
            )
            return

        print("=" * 60)
        print(f"Force entry test: {symbol}")
        print(
            f"Network: {'testnet' if testnet else 'mainnet'}, mode: {'DRY RUN' if args.dry_run else 'live'}"
        )
        print("=" * 60)

        result = scheduler.force_entry(symbol)

        if result is None:
            print("\n[RESULT] Returned None (position exists / insufficient balance / risk check failed / data load failed)")
        else:
            print("\n[RESULT] Entry succeeded:")
            print(f"  position_id: {result.get('position_id')}")
            print(f"  fill price: ${result.get('price', 0):,.2f}")
            print(f"  quantity: {result.get('quantity')}")
            print(f"  stop: ${result.get('stop_loss', 0):,.2f}")
            print(f"  leverage: {result.get('leverage')}x")
            print(f"  SL order: {result.get('sl_order_id')}")

            check = (
                "PASS"
                if result.get("stop_loss", 0) < result.get("price", 0)
                else "FAIL"
            )
            print(f"\n[CHECK] stop < entry price: {check}")

        print("\n[Position Status]")
        positions = scheduler.executor.get_all_positions()
        if positions:
            for p in positions:
                sym = p["symbol"].replace("USDT", "")
                print(
                    f"  {sym}: entry=${float(p['entry_price']):,.2f}, stop=${float(p.get('current_stop', 0)):,.2f}"
                )
        else:
            print("  No positions")

        print("=" * 60)

        asyncio.run(_cleanup_bot_session())
        return

    if args.run_now:

        async def _run_once_and_cleanup():
            try:
                return await scheduler.run_once()
            finally:
                await _cleanup_bot_session()

        signals = asyncio.run(_run_once_and_cleanup())
        print(f"Detected {len(signals)} signal(s):")
        for sig in signals:
            print(f"  {sig['type']}: {sig['symbol']} @ ${sig.get('price', 0):.2f}")
        return

    # Normal scheduler startup
    scheduler.run()


if __name__ == "__main__":
    main()
