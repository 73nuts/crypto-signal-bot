"""
Swing data provider

Contains:
  - DataProvider: data provider protocol
  - BinanceDataProvider: Binance API data source (production)
  - LocalFileDataProvider: local file data source (backtesting/testing)

Usage:
    # Use Binance data source (default)
    provider = BinanceDataProvider()
    df = provider.get_daily_data('BTC', days=100)

    # Use local files (backtesting)
    provider = LocalFileDataProvider('/path/to/data')
    df = provider.get_daily_data('BTC', days=100)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class DataProvider(Protocol):
    """
    Data provider protocol

    All data sources must implement this protocol.
    """

    def get_daily_data(self, symbol: str, days: int = 100) -> Optional[pd.DataFrame]:
        """
        Fetch daily OHLCV data.

        Args:
            symbol: Asset symbol (BTC/ETH/BNB/SOL).
            days: Number of days to fetch.

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume, datetime
            or None on failure.
        """
        ...


class BinanceDataProvider:
    """
    Binance API data source (production)

    Fetches real-time candlestick data from the Binance Futures API.
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def get_daily_data(self, symbol: str, days: int = 100) -> Optional[pd.DataFrame]:
        """
        Fetch daily data from the Binance API.

        Args:
            symbol: Asset symbol (BTC/ETH/BNB/SOL).
            days: Number of days.

        Returns:
            Daily DataFrame with the unclosed candle removed.
        """
        try:
            from src.data.exchange_client import ExchangeClient

            # Build trading pair
            trading_pair = f"{symbol}/USDT:USDT"
            client = ExchangeClient(trading_pair, symbol)

            # Fetch a few extra candles as a buffer
            df = client.fetch_ohlcv('1d', limit=days + 5)

            if df is None or df.empty:
                self.logger.error(f"[{symbol}] API returned empty data")
                return None

            # Remove unclosed candle
            df = self._remove_unclosed_candle(df, symbol)

            # Ensure enough data (strategy needs at least 50 candles for indicator calculation)
            if len(df) < 50:
                self.logger.warning(f"[{symbol}] Insufficient history: {len(df)} candles")
                return None

            # Take the most recent N days
            df = df.tail(days).copy()
            df = df.reset_index(drop=True)

            self.logger.info(
                f"[{symbol}] Loaded live daily: {len(df)} candles, "
                f"latest close: {df['datetime'].iloc[-1]}"
            )
            return df

        except Exception as e:
            self.logger.error(f"[{symbol}] Data load failed: {e}")
            return None

    def _remove_unclosed_candle(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        Remove the unclosed (in-progress) candle.

        When called at UTC 00:01 the last candle may be the current day's just-opened bar.

        Args:
            df: Raw data.
            symbol: Asset symbol (used for logging).

        Returns:
            DataFrame with unclosed candle removed.
        """
        now_utc = datetime.now(timezone.utc)
        last_candle_time = df['datetime'].iloc[-1]

        # If the last candle is today (not yet closed), remove it
        if last_candle_time.date() == now_utc.date():
            df = df.iloc[:-1]
            self.logger.info(f"[{symbol}] Removed unclosed candle (today: {now_utc.date()})")

        return df


class LocalFileDataProvider:
    """
    Local file data source (backtesting/testing)

    Reads historical data from local Parquet/CSV files.
    """

    def __init__(self, data_dir: str):
        """
        Initialize the local data provider.

        Args:
            data_dir: Path to the data directory.
        """
        self.data_dir = data_dir
        self.logger = logging.getLogger(__name__)

    def get_daily_data(self, symbol: str, days: int = 100) -> Optional[pd.DataFrame]:
        """
        Read daily data from a local file.

        Supported formats: .parquet, .csv

        Args:
            symbol: Asset symbol.
            days: Number of days.

        Returns:
            Daily DataFrame.
        """
        # Try parquet format
        parquet_path = os.path.join(self.data_dir, f"{symbol}_1d.parquet")
        if os.path.exists(parquet_path):
            return self._load_parquet(parquet_path, symbol, days)

        # Try csv format
        csv_path = os.path.join(self.data_dir, f"{symbol}_1d.csv")
        if os.path.exists(csv_path):
            return self._load_csv(csv_path, symbol, days)

        self.logger.error(f"[{symbol}] Local data file not found: {self.data_dir}")
        return None

    def _load_parquet(self, path: str, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """Load a Parquet file."""
        try:
            df = pd.read_parquet(path)
            df = self._standardize_columns(df)
            df = df.tail(days).reset_index(drop=True)
            self.logger.info(f"[{symbol}] Loaded local Parquet: {len(df)} rows")
            return df
        except Exception as e:
            self.logger.error(f"[{symbol}] Parquet load failed: {e}")
            return None

    def _load_csv(self, path: str, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """Load a CSV file."""
        try:
            df = pd.read_csv(path)
            df = self._standardize_columns(df)
            df = df.tail(days).reset_index(drop=True)
            self.logger.info(f"[{symbol}] Loaded local CSV: {len(df)} rows")
            return df
        except Exception as e:
            self.logger.error(f"[{symbol}] CSV load failed: {e}")
            return None

    def _standardize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Standardize column names.

        Ensures output contains: timestamp, open, high, low, close, volume, datetime.
        """
        # Column name mapping (handles common formats)
        column_mapping = {
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume',
            'Timestamp': 'timestamp',
            'DateTime': 'datetime',
            'date': 'datetime',
            'Date': 'datetime',
        }

        df = df.rename(columns=column_mapping)

        # Ensure datetime column exists
        if 'datetime' not in df.columns and 'timestamp' in df.columns:
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')

        return df
