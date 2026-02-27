"""
Swing Ensemble strategy - multi-period Donchian ensemble (daily trend following)

Applicable symbols: BTC, ETH, BNB

Strategy logic:
  Entry: average signal across 5 periods (20,35,50,65,80) of Donchian channels > 0.4
  Exit: trailing stop, price breaks below N-day low
    - BTC: N = 25 days (50 * 0.5)
    - ETH/BNB: N = 15 days (50 * 0.3)

Academic basis:
  - Donchian channels are an academically validated trend-following indicator
  - Multi-period ensemble reduces noise and improves signal quality
"""

import pandas as pd
from typing import Dict, Optional
from datetime import datetime

from src.strategies.swing.config import (
    ENSEMBLE_CONFIG,
    get_symbol_config,
    get_ensemble_symbols,
)
from src.strategies.swing.strategy_base import SwingSignal
from src.strategies.swing.strategies.registry import register_strategy


@register_strategy('swing-ensemble')
class SwingEnsembleStrategy:
    """Swing ensemble strategy (daily trend following)"""

    def __init__(self, symbol: str):
        """
        Initialize the strategy.

        Args:
            symbol: Asset symbol (BTC/ETH/BNB).
        """
        supported = get_ensemble_symbols()
        if symbol not in supported:
            raise ValueError(f"swing-ensemble does not support {symbol}, supported: {supported}")

        self.symbol = symbol

        # Load parameters from centralized config
        symbol_config = get_symbol_config(symbol)
        self.trailing_mult = symbol_config['trailing_mult']

        self.donchian_periods = ENSEMBLE_CONFIG['donchian_periods']
        self.signal_threshold = ENSEMBLE_CONFIG['signal_threshold']
        self.atr_period = ENSEMBLE_CONFIG['atr_period']
        self.base_exit_period = ENSEMBLE_CONFIG['base_exit_period']

        # Compute exit period
        self.exit_period = max(int(self.base_exit_period * self.trailing_mult), 5)

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
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low'] - df['close'].shift(1)).abs()
        ], axis=1).max(axis=1)
        df['atr'] = tr.rolling(self.atr_period).mean()

        # Donchian channels
        for period in self.donchian_periods:
            df[f'dc_upper_{period}'] = df['high'].rolling(period).max()

        # Trailing stop
        df[f'dc_lower_{self.exit_period}'] = df['low'].rolling(self.exit_period).min()
        df['trailing_stop'] = df[f'dc_lower_{self.exit_period}'].shift(1)

        # Generate signals
        signals = pd.DataFrame(index=df.index)
        for period in self.donchian_periods:
            upper = df[f'dc_upper_{period}'].shift(1)
            signal = pd.Series(0, index=df.index)
            signal[df['close'] > upper] = 1
            signals[f'signal_{period}'] = signal

        df['ensemble_score'] = signals.mean(axis=1)
        df['entry_signal'] = (df['ensemble_score'] > self.signal_threshold).astype(int)

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

        if current['entry_signal'] == 1:
            return SwingSignal(
                symbol=self.symbol,
                signal_type='ENTRY',
                direction='LONG',
                timestamp=current['timestamp'],
                price=current['close'],
                ensemble_score=current['ensemble_score'],
                reason=f"ensemble_score={current['ensemble_score']:.2f} > {self.signal_threshold}"
            )

        return None

    def check_exit(
        self,
        df: pd.DataFrame,
        entry_price: float,
        entry_atr: float = None,
        entry_time: datetime = None
    ) -> Optional[SwingSignal]:
        """
        Check for an exit signal.

        Args:
            df: Prepared data.
            entry_price: Entry price.
            entry_atr: Entry ATR (not used by ensemble, kept for interface compatibility).
            entry_time: Entry time (not used by ensemble, kept for interface compatibility).

        Returns:
            SwingSignal if exit signal, None otherwise.
        """
        if len(df) < 2:
            return None

        current = df.iloc[-1]
        trailing_stop = current['trailing_stop']

        if pd.notna(trailing_stop) and current['low'] <= trailing_stop:
            return SwingSignal(
                symbol=self.symbol,
                signal_type='EXIT',
                direction='LONG',
                timestamp=current['timestamp'],
                price=trailing_stop,
                ensemble_score=current['ensemble_score'],
                trailing_stop=trailing_stop,
                reason=f"Trailing stop hit ${trailing_stop:.2f}"
            )

        return None

    def get_trailing_stop(self, df: pd.DataFrame) -> Optional[float]:
        """
        Get the current trailing stop price.

        Args:
            df: Prepared data.

        Returns:
            Current trailing stop price.
        """
        if len(df) < 1:
            return None

        return df.iloc[-1]['trailing_stop']

    def get_strategy_info(self) -> Dict:
        """Return strategy info."""
        return {
            'name': 'swing-ensemble',
            'symbol': self.symbol,
            'donchian_periods': self.donchian_periods,
            'signal_threshold': self.signal_threshold,
            'trailing_mult': self.trailing_mult,
            'exit_period': self.exit_period,
        }

    def supports_trailing_stop(self) -> bool:
        """Whether this strategy supports trailing stop (unified interface)."""
        return True

    def get_fixed_take_profit(self, entry_price: float, atr: float) -> Optional[float]:
        """Get fixed take-profit (unified interface; ensemble does not use fixed TP)."""
        return None
