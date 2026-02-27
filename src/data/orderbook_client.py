"""
Orderbook data client module.
Fetches Binance perpetual futures orderbook data to identify large walls and buy/sell pressure.
"""

import logging
from typing import List, Optional, Tuple, TypedDict

import requests
from requests.exceptions import RequestException, Timeout


class OrderbookData(TypedDict):
    """Orderbook data structure."""
    bids: List[List[float]]  # [[price, quantity], ...]
    asks: List[List[float]]  # [[price, quantity], ...]
    timestamp: int


class OrderbookClient:
    """Orderbook data client — wraps Binance orderbook API."""

    def __init__(self, symbol: str):
        """
        Args:
            symbol: Coin symbol (e.g. 'ETH', 'SOL')
        """
        self.symbol = symbol
        self.logger = logging.getLogger(__name__)

        # Binance futures API base URL
        self.base_url = 'https://fapi.binance.com'

        # Set up requests session
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
        })

    def fetch_orderbook(self, limit: int = 100) -> Optional[OrderbookData]:
        """Fetch orderbook data.

        Args:
            limit: Depth levels (5/10/20/50/100/500/1000), default 100.
                   100 levels covers ~±1% from mid-price, sufficient for swing analysis.

        Returns:
            OrderbookData with:
                - bids: Buy orders [[price, qty], ...] sorted by price descending
                - asks: Sell orders [[price, qty], ...] sorted by price ascending
                - timestamp: Unix timestamp (ms)
            None on failure.

        Example:
            >>> client = OrderbookClient('ETH')
            >>> orderbook = client.fetch_orderbook(100)
            >>> if orderbook:
            ...     best_bid = orderbook['bids'][0][0]  # highest bid
            ...     best_ask = orderbook['asks'][0][0]  # lowest ask
            ...     spread = best_ask - best_bid
        """
        try:
            # Build trading pair symbol (Binance format: ETHUSDT)
            binance_symbol = f"{self.symbol}USDT"

            # Build API request
            endpoint = f"{self.base_url}/fapi/v1/depth"
            params = {
                'symbol': binance_symbol,
                'limit': limit
            }

            # Send request (5s timeout)
            response = self.session.get(endpoint, params=params, timeout=5)
            response.raise_for_status()

            data = response.json()

            # Validate data format
            if 'bids' not in data or 'asks' not in data:
                self.logger.error(f"{self.symbol} orderbook data format error: missing bids/asks fields")
                return None

            # Cast types (string -> float)
            orderbook: OrderbookData = {
                'bids': [[float(price), float(qty)] for price, qty in data['bids']],
                'asks': [[float(price), float(qty)] for price, qty in data['asks']],
                'timestamp': data.get('lastUpdateId', 0)  # use lastUpdateId as timestamp
            }

            self.logger.info(
                f"{self.symbol} orderbook fetched: "
                f"bids={len(orderbook['bids'])} levels, "
                f"asks={len(orderbook['asks'])} levels, "
                f"best_bid=${orderbook['bids'][0][0]:.2f}, "
                f"best_ask=${orderbook['asks'][0][0]:.2f}"
            )

            return orderbook

        except Timeout:
            self.logger.warning(f"{self.symbol} orderbook API timeout (>5s)")
            return None
        except RequestException as e:
            self.logger.warning(f"{self.symbol} orderbook API request failed: {e}")
            return None
        except (KeyError, ValueError, IndexError) as e:
            self.logger.error(f"{self.symbol} orderbook data parse failed: {e}")
            return None

    def get_best_bid_ask(self, orderbook: OrderbookData) -> Tuple[float, float]:
        """Get best bid and ask prices.

        Args:
            orderbook: Orderbook data

        Returns:
            Tuple[float, float]: (best_bid, best_ask)
        """
        if not orderbook or not orderbook['bids'] or not orderbook['asks']:
            return 0.0, 0.0

        best_bid = orderbook['bids'][0][0]
        best_ask = orderbook['asks'][0][0]

        return best_bid, best_ask

    def get_spread(self, orderbook: OrderbookData) -> float:
        """Calculate bid-ask spread.

        Args:
            orderbook: Orderbook data

        Returns:
            float: Spread (ask - bid)
        """
        best_bid, best_ask = self.get_best_bid_ask(orderbook)
        if best_bid == 0.0 or best_ask == 0.0:
            return 0.0

        return best_ask - best_bid

    def get_spread_percent(self, orderbook: OrderbookData) -> float:
        """Calculate bid-ask spread as percentage.

        Args:
            orderbook: Orderbook data

        Returns:
            float: Spread percentage relative to best bid
        """
        best_bid, best_ask = self.get_best_bid_ask(orderbook)
        if best_bid == 0.0:
            return 0.0

        spread = best_ask - best_bid
        return (spread / best_bid) * 100
