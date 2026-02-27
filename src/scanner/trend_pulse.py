"""
Trend Pulse (Heartbeat)

Monitors trend strategy status:
  - Detects coins approaching breakout threshold
  - Detects P&L status of held positions
  - Pushes "near breakout" and "trend change" notifications
"""

import os
import requests
import pandas as pd
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from src.core.structured_logger import get_logger


@dataclass
class TrendStatus:
    """Trend status"""
    symbol: str
    current_price: float
    breakout_price: float      # Breakout threshold (Donchian upper band)
    distance_pct: float        # Percentage distance to breakout
    near_breakout: bool        # Whether near breakout (< 5%)
    in_position: bool          # Whether in position
    pnl_pct: Optional[float]   # Position unrealized P&L (if any)
    ensemble_score: float      # Ensemble signal score


class TrendPulseMonitor:
    """
    Trend Heartbeat Monitor

    Features:
      - Fetch trend status for swing strategy coins
      - Detect near breakout (distance < 5%)
      - Supports Scanner integration
    """

    # Swing strategy symbols
    V9_SYMBOLS = ['BTC', 'ETH', 'SOL', 'BNB']

    # Donchian parameters (consistent with swing strategy)
    DONCHIAN_PERIODS = [20, 35, 50, 65, 80]
    SIGNAL_THRESHOLD = 0.4

    # Near breakout threshold
    NEAR_BREAKOUT_PCT = 5.0

    # Binance API
    BASE_URL = "https://fapi.binance.com"

    def __init__(self, position_manager=None):
        """
        Initialize monitor

        Args:
            position_manager: Position manager instance (for fetching position status)
        """
        self.logger = get_logger(__name__)
        self.position_manager = position_manager
        self.session = self._setup_session()

    def _setup_session(self) -> requests.Session:
        """Configure request session"""
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
        })

        # Proxy config
        proxies = {}
        for var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']:
            if os.environ.get(var):
                protocol = 'https' if 'https' in var.lower() else 'http'
                proxies[protocol] = os.environ[var]

        if proxies:
            session.proxies.update(proxies)

        return session

    def _get_daily_klines(self, symbol: str, limit: int = 100) -> pd.DataFrame:
        """Fetch daily kline data"""
        pair = f"{symbol}USDT"

        try:
            resp = self.session.get(
                f"{self.BASE_URL}/fapi/v1/klines",
                params={'symbol': pair, 'interval': '1d', 'limit': limit},
                timeout=10
            )
            resp.raise_for_status()
            klines = resp.json()

            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])

            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

            return df

        except Exception as e:
            self.logger.warning(f"{symbol} daily kline fetch failed: {e}")
            return pd.DataFrame()

    def _calculate_breakout_level(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        Calculate breakout threshold and ensemble signal

        Returns:
            {
                'breakout_price': average Donchian upper band,
                'ensemble_score': current ensemble signal score,
                'current_price': current price
            }
        """
        if len(df) < max(self.DONCHIAN_PERIODS) + 1:
            return {}

        # Calculate Donchian upper band for each period (shift(1) = yesterday's upper band)
        breakout_levels = []
        signals = []

        for period in self.DONCHIAN_PERIODS:
            upper = df['high'].rolling(period).max().iloc[-2]  # Yesterday's upper band
            breakout_levels.append(upper)

            # Signal: whether current close exceeds yesterday's upper band
            if df['close'].iloc[-1] > upper:
                signals.append(1)
            else:
                signals.append(0)

        # Use most conservative (highest) upper band as breakout price
        breakout_price = max(breakout_levels)

        # Ensemble signal score
        ensemble_score = sum(signals) / len(signals)

        return {
            'breakout_price': breakout_price,
            'ensemble_score': ensemble_score,
            'current_price': df['close'].iloc[-1]
        }

    def get_trend_status(self, symbol: str) -> Optional[TrendStatus]:
        """
        Get trend status for a single coin

        Args:
            symbol: Coin symbol

        Returns:
            TrendStatus or None
        """
        df = self._get_daily_klines(symbol)
        if df.empty:
            return None

        levels = self._calculate_breakout_level(df)
        if not levels:
            return None

        current_price = levels['current_price']
        breakout_price = levels['breakout_price']
        ensemble_score = levels['ensemble_score']

        # Calculate percentage distance to breakout
        distance_pct = (breakout_price - current_price) / current_price * 100

        # Check if near breakout
        near_breakout = 0 < distance_pct < self.NEAR_BREAKOUT_PCT

        # Position status (if position_manager available)
        in_position = False
        pnl_pct = None

        if self.position_manager:
            try:
                positions = self.position_manager.get_open_positions()
                for pos in positions:
                    if pos.get('symbol') == symbol:
                        in_position = True
                        entry_price = float(pos.get('entry_price', 0))
                        if entry_price > 0:
                            pnl_pct = (current_price - entry_price) / entry_price * 100
                        break
            except Exception as e:
                self.logger.debug(f"Failed to get position status: {e}")

        return TrendStatus(
            symbol=symbol,
            current_price=current_price,
            breakout_price=breakout_price,
            distance_pct=distance_pct,
            near_breakout=near_breakout,
            in_position=in_position,
            pnl_pct=pnl_pct,
            ensemble_score=ensemble_score
        )

    def get_all_status(self) -> Dict[str, TrendStatus]:
        """
        Get trend status for all v9 coins

        Returns:
            {symbol: TrendStatus}
        """
        result = {}

        for symbol in self.V9_SYMBOLS:
            status = self.get_trend_status(symbol)
            if status:
                result[symbol] = status

        return result

    def get_near_breakout_coins(self) -> List[TrendStatus]:
        """
        Get coins near breakout

        Returns:
            List of coins near breakout
        """
        all_status = self.get_all_status()
        return [s for s in all_status.values() if s.near_breakout]

    def get_v9_status_dict(self) -> Dict[str, Dict[str, Any]]:
        """
        Get v9 status dict (for daily report)

        Returns:
            {symbol: {'in_position': bool, 'pnl_pct': float, 'near_breakout': bool, 'distance_pct': float}}
        """
        all_status = self.get_all_status()

        return {
            s.symbol: {
                'in_position': s.in_position,
                'pnl_pct': s.pnl_pct,
                'near_breakout': s.near_breakout,
                'distance_pct': s.distance_pct
            }
            for s in all_status.values()
        }
