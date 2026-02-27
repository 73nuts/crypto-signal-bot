"""
Orderbook Depth Detector

Detects buy/sell pressure imbalance in the orderbook, identifies large wall signals.

Features:
  - Monitor Top 50 coins + held positions
  - Calculate bid/ask weighted depth ratio
  - Dynamic thresholds (Redis-stored, runtime adjustable)
  - Tiered push (Premium>=74%, Basic only extreme 87%)
  - Cooldown mechanism (1 hour per coin)
"""

import os
import json
import asyncio
import aiohttp
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.core.structured_logger import get_logger
from src.core.cache import get_cache, CacheBackend
from src.scanner.cooldown_manager import get_cooldown_manager, CooldownManager


@dataclass
class OrderbookAlert:
    """Orderbook signal"""
    symbol: str                     # Coin (without USDT)
    imbalance_ratio: float          # Bid/ask depth ratio (bid_depth / ask_depth)
    imbalance_side: str             # 'BID_HEAVY' | 'ASK_HEAVY'
    bid_depth_usd: float            # Bid depth (USD)
    ask_depth_usd: float            # Ask depth (USD)
    imbalance_pct: float            # Imbalance percentage (0-100)
    top_bid_price: float            # Best bid price
    top_ask_price: float            # Best ask price
    current_price: float            # Current price (mid)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __str__(self) -> str:
        side = "Bid pressure" if self.imbalance_side == 'BID_HEAVY' else "Ask pressure"
        return f"{self.symbol} {side} {self.imbalance_pct:.0f}% (ratio: {self.imbalance_ratio:.2f})"


class OrderbookDetector:
    """
    Orderbook Depth Detector

    Detection strategy:
      1. Fetch Top 50 coins + held positions
      2. Fetch orderbook depth (top 20 levels)
      3. Calculate weighted depth: sum(price * qty) for each side
      4. Calculate imbalance ratio: bid_depth / ask_depth
      5. Filter: threshold + cooldown
      6. Return triggered signals

    Threshold design:
      - Premium: ratio > 2.86 or < 0.35 (74% imbalance)
      - Basic: ratio > 6.67 or < 0.15 (87% extreme imbalance)

    Cooldown: delegated to CooldownManager, direction-aware (BID_WALL/ASK_WALL)
    """

    # API endpoints
    DEPTH_URL = "https://fapi.binance.com/fapi/v1/depth"
    TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"

    # Default thresholds (adjustable via Redis at runtime)
    # ratio > 2.86 equals bid_depth / (bid_depth + ask_depth) > 74%
    DEFAULT_THRESHOLD_PREMIUM_HIGH = 2.86   # bid/ask > 2.86 = 74% bid dominance
    DEFAULT_THRESHOLD_PREMIUM_LOW = 0.35    # bid/ask < 0.35 = 74% ask dominance
    DEFAULT_THRESHOLD_BASIC_HIGH = 6.67     # bid/ask > 6.67 = 87% bid (extreme)
    DEFAULT_THRESHOLD_BASIC_LOW = 0.15      # bid/ask < 0.15 = 87% ask (extreme)

    # Liquidity filter
    MIN_VOLUME_24H = 10_000_000  # Minimum 24h volume $10M

    # Cooldown config
    COOLDOWN_MINUTES = 60  # Per-coin cooldown (minutes)

    # Depth levels
    DEPTH_LIMIT = 20  # Fetch top 20 levels (weight=2)

    # Monitoring count
    TOP_N_COINS = 50

    # Cache key
    THRESHOLD_CACHE_KEY = "orderbook:thresholds"

    def __init__(self):
        """Initialize detector"""
        self.logger = get_logger(__name__)

        # HTTP Session config
        self._session: Optional[aiohttp.ClientSession] = None
        self._proxy = self._get_proxy()

        # Use unified CacheManager
        self._cache = get_cache()
        if not self._cache._setup_done:
            self._cache.setup(CacheBackend.REDIS)

        # Unified cooldown manager
        self._cooldown_manager = get_cooldown_manager()

        # Lazily initialized dependencies
        self._position_manager = None

        self.logger.info("OrderbookDetector initialized")

    def _get_proxy(self) -> Optional[str]:
        """Get proxy configuration"""
        for var in ['https_proxy', 'HTTPS_PROXY', 'http_proxy', 'HTTP_PROXY']:
            if os.environ.get(var):
                return os.environ[var]
        return None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
            )
        return self._session

    def _get_position_manager(self):
        """Lazily get position manager"""
        if self._position_manager is None:
            try:
                from src.trading.position_manager import PositionManager
                self._position_manager = PositionManager()
            except Exception as e:
                self.logger.debug(f"Position manager init skipped: {e}")
        return self._position_manager

    # ==========================================
    # Symbol selection
    # ==========================================

    async def get_monitoring_symbols(self) -> List[str]:
        """
        Get monitoring symbol list (Top 50 + held positions)

        Returns:
            Symbol list (e.g. ['BTCUSDT', 'ETHUSDT', ...])
        """
        symbols = set()

        # 1. Top 50 by volume
        try:
            session = await self._get_session()
            async with session.get(self.TICKER_URL, proxy=self._proxy) as resp:
                if resp.status == 200:
                    tickers = await resp.json()
                    usdt_tickers = [
                        t for t in tickers
                        if t['symbol'].endswith('USDT')
                        and float(t.get('quoteVolume', 0)) >= self.MIN_VOLUME_24H
                    ]
                    sorted_tickers = sorted(
                        usdt_tickers,
                        key=lambda x: float(x['quoteVolume']),
                        reverse=True
                    )
                    for t in sorted_tickers[:self.TOP_N_COINS]:
                        symbols.add(t['symbol'])
                    self.logger.debug(f"Top {self.TOP_N_COINS} coins: {len(symbols)}")
        except Exception as e:
            self.logger.error(f"Failed to fetch top coins: {e}")

        # 2. Held positions
        pm = self._get_position_manager()
        if pm:
            try:
                positions = pm.get_open_positions()
                for pos in positions:
                    symbol = pos.get('symbol', '')
                    if symbol:
                        symbols.add(f"{symbol}USDT")
                        self.logger.debug(f"Added held position: {symbol}USDT")
            except Exception as e:
                self.logger.debug(f"Failed to fetch held positions: {e}")

        self.logger.debug(f"Total monitoring symbols: {len(symbols)}")
        return list(symbols)

    # ==========================================
    # Orderbook fetching
    # ==========================================

    async def fetch_depth(self, symbol: str) -> Optional[Dict]:
        """
        Fetch orderbook for a single coin (weight=2)

        Args:
            symbol: Trading symbol (e.g. 'BTCUSDT')

        Returns:
            {
                'bids': [[price, qty], ...],
                'asks': [[price, qty], ...]
            }
        """
        try:
            session = await self._get_session()
            params = {'symbol': symbol, 'limit': self.DEPTH_LIMIT}
            async with session.get(
                self.DEPTH_URL,
                params=params,
                proxy=self._proxy
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    self.logger.debug(f"Failed to fetch {symbol} depth: HTTP {resp.status}")
        except asyncio.TimeoutError:
            self.logger.debug(f"Timeout fetching {symbol} depth")
        except Exception as e:
            self.logger.debug(f"Error fetching {symbol} depth: {e}")
        return None

    async def batch_fetch_depths(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        Batch fetch orderbooks (with rate limiting)

        Args:
            symbols: Symbol list

        Returns:
            {symbol: depth_data}
        """
        results = {}

        for symbol in symbols:
            depth = await self.fetch_depth(symbol)
            if depth:
                results[symbol] = depth
            await asyncio.sleep(0.05)  # 50ms interval to avoid rate limit

        self.logger.debug(f"Batch fetch depths: {len(results)}/{len(symbols)} succeeded")
        return results

    # ==========================================
    # Imbalance calculation
    # ==========================================

    def calculate_imbalance(
        self,
        symbol: str,
        depth: Dict
    ) -> Optional[OrderbookAlert]:
        """
        Calculate orderbook imbalance

        Formula:
          bid_depth = sum(price * qty for each bid level)
          ask_depth = sum(price * qty for each ask level)
          ratio = bid_depth / ask_depth

        Args:
            symbol: Trading symbol
            depth: Orderbook data

        Returns:
            OrderbookAlert or None
        """
        bids = depth.get('bids', [])
        asks = depth.get('asks', [])

        if not bids or not asks:
            return None

        # Calculate weighted depth (USD)
        try:
            bid_depth = sum(float(b[0]) * float(b[1]) for b in bids)
            ask_depth = sum(float(a[0]) * float(a[1]) for a in asks)
        except (ValueError, TypeError, IndexError):
            return None

        if ask_depth <= 0 or bid_depth <= 0:
            return None

        ratio = bid_depth / ask_depth

        # Determine side and imbalance degree
        if ratio > 1:
            side = 'BID_HEAVY'
            # bid share = bid_depth / (bid_depth + ask_depth) = ratio / (ratio + 1)
            # ratio=2.86 -> bid share = 2.86/3.86 = 74%
            imbalance_pct = (ratio / (ratio + 1)) * 100
        else:
            side = 'ASK_HEAVY'
            # ask share = ask_depth / (bid_depth + ask_depth) = 1 / (ratio + 1)
            # ratio=0.35 -> ask share = 1/1.35 = 74%
            imbalance_pct = (1 / (ratio + 1)) * 100

        # Get current price
        try:
            top_bid = float(bids[0][0])
            top_ask = float(asks[0][0])
        except (ValueError, TypeError, IndexError):
            return None

        current_price = (top_bid + top_ask) / 2

        return OrderbookAlert(
            symbol=symbol.replace('USDT', ''),
            imbalance_ratio=ratio,
            imbalance_side=side,
            bid_depth_usd=bid_depth,
            ask_depth_usd=ask_depth,
            imbalance_pct=imbalance_pct,
            top_bid_price=top_bid,
            top_ask_price=top_ask,
            current_price=current_price
        )

    # ==========================================
    # Dynamic thresholds
    # ==========================================

    async def get_thresholds(self) -> Dict[str, Dict[str, float]]:
        """
        Get dynamic thresholds (async, from Redis)

        Returns:
            {
                'premium': {'high': 2.86, 'low': 0.35},
                'basic': {'high': 6.67, 'low': 0.15}
            }
        """
        try:
            key = self._cache.make_key("scanner", "orderbook", "thresholds")
            cached = await self._cache.get(key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            self.logger.debug(f"Failed to read threshold cache: {e}")

        return {
            'premium': {
                'high': self.DEFAULT_THRESHOLD_PREMIUM_HIGH,
                'low': self.DEFAULT_THRESHOLD_PREMIUM_LOW
            },
            'basic': {
                'high': self.DEFAULT_THRESHOLD_BASIC_HIGH,
                'low': self.DEFAULT_THRESHOLD_BASIC_LOW
            }
        }

    async def set_thresholds(
        self,
        premium_high: float = None,
        premium_low: float = None,
        basic_high: float = None,
        basic_low: float = None
    ) -> bool:
        """
        Dynamically update thresholds (async, write to Redis)

        Args:
            premium_high: Premium bid pressure threshold
            premium_low: Premium ask pressure threshold
            basic_high: Basic bid pressure threshold
            basic_low: Basic ask pressure threshold

        Returns:
            Whether update succeeded
        """
        try:
            current = await self.get_thresholds()

            if premium_high is not None:
                current['premium']['high'] = float(premium_high)
            if premium_low is not None:
                current['premium']['low'] = float(premium_low)
            if basic_high is not None:
                current['basic']['high'] = float(basic_high)
            if basic_low is not None:
                current['basic']['low'] = float(basic_low)

            key = self._cache.make_key("scanner", "orderbook", "thresholds")
            await self._cache.set(key, json.dumps(current), ttl=0)  # Never expires

            self.logger.info(f"Thresholds updated: {current}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update thresholds: {e}")
            return False

    async def check_threshold(
        self,
        ratio: float,
        level: str = 'premium'
    ) -> bool:
        """
        Check if threshold is triggered (async)

        Args:
            ratio: Bid/ask depth ratio
            level: 'premium' or 'basic'

        Returns:
            Whether triggered
        """
        thresholds = await self.get_thresholds()
        t = thresholds.get(level, thresholds['premium'])
        return ratio > t['high'] or ratio < t['low']

    # ==========================================
    # Cooldown mechanism (delegated to CooldownManager)
    # ==========================================

    async def check_cooldown(self, symbol: str, direction: str = 'NEUTRAL') -> bool:
        """
        Check coin cooldown (async, delegated to CooldownManager)

        Args:
            symbol: Coin (without USDT)
            direction: Direction (BID_WALL/ASK_WALL)

        Returns:
            True = can push, False = on cooldown
        """
        return await self._cooldown_manager.check_orderbook(symbol, direction)

    async def update_cooldown(self, symbol: str, direction: str = 'NEUTRAL') -> None:
        """Update coin cooldown (async, delegated to CooldownManager)"""
        await self._cooldown_manager.update_orderbook(symbol, direction)

    async def get_cooldown_remaining(self, symbol: str, direction: str = 'NEUTRAL') -> float:
        """Get remaining cooldown time (minutes, async)"""
        from src.scanner.cooldown_manager import CooldownType
        return await self._cooldown_manager.get_remaining(CooldownType.ORDERBOOK, symbol, direction)

    # ==========================================
    # Core scan
    # ==========================================

    async def scan(self, top_n: int = 1) -> Tuple[List[OrderbookAlert], Dict]:
        """
        Execute orderbook scan (async)

        Args:
            top_n: Return Top N results

        Returns:
            (triggered alerts, current threshold config)
        """
        self.logger.info("Starting orderbook scan...")

        # 1. Get monitoring symbols
        symbols = await self.get_monitoring_symbols()
        if not symbols:
            self.logger.warning("No monitoring symbols, skipping scan")
            return [], await self.get_thresholds()

        # 2. Batch fetch orderbooks
        depths = await self.batch_fetch_depths(symbols)

        # 3. Calculate imbalance
        alerts = []
        thresholds = await self.get_thresholds()

        for symbol, depth in depths.items():
            alert = self.calculate_imbalance(symbol, depth)
            if not alert:
                continue

            # Check if Premium threshold is triggered
            if await self.check_threshold(alert.imbalance_ratio, 'premium'):
                # Direction-aware cooldown (BID_HEAVY -> BID_WALL, ASK_HEAVY -> ASK_WALL)
                direction = 'BID_WALL' if alert.imbalance_side == 'BID_HEAVY' else 'ASK_WALL'
                if await self.check_cooldown(alert.symbol, direction):
                    alerts.append(alert)
                else:
                    remaining = await self.get_cooldown_remaining(alert.symbol, direction)
                    self.logger.debug(
                        f"{alert.symbol} triggered threshold but on cooldown (remaining {remaining:.0f}min)"
                    )

        # 4. Sort by imbalance degree
        alerts.sort(key=lambda x: x.imbalance_pct, reverse=True)

        # 5. Return Top N
        result = alerts[:top_n]

        if result:
            self.logger.info(
                f"Orderbook scan complete: {len(symbols)} coins, {len(alerts)} triggered, returning Top {len(result)}"
            )
            for alert in result:
                self.logger.info(f"  - {alert}")
        else:
            self.logger.debug("Orderbook scan complete: no threshold-triggered anomalies")

        return result, thresholds

    # ==========================================
    # Status query
    # ==========================================

    async def get_status(self) -> Dict[str, Any]:
        """Get detector status (async)"""
        thresholds = await self.get_thresholds()
        cooldown_status = self._cooldown_manager.get_status()
        return {
            'thresholds': thresholds,
            'cooldown_minutes': self.COOLDOWN_MINUTES,
            'depth_limit': self.DEPTH_LIMIT,
            'top_n_coins': self.TOP_N_COINS,
            'min_volume_24h': self.MIN_VOLUME_24H,
            'cooldown_manager': cooldown_status
        }

    async def close(self):
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()


# ==========================================
# Singleton pattern
# ==========================================

_detector_instance: Optional[OrderbookDetector] = None


def get_orderbook_detector() -> OrderbookDetector:
    """Get OrderbookDetector singleton"""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = OrderbookDetector()
    return _detector_instance


# ==========================================
# CLI testing
# ==========================================

if __name__ == "__main__":
    import sys

    # Load environment variables
    from dotenv import load_dotenv
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

    async def main():
        detector = OrderbookDetector()

        if len(sys.argv) > 1:
            cmd = sys.argv[1]

            if cmd == "--status":
                print("OrderbookDetector status:")
                status = await detector.get_status()
                for k, v in status.items():
                    print(f"  {k}: {v}")

            elif cmd == "--scan":
                print("Running orderbook scan...")
                alerts, thresholds = await detector.scan(top_n=5)
                print(f"\nThresholds: Premium(>{thresholds['premium']['high']:.2f} or "
                      f"<{thresholds['premium']['low']:.2f})")
                print(f"            Basic(>{thresholds['basic']['high']:.2f} or "
                      f"<{thresholds['basic']['low']:.2f})")
                print("\nThreshold-triggered coins (Top 5):")
                for i, alert in enumerate(alerts, 1):
                    print(f"  {i}. {alert.symbol}: ratio={alert.imbalance_ratio:.2f} "
                          f"({alert.imbalance_side}) "
                          f"bid=${alert.bid_depth_usd/1e6:.1f}M / "
                          f"ask=${alert.ask_depth_usd/1e6:.1f}M")

            elif cmd == "--set-threshold":
                if len(sys.argv) >= 4:
                    param = sys.argv[2]  # premium_high, premium_low, etc.
                    value = float(sys.argv[3])
                    kwargs = {param: value}
                    await detector.set_thresholds(**kwargs)
                    print(f"Threshold updated: {param}={value}")
                else:
                    print("Usage: --set-threshold <premium_high|premium_low|basic_high|basic_low> <value>")

            else:
                print(f"Unknown command: {cmd}")
                print("Available commands: --status, --scan, --set-threshold")

        else:
            print("OrderbookDetector")
            print("Usage:")
            print("  python -m src.scanner.orderbook_detector --status")
            print("  python -m src.scanner.orderbook_detector --scan")
            print("  python -m src.scanner.orderbook_detector --set-threshold premium_high 3.0")

        await detector.close()

    asyncio.run(main())
