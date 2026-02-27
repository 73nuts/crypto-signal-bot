"""
Swing Breakout strategy - single-period breakout (daily trend following)

Applicable symbols: SOL

Strategy logic:
  Entry: close > prior day's 20-day high
  Exit:
    - Stop loss: entry price - 2 * ATR
    - Take profit: entry price + 6 * ATR
    - Timeout: 60 days

Note: SOL is highly volatile; fixed TP/SL outperforms trailing stop.
"""

from datetime import datetime
from typing import Dict, Optional

import pandas as pd

from src.strategies.swing.config import BREAKOUT_CONFIG
from src.strategies.swing.strategies.registry import register_strategy
from src.strategies.swing.strategy_base import SwingSignal


@register_strategy("swing-breakout")
class SwingBreakoutStrategy:
    """Swing breakout strategy (daily trend following)"""

    def __init__(self, symbol: str = "SOL"):
        """
        Initialize the strategy.

        Args:
            symbol: Asset symbol (default SOL).
        """
        self.symbol = symbol

        # Load parameters from centralized config
        self.breakout_period = BREAKOUT_CONFIG["breakout_period"]
        self.atr_period = BREAKOUT_CONFIG["atr_period"]
        self.stop_loss_atr = BREAKOUT_CONFIG["stop_loss_atr"]
        self.take_profit_atr = BREAKOUT_CONFIG["take_profit_atr"]
        self.max_holding_days = BREAKOUT_CONFIG["max_holding_days"]

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Prepare data and compute indicators.

        Args:
            df: Daily OHLCV data. Must contain: timestamp, open, high, low, close, volume.

        Returns:
            DataFrame with indicators added.
        """
        df = df.copy()

        # ATR
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - df["close"].shift(1)).abs(),
                (df["low"] - df["close"].shift(1)).abs(),
            ],
            axis=1,
        ).max(axis=1)
        df["atr"] = tr.rolling(self.atr_period).mean()

        # N-day high
        df[f"high_{self.breakout_period}"] = (
            df["high"].rolling(self.breakout_period).max()
        )

        # Breakout signal: close > prior day's N-day high
        df["prev_high"] = df[f"high_{self.breakout_period}"].shift(1)
        df["entry_signal"] = (df["close"] > df["prev_high"]).astype(int)

        return df

    def check_entry(self, df: pd.DataFrame) -> Optional[SwingSignal]:
        """
        Check for an entry signal.

        Args:
            df: Prepared data (call prepare_data first).

        Returns:
            SwingSignal if entry signal, None otherwise.
        """
        if len(df) < 2:
            return None

        current = df.iloc[-1]

        if current["entry_signal"] == 1:
            entry_price = current["close"]
            atr = current["atr"]
            stop_loss = entry_price - self.stop_loss_atr * atr
            take_profit = entry_price + self.take_profit_atr * atr

            return SwingSignal(
                symbol=self.symbol,
                signal_type="ENTRY",
                direction="LONG",
                timestamp=current["timestamp"],
                price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"Breakout above {self.breakout_period}-day high ${current['prev_high']:.2f}",
            )

        return None

    def check_exit(
        self,
        df: pd.DataFrame,
        entry_price: float,
        entry_atr: float,
        entry_time: datetime,
    ) -> Optional[SwingSignal]:
        """
        Check for an exit signal.

        Args:
            df: Prepared data.
            entry_price: Entry price.
            entry_atr: ATR at entry.
            entry_time: Entry time.

        Returns:
            SwingSignal if exit signal, None otherwise.
        """
        if len(df) < 1:
            return None

        current = df.iloc[-1]
        stop_loss = entry_price - self.stop_loss_atr * entry_atr
        take_profit = entry_price + self.take_profit_atr * entry_atr
        holding_days = (current["datetime"] - entry_time).days

        # Check stop-loss
        if current["low"] <= stop_loss:
            return SwingSignal(
                symbol=self.symbol,
                signal_type="EXIT",
                direction="LONG",
                timestamp=current["timestamp"],
                price=stop_loss,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"Stop loss ${stop_loss:.2f}",
            )

        # Check take-profit
        if current["high"] >= take_profit:
            return SwingSignal(
                symbol=self.symbol,
                signal_type="EXIT",
                direction="LONG",
                timestamp=current["timestamp"],
                price=take_profit,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"Take profit ${take_profit:.2f}",
            )

        # Check timeout
        if holding_days > self.max_holding_days:
            return SwingSignal(
                symbol=self.symbol,
                signal_type="EXIT",
                direction="LONG",
                timestamp=current["timestamp"],
                price=current["close"],
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"Holding timeout {holding_days} days",
            )

        return None

    def get_strategy_info(self) -> Dict:
        """Return strategy info."""
        return {
            "name": "swing-breakout",
            "symbol": self.symbol,
            "breakout_period": self.breakout_period,
            "stop_loss_atr": self.stop_loss_atr,
            "take_profit_atr": self.take_profit_atr,
            "max_holding_days": self.max_holding_days,
        }

    def supports_trailing_stop(self) -> bool:
        """Whether trailing stop is supported (unified interface; breakout uses fixed stop)."""
        return False

    def get_trailing_stop(self, df) -> Optional[float]:
        """Get trailing stop (unified interface; breakout does not use trailing stop)."""
        return None

    def get_fixed_take_profit(self, entry_price: float, atr: float) -> Optional[float]:
        """Get fixed take-profit (unified interface)."""
        return entry_price + self.take_profit_atr * atr
