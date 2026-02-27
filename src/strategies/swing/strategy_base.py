"""
Swing strategy base definitions

Contains:
  - SwingSignal: unified signal data structure
  - SwingStrategy: strategy protocol
"""

import pandas as pd
from typing import Protocol, Optional, Dict, Any, runtime_checkable
from dataclasses import dataclass
from datetime import datetime


@dataclass
class SwingSignal:
    """
    Unified swing signal data structure

    Compatible with both swing-ensemble and swing-breakout strategies.
    """
    # Required fields
    symbol: str
    signal_type: str  # 'ENTRY' or 'EXIT'
    direction: str    # 'LONG'
    timestamp: datetime
    price: float

    # Optional fields
    reason: str = ''
    trailing_stop: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    # Strategy-specific fields
    ensemble_score: float = 0.0  # swing-ensemble only

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            'symbol': self.symbol,
            'signal_type': self.signal_type,
            'direction': self.direction,
            'timestamp': self.timestamp,
            'price': self.price,
            'reason': self.reason,
            'trailing_stop': self.trailing_stop,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'ensemble_score': self.ensemble_score,
        }


@runtime_checkable
class SwingStrategy(Protocol):
    """
    Swing strategy protocol

    All swing strategies must implement these methods.
    """

    @property
    def symbol(self) -> str:
        """Asset symbol."""
        ...

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Prepare data and compute indicators.

        Args:
            df: Daily OHLCV data.

        Returns:
            DataFrame with indicators added.
        """
        ...

    def check_entry(self, df: pd.DataFrame) -> Optional[SwingSignal]:
        """
        Check for an entry signal.

        Args:
            df: Prepared data.

        Returns:
            SwingSignal if entry signal, None otherwise.
        """
        ...

    def check_exit(
        self,
        df: pd.DataFrame,
        entry_price: float,
        entry_atr: float,
        entry_time: datetime
    ) -> Optional[SwingSignal]:
        """
        Check for an exit signal.

        Args:
            df: Prepared data.
            entry_price: Entry price.
            entry_atr: Entry ATR.
            entry_time: Entry time.

        Returns:
            SwingSignal if exit signal, None otherwise.
        """
        ...

    def supports_trailing_stop(self) -> bool:
        """Whether this strategy supports trailing stop."""
        ...

    def get_trailing_stop(self, df: pd.DataFrame) -> Optional[float]:
        """Get current trailing stop price."""
        ...

    def get_fixed_take_profit(self, entry_price: float, atr: float) -> Optional[float]:
        """Get fixed take-profit price."""
        ...

    def get_strategy_info(self) -> Dict[str, Any]:
        """Get strategy info."""
        ...
