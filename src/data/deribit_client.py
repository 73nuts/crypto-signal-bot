"""
Deribit public API client.

Responsibilities: fetch DVOL (volatility index) and options implied volatility data.
Used by the Swing strategy IV regime filter.

Deribit API docs: https://docs.deribit.com/
All endpoints are public and require no authentication.
"""

import logging
import os
import time
from typing import Optional

import pandas as pd
import requests
from requests.exceptions import RequestException

logger = logging.getLogger("deribit_client")


class DeribitClient:
    """Deribit REST API client (public endpoints only)."""

    BASE_URL = "https://www.deribit.com/api/v2"
    TIMEOUT = 10

    def __init__(self):
        self.session = self._setup_session()

    def _setup_session(self) -> requests.Session:
        """Configure requests.Session (connection reuse + proxy detection)."""
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            }
        )

        proxies = {}
        for proxy_var in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
            proxy_value = os.environ.get(proxy_var)
            if proxy_value:
                protocol = "https" if "https" in proxy_var.lower() else "http"
                proxies[protocol] = proxy_value

        if proxies:
            session.proxies.update(proxies)
            logger.info(f"Deribit client proxy configured: {list(proxies.keys())}")

        return session

    def _request(self, method: str, params: dict = None) -> Optional[dict]:
        """Unified request method; returns the result field or None."""
        try:
            url = f"{self.BASE_URL}/public/{method}"
            response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            response.raise_for_status()
            data = response.json()
            return data.get("result")
        except RequestException as e:
            logger.warning(f"Deribit API request failed [{method}]: {e}")
            return None
        except (ValueError, KeyError) as e:
            logger.warning(f"Deribit API response parse failed [{method}]: {e}")
            return None

    def get_dvol(self, currency: str = "BTC") -> Optional[float]:
        """
        Get current DVOL (Deribit Volatility Index).

        Fetches the most recent 1-hour close from get_volatility_index_data.

        Returns:
            DVOL as annualized percentage (e.g. 52.3), or None on failure.
        """
        now_ms = int(time.time() * 1000)
        # Fetch 2 hours of data to ensure at least 1 data point
        start_ms = now_ms - 2 * 3600 * 1000

        result = self._request(
            "get_volatility_index_data",
            {
                "currency": currency,
                "start_timestamp": start_ms,
                "end_timestamp": now_ms,
                "resolution": "3600",
            },
        )

        if not result or not result.get("data"):
            logger.warning(f"DVOL data empty [{currency}]")
            return None

        # data format: [[timestamp, open, high, low, close], ...]
        latest = result["data"][-1]
        dvol = float(latest[4])  # close
        logger.debug(f"[{currency}] DVOL current: {dvol:.1f}%")
        return dvol

    def get_dvol_history(
        self, currency: str = "BTC", days: int = 90
    ) -> Optional[pd.DataFrame]:
        """
        Get DVOL historical data (daily frequency).

        Returns:
            DataFrame[timestamp, open, high, low, close], or None on failure.
        """
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - days * 24 * 3600 * 1000

        result = self._request(
            "get_volatility_index_data",
            {
                "currency": currency,
                "start_timestamp": start_ms,
                "end_timestamp": now_ms,
                "resolution": "1D",
            },
        )

        if not result or not result.get("data"):
            logger.warning(f"DVOL history data empty [{currency}]")
            return None

        df = pd.DataFrame(
            result["data"],
            columns=["timestamp", "open", "high", "low", "close"],
        )
        df["timestamp"] = df["timestamp"].astype(int)
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)

        logger.debug(f"[{currency}] DVOL history: {len(df)} days")
        return df

    def get_atm_iv(self, currency: str = "BTC") -> Optional[float]:
        """
        Get ATM implied volatility.

        Uses get_book_summary_by_currency(kind=option) to find the nearest-expiry
        option closest to ATM and returns its mark_iv.

        Returns:
            ATM IV as percentage (e.g. 48.5), or None on failure.
        """
        result = self._request(
            "get_book_summary_by_currency",
            {
                "currency": currency,
                "kind": "option",
            },
        )

        if not result:
            return None

        # Get underlying price
        underlying_price = None
        for item in result:
            if item.get("underlying_price"):
                underlying_price = float(item["underlying_price"])
                break

        if not underlying_price:
            logger.warning(f"Cannot get {currency} underlying price")
            return None

        best_option = None
        best_distance = float("inf")

        for item in result:
            mark_iv = item.get("mark_iv")
            if not mark_iv or mark_iv <= 0:
                continue

            # Parse instrument_name: BTC-28JUN24-70000-C
            name = item.get("instrument_name", "")
            parts = name.split("-")
            if len(parts) != 4:
                continue

            try:
                strike = float(parts[2])
            except ValueError:
                continue

            # Select call option closest to ATM
            if parts[3] != "C":
                continue

            distance = abs(strike - underlying_price) / underlying_price
            if distance < best_distance:
                best_distance = distance
                best_option = item

        if not best_option or best_distance > 0.05:
            logger.warning(f"No suitable ATM option found [{currency}]")
            return None

        atm_iv = float(best_option["mark_iv"])
        logger.debug(
            f"[{currency}] ATM IV: {atm_iv:.1f}% "
            f"(instrument={best_option['instrument_name']})"
        )
        return atm_iv

    def get_25delta_skew(self, currency: str = "BTC") -> Optional[float]:
        """
        Get 25-delta risk reversal (skew).

        skew = IV(25d put) - IV(25d call)
        Positive = put more expensive = market skewed toward downside protection.

        Uses /public/ticker to get greeks.delta for near-expiry options,
        finding delta ~= -0.25 put and delta ~= 0.25 call.

        Returns:
            Skew as percentage (e.g. 3.2 = put 3.2% more expensive than call), or None on failure.
        """
        # Fetch all options book summary first
        summaries = self._request(
            "get_book_summary_by_currency",
            {
                "currency": currency,
                "kind": "option",
            },
        )

        if not summaries:
            return None

        # Filter for active options with mark_iv
        active_options = []
        for item in summaries:
            mark_iv = item.get("mark_iv")
            name = item.get("instrument_name", "")
            if not mark_iv or mark_iv <= 0:
                continue
            if not name:
                continue
            active_options.append(name)

        if not active_options:
            logger.warning(f"No active options [{currency}]")
            return None

        # Fetch ticker for each option to get greeks.
        # Limit to first 100 (sorted by name; nearest expiry first) to reduce API calls.

        active_options.sort()
        active_options = active_options[:100]

        best_put = None  # (distance_from_0.25, mark_iv, instrument)
        best_call = None

        for instrument in active_options:
            ticker = self._request("ticker", {"instrument_name": instrument})
            if not ticker:
                continue

            greeks = ticker.get("greeks")
            if not greeks:
                continue

            delta = greeks.get("delta")
            mark_iv = ticker.get("mark_iv")
            if delta is None or mark_iv is None:
                continue

            # 25-delta put: delta ~= -0.25
            if delta < 0:
                dist = abs(abs(delta) - 0.25)
                if best_put is None or dist < best_put[0]:
                    best_put = (dist, float(mark_iv), instrument)

            # 25-delta call: delta ~= 0.25
            else:
                dist = abs(delta - 0.25)
                if best_call is None or dist < best_call[0]:
                    best_call = (dist, float(mark_iv), instrument)

        if not best_put or not best_call:
            logger.warning(f"25-delta put/call not found [{currency}]")
            return None

        # Tolerance check: delta must not deviate from 0.25 by more than 0.10
        if best_put[0] > 0.10 or best_call[0] > 0.10:
            logger.warning(
                f"25-delta match deviation too large [{currency}]: "
                f"put_dist={best_put[0]:.3f}, call_dist={best_call[0]:.3f}"
            )
            return None

        skew = best_put[1] - best_call[1]
        logger.debug(
            f"[{currency}] 25d skew: {skew:.1f}% "
            f"(put_iv={best_put[1]:.1f}% [{best_put[2]}], "
            f"call_iv={best_call[1]:.1f}% [{best_call[2]}])"
        )
        return skew
