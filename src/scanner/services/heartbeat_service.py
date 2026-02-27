"""
Trend Heartbeat Service

Service extracted from ScannerScheduler, responsible for:
  - Detecting coins approaching breakout
  - Pushing heartbeat notifications
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.scanner.trend_pulse import TrendPulseMonitor
from src.scanner.formatter import ScannerFormatter
from src.notifications.telegram_broadcaster import get_broadcaster
from src.core.structured_logger import get_logger


class HeartbeatService:
    """
    Trend heartbeat service

    Responsibilities:
      - Detect coins approaching breakout
      - Push heartbeat notifications (at most once per coin per day)
    """

    def __init__(
        self,
        trend_monitor: TrendPulseMonitor = None,
        broadcaster = None
    ):
        """
        Initialize heartbeat service

        Args:
            trend_monitor: Trend monitor
            broadcaster: Telegram broadcaster
        """
        self.logger = get_logger(__name__)

        # Dependency injection
        self._trend_monitor = trend_monitor or TrendPulseMonitor()
        self._broadcaster = broadcaster or get_broadcaster()

        # Heartbeat push records (at most once per coin per day)
        self._heartbeat_sent: Dict[str, datetime] = {}

    async def check(self) -> List[str]:
        """
        Check trend heartbeat

        Returns:
            List of coins that were notified
        """
        self.logger.info("Checking trend heartbeat...")

        try:
            near_breakout = self._trend_monitor.get_near_breakout_coins()
            sent = []

            for status in near_breakout:
                today = datetime.now(timezone.utc).date()
                key = f"{status.symbol}_{today}"

                # At most once per coin per day
                if key in self._heartbeat_sent:
                    continue

                message = ScannerFormatter.format_trend_pulse(
                    symbol=status.symbol,
                    current_price=status.current_price,
                    breakout_price=status.breakout_price,
                    direction='up'
                )

                # Push to all channels
                if self._broadcaster and self._broadcaster.is_ready:
                    for lvl in ['BASIC', 'PREMIUM']:
                        await self._broadcaster.send_to_channel(message, level=lvl)

                self._heartbeat_sent[key] = datetime.now(timezone.utc)
                sent.append(status.symbol)

                self.logger.info(
                    f"Trend heartbeat: {status.symbol} near breakout "
                    f"(${status.current_price:,.2f}, distance {status.distance_pct:.1f}%)"
                )

            return sent

        except Exception as e:
            self.logger.error(f"Heartbeat check failed: {e}")
            return []

    def get_all_status(self) -> dict:
        """Get trend status for all coins"""
        return self._trend_monitor.get_all_status()

    def clear_sent_records(self) -> None:
        """Clear push records (for testing)"""
        self._heartbeat_sent.clear()


# Factory function
_heartbeat_service: Optional[HeartbeatService] = None


def get_heartbeat_service() -> HeartbeatService:
    """Get HeartbeatService singleton"""
    global _heartbeat_service
    if _heartbeat_service is None:
        _heartbeat_service = HeartbeatService()
    return _heartbeat_service
