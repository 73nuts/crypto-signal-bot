"""
IV regime filter unit tests.

Tests the core logic of IVFilter with mocked DeribitClient and CacheManager.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd

from src.strategies.swing.iv_filter import IVFilter


def _run(coro):
    """Run a coroutine synchronously (project standard pattern)."""
    return asyncio.run(coro)


def _make_filter(dvol=50.0, history=None, skew=2.5, cache_get=None):
    """Create an IVFilter with mock dependencies."""
    client = MagicMock()
    client.get_dvol.return_value = dvol
    client.get_dvol_history.return_value = (
        history
        if history is not None
        else pd.DataFrame({"close": np.linspace(30, 70, 90)})
    )
    client.get_25delta_skew.return_value = skew

    cache = AsyncMock()
    cache.get.return_value = cache_get  # No cache by default
    cache.set.return_value = True
    cache.make_key.side_effect = lambda *args: ":".join(args)

    return IVFilter(client, cache), client, cache


class TestIVFilter:
    """IV regime filter tests."""

    def test_dvol_normal_pass(self):
        """DVOL at 50th percentile -> PASS."""
        f, _, _ = _make_filter(dvol=50.0)
        result = _run(f.check_iv_regime("BTC"))
        assert result == "PASS"

    def test_dvol_high_caution(self):
        """DVOL at 75th percentile -> CAUTION."""
        # 75th percentile ~60.0 in uniform distribution 30-70
        f, _, _ = _make_filter(dvol=60.0)
        result = _run(f.check_iv_regime("BTC"))
        assert result == "CAUTION"

    def test_dvol_extreme_skip(self):
        """DVOL at 90th percentile -> SKIP."""
        # 90th percentile ~66.0
        f, _, _ = _make_filter(dvol=66.0)
        result = _run(f.check_iv_regime("BTC"))
        assert result == "SKIP"

    def test_dvol_low_pass(self):
        """Very low DVOL -> PASS."""
        f, _, _ = _make_filter(dvol=35.0)
        result = _run(f.check_iv_regime("BTC"))
        assert result == "PASS"

    def test_deribit_api_failure_failopen(self):
        """API failure -> PASS (fail-open)."""
        f, _, _ = _make_filter(dvol=None)
        result = _run(f.check_iv_regime("BTC"))
        assert result == "PASS"

    def test_dvol_history_failure_failopen(self):
        """DVOL history retrieval failure -> PASS."""
        f, _, _ = _make_filter(dvol=50.0, history=None)
        f.client.get_dvol_history.return_value = None
        result = _run(f.check_iv_regime("BTC"))
        assert result == "PASS"

    def test_exception_failopen(self):
        """Exception -> PASS (fail-open)."""
        f, client, _ = _make_filter()
        client.get_dvol.side_effect = Exception("network error")
        result = _run(f.check_iv_regime("BTC"))
        assert result == "PASS"

    def test_skew_logging(self, caplog):
        """Skew data is correctly logged."""
        f, _, _ = _make_filter(dvol=45.0, skew=-3.2)
        with caplog.at_level(logging.INFO, logger="iv_filter"):
            _run(f.check_iv_regime("BTC"))
        assert "skew=-3.2%" in caplog.text
        assert "PASS" in caplog.text

    def test_cache_hit(self):
        """Second call uses cache, does not call API again."""
        f, client, cache = _make_filter(dvol=50.0)

        # First call: cache miss
        _run(f.check_iv_regime("BTC"))
        first_dvol_calls = client.get_dvol.call_count

        # Simulate cache hit (dvol + history + skew)
        cache.get.return_value = 50.0
        _run(f.check_iv_regime("BTC"))

        # get_dvol should not be called again
        assert client.get_dvol.call_count == first_dvol_calls

    def test_symbol_mapping_bnb(self):
        """BNB maps to BTC (Deribit has no BNB DVOL)."""
        f, _, cache = _make_filter()
        _run(f.check_iv_regime("BNB"))
        calls = [str(c) for c in cache.make_key.call_args_list]
        assert any("BTC" in c for c in calls)

    def test_symbol_mapping_eth(self):
        """ETH maps to ETH."""
        f, _, cache = _make_filter()
        _run(f.check_iv_regime("ETH"))
        calls = [str(c) for c in cache.make_key.call_args_list]
        assert any("ETH" in c for c in calls)

    def test_insufficient_history(self):
        """Insufficient history (< 10 days) -> PASS."""
        short_history = pd.DataFrame({"close": [40, 45, 50, 55, 60]})
        f, _, _ = _make_filter(dvol=50.0, history=short_history)
        result = _run(f.check_iv_regime("BTC"))
        assert result == "PASS"

    def test_percentile_boundary_at_caution(self):
        """Exactly at CAUTION threshold -> CAUTION."""
        history = pd.DataFrame({"close": list(range(100))})
        f, _, _ = _make_filter(dvol=71.0, history=history)
        result = _run(f.check_iv_regime("BTC"))
        assert result == "CAUTION"

    def test_percentile_boundary_at_skip(self):
        """Exactly at SKIP threshold -> SKIP."""
        history = pd.DataFrame({"close": list(range(100))})
        f, _, _ = _make_filter(dvol=86.0, history=history)
        result = _run(f.check_iv_regime("BTC"))
        assert result == "SKIP"
