"""
Spot-Futures Spread Detector

Detects price differences between spot and futures markets, identifying
arbitrage opportunities or market anomalies.

Features:
  - Batch fetch spot/futures prices (weight 80/call)
  - Calculate spread percentage
  - Dynamic thresholds (Redis-stored, runtime adjustable)
  - Tiered push (Premium>=3%, Basic>=10%)
  - Tiered liquidity filter (bilateral check)
"""

import os
import json
import requests
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.core.structured_logger import get_logger
from src.core.cache import get_cache, CacheBackend
from src.scanner.cooldown_manager import get_cooldown_manager, CooldownManager


@dataclass
class SpreadAlert:
    """Spread signal"""

    symbol: str  # Coin symbol (without USDT)
    spot_price: float  # Spot price
    futures_price: float  # Futures price
    spread_pct: float  # Spread percentage ((futures-spot)/spot*100)
    spread_type: str  # 'PREMIUM' (futures premium) | 'DISCOUNT' (futures discount)
    volume_24h: float = 0.0  # 24h volume (for liquidity filter)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __str__(self) -> str:
        direction = "premium" if self.spread_pct > 0 else "discount"
        return f"{self.symbol} {direction} {abs(self.spread_pct):.2f}%"


class SpreadDetector:
    """
    Spot-Futures Spread Detector

    Detection strategy:
      1. Batch fetch spot + futures full-market tickers
      2. Calculate spread: (futures - spot) / spot * 100
      3. Filter: bilateral liquidity + threshold + cooldown
      4. Return Top 1

    Dynamic thresholds (Redis adjustable):
      - Premium: default 3%
      - Basic: default 10%

    Liquidity config (Redis adjustable):
      - tier1 (BTC/ETH): $1B
      - tier2 (SOL/BNB etc): $50M
      - default: $10M
      - Bilateral check: spot + futures must both meet threshold
    """

    # API endpoints
    SPOT_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"  # weight 40
    FUTURES_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"  # weight 40

    # Default thresholds (adjustable via Redis)
    DEFAULT_THRESHOLD_PREMIUM = 3.0  # Premium user threshold (%)
    DEFAULT_THRESHOLD_BASIC = 10.0  # Basic user threshold (%)

    # Liquidity filter (tiered config, bilateral check)
    # Default config, adjustable via Redis
    DEFAULT_LIQUIDITY_CONFIG = {
        "require_both_sides": True,  # spot + futures must both meet threshold
        "tiers": {
            "tier1": {
                "min_volume_24h": 1_000_000_000,  # $1B - major coins
                "symbols": ["BTC", "ETH"],
            },
            "tier2": {
                "min_volume_24h": 50_000_000,  # $50M - top active coins
                "symbols": ["SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK"],
            },
            "default": {
                "min_volume_24h": 10_000_000  # $10M - other altcoins
            },
        },
    }

    # Cooldown config
    COOLDOWN_HOURS = 2  # Coin cooldown duration (hours)

    # Cache keys
    THRESHOLD_CACHE_KEY = "spread:thresholds"
    LIQUIDITY_CACHE_KEY = "spread:liquidity"

    def __init__(self):
        """Initialize detector"""
        self.logger = get_logger(__name__)
        self.session = self._setup_session()

        # Use unified CacheManager
        self._cache = get_cache()
        if not self._cache._setup_done:
            self._cache.setup(CacheBackend.REDIS)

        # Unified cooldown manager
        self._cooldown_manager = get_cooldown_manager()

        self.logger.info("SpreadDetector initialized")

    def _setup_session(self) -> requests.Session:
        """Configure HTTP session"""
        session = requests.Session()
        session.headers.update(
            {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        )

        # Proxy config
        proxies = {}
        for var in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
            if os.environ.get(var):
                protocol = "https" if "https" in var.lower() else "http"
                proxies[protocol] = os.environ[var]

        if proxies:
            session.proxies.update(proxies)

        return session

    # ==========================================
    # Price fetching
    # ==========================================

    def fetch_spot_prices(self) -> Dict[str, Dict[str, float]]:
        """
        Fetch spot ticker prices (weight 40)

        Returns:
            {symbol: {'price': float, 'volume': float}}
        """
        try:
            resp = self.session.get(self.SPOT_TICKER_URL, timeout=15)
            resp.raise_for_status()

            result = {}
            for t in resp.json():
                symbol = t.get("symbol", "")
                if symbol.endswith("USDT") and not symbol.endswith("DOWNUSDT"):
                    try:
                        result[symbol] = {
                            "price": float(t.get("lastPrice", 0)),
                            "volume": float(t.get("quoteVolume", 0)),
                        }
                    except (ValueError, TypeError):
                        continue

            self.logger.debug(f"Fetched spot prices: {len(result)} symbols")
            return result

        except Exception as e:
            self.logger.error(f"Failed to fetch spot prices: {e}")
            return {}

    def fetch_futures_prices(self) -> Dict[str, Dict[str, float]]:
        """
        Fetch futures ticker prices (weight 40)

        Returns:
            {symbol: {'price': float, 'volume': float}}
        """
        try:
            resp = self.session.get(self.FUTURES_TICKER_URL, timeout=15)
            resp.raise_for_status()

            result = {}
            for t in resp.json():
                symbol = t.get("symbol", "")
                if symbol.endswith("USDT"):
                    try:
                        result[symbol] = {
                            "price": float(t.get("lastPrice", 0)),
                            "volume": float(t.get("quoteVolume", 0)),
                        }
                    except (ValueError, TypeError):
                        continue

            self.logger.debug(f"Fetched futures prices: {len(result)} symbols")
            return result

        except Exception as e:
            self.logger.error(f"Failed to fetch futures prices: {e}")
            return {}

    # ==========================================
    # Spread calculation
    # ==========================================

    def calculate_spreads(
        self,
        spot_prices: Dict[str, Dict[str, float]],
        futures_prices: Dict[str, Dict[str, float]],
        liquidity_config: Dict[str, Any],
    ) -> List[SpreadAlert]:
        """
        Calculate spreads for all symbols (bilateral liquidity check)

        Formula: spread_pct = (futures - spot) / spot * 100
        Positive = futures premium (Contango, forward arbitrage opportunity)
        Negative = futures discount (Backwardation, reverse arbitrage opportunity)

        Args:
            spot_prices: Spot price data
            futures_prices: Futures price data
            liquidity_config: Liquidity config (tiered thresholds)

        Returns:
            SpreadAlert list sorted by absolute spread descending
        """
        alerts = []
        require_both_sides = liquidity_config.get("require_both_sides", True)
        skipped_low_liquidity = 0

        for symbol in futures_prices:
            if symbol not in spot_prices:
                continue

            spot_data = spot_prices[symbol]
            futures_data = futures_prices[symbol]

            spot_price = spot_data["price"]
            futures_price = futures_data["price"]
            spot_volume = spot_data["volume"]
            futures_volume = futures_data["volume"]

            # Validate prices
            if spot_price <= 0 or futures_price <= 0:
                continue

            # Liquidity filter (tiered, bilateral check)
            coin_symbol = symbol.replace("USDT", "")
            min_volume = self.get_min_volume_for_symbol(coin_symbol, liquidity_config)

            # Futures liquidity check
            if futures_volume < min_volume:
                skipped_low_liquidity += 1
                continue

            # Spot liquidity check
            if require_both_sides and spot_volume < min_volume:
                self.logger.debug(
                    f"{coin_symbol} spot liquidity insufficient: ${spot_volume:,.0f} < ${min_volume:,.0f}"
                )
                skipped_low_liquidity += 1
                continue

            # Calculate spread
            spread_pct = (futures_price - spot_price) / spot_price * 100
            spread_type = "PREMIUM" if spread_pct > 0 else "DISCOUNT"

            alerts.append(
                SpreadAlert(
                    symbol=coin_symbol,
                    spot_price=spot_price,
                    futures_price=futures_price,
                    spread_pct=spread_pct,
                    spread_type=spread_type,
                    volume_24h=futures_volume,
                )
            )

        # Sort by absolute spread descending
        alerts.sort(key=lambda x: abs(x.spread_pct), reverse=True)

        self.logger.debug(
            f"Spread calculation: {len(alerts)} symbols passed liquidity filter, "
            f"{skipped_low_liquidity} skipped due to low liquidity"
        )
        return alerts

    # ==========================================
    # Dynamic thresholds
    # ==========================================

    async def get_thresholds(self) -> Dict[str, float]:
        """
        Get dynamic thresholds (async, from Redis)

        Returns:
            {'premium': 3.0, 'basic': 10.0}
        """
        try:
            key = self._cache.make_key("scanner", "spread", "thresholds")
            cached = await self._cache.get(key)

            if cached:
                return json.loads(cached)
        except Exception as e:
            self.logger.debug(f"Failed to read threshold cache: {e}")

        # Return defaults
        return {
            "premium": self.DEFAULT_THRESHOLD_PREMIUM,
            "basic": self.DEFAULT_THRESHOLD_BASIC,
        }

    async def set_thresholds(self, premium: float = None, basic: float = None) -> bool:
        """
        Dynamically update thresholds (async, write to Redis)

        Args:
            premium: Premium user threshold (%)
            basic: Basic user threshold (%)

        Returns:
            True if updated successfully
        """
        try:
            current = await self.get_thresholds()

            if premium is not None:
                current["premium"] = float(premium)
            if basic is not None:
                current["basic"] = float(basic)

            key = self._cache.make_key("scanner", "spread", "thresholds")
            await self._cache.set(key, json.dumps(current), ttl=0)  # Never expire

            self.logger.info(
                f"Thresholds updated: premium={current['premium']}%, basic={current['basic']}%"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to update thresholds: {e}")
            return False

    # ==========================================
    # Liquidity config (tiered, bilateral check)
    # ==========================================

    async def get_liquidity_config(self) -> Dict[str, Any]:
        """
        Get liquidity config (async, from Redis)

        Returns:
            {
                'require_both_sides': True,
                'tiers': {
                    'tier1': {'min_volume_24h': 1B, 'symbols': ['BTC', 'ETH']},
                    'tier2': {'min_volume_24h': 50M, 'symbols': [...]},
                    'default': {'min_volume_24h': 10M}
                }
            }
        """
        try:
            key = self._cache.make_key("scanner", "spread", "liquidity")
            cached = await self._cache.get(key)

            if cached:
                return json.loads(cached)
        except Exception as e:
            self.logger.debug(f"Failed to read liquidity config: {e}")

        return self.DEFAULT_LIQUIDITY_CONFIG.copy()

    async def set_liquidity_config(self, config: Dict[str, Any]) -> bool:
        """
        Update liquidity config (async, write to Redis)

        Args:
            config: Full config or partial update

        Returns:
            True if updated successfully
        """
        try:
            current = await self.get_liquidity_config()
            current.update(config)

            key = self._cache.make_key("scanner", "spread", "liquidity")
            await self._cache.set(key, json.dumps(current), ttl=0)

            self.logger.info("Liquidity config updated")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update liquidity config: {e}")
            return False

    def get_min_volume_for_symbol(self, symbol: str, config: Dict[str, Any]) -> int:
        """
        Get minimum volume threshold for a symbol based on its tier

        Args:
            symbol: Coin name (without USDT)
            config: Liquidity config

        Returns:
            Minimum 24h volume threshold
        """
        tiers = config.get("tiers", {})

        # Check tier1 and tier2
        for tier_name in ["tier1", "tier2"]:
            tier = tiers.get(tier_name, {})
            symbols = tier.get("symbols", [])
            if symbol in symbols:
                return tier.get("min_volume_24h", 10_000_000)

        # Default tier
        return tiers.get("default", {}).get("min_volume_24h", 10_000_000)

    # ==========================================
    # Cooldown (delegates to CooldownManager, async)
    # ==========================================

    async def check_cooldown(self, symbol: str, direction: str = "NEUTRAL") -> bool:
        """
        Check symbol cooldown (async, delegates to CooldownManager)

        Returns:
            True = can push, False = on cooldown
        """
        return await self._cooldown_manager.check_spread(symbol, direction)

    async def update_cooldown(self, symbol: str, direction: str = "NEUTRAL") -> None:
        """Update symbol cooldown (async, delegates to CooldownManager)"""
        await self._cooldown_manager.update_spread(symbol, direction)

    async def get_cooldown_remaining(
        self, symbol: str, direction: str = "NEUTRAL"
    ) -> float:
        """Get remaining cooldown time (minutes, async)"""
        from src.scanner.cooldown_manager import CooldownType

        return await self._cooldown_manager.get_remaining(
            CooldownType.SPREAD, symbol, direction
        )

    # ==========================================
    # Core scan
    # ==========================================

    async def scan(self, top_n: int = 1) -> Tuple[List[SpreadAlert], Dict[str, float]]:
        """
        Execute spread scan (async)

        Args:
            top_n: Return Top N results

        Returns:
            (threshold-triggered alerts, current threshold config)
        """
        self.logger.info("Starting spread scan...")

        # 1. Fetch prices
        spot_prices = self.fetch_spot_prices()
        futures_prices = self.fetch_futures_prices()

        if not spot_prices or not futures_prices:
            self.logger.warning("Failed to fetch prices, skipping scan")
            return [], await self.get_thresholds()

        # 2. Get liquidity config and calculate spreads (v1.2)
        liquidity_config = await self.get_liquidity_config()
        all_spreads = self.calculate_spreads(
            spot_prices, futures_prices, liquidity_config
        )

        # 3. Get thresholds
        thresholds = await self.get_thresholds()
        premium_threshold = thresholds["premium"]

        # 4. Filter by threshold (use Premium threshold; Basic checked at push time)
        triggered = []
        for alert in all_spreads:
            if abs(alert.spread_pct) >= premium_threshold:
                # Direction-aware cooldown
                direction = alert.spread_type  # PREMIUM or DISCOUNT
                if await self.check_cooldown(alert.symbol, direction):
                    triggered.append(alert)
                else:
                    remaining = await self.get_cooldown_remaining(alert.symbol, direction)
                    self.logger.debug(
                        f"{alert.symbol} spread {alert.spread_pct:+.2f}%, "
                        f"but on cooldown (remaining {remaining:.0f}min)"
                    )

        # 5. Return Top N
        result = triggered[:top_n]

        if result:
            self.logger.info(
                f"Spread scan complete: {len(all_spreads)} symbols, "
                f"{len(triggered)} triggered threshold, returning Top {len(result)}"
            )
            for alert in result:
                self.logger.info(f"  - {alert}")
        else:
            self.logger.debug("Spread scan complete: no threshold-triggered anomalies")

        return result, thresholds

    # ==========================================
    # Status
    # ==========================================

    async def get_status(self) -> Dict[str, Any]:
        """Get detector status (async)"""
        thresholds = await self.get_thresholds()
        liquidity_config = await self.get_liquidity_config()
        cooldown_status = self._cooldown_manager.get_status()
        return {
            "thresholds": thresholds,
            "liquidity_config": liquidity_config,
            "cooldown_hours": self.COOLDOWN_HOURS,
            "cooldown_manager": cooldown_status,
        }


# ==========================================
# Singleton
# ==========================================

_detector_instance: Optional[SpreadDetector] = None


def get_spread_detector() -> SpreadDetector:
    """Get SpreadDetector singleton"""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = SpreadDetector()
    return _detector_instance


# ==========================================
# CLI test
# ==========================================

if __name__ == "__main__":
    import sys

    # Load environment variables
    from dotenv import load_dotenv

    PROJECT_ROOT = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

    detector = SpreadDetector()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "--status":
            print("SpreadDetector status:")
            status = detector.get_status()
            for k, v in status.items():
                print(f"  {k}: {v}")

        elif cmd == "--scan":
            print("Running spread scan...")
            alerts, thresholds = detector.scan(top_n=5)
            print(
                f"\nThresholds: Premium>={thresholds['premium']}%, Basic>={thresholds['basic']}%"
            )
            print(f"\nThreshold-triggered symbols (Top 5):")
            for i, alert in enumerate(alerts, 1):
                print(
                    f"  {i}. {alert.symbol}: {alert.spread_pct:+.2f}% "
                    f"(spot ${alert.spot_price:,.2f} / futures ${alert.futures_price:,.2f})"
                )

        elif cmd == "--set-threshold":
            if len(sys.argv) >= 4:
                level = sys.argv[2]  # premium or basic
                value = float(sys.argv[3])
                if level == "premium":
                    detector.set_thresholds(premium=value)
                elif level == "basic":
                    detector.set_thresholds(basic=value)
                print(f"Threshold updated: {level}={value}%")
            else:
                print("Usage: --set-threshold <premium|basic> <value>")

        else:
            print(f"Unknown command: {cmd}")
            print("Available commands: --status, --scan, --set-threshold")
    else:
        print("SpreadDetector v1.0")
        print("Usage:")
        print("  python -m src.scanner.spread_detector --status")
        print("  python -m src.scanner.spread_detector --scan")
        print("  python -m src.scanner.spread_detector --set-threshold premium 5.0")
