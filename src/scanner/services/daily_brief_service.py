"""
Daily Brief Service

Service extracted from ScannerScheduler, responsible for:
  - Generating Ignis Daily Pulse report
  - Fetching market sentiment data (F&G, Long/Short)
  - Calling AI to generate Quant View
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.core.events import DailyPulseReadyEvent
from src.core.message_bus import get_message_bus
from src.core.structured_logger import get_logger
from src.core.tracing import TraceContext
from src.scanner.ai_client import OpenRouterClient
from src.scanner.alert_detector import AlertDetector
from src.scanner.formatter import ScannerFormatter


class DailyBriefService:
    """
    Daily brief generation service

    Responsibilities:
      - Generate and publish daily brief event
      - Fetch sentiment data
      - Call AI to generate interpretation
    """

    def __init__(
        self,
        detector: AlertDetector = None,
        ai_client: OpenRouterClient = None,
        position_manager = None
    ):
        """
        Initialize daily brief service

        Args:
            detector: Alert detector (for fetching market data)
            ai_client: AI client (for generating Quant View)
            position_manager: Position manager (for fetching strategy status)
        """
        self.logger = get_logger(__name__)
        self._bus = get_message_bus()

        # Dependency injection
        self._detector = detector or AlertDetector()
        self._ai_client = ai_client or OpenRouterClient()
        self._position_manager = position_manager

    async def generate(self, target_lang: str = 'zh') -> str:
        """
        Generate and publish daily brief

        Args:
            target_lang: Target language ('zh'=Chinese channel, 'en'=English channel)

        Returns:
            FULL version daily brief content
        """
        region = 'asia' if target_lang == 'zh' else 'west'

        with TraceContext(operation='daily_brief.generate', region=region, target_lang=target_lang):
            return await self._do_generate(region, target_lang)

    async def _do_generate(self, region: str, target_lang: str = 'zh') -> str:
        """Actual implementation of daily brief generation"""
        region_label = "Asia" if region == 'asia' else "Americas/Europe"
        self.logger.info(f"Generating Ignis Daily Pulse ({region_label}, lang={target_lang})...")

        try:
            # 1. Basic market data
            overview = self._detector.get_market_overview()
            if not overview:
                self.logger.error("Failed to get market overview")
                return ""

            # 2. Fetch strategy status
            strategy_status = self._get_strategy_status()

            # 3. Fetch sentiment data
            sentiment_data = self._fetch_sentiment_data()

            # 4. Generate Quant View AI interpretation
            quant_view = self._generate_quant_view(overview, sentiment_data, region, target_lang)

            # 5. Generate daily brief
            ts = datetime.now(timezone.utc)

            lang = target_lang
            content_by_lang = {
                lang: ScannerFormatter.format_daily_report(
                    overview=overview,
                    strategy_status=strategy_status,
                    timestamp=ts,
                    sentiment_data=sentiment_data,
                    quant_view=quant_view,
                    mode='FULL',
                    lang=lang,
                    region=region
                )
            }
            content_hook_by_lang = {
                lang: ScannerFormatter.format_daily_report(
                    overview=overview,
                    strategy_status=strategy_status,
                    timestamp=ts,
                    sentiment_data=sentiment_data,
                    quant_view=quant_view,
                    mode='HOOK',
                    lang=lang,
                    region=region
                )
            }

            report_full = content_by_lang[lang]
            report_hook = content_hook_by_lang[lang]

            # Plain text version (WeChat)
            report_text = ScannerFormatter.format_daily_report_text(
                overview=overview,
                strategy_status=strategy_status,
                timestamp=ts,
                sentiment_data=sentiment_data,
                quant_view=quant_view
            )

            # Publish daily brief event
            await self._publish_event(
                report_full, report_hook, report_text,
                content_by_lang, content_hook_by_lang,
                target_lang=target_lang
            )

            self.logger.info(f"Ignis Daily Pulse event published ({region_label})")
            return report_full

        except Exception as e:
            self.logger.error(f"Daily brief generation failed: {e}")
            return ""

    async def _publish_event(
        self,
        content: str,
        content_hook: str = None,
        content_text: str = None,
        content_by_lang: dict = None,
        content_hook_by_lang: dict = None,
        target_lang: str = ''
    ) -> None:
        """Publish daily brief event"""
        event = DailyPulseReadyEvent(
            content=content,
            image_path=None,
            content_hook=content_hook,
            content_text=content_text,
            content_by_lang=content_by_lang or {},
            content_hook_by_lang=content_hook_by_lang or {},
            target_lang=target_lang
        )
        try:
            await self._bus.publish(event)
        except Exception as e:
            self.logger.warning(f"Failed to publish daily brief event: {e}")

    def _get_strategy_status(self) -> Dict[str, Any]:
        """Fetch strategy status"""
        if not self._position_manager:
            return {'has_position': False}

        try:
            positions = self._position_manager.get_open_positions()
            if not positions:
                return {'has_position': False}

            pos = positions[0]

            opened_at = pos.get('opened_at')
            if opened_at:
                days_held = (datetime.now() - opened_at).days + 1
            else:
                days_held = 1

            symbol = pos.get('symbol', 'BTC')
            entry_price = float(pos.get('entry_price', 0))
            side = pos.get('side', 'LONG')
            stop_type = pos.get('stop_type', 'FIXED')

            # Fetch current price
            tickers = self._detector.fetch_all_tickers()
            current_price = entry_price
            for t in tickers:
                if t['symbol'] == f"{symbol}USDT":
                    current_price = float(t['lastPrice'])
                    break

            if side == 'LONG':
                pnl_pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
            else:
                pnl_pct = (entry_price - current_price) / entry_price * 100 if entry_price > 0 else 0

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

    def _fetch_sentiment_data(self) -> Dict[str, Any]:
        """Fetch sentiment data"""
        sentiment = {}
        sentiment['fear_greed'] = self._detector.fetch_fear_greed_index()
        sentiment['long_short_ratio'] = self._detector.fetch_top_long_short_position_ratio(
            symbol='BTCUSDT',
            period='1d'
        )
        return sentiment

    def _generate_quant_view(
        self,
        overview: Dict[str, Any],
        sentiment_data: Dict[str, Any],
        region: str = 'asia',
        lang: str = 'en'
    ) -> Optional[str]:
        """Generate AI interpretation"""
        if not self._ai_client.is_available:
            self.logger.info("AI client unavailable, skipping Quant View generation")
            return None

        try:
            market_data = {}

            if overview.get('btc'):
                market_data['btc'] = overview['btc']
            if overview.get('eth'):
                market_data['eth'] = overview['eth']
            if 'avg_funding' in overview:
                market_data['avg_funding'] = overview['avg_funding']
            if overview.get('sentiment'):
                market_data['sentiment'] = overview['sentiment']
            if sentiment_data:
                if sentiment_data.get('fear_greed'):
                    market_data['fear_greed'] = sentiment_data['fear_greed']
                if sentiment_data.get('long_short_ratio'):
                    market_data['long_short_ratio'] = sentiment_data['long_short_ratio']
            if overview.get('top_gainers'):
                market_data['top_gainers'] = overview['top_gainers']
            if overview.get('top_losers'):
                market_data['top_losers'] = overview['top_losers']

            quant_view = self._ai_client.generate_quant_view(
                market_data=market_data,
                enable_web_search=True,
                timeout=60,
                region=region,
                lang=lang
            )

            if quant_view:
                self.logger.info(f"Quant View AI generation successful: {len(quant_view)} chars")
            else:
                self.logger.warning("Quant View AI generation returned empty")

            return quant_view

        except Exception as e:
            self.logger.error(f"Quant View AI generation error: {e}")
            return None


# Factory function (consistent with existing pattern)
_daily_brief_service: Optional[DailyBriefService] = None


def get_daily_brief_service() -> DailyBriefService:
    """Get DailyBriefService singleton"""
    global _daily_brief_service
    if _daily_brief_service is None:
        _daily_brief_service = DailyBriefService()
    return _daily_brief_service
