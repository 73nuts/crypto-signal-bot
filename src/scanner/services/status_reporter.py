"""
Scanner Status Reporter

Service extracted from ScannerScheduler, responsible for:
  - Displaying scheduler status
  - Formatting status output
"""

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.core.config import settings
from src.scanner.alert_detector import AlertDetector

if TYPE_CHECKING:
    from src.scanner.scheduler import ScannerScheduler


def show_scanner_status(scheduler: 'ScannerScheduler') -> None:
    """Display scanner status to stdout. Uses print() intentionally for CLI output."""
    print("\n" + "=" * 50)
    print("Ignis Scanner Status")
    print("=" * 50)

    # Run a test scan to get coin count
    alerts, total, market_status = asyncio.run(scheduler.detector.scan(top_n=1))

    print(f"\nMonitoring: {total} liquid coins")
    print(f"  (24h volume > ${AlertDetector.MIN_VOLUME_24H/1e6:.0f}M)")

    print("\nArchitecture: Funnel model + Z-Score dynamic threshold")
    print("  Step 1: Broad scan (Redis cache diff)")
    print("  Step 2: Precision validation (async kline fetch)")

    print("\nPush strategy:")
    print(f"  Scan interval: {scheduler.SCAN_INTERVAL_MIN} min")
    print(f"  Global cooldown: {scheduler.GLOBAL_COOLDOWN_MIN} min")
    print(f"  Coin cooldown: {scheduler.COIN_COOLDOWN_HOURS} hours (direction-aware)")
    print(f"  Max per scan: Top {scheduler.TOP_N}")

    print("\nDetection thresholds (Z-Score dynamic):")
    print(f"  Z-Score threshold: > {AlertDetector.Z_SCORE_THRESHOLD}")
    print("  Calculation: |5min change| / historical volatility (std)")
    print(f"  Volatility period: {AlertDetector.VOLATILITY_PERIOD} candles")
    print(f"  Volume spike: > {AlertDetector.VOLUME_SPIKE_X}x average volume")

    print("\nDirection-aware cooldown:")
    print("  Same direction: 4-hour cooldown")
    print("  Opposite direction: immediate trigger (captures V-reversals)")

    print("\nSector mapping auto-update:")
    print("  Schedule: Every Monday 10:00 UTC+8")
    print("  Flow: detect new coins -> AI classify -> admin review")

    print("\nMarket status:")
    if market_status.get('btc'):
        btc = market_status['btc']
        print(f"  BTC: ${btc['price']:,.0f} ({btc['change_24h']:+.1f}%)")
    print(f"  Sentiment: {market_status.get('sentiment', '-')} {market_status.get('sentiment_icon', '')}")

    print("\nNotification channels:")
    if scheduler._broadcaster and scheduler._broadcaster.is_ready:
        signal_targets = settings.get_all_signal_targets()
        targets_str = ", ".join(k for k, v in signal_targets.items() if v)
        print(f"  Telegram: enabled (channel priority: {targets_str})")
    else:
        print("  Telegram: disabled")
    print(f"  WeChat: {'enabled' if scheduler._wechat_sender and scheduler._wechat_sender.enabled else 'disabled'}")

    # Cooldown status
    if scheduler._last_push_time:
        elapsed = (datetime.now(timezone.utc) - scheduler._last_push_time).total_seconds() / 60
        print(f"\nLast push: {elapsed:.0f} minutes ago")

    print("\n" + "=" * 50)
