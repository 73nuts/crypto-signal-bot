#!/usr/bin/env python3
"""
Orphan position cleanup script.

Closes positions that exist on the exchange but have no DB record.

Usage:
    python scripts/close_orphan_position.py --symbol ETH --side SHORT

    # Dry run (query only, no execution)
    python scripts/close_orphan_position.py --symbol ETH --dry-run
"""

import argparse
import logging
import os
import sys

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from src.trading.binance_trading_client import BinanceTradingClient

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_client(symbol: str) -> BinanceTradingClient:
    """Initialize testnet trading client."""
    api_key = os.getenv("BINANCE_TESTNET_API_KEY")
    api_secret = os.getenv("BINANCE_TESTNET_API_SECRET")

    if not api_key or not api_secret:
        raise ValueError("Missing BINANCE_TESTNET_API_KEY/SECRET environment variables")

    return BinanceTradingClient(
        api_key=api_key, api_secret=api_secret, testnet=True, symbol=symbol
    )


def close_orphan_position(symbol: str, dry_run: bool = False):
    """
    Close orphan position for the given symbol.

    Args:
        symbol: Asset symbol (ETH/BTC/SOL etc.)
        dry_run: Query only, no execution
    """
    client = get_client(symbol)
    trading_symbol = f"{symbol}USDT"

    # Step 1: Query current positions
    logger.info(f"=== Query {trading_symbol} positions ===")
    positions = client.get_positions()

    position_amt = 0
    position_side = None

    for pos in positions:
        # get_positions() returns converted format:
        # {"symbol", "side", "contracts", "entryPrice", "unrealizedPnl", ...}
        contracts = float(pos.get("contracts", 0))
        side = pos.get("side", "").lower()

        if contracts != 0:
            # Calculate signed position_amt based on side
            position_amt = contracts if side == "long" else -contracts
            position_side = "LONG" if side == "long" else "SHORT"
            entry_price = pos.get("entryPrice", "N/A")
            unrealized_pnl = pos.get("unrealizedPnl", "N/A")
            logger.info(
                f"Found position: {position_side} {contracts} @ {entry_price}, "
                f"unrealized PnL: {unrealized_pnl}"
            )

    if position_amt == 0:
        logger.info(f"{trading_symbol} has no open position, nothing to clean")
        return

    # Step 2: Query open orders
    logger.info(f"=== Query {trading_symbol} open orders ===")
    open_orders = client.get_open_orders()  # No args, uses instance's trading_symbol

    if open_orders:
        logger.info(f"Found {len(open_orders)} open orders:")
        for order in open_orders:
            logger.info(
                f"  - {order.get('type')} {order.get('side')} "
                f"qty={order.get('origQty')} @ {order.get('stopPrice', order.get('price', 'N/A'))}"
            )
    else:
        logger.info("No open orders")

    if dry_run:
        logger.info("=== DRY RUN mode, no actual execution ===")
        return

    # Step 3: Cancel all open orders
    logger.info(f"=== Cancel all {trading_symbol} orders ===")
    try:
        # Cancel regular orders
        client.cancel_all_orders()
        logger.info("Regular orders cancelled")
    except Exception as e:
        logger.warning(f"Cancel regular orders failed (may have none): {e}")

    try:
        # Cancel Algo orders (stop-loss / take-profit)
        client.cancel_all_algo_orders()
        logger.info("Algo orders cancelled")
    except Exception as e:
        logger.warning(f"Cancel Algo orders failed (may have none): {e}")

    # Step 4: Market close position
    logger.info(f"=== Market close {trading_symbol} ===")

    # SHORT position: close with BUY; LONG position: close with SELL
    close_side = "BUY" if position_amt < 0 else "SELL"
    close_qty = abs(position_amt)

    # Determine position_side parameter (required for Hedge Mode)
    pos_side = "SHORT" if position_amt < 0 else "LONG"

    logger.info(f"Executing: {close_side} {close_qty} (position_side={pos_side})")

    try:
        order = client.create_market_order(
            side=close_side, quantity=close_qty, position_side=pos_side
        )

        if order and order.get("status") != "error":
            logger.info("Position closed successfully!")
            logger.info(f"  Order ID: {order.get('id')}")
            logger.info(f"  Fill price: {order.get('average', 'N/A')}")
            logger.info(f"  Status: {order.get('status')}")
        else:
            error_msg = order.get("error_message") if order else "API returned None"
            logger.error(f"Close failed: {error_msg}")

    except Exception as e:
        logger.error(f"Close exception: {e}")
        raise

    # Step 5: Verify
    logger.info("=== Verify cleanup result ===")
    client.invalidate_cache()
    positions_after = client.get_positions()

    for pos in positions_after:
        amt = float(pos.get("positionAmt", 0))
        if amt != 0:
            logger.warning(f"Warning: residual position remains: {amt}")
            return

    logger.info(f"{trading_symbol} position fully cleaned")


def main():
    parser = argparse.ArgumentParser(description="Close orphan position")
    parser.add_argument(
        "--symbol", "-s", required=True, help="Asset symbol (ETH/BTC/SOL etc.)"
    )
    parser.add_argument("--dry-run", "-d", action="store_true", help="Query only, no execution")

    args = parser.parse_args()

    try:
        close_orphan_position(symbol=args.symbol.upper(), dry_run=args.dry_run)
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
