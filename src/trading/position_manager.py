"""
Position manager.

Responsibilities:
1. Position lifecycle management (open, close, partial close)
2. Position query and PnL calculation
3. Trailing stop data management
"""

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional

from src.core.database import get_db


class PositionManager:
    """Position manager (CRUD core)."""

    def __init__(
        self,
        trading_client=None,
        host: str = None,  # deprecated, kept for backward compatibility
        port: int = None,  # deprecated, kept for backward compatibility
        user: str = None,  # deprecated, kept for backward compatibility
        password: str = None,  # deprecated, kept for backward compatibility
        database: str = None,  # deprecated, kept for backward compatibility
        **_,  # absorb other deprecated params
    ):
        """
        Initialize position manager.

        Args:
            trading_client: BinanceTradingClient instance (optional, for reconciliation)

        Note:
            host/port/user/password/database params are deprecated, kept for backward compatibility.
            Internally uses DatabasePool connection pool.
        """
        self.logger = logging.getLogger(__name__)
        self.trading_client = trading_client
        self._db_pool = get_db()

        # Sub-modules (lazy initialization)
        self._trailing_manager = None

        self.logger.debug("PositionManager initialized (connection pool mode)")

    @contextmanager
    def _get_connection_ctx(self):
        """
        Context manager for database connection.

        Ensures connection is returned to pool under all conditions (including exceptions).

        Usage:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                connection.commit()
        """
        conn = self._db_pool.get_connection()
        try:
            yield conn
        finally:
            conn.close()  # return to pool

    def _get_trailing_manager(self):
        """Get trailing stop manager (lazy initialization)."""
        if self._trailing_manager is None:
            from .trailing_stop_manager import TrailingStopManager

            self._trailing_manager = TrailingStopManager()
        return self._trailing_manager

    # ========================================
    # CRUD core methods
    # ========================================

    def open_position(
        self,
        symbol: str,
        side: str,
        entry_signal_id: int,
        entry_order_id: int,
        entry_price: float,
        quantity: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float,
        strategy_name: str = None,
        testnet: bool = True,
        # Trailing stop support
        stop_type: str = "FIXED",
        trailing_period: int = None,
        trailing_mult: float = None,
        entry_atr: float = None,
    ) -> Optional[int]:
        """
        Create position record.

        Args:
            symbol: Token symbol (ETH/SOL/BNB/BTC)
            side: LONG/SHORT
            entry_signal_id: Entry signal ID
            entry_order_id: Entry order ID
            entry_price: Entry price
            quantity: Position size
            stop_loss: Stop loss price
            take_profit_1: First take profit price
            take_profit_2: Second take profit price
            strategy_name: Strategy name
            testnet: Whether using testnet
            stop_type: Stop type FIXED/TRAILING_LOWEST/TRAILING_ATR
            trailing_period: N-day low period
            trailing_mult: ATR multiplier
            entry_atr: ATR at entry

        Returns:
            Position ID, None on failure
        """
        try:
            current_stop = stop_loss

            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        INSERT INTO positions (
                            symbol, side, entry_signal_id, entry_order_id,
                            entry_price, quantity, stop_loss,
                            stop_type, trailing_period, trailing_mult,
                            current_stop, entry_atr, highest_since_entry,
                            take_profit_1, take_profit_2,
                            status, testnet, strategy_name, opened_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """

                    cursor.execute(
                        sql,
                        (
                            symbol,
                            side,
                            entry_signal_id,
                            entry_order_id,
                            entry_price,
                            quantity,
                            stop_loss,
                            stop_type,
                            trailing_period,
                            trailing_mult,
                            current_stop,
                            entry_atr,
                            entry_price,
                            take_profit_1,
                            take_profit_2,
                            "OPEN",
                            testnet,
                            strategy_name,
                            datetime.now(),
                        ),
                    )

                    position_id = cursor.lastrowid

                connection.commit()

            stop_info = (
                f"SL: ${stop_loss}"
                if stop_type == "FIXED"
                else f"Trailing({stop_type}): ${current_stop}"
            )
            self.logger.info(
                f"Position opened - ID: {position_id}, "
                f"{symbol} {side} {quantity} @ ${entry_price}, "
                f"{stop_info}, TP1: ${take_profit_1}, TP2: ${take_profit_2}"
            )

            return position_id

        except Exception as e:
            self.logger.error(f"Failed to create position record: {e}")
            return None

    def close_position(
        self,
        position_id: int,
        exit_order_id: int,
        exit_price: float,
        exit_reason: str,
        exit_signal_id: Optional[int] = None,
    ) -> bool:
        """
Close position (full close or second take profit).

        Args:
            position_id: Position ID
            exit_order_id: Exit order ID
            exit_price: Exit price
            exit_reason: Exit reason
            exit_signal_id: Exit signal ID

        Returns:
            Whether succeeded
        """
        try:
            position = self.get_position_by_id(position_id)
            if not position:
                self.logger.error(f"Position not found: {position_id}")
                return False

            entry_price = float(position["entry_price"])
            quantity = float(position["quantity"])
            side = position["side"]

            if position["status"] == "PARTIAL_CLOSED":
                partial_qty = float(position.get("partial_exit_1_quantity", 0))
                quantity = quantity - partial_qty

            realized_pnl, realized_pnl_percent = self._calculate_pnl(
                entry_price, exit_price, quantity, side
            )

            if position.get("realized_pnl"):
                realized_pnl += float(position["realized_pnl"])
                total_quantity = float(position["quantity"])
                total_entry_value = entry_price * total_quantity
                realized_pnl_percent = (realized_pnl / total_entry_value) * 100

            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        UPDATE positions SET
                            exit_signal_id = %s,
                            exit_order_id = %s,
                            exit_price = %s,
                            exit_reason = %s,
                            status = 'CLOSED',
                            realized_pnl = %s,
                            realized_pnl_percent = %s,
                            closed_at = %s
                        WHERE id = %s
                    """

                    cursor.execute(
                        sql,
                        (
                            exit_signal_id,
                            exit_order_id,
                            exit_price,
                            exit_reason,
                            realized_pnl,
                            realized_pnl_percent,
                            datetime.now(),
                            position_id,
                        ),
                    )

                connection.commit()

            self.logger.info(
                f"Position closed - ID: {position_id}, "
                f"reason: {exit_reason}, exit_price: ${exit_price}, "
                f"pnl: ${realized_pnl:.2f} ({realized_pnl_percent:+.2f}%)"
            )

            return True

        except Exception as e:
            self.logger.error(f"Position close failed: {e}")
            return False

    def partial_close_position(
        self,
        position_id: int,
        exit_order_id: int,
        exit_price: float,
        exit_quantity: float,
    ) -> bool:
        """
Partial close position (first take profit, 50%).

        Args:
            position_id: Position ID
            exit_order_id: Exit order ID
            exit_price: Exit price
            exit_quantity: Exit quantity

        Returns:
            Whether succeeded
        """
        try:
            position = self.get_position_by_id(position_id)
            if not position:
                self.logger.error(f"Position not found: {position_id}")
                return False

            entry_price = float(position["entry_price"])
            side = position["side"]

            partial_pnl, partial_pnl_percent = self._calculate_pnl(
                entry_price, exit_price, exit_quantity, side
            )

            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        UPDATE positions SET
                            partial_exit_1_order_id = %s,
                            partial_exit_1_price = %s,
                            partial_exit_1_quantity = %s,
                            partial_exit_1_at = %s,
                            status = 'PARTIAL_CLOSED',
                            realized_pnl = %s,
                            realized_pnl_percent = %s
                        WHERE id = %s
                    """

                    cursor.execute(
                        sql,
                        (
                            exit_order_id,
                            exit_price,
                            exit_quantity,
                            datetime.now(),
                            partial_pnl,
                            partial_pnl_percent,
                            position_id,
                        ),
                    )

                connection.commit()

            self.logger.info(
                f"Partial close success - ID: {position_id}, "
                f"exit_price: ${exit_price}, qty: {exit_quantity}, "
                f"pnl: ${partial_pnl:.2f} ({partial_pnl_percent:+.2f}%)"
            )

            return True

        except Exception as e:
            self.logger.error(f"Partial close failed: {e}")
            return False

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """
Query open positions.

        Args:
            symbol: Token filter (optional)

        Returns:
            List of positions
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    if symbol:
                        sql = """
                            SELECT * FROM positions
                            WHERE symbol = %s AND status IN ('OPEN', 'PARTIAL_CLOSED')
                            ORDER BY opened_at DESC
                        """
                        cursor.execute(sql, (symbol,))
                    else:
                        sql = """
                            SELECT * FROM positions
                            WHERE status IN ('OPEN', 'PARTIAL_CLOSED')
                            ORDER BY opened_at DESC
                        """
                        cursor.execute(sql)

                    positions = cursor.fetchall()

            return positions

        except Exception as e:
            self.logger.error(f"Failed to query positions: {e}")
            return []

    def get_position_by_id(self, position_id: int) -> Optional[Dict]:
        """
Query position by ID.

        Args:
            position_id: Position ID

        Returns:
            Position dict, None if not found
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = "SELECT * FROM positions WHERE id = %s"
                    cursor.execute(sql, (position_id,))
                    position = cursor.fetchone()

            return position

        except Exception as e:
            self.logger.error(f"Failed to query position: {e}")
            return None

    def get_closed_trades(
        self,
        year: Optional[int] = None,
        limit: int = 50,
        strategy_name: Optional[str] = None,
    ) -> List[Dict]:
        """
Query closed trade records (for performance display).

        Args:
            year: Filter by year (e.g. 2025), None for no filter
            limit: Return count limit
            strategy_name: Strategy name filter (e.g. 'swing-ensemble'), None for no filter

        Returns:
            Trade records sorted by close time descending
            Each record contains: symbol, side, entry_price, exit_price,
                                  realized_pnl_percent, closed_at, exit_reason
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        SELECT
                            symbol, side, entry_price, exit_price,
                            realized_pnl, realized_pnl_percent,
                            opened_at, closed_at, exit_reason, strategy_name
                        FROM positions
                        WHERE status = 'CLOSED'
                    """
                    params = []

                    if year:
                        sql += " AND YEAR(closed_at) = %s"
                        params.append(year)

                    if strategy_name:
                        sql += " AND strategy_name = %s"
                        params.append(strategy_name)

                    sql += " ORDER BY closed_at ASC LIMIT %s"
                    params.append(limit)

                    cursor.execute(sql, tuple(params))
                    trades = cursor.fetchall()

            return trades or []

        except Exception as e:
            self.logger.error(f"Failed to query closed trades: {e}")
            return []

    def get_trade_stats(
        self, year: Optional[int] = None, strategy_name: Optional[str] = None
    ) -> Dict:
        """
Calculate trade statistics (for performance display).

        Args:
            year: Filter by year
            strategy_name: Strategy name filter

        Returns:
            {
                'total_trades': int,
                'winners': int,
                'losers': int,
                'win_rate': float,
                'avg_win': float,
                'avg_loss': float,
                'profit_factor': float
            }
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        SELECT
                            COUNT(*) as total,
                            SUM(CASE WHEN realized_pnl_percent > 0 THEN 1 ELSE 0 END) as wins,
                            SUM(CASE WHEN realized_pnl_percent <= 0 THEN 1 ELSE 0 END) as losses,
                            AVG(CASE WHEN realized_pnl_percent > 0
                                THEN realized_pnl_percent ELSE NULL END) as avg_win,
                            AVG(CASE WHEN realized_pnl_percent < 0
                                THEN realized_pnl_percent ELSE NULL END) as avg_loss,
                            SUM(CASE WHEN realized_pnl_percent > 0
                                THEN realized_pnl_percent ELSE 0 END) as total_win,
                            SUM(CASE WHEN realized_pnl_percent < 0
                                THEN ABS(realized_pnl_percent) ELSE 0 END) as total_loss
                        FROM positions
                        WHERE status = 'CLOSED'
                    """
                    params = []

                    if year:
                        sql += " AND YEAR(closed_at) = %s"
                        params.append(year)

                    if strategy_name:
                        sql += " AND strategy_name = %s"
                        params.append(strategy_name)

                    cursor.execute(sql, tuple(params))
                    row = cursor.fetchone()

            if not row or not row["total"]:
                return {
                    "total_trades": 0,
                    "winners": 0,
                    "losers": 0,
                    "win_rate": 0.0,
                    "avg_win": 0.0,
                    "avg_loss": 0.0,
                    "profit_factor": 0.0,
                }

            total = int(row["total"])
            wins = int(row["wins"] or 0)
            losses = int(row["losses"] or 0)
            win_rate = (wins / total * 100) if total > 0 else 0.0
            avg_win = float(row["avg_win"] or 0)
            avg_loss = float(row["avg_loss"] or 0)
            total_win = float(row["total_win"] or 0)
            total_loss = float(row["total_loss"] or 0)
            pf = (total_win / total_loss) if total_loss > 0 else 0.0

            # Calculate risk/reward ratio (avg_rr = avg_win / |avg_loss|)
            avg_rr = (avg_win / abs(avg_loss)) if avg_loss < 0 else 0.0

            return {
                "total_trades": total,
                "winners": wins,
                "losers": losses,
                "win_rate": round(win_rate, 1),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "profit_factor": round(pf, 2),
                "avg_rr": round(avg_rr, 1),
                "max_drawdown": 10.6,  # Swing strategy backtest max drawdown
            }

        except Exception as e:
            self.logger.error(f"Failed to calculate trade statistics: {e}")
            return {
                "total_trades": 0,
                "winners": 0,
                "losers": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "avg_rr": 0.0,
                "max_drawdown": 0.0,
            }

    def calculate_unrealized_pnl(self, position: Dict, current_price: float) -> tuple:
        """
Calculate unrealized PnL.

        Args:
            position: Position dict
            current_price: Current price

        Returns:
            (unrealized PnL USD, unrealized PnL percent)
        """
        entry_price = float(position["entry_price"])
        quantity = float(position["quantity"])
        side = position["side"]

        if position["status"] == "PARTIAL_CLOSED":
            partial_qty = float(position.get("partial_exit_1_quantity", 0))
            quantity = quantity - partial_qty

        return self._calculate_pnl(entry_price, current_price, quantity, side)

    def _calculate_pnl(
        self, entry_price: float, exit_price: float, quantity: float, side: str
    ) -> tuple:
        """
Calculate PnL.

        Args:
            entry_price: Entry price
            exit_price: Exit price
            quantity: Quantity
            side: LONG/SHORT

        Returns:
            (PnL USD, PnL percent)
        """
        if side == "LONG":
            pnl = (exit_price - entry_price) * quantity
            pnl_percent = ((exit_price - entry_price) / entry_price) * 100
        else:
            pnl = (entry_price - exit_price) * quantity
            pnl_percent = ((entry_price - exit_price) / entry_price) * 100

        return pnl, pnl_percent

    # ========================================
    # Telegram message ID management
    # ========================================

    def update_telegram_message_id(
        self, position_id: int, telegram_message_id: int
    ) -> bool:
        """
Update Telegram message ID for position.

        Used for reply loop: stop raise/exit messages reply to entry message.

        Args:
            position_id: Position ID
            telegram_message_id: Telegram message ID

        Returns:
            Whether update succeeded
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        UPDATE positions
                        SET telegram_message_id = %s
                        WHERE id = %s
                    """
                    cursor.execute(sql, (telegram_message_id, position_id))

                connection.commit()

            self.logger.debug(
                f"Telegram message ID updated: position_id={position_id}, msg_id={telegram_message_id}"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to update Telegram message ID: {e}")
            return False

    def get_telegram_message_id(self, symbol: str) -> Optional[int]:
        """
Get Telegram message ID for position.

        Args:
            symbol: Token symbol (with USDT suffix)

        Returns:
            Telegram message ID, None if not found
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        SELECT telegram_message_id FROM positions
                        WHERE symbol = %s AND status IN ('OPEN', 'PARTIAL_CLOSED')
                        ORDER BY opened_at DESC
                        LIMIT 1
                    """
                    cursor.execute(sql, (symbol,))
                    result = cursor.fetchone()

            if result and result.get("telegram_message_id"):
                return int(result["telegram_message_id"])
            return None

        except Exception as e:
            self.logger.error(f"Failed to get Telegram message ID: {e}")
            return None

    # ========================================
    # Stop loss order management
    # ========================================

    def update_sl_order(
        self, position_id: int, sl_order_id: str, sl_trigger_price: float
    ) -> bool:
        """
Update stop loss order info.

        Args:
            position_id: Position ID
            sl_order_id: Stop loss order ID
            sl_trigger_price: Stop loss trigger price

        Returns:
            Whether update succeeded
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        UPDATE positions
                        SET sl_order_id = %s,
                            sl_trigger_price = %s,
                            current_stop = %s,
                            updated_at = NOW()
                        WHERE id = %s
                    """
                    cursor.execute(
                        sql,
                        (sl_order_id, sl_trigger_price, sl_trigger_price, position_id),
                    )

                connection.commit()

            self.logger.debug(
                f"Stop loss order updated: position_id={position_id}, "
                f"sl_order_id={sl_order_id}, price={sl_trigger_price}"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to update stop loss order: {e}")
            return False

    def get_sl_order_id(self, symbol: str) -> Optional[str]:
        """
Get stop loss order ID for position.

        Args:
            symbol: Token symbol (with USDT suffix)

        Returns:
            Stop loss order ID, None if not found
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        SELECT sl_order_id FROM positions
                        WHERE symbol = %s AND status IN ('OPEN', 'PARTIAL_CLOSED')
                        ORDER BY opened_at DESC
                        LIMIT 1
                    """
                    cursor.execute(sql, (symbol,))
                    result = cursor.fetchone()

            if result and result.get("sl_order_id"):
                return str(result["sl_order_id"])
            return None

        except Exception as e:
            self.logger.error(f"Failed to get stop loss order ID: {e}")
            return None

    def clear_sl_order(self, position_id: int) -> bool:
        """
Clear stop loss order info (call after position close).

        Args:
            position_id: Position ID

        Returns:
            Whether succeeded
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        UPDATE positions
                        SET sl_order_id = NULL,
                            updated_at = NOW()
                        WHERE id = %s
                    """
                    cursor.execute(sql, (position_id,))

                connection.commit()

            self.logger.debug(f"Stop loss order cleared: position_id={position_id}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to clear stop loss order: {e}")
            return False

    # ========================================
    # Trailing stop methods
    # ========================================

    def update_trailing_stop(
        self, position_id: int, new_stop: float, highest_price: float = None
    ) -> bool:
        """Update trailing stop price."""
        return self._get_trailing_manager().update_trailing_stop(
            position_id, new_stop, highest_price
        )

    def get_trailing_stop_positions(self) -> List[Dict]:
        """Get all positions using trailing stop."""
        return self._get_trailing_manager().get_trailing_stop_positions()

    def batch_update_trailing_stops(self, updates: List[Dict]) -> int:
        """Batch update trailing stops."""
        return self._get_trailing_manager().batch_update_trailing_stops(updates)

    # ========================================
    # PENDING position management (limit order entry)
    # ========================================

    def create_pending_position(
        self,
        symbol: str,
        side: str,
        pending_order_id: str,
        pending_limit_price: float,
        target_quantity: float,
        stop_loss: float,
        entry_atr: float,
        strategy_name: str = None,
        testnet: bool = True,
        stop_type: str = "FIXED",
        trailing_period: int = None,
        trailing_mult: float = None,
        take_profit_1: float = None,
        take_profit_2: float = None,
    ) -> Optional[int]:
        """
Create PENDING position record (limit order placed).

        Args:
            symbol: Token symbol
            side: LONG/SHORT
            pending_order_id: Limit order ID
            pending_limit_price: Limit order price
            target_quantity: Target quantity
            stop_loss: Stop loss price
            entry_atr: ATR at entry
            strategy_name: Strategy name
            testnet: Whether using testnet
            stop_type: Stop type
            trailing_period: Trailing stop period
            trailing_mult: Trailing stop multiplier
            take_profit_1: Take profit 1
            take_profit_2: Take profit 2

        Returns:
            Position ID, None on failure
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        INSERT INTO positions (
                            symbol, side, status,
                            pending_order_id, pending_limit_price, pending_created_at,
                            entry_price, quantity, stop_loss, entry_atr,
                            stop_type, trailing_period, trailing_mult,
                            take_profit_1, take_profit_2,
                            testnet, strategy_name, opened_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """

                    now = datetime.now()
                    cursor.execute(
                        sql,
                        (
                            symbol,
                            side,
                            "PENDING",
                            pending_order_id,
                            pending_limit_price,
                            now,
                            0,  # entry_price set to 0, updated after fill
                            target_quantity,
                            stop_loss,
                            entry_atr,
                            stop_type,
                            trailing_period,
                            trailing_mult,
                            take_profit_1,
                            take_profit_2,
                            testnet,
                            strategy_name,
                            now,
                        ),
                    )

                    position_id = cursor.lastrowid

                connection.commit()

            self.logger.info(
                f"PENDING position created - ID: {position_id}, "
                f"{symbol} {side}, limit: ${pending_limit_price:.2f}, "
                f"qty: {target_quantity}, sl: ${stop_loss:.2f}"
            )

            return position_id

        except Exception as e:
            self.logger.error(f"Failed to create PENDING position: {e}")
            return None

    def get_pending_positions(
        self, symbol: Optional[str] = None, timeout_hours: Optional[int] = None
    ) -> List[Dict]:
        """
Query PENDING positions.

        Args:
            symbol: Token filter (optional)
            timeout_hours: Timeout hours filter (optional, returns those exceeding duration)

        Returns:
            List of PENDING positions
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    if timeout_hours:
                        if symbol:
                            sql = """
                                SELECT * FROM positions
                                WHERE symbol = %s AND status = 'PENDING'
                                AND pending_created_at < DATE_SUB(NOW(), INTERVAL %s HOUR)
                                ORDER BY pending_created_at ASC
                            """
                            cursor.execute(sql, (symbol, timeout_hours))
                        else:
                            sql = """
                                SELECT * FROM positions
                                WHERE status = 'PENDING'
                                AND pending_created_at < DATE_SUB(NOW(), INTERVAL %s HOUR)
                                ORDER BY pending_created_at ASC
                            """
                            cursor.execute(sql, (timeout_hours,))
                    else:
                        if symbol:
                            sql = """
                                SELECT * FROM positions
                                WHERE symbol = %s AND status = 'PENDING'
                                ORDER BY pending_created_at ASC
                            """
                            cursor.execute(sql, (symbol,))
                        else:
                            sql = """
                                SELECT * FROM positions
                                WHERE status = 'PENDING'
                                ORDER BY pending_created_at ASC
                            """
                            cursor.execute(sql)

                    positions = cursor.fetchall()

            return positions

        except Exception as e:
            self.logger.error(f"Failed to query PENDING positions: {e}")
            return []

    def has_pending_or_open_position(self, symbol: str) -> bool:
        """
Check if there is a PENDING or OPEN position.

        Args:
            symbol: Token symbol

        Returns:
            Whether exists
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        SELECT COUNT(*) as cnt FROM positions
                        WHERE symbol = %s AND status IN ('PENDING', 'OPEN', 'PARTIAL_CLOSED')
                    """
                    cursor.execute(sql, (symbol,))
                    result = cursor.fetchone()

            return result["cnt"] > 0

        except Exception as e:
            self.logger.error(f"Failed to check position status: {e}")
            return False

    def promote_pending_to_open(
        self, position_id: int, entry_price: float, quantity: float, entry_order_id: str
    ) -> bool:
        """
Promote PENDING position to OPEN (call after limit order fill).

        Args:
            position_id: Position ID
            entry_price: Actual fill price
            quantity: Actual fill quantity
            entry_order_id: Fill order ID

        Returns:
            Whether succeeded
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        UPDATE positions SET
                            status = 'OPEN',
                            entry_price = %s,
                            quantity = %s,
                            entry_order_id = %s,
                            current_stop = stop_loss,
                            highest_since_entry = %s,
                            updated_at = NOW()
                        WHERE id = %s AND status = 'PENDING'
                    """

                    cursor.execute(
                        sql,
                        (
                            entry_price,
                            quantity,
                            entry_order_id,
                            entry_price,
                            position_id,
                        ),
                    )

                    affected = cursor.rowcount

                connection.commit()

            if affected > 0:
                self.logger.info(
                    f"PENDING->OPEN success - ID: {position_id}, "
                    f"fill_price: ${entry_price:.2f}, qty: {quantity}"
                )
                return True
            else:
                self.logger.warning(
                    f"PENDING->OPEN failed: position {position_id} not found or not in PENDING status"
                )
                return False

        except Exception as e:
            self.logger.error(f"PENDING->OPEN failed: {e}")
            return False

    def has_entry_today(self, symbol: str) -> bool:
        """
Check if there is an entry record for this token today (idempotency check).

        Prevents duplicate processing of the same signal after scheduler restart.

        Args:
            symbol: Token symbol (with USDT suffix, e.g. BTCUSDT)

        Returns:
            True=entry exists today, False=no entry today
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        SELECT COUNT(*) as cnt FROM positions
                        WHERE symbol = %s
                        AND DATE(opened_at) = CURDATE()
                    """
                    cursor.execute(sql, (symbol,))
                    result = cursor.fetchone()

            return result["cnt"] > 0 if result else False

        except Exception as e:
            self.logger.error(f"Failed to check today's entry record: {e}")
            return False

    # ========================================
    # Implementation Shortfall recording
    # ========================================

    def record_implementation_shortfall(
        self,
        position_id: int,
        symbol: str,
        side: str,
        signal_price: float,
        fill_price: float,
        quantity: float,
    ) -> bool:
        """
Record Implementation Shortfall (IS).

        IS = difference between signal price and fill price, used to
        quantify the gap between backtest and live trading.

        Args:
            position_id: Position ID
            symbol: Trading pair (e.g. BTCUSDT)
            side: Direction LONG/SHORT
            signal_price: Signal price (daily close price)
            fill_price: Actual fill price
            quantity: Fill quantity

        Returns:
            Whether recorded successfully
        """
        try:
            # Calculate IS
            # For LONG: fill price above signal price is negative slippage
            # shortfall_bps > 0 means fill price above signal price (unfavorable for LONG)
            shortfall_bps = (fill_price / signal_price - 1) * 10000
            shortfall_usd = (fill_price - signal_price) * quantity

            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        INSERT INTO implementation_shortfall (
                            position_id, symbol, side,
                            signal_price, fill_price, quantity,
                            shortfall_bps, shortfall_usd
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    cursor.execute(
                        sql,
                        (
                            position_id,
                            symbol,
                            side,
                            signal_price,
                            fill_price,
                            quantity,
                            shortfall_bps,
                            shortfall_usd,
                        ),
                    )

                connection.commit()

            self.logger.info(
                f"IS recorded - {symbol}: signal_price=${signal_price:.2f}, "
                f"fill_price=${fill_price:.2f}, IS={shortfall_bps:+.2f}bps (${shortfall_usd:+.2f})"
            )

            return True

        except Exception as e:
            # IS recording failure does not affect main flow
            self.logger.warning(f"IS recording failed: {e}")
            return False

    def cancel_pending_position(
        self, position_id: int, reason: str = "TIMEOUT"
    ) -> bool:
        """
Cancel PENDING position (timeout or price moved away).

        Args:
            position_id: Position ID
            reason: Cancellation reason

        Returns:
            Whether succeeded
        """
        try:
            with self._get_connection_ctx() as connection:
                with connection.cursor() as cursor:
                    sql = """
                        UPDATE positions SET
                            status = 'CLOSED',
                            exit_reason = %s,
                            closed_at = NOW(),
                            updated_at = NOW()
                        WHERE id = %s AND status = 'PENDING'
                    """

                    cursor.execute(sql, (reason, position_id))
                    affected = cursor.rowcount

                connection.commit()

            if affected > 0:
                self.logger.info(f"PENDING position cancelled - ID: {position_id}, reason: {reason}")
                return True
            else:
                self.logger.warning(
                    f"Cancel PENDING failed: position {position_id} not found or not in PENDING status"
                )
                return False

        except Exception as e:
            self.logger.error(f"Failed to cancel PENDING position: {e}")
            return False
