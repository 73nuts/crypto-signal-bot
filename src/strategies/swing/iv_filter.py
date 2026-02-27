"""
IV regime filter

Judges market volatility regime using the Deribit DVOL index.
Observation mode: records data only, does not block entries (controlled by feature flag).

Matches the fail-open pattern of _check_funding_rate():
- fail-open: returns "PASS" on API failure
- Three-level result: PASS / CAUTION / SKIP
"""

import logging
from typing import Optional

import numpy as np

from src.core.cache import CacheManager
from src.data.deribit_client import DeribitClient
from src.strategies.swing.config import IV_FILTER_CONFIG

logger = logging.getLogger("iv_filter")


class IVFilter:
    """IV regime filter."""

    def __init__(self, deribit_client: DeribitClient, cache: CacheManager):
        self.client = deribit_client
        self.cache = cache
        self._config = IV_FILTER_CONFIG

    async def check_iv_regime(self, symbol: str) -> str:
        """
        Check IV regime (called before entry).

        Args:
            symbol: Asset symbol (BTC/ETH), mapped to Deribit currency.

        Returns:
            "PASS"    - IV normal, entry allowed
            "CAUTION" - IV elevated, log warning
            "SKIP"    - IV extreme, suggest skipping entry
        """
        try:
            currency = self._symbol_to_currency(symbol)

            # 1. Get current DVOL (with cache)
            dvol = await self._get_dvol_cached(currency)
            if dvol is None:
                logger.warning(f"[{symbol}] DVOL fetch failed, skipping IV check")
                return "PASS"  # fail-open

            # 2. Get DVOL history -> compute percentile
            dvol_percentile = await self._get_dvol_percentile(currency, dvol)

            # 3. Get 25-delta skew (recorded only, not used for decisions in observation mode)
            skew = await self._get_skew_cached(currency)

            # 4. Determine regime
            skip_pct = self._config.get("skip_percentile", 0.85)
            caution_pct = self._config.get("caution_percentile", 0.70)

            if dvol_percentile is not None and dvol_percentile > skip_pct:
                result = "SKIP"
            elif dvol_percentile is not None and dvol_percentile > caution_pct:
                result = "CAUTION"
            else:
                result = "PASS"

            # 5. Log all values
            pct_str = f"{dvol_percentile:.0%}" if dvol_percentile is not None else "N/A"
            skew_str = f"{skew:.1f}%" if skew is not None else "N/A"
            logger.info(
                f"[{symbol}] IV regime: DVOL={dvol:.1f}%, "
                f"percentile={pct_str}, skew={skew_str} -> {result}"
            )

            return result

        except Exception as e:
            logger.error(f"[{symbol}] IV regime check error: {e}")
            return "PASS"  # fail-open

    async def _get_dvol_cached(self, currency: str) -> Optional[float]:
        """Get DVOL with Redis cache."""
        cache_key = self.cache.make_key("deribit", "dvol", currency)
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return float(cached)

        dvol = self.client.get_dvol(currency)
        if dvol is not None:
            ttl = self._config.get("cache_ttl", 3600)
            await self.cache.set(cache_key, dvol, ttl=ttl)
        return dvol

    async def _get_dvol_percentile(
        self, currency: str, current_dvol: float
    ) -> Optional[float]:
        """Compute the percentile rank of the current DVOL in historical data."""
        cache_key = self.cache.make_key("deribit", "dvol_hist", currency)
        cached_closes = await self.cache.get(cache_key)

        if cached_closes is not None and isinstance(cached_closes, list):
            closes = cached_closes
        else:
            days = self._config.get("dvol_lookback_days", 90)
            df = self.client.get_dvol_history(currency, days=days)
            if df is None or df.empty:
                logger.warning(f"Insufficient DVOL history [{currency}]")
                return None
            closes = df["close"].tolist()
            ttl = self._config.get("cache_ttl", 3600)
            await self.cache.set(cache_key, closes, ttl=ttl)

        if len(closes) < 10:
            logger.warning(f"Insufficient DVOL history points [{currency}]: {len(closes)}")
            return None

        arr = np.array(closes, dtype=float)
        percentile = float(np.sum(arr < current_dvol) / len(arr))
        return percentile

    async def _get_skew_cached(self, currency: str) -> Optional[float]:
        """Get 25-delta skew with Redis cache."""
        cache_key = self.cache.make_key("deribit", "skew", currency)
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return float(cached)

        skew = self.client.get_25delta_skew(currency)
        if skew is not None:
            ttl = self._config.get("cache_ttl", 3600)
            await self.cache.set(cache_key, skew, ttl=ttl)
        return skew

    @staticmethod
    def _symbol_to_currency(symbol: str) -> str:
        """Map Binance symbol to Deribit currency."""
        # Deribit supports: BTC, ETH
        # BNB/SOL have no DVOL on Deribit, fall back to BTC
        mapping = {
            "BTC": "BTC",
            "ETH": "ETH",
            "BNB": "BTC",  # no BNB DVOL, use BTC
            "SOL": "BTC",  # no SOL DVOL, use BTC
        }
        return mapping.get(symbol, "BTC")
