"""
Scanner Message Formatter (Language-separated)

HTML output, compatible with Telegram parse_mode='HTML'

Templates:
  - format_daily_report: Ignis Daily Pulse (VIP daily report)
  - format_anomaly_radar: Ignis Scanner (anomaly push)
  - format_radar_card: Ignis Scanner Card (compact)
  - format_daily_brief: Daily Brief (morning briefing)

Features:
  - <b>bold</b> for titles and key info
  - <code>monospace</code> for prices and changes
  - <i>italic</i> for footer notes
  - Web3-native terminology (DYOR, Alpha, Scanner)
  - Ignis Prime strategy status (stop-loss desensitized)
  - Smart sector effect aggregation
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from src.telegram.i18n import t

from .alert_detector import Alert, EventTag
from .sector_aggregator import get_sector_aggregator


class ScannerFormatter:
    """Scanner message formatter"""

    @classmethod
    def format_radar_card(
        cls,
        alerts: List[Alert],
        total_monitored: int,
        market_status: Dict[str, Any],
        timestamp: Optional[datetime] = None,
        lang: str = "en",
    ) -> str:
        """
        Format Scanner card (HTML, language-separated)

        Args:
            alerts: Anomaly list
            total_monitored: Number of monitored coins
            market_status: Market status
            timestamp: Timestamp
            lang: Language code ('zh' | 'en')
        """
        if not alerts:
            return ""

        ts = timestamp or datetime.now()
        time_str = ts.strftime("%H:%M")

        lines = []

        # Title
        lines.append(f"<b>🚨 {t('scanner.title_scanner', lang)} | {time_str}</b>")
        lines.append("")

        # Group by event type
        grouped = cls._group_by_event(alerts)

        for event_tag, group_alerts in grouped.items():
            # Event title
            lines.append(f"<b>{event_tag.value}</b>")

            # Anomaly list
            for i, alert in enumerate(group_alerts, 1):
                line = cls._format_alert_line(i, alert, lang)
                lines.append(line)

            lines.append("")

        # Market status
        lines.append(f"<b>📊 {t('scanner.market_status', lang)}</b>")

        btc = market_status.get("btc")
        if btc:
            sign = "+" if btc["change_24h"] >= 0 else ""
            icon = "🟢" if btc["change_24h"] >= 0 else "🔴"
            lines.append(
                f"• BTC: <code>${btc['price']:,.0f}</code> (<code>{sign}{btc['change_24h']:.1f}%</code>) {icon}"
            )

        sentiment = market_status.get("sentiment", t("scanner.ls_balanced", lang))
        sentiment_icon = market_status.get("sentiment_icon", "⚪")
        avg_funding = market_status.get("avg_funding", 0)
        lines.append(
            f"• {t('scanner.sentiment', lang)}: {sentiment} {sentiment_icon} | FR: <code>{avg_funding:+.3f}%</code>"
        )
        lines.append(
            f"• {t('scanner.scanning', lang)}: {total_monitored} {t('scanner.pairs', lang)}"
        )

        # Footer
        lines.append("")
        lines.append(f"<i>🔒 {t('scanner.footer_pass', lang)}</i>")

        return "\n".join(lines)

    @classmethod
    def _group_by_event(cls, alerts: List[Alert]) -> Dict[EventTag, List[Alert]]:
        """Group by event type"""
        grouped = {}
        for alert in alerts:
            tag = alert.event_tag
            if tag not in grouped:
                grouped[tag] = []
            grouped[tag].append(alert)
        return grouped

    @classmethod
    def _format_alert_line(cls, index: int, alert: Alert, lang: str = "en") -> str:
        """Format single anomaly line (HTML, language-separated)"""
        # Base info
        sign = "+" if alert.change_pct >= 0 else ""
        line = f"{index}. <b>{alert.symbol}</b>: <code>{sign}{alert.change_pct:.1f}%</code> (5min)"

        # Extra tags
        tags = []

        # Volume spike marker
        if alert.extra.get("is_volume_spike"):
            vol_label = t("scanner.vol_spike", lang)
            tags.append(f"🔥 {alert.volume_ratio:.1f}x {vol_label}")

        # Price
        if alert.price >= 1000:
            tags.append(f"<code>${alert.price:,.0f}</code>")
        elif alert.price >= 1:
            tags.append(f"<code>${alert.price:.2f}</code>")
        else:
            tags.append(f"<code>${alert.price:.4f}</code>")

        if tags:
            line += " | " + " ".join(tags)

        return line

    @classmethod
    def format_daily_brief(
        cls,
        overview: Dict[str, Any],
        timestamp: Optional[datetime] = None,
        lang: str = "en",
    ) -> str:
        """
        Format Daily Brief (HTML, language-separated)

        Args:
            overview: Market overview data
            timestamp: Timestamp
            lang: Language code ('zh' | 'en')
        """
        if not overview:
            return ""

        ts = timestamp or datetime.now()
        date_str = ts.strftime("%m-%d")

        lines = []

        # Title
        lines.append(f"<b>☀️ {t('scanner.title_daily_brief', lang)} | {date_str}</b>")
        lines.append("")

        # Macro overview
        lines.append(f"<b>🌍 {t('scanner.market_overview', lang)}</b>")

        btc = overview.get("btc")
        if btc:
            sign = "+" if btc["change_24h"] >= 0 else ""
            if btc["change_24h"] >= 3:
                mood = f"🟢 {t('scanner.mood_bullish', lang)}"
            elif btc["change_24h"] >= 0:
                mood = f"🟢 {t('scanner.mood_neutral', lang)}"
            elif btc["change_24h"] >= -3:
                mood = f"🔴 {t('scanner.mood_bearish', lang)}"
            else:
                mood = f"🔴 {t('scanner.mood_panic', lang)}"
            lines.append(
                f"<b>BTC</b>: <code>${btc['price']:,.0f}</code> (<code>{sign}{btc['change_24h']:.1f}%</code>) {mood}"
            )

        sentiment = overview.get("sentiment", t("scanner.ls_balanced", lang))
        sentiment_icon = overview.get("sentiment_icon", "⚪")
        avg_funding = overview.get("avg_funding", 0)
        lines.append(
            f"<b>{t('scanner.sentiment', lang)}</b>: {sentiment} {sentiment_icon} | FR: <code>{avg_funding:+.3f}%</code>"
        )
        lines.append("")

        # Capital flow
        lines.append(f"<b>🌊 {t('scanner.money_flow', lang)}</b>")

        top_gainers = overview.get("top_gainers", [])
        top_losers = overview.get("top_losers", [])

        if top_gainers:
            gainer_str = " / ".join([g["symbol"] for g in top_gainers[:3]])
            lines.append(f"✅ <b>{t('scanner.gainers', lang)}</b>: {gainer_str}")

        if top_losers:
            loser_str = " / ".join([coin["symbol"] for coin in top_losers[:3]])
            lines.append(f"❌ <b>{t('scanner.losers', lang)}</b>: {loser_str}")

        lines.append("")

        # Quant View (auto-generated)
        lines.append(f"<b>💡 {t('scanner.quant_view', lang)}</b>")
        commentary = cls._generate_commentary(overview, lang)
        lines.append(commentary)
        lines.append("")

        # Footer
        total_pairs = overview.get("total_pairs", 0)
        lines.append(
            f"<i>{t('scanner.powered_by', lang).format(count=total_pairs)}</i>"
        )

        return "\n".join(lines)

    @classmethod
    def _generate_commentary(cls, overview: Dict[str, Any], lang: str = "en") -> str:
        """
        Auto-generate trader commentary (language-separated)

        Based on:
        - BTC change
        - Average funding rate
        - Market sentiment
        """
        btc = overview.get("btc", {})
        btc_change = btc.get("change_24h", 0) if btc else 0
        avg_funding = overview.get("avg_funding", 0)

        parts = []

        # BTC status
        if btc_change >= 5:
            parts.append(t("scanner.btc_surge", lang))
        elif btc_change >= 2:
            parts.append(t("scanner.btc_up", lang))
        elif btc_change >= -2:
            parts.append(t("scanner.btc_sideways", lang))
        elif btc_change >= -5:
            parts.append(t("scanner.btc_down", lang))
        else:
            parts.append(t("scanner.btc_crash", lang))

        # Funding analysis
        if avg_funding > 0.03:
            parts.append(t("scanner.fr_high", lang))
        elif avg_funding < -0.02:
            parts.append(t("scanner.fr_negative", lang))
        else:
            parts.append(t("scanner.fr_neutral", lang))

        # Trading suggestion
        if btc_change < -3 and avg_funding < -0.01:
            parts.append(t("scanner.suggest_wait", lang))
        elif btc_change > 3 and avg_funding > 0.03:
            parts.append(t("scanner.suggest_caution", lang))
        else:
            parts.append(t("scanner.suggest_watch", lang))

        # Choose separator based on language
        separator = "。" if lang == "zh" else ". "
        ending = "。" if lang == "zh" else "."
        return separator.join(parts) + ending

    @classmethod
    def format_no_alert(
        cls, total_monitored: int, market_status: Dict[str, Any], lang: str = "en"
    ) -> str:
        """
        Format no-anomaly message (HTML, language-separated)
        """
        sentiment = market_status.get("sentiment", t("scanner.ls_balanced", lang))
        sentiment_icon = market_status.get("sentiment_icon", "⚪")

        return (
            f"<b>📡 {t('scanner.title_scanner', lang)}</b>\n\n"
            f"{t('scanner.market_quiet', lang)}\n"
            f"{t('scanner.sentiment', lang)}: {sentiment} {sentiment_icon}\n"
            f"{t('scanner.scanning', lang)}: {total_monitored} {t('scanner.pairs', lang)}"
        )

    # ========================================
    # Advanced templates
    # ========================================

    @classmethod
    def format_daily_report(
        cls,
        overview: Dict[str, Any],
        strategy_status: Dict[str, Any],
        timestamp: Optional[datetime] = None,
        sentiment_data: Optional[Dict[str, Any]] = None,
        quant_view: Optional[str] = None,
        mode: str = "full",
        lang: str = "en",
        region: str = "asia",
    ) -> str:
        """
        Format Ignis Daily Pulse report (HTML, language-separated)

        Args:
            overview: Market overview data (includes top_gainers/top_losers)
            strategy_status: Ignis Prime strategy status
            timestamp: Timestamp
            sentiment_data: Sentiment data (F&G + L/S)
            quant_view: AI-generated market commentary
            mode: Version mode ('FULL' | 'HOOK')
            lang: Language code ('zh' | 'en')
            region: Timezone region ('asia' | 'west')
        """
        ts = timestamp or datetime.now()
        date_str = ts.strftime("%m-%d")

        lines = []
        btc = overview.get("btc")
        eth = overview.get("eth")
        has_position = strategy_status and strategy_status.get("has_position", False)

        # ===== Title (with timezone) =====
        if lang == "zh":
            tz_label = "08:00 CST"
        else:
            tz_label = "08:00 UTC"
        lines.append(
            f"<b>📈 {t('scanner.title_daily_pulse', lang)} | {date_str} ({tz_label})</b>"
        )
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # ===== [ Market ] =====
        lines.append(f"<b>[ {t('scanner.market_temp', lang)} ]</b>")

        # Sentiment indicators: F&G + L/S
        sentiment_parts = []
        if sentiment_data and sentiment_data.get("fear_greed"):
            fg = sentiment_data["fear_greed"]
            fg_value = fg.get("value", 0)
            fg_emoji = cls._get_fg_emoji(fg_value)
            fg_desc = cls._get_fg_description(fg_value, lang)
            sentiment_parts.append(f"{fg_emoji} {fg_desc} (F&G {fg_value})")

        if sentiment_data and sentiment_data.get("long_short_ratio"):
            ls = sentiment_data["long_short_ratio"]
            ls_ratio = ls.get("long_short_ratio", 1.0)
            ls_desc, ls_emoji = cls._get_ls_description(ls_ratio, lang)
            sentiment_parts.append(f"{ls_emoji} {ls_desc} (L/S {ls_ratio:.2f})")

        if sentiment_parts:
            lines.append(" | ".join(sentiment_parts))

        # BTC/ETH one line
        price_parts = []
        if btc:
            btc_change = btc.get("change_24h", 0)
            price_parts.append(
                f"BTC <code>${btc['price']:,.0f}</code> ({btc_change:+.1f}%)"
            )
        if eth:
            eth_change = eth.get("change_24h", 0)
            price_parts.append(
                f"ETH <code>${eth['price']:,.0f}</code> ({eth_change:+.1f}%)"
            )
        if price_parts:
            lines.append(" | ".join(price_parts))
        lines.append("")

        # ===== In position: strategy status shown at top =====
        if has_position:
            lines.extend(
                cls._format_strategy_section(
                    strategy_status, mode, expanded=True, lang=lang
                )
            )
            lines.append("")

        # ===== [ Scanner radar ] =====
        lines.append(f"<b>[ {t('scanner.scanner_radar', lang)} ]</b>")

        # Sector effect
        aggregator = get_sector_aggregator()
        top_gainers = overview.get("top_gainers", [])
        top_losers = overview.get("top_losers", [])
        active_sectors = aggregator.detect_active_sectors(top_gainers, top_losers)
        sector_line = aggregator.format_sector_line(
            active_sectors, top_gainers=top_gainers
        )
        lines.append(f"• {t('scanner.sector', lang)}: {sector_line}")

        # Mover coins
        anomaly_line = aggregator.format_anomaly_line(top_gainers, max_display=2)
        lines.append(f"• {t('scanner.movers', lang)}: {anomaly_line}")
        lines.append("")

        # ===== [ Quant View ] (graceful degradation) =====
        if quant_view:
            lines.append(f"<b>[ {t('scanner.quant_view', lang)} ]</b>")
            lines.append(quant_view)
            lines.append("")

        # ===== Flat: strategy status compressed to 1 line at bottom =====
        if not has_position:
            lines.extend(
                cls._format_strategy_section(
                    strategy_status, mode, expanded=False, lang=lang
                )
            )
            lines.append("")

        # ===== Footer =====
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>{t('scanner.footer_nfa', lang)}</i>")

        return "\n".join(lines)

    @classmethod
    def _get_fg_emoji(cls, fg_value: int) -> str:
        """Return emoji based on F&G value"""
        if fg_value >= 75:
            return "🤑"
        elif fg_value >= 55:
            return "😊"
        elif fg_value >= 45:
            return "😐"
        elif fg_value >= 25:
            return "😰"
        else:
            return "🥶"

    @classmethod
    def _get_fg_description(cls, fg_value: int, lang: str = "en") -> str:
        """Return plain-language description based on F&G value (language-separated)"""
        if fg_value >= 75:
            return t("scanner.fg_extreme_greed", lang)
        elif fg_value >= 55:
            return t("scanner.fg_greed", lang)
        elif fg_value >= 45:
            return t("scanner.fg_neutral", lang)
        elif fg_value >= 25:
            return t("scanner.fg_fear", lang)
        else:
            return t("scanner.fg_extreme_fear", lang)

    @classmethod
    def _get_ls_description(cls, ls_ratio: float, lang: str = "en") -> tuple:
        """Return plain-language description and emoji based on L/S ratio (language-separated)"""
        if ls_ratio >= 2.0:
            return t("scanner.ls_strong_long", lang), "🐋"
        elif ls_ratio >= 1.5:
            return t("scanner.ls_long", lang), "🐋"
        elif ls_ratio >= 1.2:
            return t("scanner.ls_slight_long", lang), "🐋"
        elif ls_ratio <= 0.5:
            return t("scanner.ls_strong_short", lang), "🐻"
        elif ls_ratio <= 0.7:
            return t("scanner.ls_short", lang), "🐻"
        elif ls_ratio <= 0.8:
            return t("scanner.ls_slight_short", lang), "🐻"
        else:
            return t("scanner.ls_balanced", lang), "⚖️"

    @classmethod
    def _format_strategy_section(
        cls,
        strategy_status: Dict[str, Any],
        mode: str,
        expanded: bool,
        lang: str = "en",
    ) -> List[str]:
        """
        Format strategy status section (language-separated)

        Args:
            strategy_status: Strategy status
            mode: FULL/HOOK
            expanded: True=expanded multi-line (in position), False=compressed single-line (flat)
            lang: Language code ('zh' | 'en')
        """
        # Hide section when no position
        if not strategy_status or not strategy_status.get("has_position"):
            return []

        # In position: show status
        lines = []
        symbol = strategy_status.get("symbol", "BTC")
        side = strategy_status.get("side", "LONG")
        side_icon = "🟢" if side == "LONG" else "🔴"
        entry_price = strategy_status.get("entry_price", 0)
        pnl_pct = strategy_status.get("pnl_pct") or strategy_status.get(
            "pnl_percent", 0
        )
        has_trailing = strategy_status.get("has_trailing_stop", False)

        # P&L icon
        if pnl_pct > 5:
            pnl_icon = "💰"
        elif pnl_pct > 0:
            pnl_icon = "📈"
        else:
            pnl_icon = "📉"

        # Stop-loss status description
        if has_trailing:
            stop_desc = f"{t('scanner.profit_locked', lang)} 🛡"
        else:
            stop_desc = t("scanner.stop_protected", lang)

        lines.append(f"<b>[ {t('scanner.strategy_active', lang)} ]</b>")

        if mode.upper() == "FULL":
            # Premium channel: show entry price
            lines.append(
                f"{symbol} {side} {side_icon} @ <code>${entry_price:,.0f}</code> | "
                f"<code>{pnl_pct:+.1f}%</code> {pnl_icon} | {stop_desc}"
            )
        else:
            # Basic channel (HOOK): hide entry price
            lines.append(
                f"{symbol} {side} {side_icon} | "
                f"<code>{pnl_pct:+.1f}%</code> {pnl_icon} | {stop_desc}"
            )
            lines.append(f"<i>🔒 {t('scanner.premium_only', lang)}</i>")

        return lines

    @classmethod
    def format_daily_report_text(
        cls,
        overview: Dict[str, Any],
        strategy_status: Dict[str, Any],
        timestamp: Optional[datetime] = None,
        sentiment_data: Optional[Dict[str, Any]] = None,
        quant_view: Optional[str] = None,
    ) -> str:
        """
        Format Ignis Daily Pulse report (plain text) - for WeCom/WeChat/email

        Same structure as format_daily_report() but without HTML tags
        """
        ts = timestamp or datetime.now()
        date_str = ts.strftime("%m-%d")

        lines = []
        btc = overview.get("btc")
        eth = overview.get("eth")
        has_position = strategy_status and strategy_status.get("has_position", False)

        # ===== Title =====
        lines.append(f"Ignis Daily Pulse | {date_str}")
        lines.append("=" * 30)
        lines.append("")

        # ===== [ Market ] =====
        lines.append("[ Market ]")

        # Sentiment indicators: F&G + L/S
        sentiment_parts = []
        if sentiment_data and sentiment_data.get("fear_greed"):
            fg = sentiment_data["fear_greed"]
            fg_value = fg.get("value", 0)
            fg_desc = cls._get_fg_description(fg_value)
            sentiment_parts.append(f"{fg_desc} (F&G {fg_value})")

        if sentiment_data and sentiment_data.get("long_short_ratio"):
            ls = sentiment_data["long_short_ratio"]
            ls_ratio = ls.get("long_short_ratio", 1.0)
            ls_desc, _ = cls._get_ls_description(ls_ratio)
            sentiment_parts.append(f"{ls_desc} (L/S {ls_ratio:.2f})")

        if sentiment_parts:
            lines.append("  " + " | ".join(sentiment_parts))

        # BTC/ETH
        price_parts = []
        if btc:
            btc_change = btc.get("change_24h", 0)
            price_parts.append(f"BTC ${btc['price']:,.0f} ({btc_change:+.1f}%)")
        if eth:
            eth_change = eth.get("change_24h", 0)
            price_parts.append(f"ETH ${eth['price']:,.0f} ({eth_change:+.1f}%)")
        if price_parts:
            lines.append("  " + " | ".join(price_parts))
        lines.append("")

        # ===== In position: strategy status =====
        if has_position:
            lines.extend(cls._format_strategy_section_text(strategy_status))
            lines.append("")

        # ===== [ Scanner radar ] =====
        lines.append("[ Scanner ]")
        aggregator = get_sector_aggregator()
        top_gainers = overview.get("top_gainers", [])
        top_losers = overview.get("top_losers", [])
        active_sectors = aggregator.detect_active_sectors(top_gainers, top_losers)
        sector_line = aggregator.format_sector_line(
            active_sectors, top_gainers=top_gainers
        )
        lines.append(f"  Sector: {sector_line}")
        anomaly_line = aggregator.format_anomaly_line(top_gainers, max_display=2)
        lines.append(f"  Movers: {anomaly_line}")
        lines.append("")

        # ===== [ Quant View ] =====
        if quant_view:
            lines.append("[ Quant View ]")
            lines.append(f"  {quant_view}")
            lines.append("")

        # ===== Flat: strategy status =====
        if not has_position:
            lines.append("[ Strategy ] Flat | Waiting for entry signal")
            lines.append("")

        # ===== Footer =====
        lines.append("=" * 30)
        lines.append("Ignis Quant | NFA DYOR")

        return "\n".join(lines)

    @classmethod
    def _format_strategy_section_text(
        cls, strategy_status: Dict[str, Any]
    ) -> List[str]:
        """Format strategy status (plain text version)"""
        # Hide section when no position
        if not strategy_status or not strategy_status.get("has_position"):
            return []

        # In position: show status
        symbol = strategy_status.get("symbol", "BTC")
        side = strategy_status.get("side", "LONG")
        entry_price = strategy_status.get("entry_price", 0)
        pnl_pct = strategy_status.get("pnl_pct") or strategy_status.get(
            "pnl_percent", 0
        )
        has_trailing = strategy_status.get("has_trailing_stop", False)

        # Stop-loss status description
        stop_desc = "Profit locked" if has_trailing else "Stop protected"

        return [
            "[ Strategy Active ]",
            f"{symbol} {side} @ ${entry_price:,.0f} | {pnl_pct:+.1f}% | {stop_desc}",
        ]

    @classmethod
    def _generate_trader_opinion(
        cls, overview: Dict[str, Any], strategy_status: Dict[str, Any]
    ) -> str:
        """
        Generate trader opinion (rule-based)

        Combines market status and strategy position to give actionable advice
        """
        btc = overview.get("btc", {})
        btc_change = btc.get("change_24h", 0) if btc else 0
        avg_funding = overview.get("avg_funding", 0)

        has_position = (
            strategy_status.get("has_position", False) if strategy_status else False
        )
        side = strategy_status.get("side", "LONG") if strategy_status else "LONG"
        pnl_pct = (
            strategy_status.get("pnl_pct") or strategy_status.get("pnl_percent", 0)
            if strategy_status
            else 0
        )

        parts = []

        # Market status
        if avg_funding > 0.03:
            parts.append("Funding rate shows long crowding")
        elif avg_funding < -0.02:
            parts.append("Funding rate shows short crowding")

        # Position advice
        if has_position:
            if side == "SHORT" and btc_change < -2:
                parts.append("No bottom structure yet, hold trend short")
            elif side == "SHORT" and btc_change > 2:
                parts.append("Market rebounding, watch stop-loss, manage short risk")
            elif side == "LONG" and btc_change > 2:
                parts.append("Trend continuing well, hold long position")
            elif side == "LONG" and btc_change < -2:
                parts.append("Market pulling back, watch support, manage long risk")
            else:
                parts.append("Market choppy, hold and wait for direction")

            if pnl_pct > 5:
                parts.append("Strong unrealized gains, consider partial take-profit")
        else:
            # Flat
            if btc_change > 3:
                parts.append("Strong market, but be cautious chasing, wait for pullback entry")
            elif btc_change < -3:
                parts.append("Market panicking, don't blindly catch knives, wait for stabilization")
            else:
                parts.append("Market choppy, wait patiently for clear direction")

        return ", ".join(parts) + "." if parts else "Normal market volatility, execute as planned."

    @classmethod
    def format_anomaly_radar(
        cls,
        alerts: List[Alert],
        market_status: Dict[str, Any],
        strategy_status: Dict[str, Any],
        timestamp: Optional[datetime] = None,
        lang: str = "en",
    ) -> str:
        """
        Format Ignis Scanner anomaly radar (HTML, language-separated)

        Args:
            alerts: Anomaly list
            market_status: Market status
            strategy_status: Ignis Prime strategy status
            timestamp: Timestamp
            lang: Language code ('zh' | 'en')
        """
        ts = timestamp or datetime.now()
        time_str = ts.strftime("%H:%M")

        lines = []

        # Title
        lines.append(f"<b>📡 {t('scanner.title_scanner', lang)} | {time_str}</b>")
        lines.append("")

        # Anomaly alert (Price & Vol)
        lines.append(f"<b>🚨 {t('scanner.title_movers', lang)}</b>")

        if alerts:
            for i, alert in enumerate(alerts[:3], 1):
                sign = "+" if alert.change_pct >= 0 else ""

                # Build description
                desc_parts = []
                if alert.extra.get("is_volume_spike"):
                    vol_label = t("scanner.vol_spike", lang)
                    desc_parts.append(
                        f"🔥 <b>{vol_label} {alert.volume_ratio:.0f}x</b>"
                    )

                desc = " | ".join(desc_parts) if desc_parts else ""
                if desc:
                    lines.append(
                        f"{i}. <b>{alert.symbol}</b>: <code>{sign}{alert.change_pct:.1f}%</code> (5min) | {desc}"
                    )
                else:
                    lines.append(
                        f"{i}. <b>{alert.symbol}</b>: <code>{sign}{alert.change_pct:.1f}%</code> (5min)"
                    )
        else:
            lines.append(f"• {t('scanner.no_movers', lang)}")

        lines.append("")

        # Market status
        lines.append(f"<b>📊 {t('scanner.market_status', lang)}</b>")

        btc = market_status.get("btc")
        if btc:
            sign = "+" if btc["change_24h"] >= 0 else ""
            icon = "🟢" if btc["change_24h"] >= 0 else "🔴"
            lines.append(
                f"• <b>BTC</b>: <code>${btc['price']:,.0f}</code> (<code>{sign}{btc['change_24h']:.1f}%</code>) {icon}"
            )

        sentiment = market_status.get("sentiment", t("scanner.ls_balanced", lang))
        avg_funding = market_status.get("avg_funding", 0)
        lines.append(
            f"• <b>{t('scanner.sentiment', lang)}</b>: {sentiment} | FR: <code>{avg_funding:+.3f}%</code>"
        )
        lines.append("")

        # Scanner Insight (rule-based opinion)
        lines.append(f"<b>💡 {t('scanner.scanner_insight', lang)}</b>")
        scan_opinion = cls._generate_radar_opinion(alerts, market_status, lang)
        lines.append(scan_opinion)
        lines.append("")

        # Footer
        lines.append(f"<i>🔒 {t('scanner.footer_pass', lang)}</i>")

        return "\n".join(lines)

    @classmethod
    def _generate_radar_opinion(
        cls, alerts: List[Alert], market_status: Dict[str, Any], lang: str = "en"
    ) -> str:
        """Generate radar scan opinion (language-separated)"""
        btc = market_status.get("btc", {})
        btc_change = btc.get("change_24h", 0) if btc else 0
        avg_funding = market_status.get("avg_funding", 0)

        parts = []

        # Overall market status
        if btc_change < -3:
            parts.append(t("scanner.market_dump", lang))
        elif btc_change > 3:
            parts.append(t("scanner.market_pump", lang))
        else:
            parts.append(t("scanner.market_choppy", lang))

        # Anomaly analysis
        if alerts:
            top = alerts[0]
            if top.extra.get("is_volume_spike") and top.change_pct < -2:
                parts.append(f"{top.symbol} {t('scanner.panic_sell', lang)}")
            elif top.extra.get("is_volume_spike") and top.change_pct > 2:
                parts.append(f"{top.symbol} {t('scanner.volume_pump', lang)}")
            elif top.change_pct < -3:
                parts.append(f"{top.symbol} {t('scanner.fast_drop', lang)}")
            elif top.change_pct > 3:
                parts.append(f"{top.symbol} {t('scanner.fast_pump', lang)}")

        # Funding
        if avg_funding > 0.03:
            parts.append(t("scanner.fr_high", lang))
        elif avg_funding < -0.02:
            parts.append(t("scanner.fr_negative", lang))

        # Choose separator by language
        separator = "，" if lang == "zh" else ", "
        ending = "。" if lang == "zh" else "."
        return (
            separator.join(parts) + ending
            if parts
            else t("scanner.market_quiet", lang) + ending
        )

    @classmethod
    def _generate_asset_comment(cls, symbol: str, change_24h: float) -> str:
        """Generate asset brief comment"""
        if change_24h >= 5:
            return "Strong breakout, bulls in control"
        elif change_24h >= 2:
            return "Grinding up, watch resistance"
        elif change_24h >= 0:
            return "Narrow consolidation, direction pending"
        elif change_24h >= -3:
            return "Weak pullback, watch support"
        elif change_24h >= -5:
            return "Accelerating down, risk caution"
        else:
            return "Panic sell, do not catch knife"

    @classmethod
    def _generate_alert_opinion(cls, alert: Alert) -> str:
        """Generate anomaly opinion"""
        if alert.change_pct > 3:
            if alert.funding_rate > 0.03:
                return "Pump with high funding, caution chasing, wait for pullback confirmation"
            else:
                return "Volume breakout, watch if it holds, small position follow"
        elif alert.change_pct < -3:
            if alert.funding_rate < -0.02:
                return "Panic sell but shorts crowded, technical bounce possible"
            else:
                return "Heavy sell pressure, don't blindly catch knife, wait for stabilization"
        elif alert.extra.get("is_volume_spike"):
            return "Abnormal volume, smart money move, watch subsequent direction closely"
        else:
            return "Volatility increasing, suggest watching"

    # ========================================
    # Spread monitor (D.1.1)
    # ========================================

    @classmethod
    def format_spread_alert(
        cls,
        symbol: str,
        spot_price: float,
        futures_price: float,
        spread_pct: float,
        spread_type: str,
        mode: str = "FULL",
        lang: str = "en",
    ) -> str:
        """
        Format spot-futures spread alert (HTML, language-separated)

        Args:
            symbol: Coin symbol (without USDT)
            spot_price: Spot price
            futures_price: Futures price
            spread_pct: Spread percentage ((futures-spot)/spot*100)
            spread_type: 'PREMIUM' (futures premium) | 'DISCOUNT' (futures discount)
            mode: Version mode ('FULL' = Premium | 'HOOK' = Basic+FOMO)
            lang: Language code ('zh' | 'en')

        Returns:
            HTML-formatted spread alert message
        """
        lines = []

        # Title
        lines.append(f"<b>📊 {t('spread.title', lang)}</b>")
        lines.append("")

        # Symbol + spread type
        type_label = (
            t("spread.premium", lang)
            if spread_type == "PREMIUM"
            else t("spread.discount", lang)
        )
        type_icon = "📈" if spread_type == "PREMIUM" else "📉"
        lines.append(f"<b>{symbol}</b> | {type_label} {type_icon}")
        lines.append("")

        # Spread value
        lines.append(
            f"<b>{t('spread.spread', lang)}</b>: <code>{abs(spread_pct):+.2f}%</code>"
        )

        # Price info
        if spot_price >= 1000:
            spot_str = f"${spot_price:,.0f}"
            futures_str = f"${futures_price:,.0f}"
        elif spot_price >= 1:
            spot_str = f"${spot_price:.2f}"
            futures_str = f"${futures_price:.2f}"
        else:
            spot_str = f"${spot_price:.4f}"
            futures_str = f"${futures_price:.4f}"

        lines.append(f"<b>{t('spread.spot', lang)}</b>: <code>{spot_str}</code>")
        lines.append(f"<b>{t('spread.futures', lang)}</b>: <code>{futures_str}</code>")
        lines.append("")

        # Insight (dynamically generated based on type and spread size)
        lines.append(f"<b>💡 {t('spread.insight', lang)}</b>")
        abs_pct = abs(spread_pct)
        if spread_type == "PREMIUM":
            if abs_pct >= 10:
                insight = t("spread.insight_premium_10", lang)
            elif abs_pct >= 5:
                insight = t("spread.insight_premium_5", lang)
            else:
                insight = t("spread.insight_premium_low", lang)
        else:  # DISCOUNT
            if abs_pct >= 10:
                insight = t("spread.insight_discount_10", lang)
            elif abs_pct >= 5:
                insight = t("spread.insight_discount_5", lang)
            else:
                insight = t("spread.insight_discount_low", lang)
        lines.append(insight)

        # HOOK mode: add FOMO copy
        if mode.upper() == "HOOK":
            lines.append("")
            lines.append(f"<i>⏰ {t('spread.premium_early', lang)}</i>")
            lines.append(f"<i>🚀 {t('spread.upgrade_cta', lang)}</i>")

        return "\n".join(lines)

    # ========================================
    # Orderbook depth monitor (D.1.2)
    # ========================================

    @classmethod
    def format_orderbook_imbalance(
        cls,
        symbol: str,
        imbalance_ratio: float,
        imbalance_side: str,
        imbalance_pct: float,
        bid_depth_usd: float,
        ask_depth_usd: float,
        current_price: float,
        mode: str = "FULL",
        lang: str = "en",
    ) -> str:
        """
        Format orderbook imbalance alert (HTML, language-separated)

        Args:
            symbol: Coin symbol (without USDT)
            imbalance_ratio: Bid/ask depth ratio (bid_depth / ask_depth)
            imbalance_side: 'BID_HEAVY' | 'ASK_HEAVY'
            imbalance_pct: Imbalance percentage (one-sided proportion, 0-100)
            bid_depth_usd: Bid depth (USD)
            ask_depth_usd: Ask depth (USD)
            current_price: Current price
            mode: Version mode ('FULL' = Premium | 'HOOK' = Basic+FOMO)
            lang: Language code ('zh' | 'en')

        Returns:
            HTML-formatted orderbook alert message
        """
        lines = []

        # Title (with warning icon)
        lines.append(f"<b>🚨 {t('orderbook.title', lang)}</b>")
        lines.append("")

        # Symbol + pressure direction
        if imbalance_side == "BID_HEAVY":
            side_label = t("orderbook.bid_wall", lang)
            side_icon = "🟢"
        else:
            side_label = t("orderbook.ask_wall", lang)
            side_icon = "🔴"

        lines.append(f"<b>{symbol}</b> | {side_label} {side_icon}")
        lines.append("")

        # Depth comparison (unified units for quick comparison)
        max_depth = max(bid_depth_usd, ask_depth_usd)
        if max_depth >= 1e6:
            # Use M
            bid_str = f"${bid_depth_usd / 1e6:.2f}M"
            ask_str = f"${ask_depth_usd / 1e6:.2f}M"
        elif max_depth >= 1e3:
            # Use K
            bid_str = f"${bid_depth_usd / 1e3:.0f}K"
            ask_str = f"${ask_depth_usd / 1e3:.0f}K"
        else:
            bid_str = f"${bid_depth_usd:.0f}"
            ask_str = f"${ask_depth_usd:.0f}"
        lines.append(
            f"<b>{t('orderbook.depth_compare', lang)}</b>: "
            f"<code>{bid_str}</code> vs <code>{ask_str}</code> "
            f"(<code>{imbalance_pct:.0f}%</code>)"
        )

        # Price
        if current_price >= 1000:
            price_str = f"${current_price:,.0f}"
        elif current_price >= 1:
            price_str = f"${current_price:.2f}"
        else:
            price_str = f"${current_price:.4f}"
        lines.append(f"<b>{t('orderbook.price', lang)}</b>: <code>{price_str}</code>")
        lines.append("")

        # Insight (dynamically generated, includes amount)
        lines.append(f"<b>💡 {t('orderbook.insight', lang)}</b>")

        # Get larger depth amount for display
        major_depth = ask_str if imbalance_side == "ASK_HEAVY" else bid_str

        if imbalance_pct >= 90:
            if imbalance_side == "ASK_HEAVY":
                insight = t("orderbook.insight_ask_90", lang, amount=ask_str)
            else:
                insight = t("orderbook.insight_bid_90", lang, amount=bid_str)
        elif imbalance_pct >= 80:
            if imbalance_side == "ASK_HEAVY":
                insight = t("orderbook.insight_ask_80", lang, amount=ask_str)
            else:
                insight = t("orderbook.insight_bid_80", lang, amount=bid_str)
        else:
            insight = t("orderbook.insight_balanced", lang)

        lines.append(insight)

        # HOOK mode: add FOMO copy
        if mode.upper() == "HOOK":
            lines.append("")
            lines.append(f"<i>⏰ {t('orderbook.premium_early', lang)}</i>")
            lines.append(f"<i>🚀 {t('orderbook.upgrade_cta', lang)}</i>")

        return "\n".join(lines)

    # ========================================
    # Multilang helper methods
    # ========================================

    @classmethod
    def format_spread_alert_multilang(
        cls,
        symbol: str,
        spot_price: float,
        futures_price: float,
        spread_pct: float,
        spread_type: str,
        mode: str = "FULL",
    ) -> Dict[str, str]:
        """
        Format spread alert (returns bilingual dict)

        Returns:
            {'zh': Chinese message, 'en': English message}
        """
        return {
            lang: cls.format_spread_alert(
                symbol=symbol,
                spot_price=spot_price,
                futures_price=futures_price,
                spread_pct=spread_pct,
                spread_type=spread_type,
                mode=mode,
                lang=lang,
            )
            for lang in ["zh", "en"]
        }

    @classmethod
    def format_orderbook_multilang(
        cls,
        symbol: str,
        imbalance_ratio: float,
        imbalance_side: str,
        imbalance_pct: float,
        bid_depth_usd: float,
        ask_depth_usd: float,
        current_price: float,
        mode: str = "FULL",
    ) -> Dict[str, str]:
        """
        Format orderbook alert (returns bilingual dict)

        Returns:
            {'zh': Chinese message, 'en': English message}
        """
        return {
            lang: cls.format_orderbook_imbalance(
                symbol=symbol,
                imbalance_ratio=imbalance_ratio,
                imbalance_side=imbalance_side,
                imbalance_pct=imbalance_pct,
                bid_depth_usd=bid_depth_usd,
                ask_depth_usd=ask_depth_usd,
                current_price=current_price,
                mode=mode,
                lang=lang,
            )
            for lang in ["zh", "en"]
        }
