"""
Exchange data client module.
Handles interaction with the Binance perpetual futures API to fetch market data.

Supports automatic proxy detection:
- Proxy available: use requests to call Binance API directly
- No proxy: use ccxt (original logic)
"""

import logging
import os
from typing import Dict, Optional, TypedDict

import ccxt
import pandas as pd
import requests
from requests.exceptions import RequestException


class TickerData(TypedDict):
    """Ticker price data."""
    symbol: str
    last: float
    timestamp: int


class ExchangeClient:
    """Exchange data client — wraps Binance perpetual futures API."""

    def __init__(self, trading_pair, symbol, binance_config=None):
        """
        Args:
            trading_pair: Trading pair (e.g. 'ETH/USDT:USDT')
            symbol: Coin symbol (e.g. 'ETH', 'SOL')
            binance_config: Binance config dict (optional)
        """
        self.trading_pair = trading_pair
        self.symbol = symbol
        self.logger = logging.getLogger(__name__)

        # Binance futures API base URL
        self.base_url = 'https://fapi.binance.com'

        # Detect proxy environment
        self.use_requests = self._should_use_requests()

        if self.use_requests:
            self.logger.info("Proxy detected, using requests to access Binance API directly")
            self.session = self._setup_requests_session()
            self.exchange = None
        else:
            self.logger.info("Using ccxt to access Binance API")
            self.exchange = self._setup_exchange(binance_config or {})
            self.session = None

    def _should_use_requests(self):
        """Detect whether to use requests.

        Returns:
            bool: True = use requests, False = use ccxt
        """
        # Strategy: prefer requests
        # Rationale:
        # 1. requests.Session supports explicit proxy config (see _setup_requests_session)
        # 2. ccxt proxy support is unstable
        # 3. requests is lighter and easier to debug

        self.logger.info("Using requests to access Binance API (supports proxy auto-detection)")
        return True

    def _setup_requests_session(self):
        """Configure requests session.

        Returns:
            requests.Session: Configured session
        """
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
        })

        # Important: requests.Session() does NOT use env-var proxies by default.
        # Must configure explicitly if proxy env vars are present.
        # This differs from requests.get(), which picks them up automatically.
        proxies = {}
        for proxy_var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']:
            proxy_value = os.environ.get(proxy_var)
            if proxy_value:
                protocol = 'https' if 'https' in proxy_var.lower() else 'http'
                proxies[protocol] = proxy_value

        if proxies:
            session.proxies.update(proxies)
            self.logger.info(f"requests proxy configured: {proxies}")

        # Test connection
        try:
            response = session.get(
                f'{self.base_url}/fapi/v1/ticker/price',
                params={'symbol': self._convert_symbol(self.trading_pair)},
                timeout=10
            )
            response.raise_for_status()
            price = response.json()['price']
            self.logger.info(f"Binance futures API connected, {self.symbol} price: ${float(price):.2f}")
        except (RequestException, KeyError, ValueError) as e:
            self.logger.warning(f"Binance API connection test failed: {e}")

        return session

    def _setup_exchange(self, binance_config):
        """Set up ccxt exchange connection.

        Args:
            binance_config: Binance config dict

        Returns:
            ccxt.Exchange: Configured exchange instance
        """
        # Configure proxy if env vars are present
        proxies = {}
        http_proxy = os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY')
        https_proxy = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')

        if http_proxy:
            proxies['http'] = http_proxy
        if https_proxy:
            proxies['https'] = https_proxy

        exchange_config = {
            'enableRateLimit': True,
            'urls': {
                'api': {
                    'public': 'https://fapi.binance.com/fapi/v1',
                    'private': 'https://fapi.binance.com/fapi/v1'
                }
            },
            'options': {
                'defaultType': 'future'
            }
        }

        # Add proxy config if available
        if proxies:
            exchange_config['proxies'] = proxies
            self.logger.info(f"ccxt proxy configured: {proxies}")

        exchange = ccxt.binance(exchange_config)

        # Test connection
        try:
            ticker = exchange.fetch_ticker(self.trading_pair)
            self.logger.info(f"Binance futures API connected, {self.symbol} price: ${ticker['last']:.2f}")
        except (ccxt.NetworkError, ccxt.ExchangeError, KeyError) as e:
            self.logger.warning(f"Binance API connection failed: {e}")
            # Do not raise; continue running

        return exchange

    def _convert_symbol(self, trading_pair):
        """Convert trading pair format.

        Args:
            trading_pair: ccxt format 'ETH/USDT:USDT'

        Returns:
            str: Binance format 'ETHUSDT'
        """
        # 'ETH/USDT:USDT' -> 'ETHUSDT'
        return trading_pair.split('/')[0] + trading_pair.split('/')[1].split(':')[0]

    def _fetch_ohlcv_requests(self, timeframe='15m', limit=100):
        """Fetch OHLCV data using requests.

        Args:
            timeframe: Timeframe string
            limit: Number of candles

        Returns:
            DataFrame or None
        """
        try:
            symbol = self._convert_symbol(self.trading_pair)
            response = self.session.get(
                f'{self.base_url}/fapi/v1/klines',
                params={
                    'symbol': symbol,
                    'interval': timeframe,
                    'limit': limit
                },
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            df = pd.DataFrame(data, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])

            # Keep only needed columns and cast types
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            df['timestamp'] = df['timestamp'].astype(int)
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')

            self.logger.info(f"Fetched {self.symbol} futures data: {timeframe} {len(df)} candles")
            return df

        except (RequestException, KeyError, ValueError, pd.errors.EmptyDataError) as e:
            self.logger.error(f"Failed to fetch {self.symbol} price data: {e}")
            return None

    def fetch_ohlcv(self, timeframe: str = '15m', limit: int = 100) -> Optional[pd.DataFrame]:
        """Fetch crypto perpetual futures OHLCV data.

        Args:
            timeframe: Timeframe string ('15m', '1h', '4h', '1d', etc.)
            limit: Number of candles to return (default 100)

        Returns:
            Optional[pd.DataFrame]: OHLCV dataframe with columns:
                - timestamp: Unix timestamp (ms)
                - open: Open price
                - high: High price
                - low: Low price
                - close: Close price
                - volume: Volume
                - datetime: pandas Timestamp object
            None on failure.

        Example:
            >>> client = ExchangeClient('ETH/USDT:USDT', 'ETH')
            >>> df = client.fetch_ohlcv('1h', 100)
            >>> if df is not None:
            ...     print(f"Latest price: ${df.iloc[-1]['close']:.2f}")
        """
        if self.use_requests:
            return self._fetch_ohlcv_requests(timeframe, limit)

        # Fall back to ccxt
        try:
            ohlcv = self.exchange.fetch_ohlcv(self.trading_pair, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')

            self.logger.info(f"Fetched {self.symbol} futures data: {timeframe} {len(df)} candles")
            return df

        except (ccxt.NetworkError, ccxt.ExchangeError, KeyError, ValueError) as e:
            self.logger.error(f"Failed to fetch {self.symbol} price data: {e}")
            return None

    def fetch_multi_timeframe(self, timeframes: Optional[Dict[str, int]] = None) -> Dict[str, pd.DataFrame]:
        """Fetch multi-timeframe data (for multi-period confirmation analysis).

        Args:
            timeframes: Dict of {timeframe: limit}
                        Default: {'15m': 100, '1h': 168, '4h': 180}

        Returns:
            Dict[str, pd.DataFrame]: Mapping of timeframe string to OHLCV dataframe.
                Keys: timeframe strings ('15m', '1h', '4h')
                Values: corresponding OHLCV DataFrames
                Note: only includes successfully fetched timeframes.

        Example:
            >>> client = ExchangeClient('ETH/USDT:USDT', 'ETH')
            >>> mtf_data = client.fetch_multi_timeframe()
            >>> print(list(mtf_data.keys()))  # ['15m', '1h', '4h']
            >>> df_1h = mtf_data['1h']
        """
        if timeframes is None:
            timeframes = {
                '15m': 100,  # 25 hours of data
                '1h': 168,   # 7 days of data
                '4h': 180    # 30 days of data
            }

        data = {}
        for tf, limit in timeframes.items():
            df = self.fetch_ohlcv(tf, limit)
            if df is not None:
                data[tf] = df

        return data

    def fetch_ticker(self) -> Optional[TickerData]:
        """Fetch current ticker data.

        Returns:
            Optional[TickerData]: Ticker dict with fields:
                - symbol: Trading pair symbol ('ETH/USDT:USDT')
                - last: Latest trade price
                - timestamp: Ticker timestamp (Unix ms)
            None on failure.

        Example:
            >>> client = ExchangeClient('ETH/USDT:USDT', 'ETH')
            >>> ticker = client.fetch_ticker()
            >>> if ticker:
            ...     print(f"Current price: ${ticker['last']:.2f}")
        """
        if self.use_requests:
            try:
                symbol = self._convert_symbol(self.trading_pair)
                response = self.session.get(
                    f'{self.base_url}/fapi/v1/ticker/price',
                    params={'symbol': symbol},
                    timeout=10
                )
                response.raise_for_status()
                data = response.json()

                # Convert to ccxt format
                return {
                    'symbol': self.trading_pair,
                    'last': float(data['price']),
                    'timestamp': int(data.get('time', 0))
                }
            except (RequestException, KeyError, ValueError) as e:
                self.logger.error(f"Failed to fetch {self.symbol} ticker: {e}")
                return None

        # Fall back to ccxt
        try:
            ticker = self.exchange.fetch_ticker(self.trading_pair)
            return ticker
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            self.logger.error(f"Failed to fetch {self.symbol} ticker: {e}")
            return None
