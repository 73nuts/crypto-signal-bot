"""
Scanner Scheduler

Aggregated push mode, no information overload.

Core features:
  - Single full-market API scan (weight 40)
  - 60-minute aggregation window
  - Top 1 principle, only push most significant
  - 4-hour per-coin+direction cooldown
  - Cross-type cooldown: 10 minutes per coin
  - Silent mode: no push when no anomalies

Usage:
  python -m src.scanner.scheduler --status
  python -m src.scanner.scheduler --scan-now
  python -m src.scanner.scheduler --daily-brief
  python -m src.scanner.scheduler

Refactored: delegated to independent services (DailyBriefService, HeartbeatService, SectorService)
"""

import os
import sys
import asyncio
import signal
import schedule
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

# Load environment variables
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))
load_dotenv(os.path.join(PROJECT_ROOT, '.env.telegram'))

from src.scanner.alert_detector import AlertDetector, Alert
from src.scanner.formatter import ScannerFormatter
from src.scanner.trend_pulse import TrendPulseMonitor
from src.scanner.ai_client import OpenRouterClient
from src.scanner.sector_aggregator import get_sector_aggregator
from src.scanner.spread_detector import SpreadDetector, get_spread_detector
from src.scanner.orderbook_detector import get_orderbook_detector
from src.scanner.cooldown_manager import (
    get_cooldown_manager,
    CooldownManager,
    CooldownType,
    Direction,
)
from src.notifications.telegram_broadcaster import get_broadcaster
from src.core.config import settings
from src.core.message_bus import get_message_bus
from src.core.events import (
    AlertDetectedEvent,
    DailyPulseReadyEvent,
    SpreadDetectedEvent,
    OrderbookImbalanceEvent,
)
from src.core.tracing import TraceContext
from src.core.structured_logger import get_logger

# Extracted independent services
from src.scanner.services import (
    DailyBriefService,
    HeartbeatService,
    SectorService,
    show_scanner_status,
)


class ScannerScheduler:
    """
    Scanner Scheduler (Async Native)

    Push strategy:
      - Scan interval: 3 minutes
      - Global cooldown: 60 minutes (at least 60 minutes between pushes)
      - Coin cooldown: 4 hours (same coin+direction, reverse not cooled)
      - Top N: at most 1 per scan (only most significant)
      - Silent mode: no push when no anomalies

    Channel priority + noise reduction:
      - Unified push via TelegramBroadcaster
      - Channel priority, fallback to Group
      - Global cooldown increased to 60 minutes, TopN reduced to 1

    Direction-aware cooldown:
      - Cooldown key = symbol + direction (PUMP/DROP)
      - After BTC PUMP, no BTC PUMP for 4 hours
      - But if BTC DROP, push immediately (V-reversal signal)
    """

    # Push parameters
    SCAN_INTERVAL_MIN = 3           # Scan interval (minutes)
    GLOBAL_COOLDOWN_MIN = 60        # Global cooldown (minutes)
    COIN_COOLDOWN_HOURS = 4         # Coin cooldown (hours)
    TOP_N = 1                       # Max pushes per scan, only most significant

    def __init__(self):
        """Initialize scheduler"""
        self.logger = get_logger(__name__)

        # Message bus
        self._bus = get_message_bus()

        # Initialize detector
        self.detector = AlertDetector()

        # Initialize trend heartbeat monitor
        self.trend_monitor = TrendPulseMonitor()

        # Initialize spread detector
        self.spread_detector = get_spread_detector()

        # Initialize orderbook detector
        self.orderbook_detector = get_orderbook_detector()

        # Initialize AI client
        self._ai_client = OpenRouterClient()

        # Initialize position manager (for Ignis Prime strategy status)
        self._position_manager = None
        self._init_position_manager()

        # Notification channels
        self._broadcaster = None
        self._wechat_sender = None

        # Unified cooldown manager
        self._cooldown_manager: CooldownManager = get_cooldown_manager()

        # Heartbeat push records (at most 1 per coin per day)
        self._heartbeat_sent: Dict[str, datetime] = {}

        # Last push time (for status display)
        self._last_push_time: Optional[datetime] = None

        # Async scheduler state
        self._running: bool = False
        self._last_run_times: Dict[str, datetime] = {}
        self._pending_cron_tasks: List[str] = []
        self._task: Optional[Any] = None  # asyncio.Task

        # Initialize notification channels
        self._init_notifications()

        # Initialize independent services (delegation pattern)
        self._daily_brief_service = DailyBriefService(
            detector=self.detector,
            ai_client=self._ai_client,
            position_manager=self._position_manager
        )
        self._heartbeat_service = HeartbeatService(
            trend_monitor=self.trend_monitor,
            broadcaster=self._broadcaster
        )
        self._sector_service = SectorService(
            broadcaster=self._broadcaster
        )

    def _init_position_manager(self) -> None:
        """Initialize position manager"""
        try:
            from src.trading.position_manager import PositionManager

            mysql_config = settings.get_mysql_config()
            self._position_manager = PositionManager(
                host=mysql_config['host'],
                port=mysql_config['port'],
                password=mysql_config['password']
            )
            self.logger.info("Position manager initialized")
        except Exception as e:
            self.logger.warning(f"Position manager init failed: {e}")
            self._position_manager = None

    def _init_notifications(self) -> None:
        """Initialize notification channels"""
        # Telegram
        self._broadcaster = get_broadcaster()
        if self._broadcaster.is_ready:
            self.logger.info("TelegramBroadcaster initialized")
        else:
            self.logger.warning("TelegramBroadcaster not ready (Token not configured)")

        # WeChat (ServerChan)
        try:
            from src.notifications.wechat_sender import WeChatSender
            self._wechat_sender = WeChatSender()
            if self._wechat_sender.enabled:
                self.logger.info("WeChat push enabled")
        except Exception as e:
            self.logger.debug(f"WeChat init skipped: {e}")

    async def _publish_alert_events(
        self,
        alerts: List[Alert],
        message: str,
        max_score: float,
        messages_by_lang: dict = None
    ) -> None:
        """
        Publish alert detection events via message bus.

        Publishes one AlertDetectedEvent per alert,
        decouples Scanner from notification modules.

        Args:
            alerts: Alert list
            message: Formatted message (backward compat)
            max_score: Highest score
            messages_by_lang: Language-separated messages {'zh': '...', 'en': '...'}
        """
        for alert in alerts:
            event = AlertDetectedEvent(
                symbol=alert.symbol,
                alert_type=alert.alert_type.value,
                score=getattr(alert, 'score', max_score),
                message=message,
                data={
                    'change_pct': getattr(alert, 'change_pct', 0),
                    'price': getattr(alert, 'price', 0),
                },
                messages_by_lang=messages_by_lang or {}
            )
            try:
                await self._bus.publish(event)
            except Exception as e:
                self.logger.warning(f"Failed to publish alert event: {e}")

    async def _publish_daily_pulse_event(
        self,
        content: str,
        content_hook: str = None,
        content_text: str = None,
        content_by_lang: dict = None,
        content_hook_by_lang: dict = None,
        target_lang: str = ''
    ) -> None:
        """
        Publish daily pulse event (multi-version content).

        Args:
            content: Premium version (full content) - backward compat
            content_hook: Basic version (partial hidden) - backward compat
            content_text: WeChat version (plain text)
            content_by_lang: Language-separated Premium content {'zh': '...'}
            content_hook_by_lang: Language-separated Basic content {'zh': '...'}
            target_lang: Target language ('zh'|'en'|''), empty=all
        """
        event = DailyPulseReadyEvent(
            content=content,
            image_path=None,  # F&G image sent via URL, not local path
            content_hook=content_hook,
            content_text=content_text,
            content_by_lang=content_by_lang or {},
            content_hook_by_lang=content_hook_by_lang or {},
            target_lang=target_lang
        )
        try:
            await self._bus.publish(event)
        except Exception as e:
            self.logger.warning(f"Failed to publish daily pulse event: {e}")

    # Direct push methods removed
    # Original _send_telegram, _send_telegram_daily_pulse, _send_wechat,
    # _send_all_channels, _send_daily_pulse_all_channels, _strip_html
    # Now handled by handlers.py via event bus

    def _get_strategy_status(self) -> Dict[str, Any]:
        """
        Get Ignis Prime strategy status (for Ignis Alpha and Ignis Scanner)

        Returns:
            {
                'has_position': bool,
                'symbol': str,
                'side': str,
                'entry_price': float,
                'current_price': float,
                'pnl_pct': float,
                'days_held': int,
                'has_trailing_stop': bool  # Whether trailing stop is active
            }
        """
        if not self._position_manager:
            return {'has_position': False}

        try:
            positions = self._position_manager.get_open_positions()
            if not positions:
                return {'has_position': False}

            # Take first position (strategy typically has one position)
            pos = positions[0]

            # Calculate days held
            opened_at = pos.get('opened_at')
            if opened_at:
                days_held = (datetime.now() - opened_at).days + 1
            else:
                days_held = 1

            # Get current price to calculate unrealized PnL
            symbol = pos.get('symbol', 'BTC')
            entry_price = float(pos.get('entry_price', 0))
            side = pos.get('side', 'LONG')
            stop_type = pos.get('stop_type', 'FIXED')

            # Get current price from detector
            tickers = self.detector.fetch_all_tickers()
            current_price = entry_price
            for t in tickers:
                if t['symbol'] == f"{symbol}USDT":
                    current_price = float(t['lastPrice'])
                    break

            # Calculate PnL
            if side == 'LONG':
                pnl_pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
            else:
                pnl_pct = (entry_price - current_price) / entry_price * 100 if entry_price > 0 else 0

            # Check if trailing stop is active (TRAILING means trailing stop enabled)
            has_trailing_stop = (stop_type == 'TRAILING')

            return {
                'has_position': True,
                'symbol': symbol,
                'side': side,
                'entry_price': entry_price,
                'current_price': current_price,
                'pnl_pct': pnl_pct,
                'days_held': days_held,
                'has_trailing_stop': has_trailing_stop
            }

        except Exception as e:
            self.logger.error(f"Failed to get strategy status: {e}")
            return {'has_position': False}

    async def scan_and_push(self) -> Dict[str, Any]:
        """
        Execute scan and push (core method, async)

        Flow:
          1. Check global cooldown
          2. Full market scan
          3. Filter cooled-down coins
          4. Generate aggregated card
          5. Push (if any)

        Returns:
            {
                'total_monitored': int,
                'total_alerts': int,
                'pushed_alerts': int,
                'skipped_reason': str or None
            }
        """
        with TraceContext(operation='scanner.scan_and_push'):
            return await self._do_scan_and_push()

    async def _do_scan_and_push(self) -> Dict[str, Any]:
        """Actual implementation of scan and push (async)"""
        result = {
            'total_monitored': 0,
            'total_alerts': 0,
            'pushed_alerts': 0,
            'skipped_reason': None
        }

        # 1. Check global cooldown
        if not self._cooldown_manager.check_global():
            if self._last_push_time:
                elapsed = (datetime.now(timezone.utc) - self._last_push_time).total_seconds() / 60
                remaining = self.GLOBAL_COOLDOWN_MIN - elapsed
                result['skipped_reason'] = f"Global cooldown active (remaining {remaining:.0f}min)"
            else:
                result['skipped_reason'] = "Global cooldown active (Redis state restored)"
            self.logger.debug(result['skipped_reason'])
            return result

        # 2. Full market scan (funnel model) - AlertDetector.scan is now async
        alerts, total_monitored, market_status = await self.detector.scan(top_n=self.TOP_N * 2)
        result['total_monitored'] = total_monitored
        result['total_alerts'] = len(alerts)

        if not alerts:
            result['skipped_reason'] = "No anomalies (no significant 5-minute changes)"
            self.logger.info(f"Scan complete: {total_monitored} coins, no 5-minute anomalies")
            return result

        # 3. Filter cooled-down coins
        filtered_alerts = []
        for a in alerts:
            direction = CooldownManager.get_direction_from_alert_type(a.alert_type.value)
            if await self._cooldown_manager.check_alert(a.symbol, direction):
                filtered_alerts.append(a)

        if not filtered_alerts:
            result['skipped_reason'] = "All alert coins on cooldown"
            self.logger.info(f"Scan complete: {len(alerts)} alerts but all on cooldown")
            return result

        # 4. Sector aggregation detection
        aggregator = get_sector_aggregator()
        should_aggregate, sector_name, sector_alerts, other_alerts = \
            aggregator.aggregate_alerts(filtered_alerts)

        # 5. Choose push strategy based on aggregation result
        if should_aggregate and sector_alerts:
            # Sector aggregation mode: push sector message
            top_alerts = sector_alerts[:self.TOP_N]
            result['pushed_alerts'] = len(top_alerts)

            # Language-separated sector aggregation
            message = aggregator.format_sector_alert_message(
                sector_name=sector_name,
                sector_alerts=sector_alerts,
                lang='zh'  # backward compat
            )
            messages_by_lang = {
                'zh': aggregator.format_sector_alert_message(
                    sector_name=sector_name,
                    sector_alerts=sector_alerts,
                    lang='zh'
                ),
                'en': aggregator.format_sector_alert_message(
                    sector_name=sector_name,
                    sector_alerts=sector_alerts,
                    lang='en'
                )
            }
            self.logger.info(f"Sector aggregation triggered: {sector_name} ({len(sector_alerts)} coins)")
        else:
            # Normal mode: take Top N
            top_alerts = filtered_alerts[:self.TOP_N]
            result['pushed_alerts'] = len(top_alerts)

            # Get strategy status
            strategy_status = self._get_strategy_status()

            # Language-separated formatting
            ts = datetime.now()
            message = ScannerFormatter.format_anomaly_radar(
                alerts=top_alerts,
                market_status=market_status,
                strategy_status=strategy_status,
                timestamp=ts,
                lang='zh'  # backward compat
            )
            messages_by_lang = {
                'zh': ScannerFormatter.format_anomaly_radar(
                    alerts=top_alerts,
                    market_status=market_status,
                    strategy_status=strategy_status,
                    timestamp=ts,
                    lang='zh'
                ),
                'en': ScannerFormatter.format_anomaly_radar(
                    alerts=top_alerts,
                    market_status=market_status,
                    strategy_status=strategy_status,
                    timestamp=ts,
                    lang='en'
                )
            }

        # 6. Silent push (silent when score < 90)
        max_score = max((getattr(a, 'score', 0) for a in top_alerts), default=0)
        disable_notification = max_score < 90

        if disable_notification:
            self.logger.info(f"Silent push: max_score={max_score:.1f} < 90")

        # 7. Publish alert events (handlers.py handles push via events)
        await self._publish_alert_events(top_alerts, message, max_score, messages_by_lang)

        # Update cooldowns
        self._cooldown_manager.update_global()
        for alert in top_alerts:
            direction = CooldownManager.get_direction_from_alert_type(alert.alert_type.value)
            await self._cooldown_manager.update_alert(alert.symbol, direction)
            await self._cooldown_manager.update_cross_type(alert.symbol)

        self.logger.info(
            f"Push complete: {total_monitored} coins, "
            f"{len(alerts)} alerts, pushed Top {len(top_alerts)}"
        )

        return result

    async def scan_spreads(self) -> Dict[str, Any]:
        """
        Execute spread scan (D.1.1, async)

        Flow:
          1. Call SpreadDetector.scan() to get spread anomalies
          2. Format messages (Premium/Basic, zh/en)
          3. Publish SpreadDetectedEvent
          4. Update cooldowns

        Returns:
            {
                'triggered': int,  # number of threshold-triggered anomalies
                'pushed': int,     # actual push count
                'thresholds': dict # current threshold config
            }
        """
        with TraceContext(operation='scanner.scan_spreads'):
            return await self._do_scan_spreads()

    async def _do_scan_spreads(self) -> Dict[str, Any]:
        """Actual implementation of spread scan (async)"""
        result = {
            'triggered': 0,
            'pushed': 0,
            'thresholds': {}
        }

        try:
            # 1. Execute scan
            alerts, thresholds = await self.spread_detector.scan(top_n=1)
            result['thresholds'] = thresholds

            if not alerts:
                self.logger.debug("Spread scan: no threshold-triggered anomalies")
                return result

            result['triggered'] = len(alerts)

            # 2. Process each spread anomaly
            for alert in alerts:
                # Check cross-type cooldown (same coin at most 1 alert per 10 minutes)
                if not await self._cooldown_manager.check_cross_type(alert.symbol):
                    self.logger.debug(
                        f"Spread skipped (cross-type cooldown): {alert.symbol}"
                    )
                    continue

                # Format messages
                messages_by_lang = ScannerFormatter.format_spread_alert_multilang(
                    symbol=alert.symbol,
                    spot_price=alert.spot_price,
                    futures_price=alert.futures_price,
                    spread_pct=alert.spread_pct,
                    spread_type=alert.spread_type,
                    mode='FULL'
                )
                messages_basic_by_lang = ScannerFormatter.format_spread_alert_multilang(
                    symbol=alert.symbol,
                    spot_price=alert.spot_price,
                    futures_price=alert.futures_price,
                    spread_pct=alert.spread_pct,
                    spread_type=alert.spread_type,
                    mode='HOOK'
                )

                # 3. Publish event
                event = SpreadDetectedEvent(
                    symbol=alert.symbol,
                    spot_price=alert.spot_price,
                    futures_price=alert.futures_price,
                    spread_pct=alert.spread_pct,
                    spread_type=alert.spread_type,
                    messages_by_lang=messages_by_lang,
                    messages_basic_by_lang=messages_basic_by_lang,
                    data={'thresholds': thresholds}
                )

                try:
                    await self._bus.publish(event)
                    result['pushed'] += 1
                    self.logger.info(
                        f"Spread event published: {alert.symbol} {alert.spread_pct:+.2f}% "
                        f"({alert.spread_type})"
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to publish spread event: {e}")

                # 4. Update cooldowns
                direction = CooldownManager.get_direction_from_spread(alert.spread_pct)
                await self._cooldown_manager.update_spread(alert.symbol, direction)
                await self._cooldown_manager.update_cross_type(alert.symbol)

            return result

        except Exception as e:
            self.logger.error(f"Spread scan failed: {e}", exc_info=True)
            return result

    async def scan_orderbooks(self) -> Dict[str, Any]:
        """
        Execute orderbook scan (async)

        Flow:
          1. Call OrderbookDetector.scan() to get orderbook anomalies
          2. Format messages (Premium/Basic, zh/en)
          3. Publish OrderbookImbalanceEvent
          4. Update cooldowns

        Returns:
            {
                'triggered': int,  # number of threshold-triggered anomalies
                'pushed': int,     # actual push count
                'thresholds': dict # current threshold config
            }
        """
        with TraceContext(operation='scanner.scan_orderbooks'):
            return await self._do_scan_orderbooks()

    async def _do_scan_orderbooks(self) -> Dict[str, Any]:
        """Actual implementation of orderbook scan (async)"""
        result = {
            'triggered': 0,
            'pushed': 0,
            'thresholds': {}
        }

        try:
            # 1. Execute scan
            alerts, thresholds = await self.orderbook_detector.scan(top_n=1)
            result['thresholds'] = thresholds

            if not alerts:
                self.logger.debug("Orderbook scan: no threshold-triggered anomalies")
                return result

            result['triggered'] = len(alerts)

            # 2. Process each orderbook anomaly
            for alert in alerts:
                # Check cross-type cooldown (same coin at most 1 alert per 10 minutes)
                if not await self._cooldown_manager.check_cross_type(alert.symbol):
                    self.logger.debug(
                        f"Orderbook skipped (cross-type cooldown): {alert.symbol}"
                    )
                    continue

                # Format messages
                messages_by_lang = ScannerFormatter.format_orderbook_multilang(
                    symbol=alert.symbol,
                    imbalance_ratio=alert.imbalance_ratio,
                    imbalance_side=alert.imbalance_side,
                    imbalance_pct=alert.imbalance_pct,
                    bid_depth_usd=alert.bid_depth_usd,
                    ask_depth_usd=alert.ask_depth_usd,
                    current_price=alert.current_price,
                    mode='FULL'
                )
                messages_basic_by_lang = ScannerFormatter.format_orderbook_multilang(
                    symbol=alert.symbol,
                    imbalance_ratio=alert.imbalance_ratio,
                    imbalance_side=alert.imbalance_side,
                    imbalance_pct=alert.imbalance_pct,
                    bid_depth_usd=alert.bid_depth_usd,
                    ask_depth_usd=alert.ask_depth_usd,
                    current_price=alert.current_price,
                    mode='HOOK'
                )

                # 3. Publish event
                event = OrderbookImbalanceEvent(
                    symbol=alert.symbol,
                    imbalance_ratio=alert.imbalance_ratio,
                    imbalance_side=alert.imbalance_side,
                    imbalance_pct=alert.imbalance_pct,
                    bid_depth_usd=alert.bid_depth_usd,
                    ask_depth_usd=alert.ask_depth_usd,
                    messages_by_lang=messages_by_lang,
                    messages_basic_by_lang=messages_basic_by_lang,
                    data={'thresholds': thresholds}
                )

                try:
                    await self._bus.publish(event)
                    result['pushed'] += 1
                    self.logger.info(
                        f"Orderbook event published: {alert.symbol} {alert.imbalance_side} "
                        f"ratio={alert.imbalance_ratio:.2f} pct={alert.imbalance_pct:.0f}%"
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to publish orderbook event: {e}")

                # 4. Update cooldowns
                direction = CooldownManager.get_direction_from_orderbook(
                    alert.bid_depth_usd, alert.ask_depth_usd
                )
                await self._cooldown_manager.update_orderbook(alert.symbol, direction)
                await self._cooldown_manager.update_cross_type(alert.symbol)

            return result

        except Exception as e:
            self.logger.error(f"Orderbook scan failed: {e}", exc_info=True)
            return result

    async def generate_daily_brief(self, target_lang: str = 'zh') -> str:
        """
        Generate and push Ignis Daily Pulse report

        Delegates to DailyBriefService

        Args:
            target_lang: Target language ('zh'=Chinese channel, 'en'=English channel)

        Returns:
            FULL version report content
        """
        return await self._daily_brief_service.generate(target_lang)

    async def check_sector_updates(self) -> bool:
        """
        Check sector mapping updates (runs weekly)

        Delegates to SectorService

        Returns:
            True if new coins need review
        """
        return await self._sector_service.check_updates()

    async def check_trend_heartbeat(self) -> List[str]:
        """
        Check trend heartbeat (coins near breakout)

        Delegates to HeartbeatService

        Returns:
            List of coins that were pushed
        """
        return await self._heartbeat_service.check()

    def show_status(self) -> None:
        """Display scheduler status (delegates to status_reporter)"""
        show_scanner_status(self)

    # ==========================================
    # Async Native Scheduler Loop
    # ==========================================

    def _schedule_cron_task(self, task_type: str) -> None:
        """
        Mark a cron task for execution (triggered by schedule)

        The schedule library calls this only to mark tasks as pending;
        actual execution is handled by _process_cron_tasks() in the main loop.
        """
        self._pending_cron_tasks.append(task_type)
        self.logger.debug(f"Cron task scheduled: {task_type}")

    async def _process_cron_tasks(self) -> None:
        """Process pending cron tasks (native async calls)"""
        while self._pending_cron_tasks:
            task = self._pending_cron_tasks.pop(0)
            try:
                if task == 'daily_zh':
                    await self.generate_daily_brief(target_lang='zh')
                elif task == 'daily_en':
                    await self.generate_daily_brief(target_lang='en')
                elif task == 'sector':
                    await self.check_sector_updates()
                self.logger.info(f"Cron task completed: {task}")
            except Exception as e:
                self.logger.error(f"Cron task {task} failed: {e}", exc_info=True)

    def _should_run(self, task_name: str, interval_min: int) -> bool:
        """
        Check if an interval task should run

        Args:
            task_name: Task name
            interval_min: Execution interval (minutes)

        Returns:
            True if should run, False if not yet time
        """
        last_run = self._last_run_times.get(task_name)
        if last_run is None:
            return True
        elapsed = (datetime.now(timezone.utc) - last_run).total_seconds() / 60
        return elapsed >= interval_min

    def _mark_run(self, task_name: str) -> None:
        """Record task last run time"""
        self._last_run_times[task_name] = datetime.now(timezone.utc)

    async def _execute_all_scans(self) -> None:
        """Execute all interval tasks (with independent cooldowns)"""
        # Anomaly scan: every 3 minutes
        if self._should_run('scan', interval_min=self.SCAN_INTERVAL_MIN):
            try:
                await self.scan_and_push()
                self._mark_run('scan')
            except Exception as e:
                self.logger.error(f"Scan task failed: {e}", exc_info=True)

        # Spread scan: every 1 minute
        if self._should_run('spread', interval_min=1):
            try:
                await self.scan_spreads()
                self._mark_run('spread')
            except Exception as e:
                self.logger.error(f"Spread scan failed: {e}", exc_info=True)

        # Orderbook scan: every 5 minutes
        if self._should_run('orderbook', interval_min=5):
            try:
                await self.scan_orderbooks()
                self._mark_run('orderbook')
            except Exception as e:
                self.logger.error(f"Orderbook scan failed: {e}", exc_info=True)

        # Trend heartbeat: every 60 minutes
        if self._should_run('heartbeat', interval_min=60):
            try:
                await self.check_trend_heartbeat()
                self._mark_run('heartbeat')
            except Exception as e:
                self.logger.error(f"Heartbeat task failed: {e}", exc_info=True)

    async def start(self) -> None:
        """Start scheduler (async entry point)"""
        if self._running:
            self.logger.warning("Scheduler already running")
            return

        self._running = True
        self.logger.info("Scanner Scheduler starting (Async Native)...")

        try:
            await self._run_scheduler_loop()
        except asyncio.CancelledError:
            self.logger.info("Scheduler cancelled")
        finally:
            await self._cleanup()

    async def stop(self) -> None:
        """Graceful shutdown"""
        self.logger.info("Stopping Scanner Scheduler...")
        self._running = False

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _cleanup(self) -> None:
        """Clean up resources"""
        try:
            from src.notifications.priority import get_digest_manager
            digest_mgr = get_digest_manager()
            await digest_mgr.flush_all()
            digest_mgr.stop_timer()
            self.logger.info("DigestManager flushed on shutdown")
        except Exception as e:
            self.logger.warning(f"DigestManager flush failed: {e}")

        self.logger.info("Scanner Scheduler stopped")

    async def _run_scheduler_loop(self) -> None:
        """
        Main scheduler loop (Async Native)

        Architecture:
        - schedule library used only as cron trigger
        - Main loop driven by asyncio, non-blocking
        - Exception Containment prevents Boot-Loop
        """
        # Explicitly set main loop so sync cache methods can dispatch to it
        from src.core.cache import CacheManager
        CacheManager.set_main_loop(asyncio.get_running_loop())

        # 1. Register cron triggers (lambdas only mark tasks, don't execute)
        # Server local timezone (UTC+8): Chinese daily 08:00, English daily 16:00
        schedule.every().day.at("08:00").do(
            lambda: self._schedule_cron_task('daily_zh')
        )
        schedule.every().day.at("16:00").do(
            lambda: self._schedule_cron_task('daily_en')
        )
        schedule.every().monday.at("02:00").do(
            lambda: self._schedule_cron_task('sector')
        )

        # 2. Warm-up (wait for connection pool initialization)
        await asyncio.sleep(5)
        self.logger.info("Warm-up complete, starting main loop...")

        # 3. First run
        first_run = True

        while self._running:
            try:
                # A. Trigger cron check (sync, fast, only appends to list)
                schedule.run_pending()

                # B. Consume and execute cron tasks
                if self._pending_cron_tasks:
                    await self._process_cron_tasks()

                # C. Interval tasks
                if first_run:
                    self.logger.info("Scanner first start: forcing full scan...")
                    await self.scan_and_push()
                    await self.scan_spreads()
                    await self.scan_orderbooks()
                    self._mark_run('scan')
                    self._mark_run('spread')
                    self._mark_run('orderbook')
                    first_run = False
                else:
                    await self._execute_all_scans()

            except asyncio.CancelledError:
                self.logger.info("Scheduler loop cancelled")
                break
            except Exception as e:
                # [Safety Net] Exception Containment prevents container restart
                self.logger.error(f"Critical Scheduler Loop Error: {e}", exc_info=True)
                await asyncio.sleep(30)  # Cooldown to avoid CPU spin
                continue

            # Non-blocking Interval
            await asyncio.sleep(60)


def setup_logging() -> None:
    """Configure logging"""
    from src.core.structured_logger import setup_structured_logging
    setup_structured_logging(level="INFO")


async def _run_with_cleanup(coro):
    """Run coroutine and ensure bot session cleanup (avoids "Unclosed client session" warning)"""
    from src.notifications.telegram_app import close_bot
    try:
        return await coro
    finally:
        await close_bot()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Ignis Scanner Scheduler')
    parser.add_argument('--scan-now', action='store_true', help='Run one anomaly scan immediately')
    parser.add_argument('--spread-scan', action='store_true', help='Run one spread scan immediately (D.1.1)')
    parser.add_argument('--orderbook-scan', action='store_true', help='Run one orderbook scan immediately (D.1.2)')
    parser.add_argument('--daily-brief', action='store_true', help='Generate and push daily report')
    parser.add_argument('--heartbeat', action='store_true', help='Check trend heartbeat')
    parser.add_argument('--sector-check', action='store_true', help='Check sector mapping updates')
    parser.add_argument('--status', action='store_true', help='Show status')

    args = parser.parse_args()

    setup_logging()
    scheduler = ScannerScheduler()

    if args.status:
        scheduler.show_status()
    elif args.scan_now:
        result = asyncio.run(_run_with_cleanup(scheduler.scan_and_push()))
        print("\nAnomaly scan result:")
        print(f"  Monitored: {result['total_monitored']}")
        print(f"  Alerts: {result['total_alerts']}")
        print(f"  Pushed: {result['pushed_alerts']}")
        if result['skipped_reason']:
            print(f"  Skipped: {result['skipped_reason']}")
    elif args.spread_scan:
        result = asyncio.run(_run_with_cleanup(scheduler.scan_spreads()))
        print("\nSpread scan result:")
        print(f"  Triggered: {result['triggered']}")
        print(f"  Pushed: {result['pushed']}")
        print(f"  Thresholds: Premium>={result['thresholds'].get('premium', 3.0)}%, "
              f"Basic>={result['thresholds'].get('basic', 10.0)}%")
    elif args.orderbook_scan:
        result = asyncio.run(_run_with_cleanup(scheduler.scan_orderbooks()))
        print("\nOrderbook scan result:")
        print(f"  Triggered: {result['triggered']}")
        print(f"  Pushed: {result['pushed']}")
        t = result['thresholds']
        if t:
            print(f"  Thresholds: Premium(>{t.get('premium', {}).get('high', 2.86):.2f} or "
                  f"<{t.get('premium', {}).get('low', 0.35):.2f})")
    elif args.daily_brief:
        brief = asyncio.run(_run_with_cleanup(scheduler.generate_daily_brief()))
        print(f"\n{brief}")
    elif args.heartbeat:
        sent = asyncio.run(_run_with_cleanup(scheduler.check_trend_heartbeat()))
        if sent:
            print(f"\nPushed {len(sent)} heartbeats: {', '.join(sent)}")
        else:
            print("\nNo coins near breakout")

        print("\nTrend status:")
        for symbol, status in scheduler.trend_monitor.get_all_status().items():
            dist = status.distance_pct
            near = " [near breakout]" if status.near_breakout else ""
            print(f"  {symbol}: ${status.current_price:,.2f} -> ${status.breakout_price:,.2f} (distance {dist:.1f}%){near}")
    elif args.sector_check:
        print("\nChecking sector mapping updates...")
        has_updates = asyncio.run(_run_with_cleanup(scheduler.check_sector_updates()))
        if has_updates:
            print("New coins found, admin review notification sent")
        else:
            print("No new coins need classification")
    else:
        asyncio.run(scheduler.start())


if __name__ == '__main__':
    main()
