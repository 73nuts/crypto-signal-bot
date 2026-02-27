"""
Alert Detector

Funnel model architecture:
  Step 1: Coarse filter (ticker/24hr + Redis cache comparison)
  Step 2: Fine filter (klines only for candidates, asyncio concurrent)

Features:
  - Redis-persisted price cache, solves cold-start problem
  - 5-minute price change calculation (not 24h)
  - Volume spike detection (vol > 3 * avg)
  - Event classifier (pump/drop/volume spike)

"""

import os
import time
import asyncio
import aiohttp
import requests
from typing import Dict, List, Optional, Any, Tuple

from src.core.structured_logger import get_logger
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from src.core.cache import get_cache, CacheBackend


class AlertType(Enum):
    """Alert type"""
    FLASH_PUMP = "flash_pump"       # Flash pump (5min > +2%)
    FLASH_DROP = "flash_drop"       # Flash drop (5min < -2%)
    VOLUME_SPIKE = "volume_spike"   # Volume spike (vol > 3x avg)
    FUNDING_HIGH = "funding_high"   # High funding (long crowded)
    FUNDING_LOW = "funding_low"     # Low funding (short crowded)


class EventTag(Enum):
    """Event tag (for formatting) - Web3 style"""
    PUMP = "🚀 Pump"
    DROP = "📉 Dump"
    VOLUME = "🔥 Vol Spike"
    FUNDING = "💰 FR Anomaly"


@dataclass
class Alert:
    """Anomaly signal"""
    symbol: str
    alert_type: AlertType
    event_tag: EventTag
    price: float
    change_pct: float           # 5-minute price change
    change_24h: float           # 24h price change (reference)
    volume_24h: float           # 24h volume (USD)
    funding_rate: float         # Current funding rate
    score: float = 0.0          # Composite score
    volume_ratio: float = 1.0   # Volume multiplier (current/average)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    extra: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.symbol} {self.event_tag.value}: {self.change_pct:+.2f}% (5min)"


class AlertDetector:
    """
    Alert Detector - Funnel model + Z-Score dynamic threshold

    Architecture:
      1. Coarse filter: ticker/24hr (weight 40) + Redis cache comparison
      2. Fine filter: klines only for candidates (asyncio concurrent)

    Z-Score dynamic threshold:
      - threshold = Z_SCORE_THRESHOLD * historical_volatility(std)
      - Automatically adapts to each coin's volatility characteristics
      - Threshold rises in bull market volatility, falls in bear market
    """

    # Liquidity filter
    MIN_VOLUME_24H = 10_000_000  # Minimum 24h volume (USD)

    # Liquidity-weighted scoring
    LIQUIDITY_BASE = 100_000_000  # $100M as baseline
    LIQUIDITY_WEIGHT_MAX = 0.5    # Max 50% weight bonus

    # Z-Score dynamic threshold
    Z_SCORE_THRESHOLD = 2.5     # Z-Score threshold (2.5 = ~2% trigger rate)
    VOLATILITY_PERIOD = 20      # Volatility calculation period (20 x 5min klines)
    VOLATILITY_CACHE_TTL = 3600  # Volatility cache TTL (1 hour)

    # Coarse filter threshold (5min) - keep fixed threshold for fast coarse filtering
    COARSE_THRESHOLD_PCT = 1.0  # Coarse threshold lowered to 1%, fine filter uses Z-Score

    # Fine filter thresholds (deprecated, kept for compatibility)
    PUMP_THRESHOLD_PCT = 2.0    # Pump threshold (replaced by Z-Score)
    DROP_THRESHOLD_PCT = -2.0   # Drop threshold (replaced by Z-Score)
    VOLUME_SPIKE_X = 3.0        # Volume spike multiplier (current kline vs prev 5 avg)

    # Funding thresholds
    FUNDING_HIGH_PCT = 0.05     # Long crowded
    FUNDING_LOW_PCT = -0.03     # Short crowded
    FUNDING_SENTIMENT_PCT = 0.02  # Market sentiment threshold (long/short dominant)

    # Major coins (priority display)
    MAJOR_COINS = {'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX'}

    # Cache config
    PRICE_CACHE_TTL = 600  # Price cache 10 minutes

    # Binance API
    BASE_URL = "https://fapi.binance.com"

    def __init__(self, redis_host: str = None, redis_port: int = 6379, redis_password: str = None):
        """Initialize detector"""
        self.logger = get_logger(__name__)
        self.session = self._setup_session()

        # Use unified CacheManager
        self._cache = get_cache()
        if not self._cache._setup_done:
            self._cache.setup(CacheBackend.REDIS)
        self.logger.info("AlertDetector initialized")

    def _setup_session(self) -> requests.Session:
        """Configure HTTP session"""
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
        })

        # Proxy configuration
        proxies = {}
        for var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']:
            if os.environ.get(var):
                protocol = 'https' if 'https' in var.lower() else 'http'
                proxies[protocol] = os.environ[var]

        if proxies:
            session.proxies.update(proxies)

        return session

    async def _get_cached_price(self, symbol: str) -> Optional[float]:
        """Get cached price (async)"""
        try:
            key = self._cache.make_key("scanner", "price", symbol)
            value = await self._cache.get(key)
            return float(value) if value else None
        except Exception as e:
            self.logger.debug(f"Failed to get cached price for {symbol}: {e}")
            return None

    async def _set_cached_price(self, symbol: str, price: float) -> None:
        """Cache price (async)"""
        try:
            key = self._cache.make_key("scanner", "price", symbol)
            await self._cache.set(key, str(price), ttl=self.PRICE_CACHE_TTL)
        except Exception as e:
            self.logger.debug(f"Cache write failed: {e}")

    async def _batch_update_cache(self, prices: Dict[str, float]) -> None:
        """Batch update cache (async)"""
        try:
            for symbol, price in prices.items():
                key = self._cache.make_key("scanner", "price", symbol)
                await self._cache.set(key, str(price), ttl=self.PRICE_CACHE_TTL)
        except Exception as e:
            self.logger.debug(f"Batch cache write failed: {e}")

    async def _get_cached_volatility(self, symbol: str) -> Optional[float]:
        """Get cached volatility (async)"""
        try:
            key = self._cache.make_key("scanner", "volatility", symbol)
            value = await self._cache.get(key)
            return float(value) if value else None
        except Exception as e:
            self.logger.debug(f"Failed to get cached volatility for {symbol}: {e}")
            return None

    async def _set_cached_volatility(self, symbol: str, volatility: float) -> None:
        """Cache volatility (async)"""
        try:
            key = self._cache.make_key("scanner", "volatility", symbol)
            await self._cache.set(key, str(volatility), ttl=self.VOLATILITY_CACHE_TTL)
        except Exception as e:
            self.logger.debug(f"Failed to cache volatility: {e}")

    def _calculate_volatility_from_klines(self, klines: List[List]) -> Optional[float]:
        """
        Calculate volatility from kline data (return std dev)

        Args:
            klines: Kline list, format [open_time, open, high, low, close, volume, ...]

        Returns:
            Volatility (percentage), e.g. 0.25 means 0.25%
        """
        if len(klines) < self.VOLATILITY_PERIOD:
            return None

        try:
            # Extract close prices
            closes = [float(k[4]) for k in klines]

            # Calculate returns (percentage)
            returns = []
            for i in range(1, len(closes)):
                ret = (closes[i] - closes[i-1]) / closes[i-1] * 100
                returns.append(ret)

            if len(returns) < self.VOLATILITY_PERIOD - 1:
                return None

            # Calculate std dev (using most recent N returns)
            recent_returns = returns[-(self.VOLATILITY_PERIOD - 1):]
            import numpy as np
            volatility = float(np.std(recent_returns))

            return volatility

        except Exception as e:
            self.logger.debug(f"Volatility calculation failed: {e}")
            return None

    def _get_dynamic_threshold(self, symbol: str, volatility: float) -> float:
        """
        Calculate dynamic threshold

        Args:
            symbol: Coin symbol
            volatility: Volatility (std dev)

        Returns:
            Dynamic threshold (percentage)
        """
        threshold = self.Z_SCORE_THRESHOLD * volatility

        # Set minimum threshold to avoid too-low thresholds during extremely quiet periods
        min_threshold = 0.3  # Minimum 0.3%
        return max(threshold, min_threshold)

    def fetch_all_tickers(self) -> List[Dict[str, Any]]:
        """
        Fetch all market tickers (weight: 40)
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/fapi/v1/ticker/24hr",
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.logger.error(f"Failed to fetch tickers: {e}")
            return []

    def fetch_funding_rates(self) -> Dict[str, float]:
        """Fetch all market funding rates"""
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/fapi/v1/premiumIndex",
                timeout=15
            )
            resp.raise_for_status()
            return {
                item['symbol']: float(item['lastFundingRate']) * 100
                for item in resp.json()
            }
        except Exception as e:
            self.logger.error(f"Failed to fetch funding rates: {e}")
            return {}

    def fetch_top_long_short_position_ratio(
        self,
        symbol: str = 'BTCUSDT',
        period: str = '1d'
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch top trader long/short position ratio

        This represents "real money" votes, reflecting smart money's actual position direction.
        More valuable than globalLongShortAccountRatio (retail noise).

        API docs: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Top-Long-Short-Position-Ratio

        Args:
            symbol: Trading pair (default BTCUSDT represents overall market)
            period: Period ('5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d')

        Returns:
            {
                'long_short_ratio': float,  # L/S ratio (>1 long dominant, <1 short dominant)
                'long_account': float,      # Long position share %
                'short_account': float,     # Short position share %
                'timestamp': int
            }
            Returns None on failure
        """
        try:
            # Correct data endpoint (/futures/data/ not /fapi/v1/)
            resp = self.session.get(
                f"{self.BASE_URL}/futures/data/topLongShortPositionRatio",
                params={'symbol': symbol, 'period': period, 'limit': 1},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

            if data and len(data) > 0:
                item = data[0]
                return {
                    'long_short_ratio': float(item['longShortRatio']),
                    'long_account': float(item['longAccount']) * 100,
                    'short_account': float(item['shortAccount']) * 100,
                    'timestamp': int(item['timestamp'])
                }
            return None

        except Exception as e:
            self.logger.warning(f"Failed to fetch top trader position ratio: {e}")
            return None

    def fetch_fear_greed_index(self) -> Optional[Dict[str, Any]]:
        """
        Fetch Fear & Greed Index

        Data source: alternative.me
        Note: This API may rate-limit or timeout, handle gracefully

        Returns:
            {
                'value': int,           # Index value (0-100)
                'classification': str,  # Classification (Extreme Fear/Fear/Neutral/Greed/Extreme Greed)
                'timestamp': str
            }
            Returns None on failure (does not affect daily brief generation)
        """
        try:
            resp = self.session.get(
                "https://api.alternative.me/fng/",
                params={'limit': 1},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

            if data and 'data' in data and len(data['data']) > 0:
                item = data['data'][0]
                return {
                    'value': int(item['value']),
                    'classification': item['value_classification'],
                    'timestamp': item['timestamp']
                }
            return None

        except Exception as e:
            # Graceful degradation: log warning but don't raise, return None
            self.logger.warning(f"Failed to fetch Fear & Greed index (gracefully degraded): {e}")
            return None

    async def fetch_klines_async(self, session: aiohttp.ClientSession,
                                  symbol: str, interval: str = '5m', limit: int = 25) -> List[List]:
        """
        Fetch klines asynchronously

        Returns:
            Kline list, each entry: [open_time, open, high, low, close, volume, ...]
        """
        url = f"{self.BASE_URL}/fapi/v1/klines"
        params = {'symbol': symbol, 'interval': interval, 'limit': limit}

        try:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            self.logger.debug(f"Kline fetch failed {symbol}: {e}")

        return []

    async def batch_fetch_klines(self, symbols: List[str]) -> Dict[str, List[List]]:
        """
        Batch fetch klines asynchronously (concurrent)

        Args:
            symbols: Symbol list (e.g. ['BTCUSDT', 'ETHUSDT'])

        Returns:
            {symbol: klines}
        """
        if not symbols:
            return {}

        # Proxy configuration
        connector = None
        os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')

        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [
                self.fetch_klines_async(session, symbol)
                for symbol in symbols
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        klines_map = {}
        for symbol, result in zip(symbols, results):
            if isinstance(result, list) and result:
                klines_map[symbol] = result

        return klines_map

    def check_volume_spike(self, klines: List[List]) -> Tuple[bool, float]:
        """
        Detect volume spike

        Args:
            klines: Kline list (latest last)

        Returns:
            (is_volume_spike, multiplier)
        """
        if len(klines) < 2:
            return False, 1.0

        # Latest kline volume
        current_vol = float(klines[-1][5])

        # Average of previous N klines (excluding latest)
        prev_vols = [float(k[5]) for k in klines[:-1]]
        if not prev_vols:
            return False, 1.0

        avg_vol = sum(prev_vols) / len(prev_vols)
        if avg_vol == 0:
            return False, 1.0

        ratio = current_vol / avg_vol

        return ratio >= self.VOLUME_SPIKE_X, ratio

    async def coarse_filter(self, tickers: List[Dict]) -> Tuple[List[Dict], Dict[str, float]]:
        """
        Step 1: Coarse filter (async)

        - Liquidity filter
        - Compare with cache to calculate 5-minute price change
        - Select candidates exceeding threshold

        Returns:
            (candidate list, 5-minute change dict)
        """
        candidates = []
        change_5m_map = {}
        new_prices = {}

        for t in tickers:
            symbol = t.get('symbol', '')

            # USDT perpetuals only
            if not symbol.endswith('USDT'):
                continue

            # Liquidity filter
            volume_24h = float(t.get('quoteVolume', 0))
            if volume_24h < self.MIN_VOLUME_24H:
                continue

            current_price = float(t['lastPrice'])
            new_prices[symbol] = current_price

            # Compare with cached price
            cached_price = await self._get_cached_price(symbol)
            if cached_price and cached_price > 0:
                change_5m = (current_price - cached_price) / cached_price * 100
                change_5m_map[symbol] = change_5m

                # Exceeds coarse threshold
                if abs(change_5m) >= self.COARSE_THRESHOLD_PCT:
                    candidates.append(t)

        # Batch update cache
        await self._batch_update_cache(new_prices)

        self.logger.info(f"Coarse filter: {len(new_prices)} liquid coins, {len(candidates)} candidates")

        return candidates, change_5m_map

    async def fine_filter(self, candidates: List[Dict], change_5m_map: Dict[str, float],
                    klines_map: Dict[str, List], funding_rates: Dict[str, float]) -> List[Alert]:
        """
        Step 2: Fine filter (async, Z-Score dynamic threshold)

        - Calculate/fetch volatility
        - Apply Z-Score dynamic threshold
        - Volume spike detection
        - Event classification
        - Generate Alert

        Returns:
            Alert list
        """
        alerts = []

        for t in candidates:
            symbol_full = t['symbol']
            symbol = symbol_full.replace('USDT', '')

            price = float(t['lastPrice'])
            change_5m = change_5m_map.get(symbol_full, 0)
            change_24h = float(t['priceChangePercent'])
            volume_24h = float(t['quoteVolume'])
            funding = funding_rates.get(symbol_full, 0)

            # Get kline data
            klines = klines_map.get(symbol_full, [])

            # Z-Score dynamic threshold
            # 1. Try to get cached volatility
            volatility = await self._get_cached_volatility(symbol_full)

            # 2. If not cached, calculate from klines
            if volatility is None and klines:
                volatility = self._calculate_volatility_from_klines(klines)
                if volatility is not None:
                    await self._set_cached_volatility(symbol_full, volatility)

            # 3. Calculate dynamic threshold
            if volatility is not None and volatility > 0:
                dynamic_threshold = self._get_dynamic_threshold(symbol_full, volatility)
                z_score = abs(change_5m) / volatility if volatility > 0 else 0
            else:
                # Fall back to fixed threshold
                dynamic_threshold = self.PUMP_THRESHOLD_PCT
                z_score = 0

            # Volume spike detection
            is_volume_spike, volume_ratio = self.check_volume_spike(klines)

            # Event classification (use dynamic threshold)
            alert_type = None
            event_tag = None
            score = 0

            # Use Z-Score to determine significance
            is_significant = abs(change_5m) >= dynamic_threshold

            if change_5m > 0 and is_significant:
                alert_type = AlertType.FLASH_PUMP
                event_tag = EventTag.PUMP
                # Score based on Z-Score for fairer cross-coin comparison
                score = z_score * 10 if z_score > 0 else change_5m * 5

            elif change_5m < 0 and is_significant:
                alert_type = AlertType.FLASH_DROP
                event_tag = EventTag.DROP
                score = z_score * 10 if z_score > 0 else abs(change_5m) * 5

            elif is_volume_spike:
                alert_type = AlertType.VOLUME_SPIKE
                event_tag = EventTag.VOLUME
                score = volume_ratio * 5

            if alert_type:
                # Volume spike bonus
                if is_volume_spike and alert_type != AlertType.VOLUME_SPIKE:
                    score += volume_ratio * 3

                # Liquidity weight (higher liquidity coins are more important)
                liquidity_factor = min(1.0, volume_24h / self.LIQUIDITY_BASE)
                liquidity_bonus = score * liquidity_factor * self.LIQUIDITY_WEIGHT_MAX
                score += liquidity_bonus

                alerts.append(Alert(
                    symbol=symbol,
                    alert_type=alert_type,
                    event_tag=event_tag,
                    price=price,
                    change_pct=change_5m,
                    change_24h=change_24h,
                    volume_24h=volume_24h,
                    funding_rate=funding,
                    score=score,
                    volume_ratio=volume_ratio,
                    extra={
                        'is_volume_spike': is_volume_spike,
                        'is_major': symbol in self.MAJOR_COINS,
                        'volatility': volatility,
                        'z_score': z_score,
                        'dynamic_threshold': dynamic_threshold,
                        'liquidity_factor': liquidity_factor,
                    }
                ))

        return alerts

    def rank_alerts(self, alerts: List[Alert], top_n: int = 3) -> List[Alert]:
        """
        Sort and return Top N

        Sort logic:
          1. Major coins first
          2. Sort by score descending
        """
        if not alerts:
            return []

        # Deduplicate by coin (keep highest score)
        best_per_coin: Dict[str, Alert] = {}
        for alert in alerts:
            if alert.symbol not in best_per_coin or alert.score > best_per_coin[alert.symbol].score:
                best_per_coin[alert.symbol] = alert

        unique = list(best_per_coin.values())

        # Sort
        def sort_key(a: Alert) -> Tuple[int, float]:
            is_major = 0 if a.extra.get('is_major') else 1
            return (is_major, -a.score)

        unique.sort(key=sort_key)

        return unique[:top_n]

    async def scan(self, top_n: int = 3) -> Tuple[List[Alert], int, Dict[str, Any]]:
        """
        Execute full market scan (async, funnel model)

        Returns:
            (Top N alerts, monitored coin count, market status)
        """
        # Step 0: Fetch all market data
        tickers = self.fetch_all_tickers()
        if not tickers:
            return [], 0, {}

        funding_rates = self.fetch_funding_rates()

        # Step 1: Coarse filter (async)
        candidates, change_5m_map = await self.coarse_filter(tickers)
        total_monitored = len(change_5m_map)  # Coins with cache comparison

        if not candidates:
            # Calculate market status
            market_status = self._calc_market_status(tickers, funding_rates)
            return [], total_monitored, market_status

        # Step 2: Fetch klines for candidates (async concurrent)
        candidate_symbols = [t['symbol'] for t in candidates]
        klines_map = await self.batch_fetch_klines(candidate_symbols)

        # Step 3: Fine filter (async)
        alerts = await self.fine_filter(candidates, change_5m_map, klines_map, funding_rates)

        # Step 4: Rank
        top_alerts = self.rank_alerts(alerts, top_n)

        # Market status
        market_status = self._calc_market_status(tickers, funding_rates)

        self.logger.info(
            f"Scan complete: {total_monitored} coins, "
            f"{len(candidates)} candidates, {len(alerts)} triggered, Top {len(top_alerts)} pushed"
        )

        return top_alerts, total_monitored, market_status

    def _calc_market_status(self, tickers: List[Dict], funding_rates: Dict[str, float]) -> Dict[str, Any]:
        """
        Calculate market status (for human-readable summary)
        """
        # BTC data
        btc_data = None
        for t in tickers:
            if t['symbol'] == 'BTCUSDT':
                btc_data = {
                    'price': float(t['lastPrice']),
                    'change_24h': float(t['priceChangePercent']),
                    'funding': funding_rates.get('BTCUSDT', 0)
                }
                break

        # Average funding
        funding_values = [f for f in funding_rates.values() if f != 0]
        avg_funding = sum(funding_values) / len(funding_values) if funding_values else 0

        # Market sentiment
        if avg_funding > self.FUNDING_SENTIMENT_PCT:
            sentiment = "Long dominant"
            sentiment_icon = "🟢"
        elif avg_funding < -self.FUNDING_SENTIMENT_PCT:
            sentiment = "Short dominant"
            sentiment_icon = "🔴"
        else:
            sentiment = "Balanced"
            sentiment_icon = "⚪"

        return {
            'btc': btc_data,
            'avg_funding': avg_funding,
            'sentiment': sentiment,
            'sentiment_icon': sentiment_icon,
            'total_pairs': len([t for t in tickers if t['symbol'].endswith('USDT')])
        }

    def get_market_overview(self) -> Dict[str, Any]:
        """
        Get market overview (for daily brief)
        """
        tickers = self.fetch_all_tickers()
        if not tickers:
            return {}

        funding_rates = self.fetch_funding_rates()

        # Filter liquid coins
        liquid = [t for t in tickers
                  if t['symbol'].endswith('USDT')
                  and float(t.get('quoteVolume', 0)) >= self.MIN_VOLUME_24H]

        # Sort by price change
        sorted_by_change = sorted(
            liquid,
            key=lambda x: float(x['priceChangePercent']),
            reverse=True
        )

        # Major coin data
        def get_coin(symbol_full: str) -> Optional[Dict]:
            for t in liquid:
                if t['symbol'] == symbol_full:
                    return {
                        'price': float(t['lastPrice']),
                        'change_24h': float(t['priceChangePercent']),
                        'volume_24h': float(t['quoteVolume']),
                        'funding': funding_rates.get(symbol_full, 0)
                    }
            return None

        # Top gainers/losers (filter low liquidity + delisted contracts)
        # Filter criteria:
        # 1. Liquidity: 24h volume >= $50M
        # 2. Zero volume: exclude zombie contracts
        # 3. Data freshness: closeTime within 10 minutes (delisted contracts freeze at delisting time)
        now_ms = int(time.time() * 1000)
        max_stale_ms = 10 * 60 * 1000  # 10 minutes
        high_liquid = [
            t for t in sorted_by_change
            if float(t['quoteVolume']) > 0  # Zero volume filter
            and float(t['quoteVolume']) >= 50_000_000  # Liquidity filter
            and (now_ms - int(t.get('closeTime', now_ms))) < max_stale_ms  # Freshness filter
        ]

        top_gainers = [
            {
                'symbol': t['symbol'].replace('USDT', ''),
                'change': float(t['priceChangePercent']),
                'volume_usd': float(t['quoteVolume']),
                'price': float(t['lastPrice'])
            }
            for t in high_liquid[:5]
        ]

        top_losers = [
            {
                'symbol': t['symbol'].replace('USDT', ''),
                'change': float(t['priceChangePercent']),
                'volume_usd': float(t['quoteVolume']),
                'price': float(t['lastPrice'])
            }
            for t in high_liquid[-5:]
        ]

        # Market status
        market_status = self._calc_market_status(tickers, funding_rates)

        return {
            'total_pairs': len(liquid),
            'btc': get_coin('BTCUSDT'),
            'eth': get_coin('ETHUSDT'),
            'sol': get_coin('SOLUSDT'),
            'top_gainers': top_gainers,
            'top_losers': top_losers,
            'avg_funding': market_status['avg_funding'],
            'sentiment': market_status['sentiment'],
            'sentiment_icon': market_status['sentiment_icon']
        }
