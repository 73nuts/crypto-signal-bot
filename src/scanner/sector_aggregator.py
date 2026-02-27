"""
Sector Aggregation Module

Features:
  - Detect active sector effects for Daily Pulse report display
  - Anomaly aggregation: merge multiple coin anomalies in same sector into 1 push

Logic:
  - Detect sector correlation from top_gainers/losers
  - If a sector has >=N coins appearing -> mark as active
  - In anomaly radar, same sector >=3 coins -> aggregate into sector message
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any

from src.core.structured_logger import get_logger
from src.telegram.i18n import t


class SectorAggregator:
    """Sector aggregator"""

    # Config file path
    CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sector_mapping.json"

    def __init__(self):
        self.logger = get_logger(__name__)
        self._mapping: Dict[str, List[str]] = {}
        self._reverse_mapping: Dict[str, str] = {}  # symbol -> sector
        self._min_coins = 2
        self._load_mapping()

    def _load_mapping(self) -> None:
        """Load sector mapping config"""
        try:
            if not self.CONFIG_PATH.exists():
                self.logger.warning(f"Sector mapping config not found: {self.CONFIG_PATH}")
                return

            with open(self.CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)

            self._mapping = config.get('sectors', {})
            self._min_coins = config.get('min_coins_for_active', 2)
            self._version = config.get('version', '1.0')

            # Clear and rebuild reverse mapping (symbol -> sector)
            self._reverse_mapping.clear()
            for sector, symbols in self._mapping.items():
                for symbol in symbols:
                    self._reverse_mapping[symbol.upper()] = sector

            self.logger.info(
                f"Sector mapping loaded: {len(self._mapping)} sectors, "
                f"{len(self._reverse_mapping)} symbols, version {self._version}"
            )

        except Exception as e:
            self.logger.error(f"Failed to load sector mapping: {e}")

    def reload(self) -> bool:
        """
        Hot reload config (no restart required)

        Returns:
            True if loaded successfully
        """
        try:
            old_count = len(self._reverse_mapping)
            self._load_mapping()
            new_count = len(self._reverse_mapping)
            self.logger.info(f"Hot reload complete: {old_count} -> {new_count} symbols")
            return True
        except Exception as e:
            self.logger.error(f"Hot reload failed: {e}")
            return False

    @property
    def version(self) -> str:
        """Get current config version"""
        return getattr(self, '_version', '1.0')

    def get_symbol_sector(self, symbol: str) -> Optional[str]:
        """Get sector for a symbol"""
        # Remove USDT suffix
        clean_symbol = symbol.replace('USDT', '').upper()
        return self._reverse_mapping.get(clean_symbol)

    def detect_active_sectors(
        self,
        top_gainers: List[Dict[str, Any]],
        top_losers: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, List[str]]:
        """
        Detect active sectors

        Args:
            top_gainers: Top gainer coins [{"symbol": "SOL", "change": 5.2}, ...]
            top_losers: Top loser coins (optional)

        Returns:
            Active sectors dict {"AI": ["RNDR", "FET", "TAO"], "Meme": ["DOGE", "SHIB"]}
            Returns empty dict if no active sectors
        """
        if not self._mapping:
            return {}

        # Merge gainer and loser coins
        all_coins = []
        for coin in (top_gainers or []):
            symbol = coin.get('symbol', '').replace('USDT', '').upper()
            if symbol:
                all_coins.append(symbol)

        for coin in (top_losers or []):
            symbol = coin.get('symbol', '').replace('USDT', '').upper()
            if symbol and symbol not in all_coins:
                all_coins.append(symbol)

        # Count coins per sector
        sector_counts: Dict[str, List[str]] = {}
        for symbol in all_coins:
            sector = self._reverse_mapping.get(symbol)
            if sector:
                if sector not in sector_counts:
                    sector_counts[sector] = []
                sector_counts[sector].append(symbol)

        # Filter sectors that reached the threshold
        active_sectors = {
            sector: coins
            for sector, coins in sector_counts.items()
            if len(coins) >= self._min_coins
        }

        if active_sectors:
            self.logger.info(f"Active sectors detected: {list(active_sectors.keys())}")

        return active_sectors

    def format_sector_line(
        self,
        active_sectors: Dict[str, List[str]],
        max_display: int = 1,
        top_gainers: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Format sector display line

        Args:
            active_sectors: Active sectors dict
            max_display: Max number of sectors to display
            top_gainers: Top gainer list (for display when no sector active)

        Returns:
            Formatted string e.g. "AI (RNDR/FET/TAO linked)"
            Returns top gainer coins if no active sectors
        """
        if not active_sectors:
            # No sector correlation, show top gainer coins
            if top_gainers and len(top_gainers) >= 2:
                coins = [c.get('symbol', '').replace('USDT', '') for c in top_gainers[:3]]
                return f"{'/'.join(coins)} leading"
            return "dispersed market"

        # Sort by coin count, take Top N
        sorted_sectors = sorted(
            active_sectors.items(),
            key=lambda x: len(x[1]),
            reverse=True
        )[:max_display]

        parts = []
        for sector, coins in sorted_sectors:
            # Display at most 3 coins
            display_coins = "/".join(coins[:3])
            if len(coins) > 3:
                parts.append(f"{sector} ({display_coins}... linked)")
            else:
                parts.append(f"{sector} ({display_coins} linked)")

        return " | ".join(parts)

    def format_anomaly_line(
        self,
        top_gainers: List[Dict[str, Any]],
        max_display: int = 2
    ) -> str:
        """
        Format anomaly display line (gain-first)

        Args:
            top_gainers: Top gainer list [{symbol, change, volume_usd, price}, ...]
            max_display: Max number of coins to display

        Returns:
            Formatted string e.g. "ALPACA +391% (high vol) | BNX +66%"
        """
        if not top_gainers:
            return "no significant movers"

        parts = []
        for coin in top_gainers[:max_display]:
            symbol = coin.get('symbol', '').replace('USDT', '')
            change = coin.get('change', 0)
            volume_usd = coin.get('volume_usd', 0)

            # Check high volume (>$200M)
            is_high_volume = volume_usd >= 200_000_000

            # Format: symbol change (tag)
            if is_high_volume:
                parts.append(f"{symbol} {change:+.0f}% (high vol)")
            else:
                parts.append(f"{symbol} {change:+.0f}%")

        return " | ".join(parts)

    def _generate_anomaly_desc(
        self,
        symbol: str,
        change: float,
        volume_usd: float
    ) -> str:
        """
        Generate smart anomaly description

        Based on change + volume + sector attributes
        """
        sector = self.get_symbol_sector(symbol)
        is_high_volume = volume_usd >= 200_000_000  # >$200M = high volume

        # Upward descriptions
        if change >= 10:
            if is_high_volume:
                return "massive volume pump"
            return "explosive pump"
        elif change >= 5:
            if is_high_volume:
                return "volume breakout"
            if sector == "Meme":
                return "FOMO pump"
            return "strong breakout"
        elif change >= 3:
            if is_high_volume:
                return "capital inflow"
            return "steady rise"

        # Downward descriptions
        elif change <= -10:
            if is_high_volume:
                return "panic sell"
            return "crash dump"
        elif change <= -5:
            if is_high_volume:
                return "volume dump"
            return "sharp decline"
        elif change <= -3:
            if is_high_volume:
                return "capital outflow"
            return "weak pullback"

        # Small fluctuation
        else:
            return f"{change:+.1f}%"

    # Aggregation threshold
    AGGREGATE_MIN_COINS = 3  # Trigger aggregation when same sector has >=3 coins

    def aggregate_alerts(
        self,
        alerts: List[Any]
    ) -> tuple:
        """
        Anomaly aggregation

        Detects multi-coin anomalies in same sector, aggregates into single sector
        message to reduce push frequency.

        Args:
            alerts: Alert list (must have symbol attribute)

        Returns:
            (should_aggregate, sector_name, sector_alerts, other_alerts)
            - should_aggregate: Whether aggregation triggered
            - sector_name: Aggregated sector name
            - sector_alerts: Alerts for that sector
            - other_alerts: Other alerts
        """
        if not alerts or not self._mapping:
            return False, None, [], alerts

        # Group by sector
        sector_groups: Dict[str, List[Any]] = {}
        no_sector_alerts: List[Any] = []

        for alert in alerts:
            symbol = getattr(alert, 'symbol', '').upper()
            sector = self._reverse_mapping.get(symbol)

            if sector:
                if sector not in sector_groups:
                    sector_groups[sector] = []
                sector_groups[sector].append(alert)
            else:
                no_sector_alerts.append(alert)

        # Check if any sector reached aggregation threshold
        for sector, group_alerts in sector_groups.items():
            if len(group_alerts) >= self.AGGREGATE_MIN_COINS:
                # Sort by score descending
                group_alerts.sort(key=lambda a: getattr(a, 'score', 0), reverse=True)

                # Other sectors' alerts + no-sector alerts
                other = no_sector_alerts.copy()
                for s, ga in sector_groups.items():
                    if s != sector:
                        other.extend(ga)

                self.logger.info(
                    f"Sector aggregation triggered: {sector} ({len(group_alerts)} coins: "
                    f"{[a.symbol for a in group_alerts]})"
                )
                return True, sector, group_alerts, other

        # No aggregation triggered
        return False, None, [], alerts

    def format_sector_alert_message(
        self,
        sector_name: str,
        sector_alerts: List[Any],
        direction: str = 'mixed',
        lang: str = 'zh'
    ) -> str:
        """
        Format sector aggregation message (HTML)

        Args:
            sector_name: Sector name
            sector_alerts: Alerts for that sector
            direction: Direction ('pump'/'drop'/'mixed')
            lang: Language ('zh' / 'en')

        Returns:
            HTML-formatted sector anomaly message
        """
        if not sector_alerts:
            return ""

        # Determine direction
        avg_change = sum(getattr(a, 'change_pct', 0) for a in sector_alerts) / len(sector_alerts)
        if avg_change > 0:
            direction_icon = "🚀"
            direction_text = t('scanner.sector_pump', lang)
        else:
            direction_icon = "📉"
            direction_text = t('scanner.sector_drop', lang)

        # Coin list
        coins = [getattr(a, 'symbol', '') for a in sector_alerts[:5]]
        coins_str = " / ".join(coins)

        # Change range
        changes = [getattr(a, 'change_pct', 0) for a in sector_alerts]
        min_change = min(changes)
        max_change = max(changes)

        # Localized labels
        sector_title = t('scanner.sector_alert', lang)
        coins_label = t('scanner.linked_coins', lang)
        range_label = t('scanner.change_range', lang)
        signal_label = t('scanner.signal', lang)
        rotation_hint = t('scanner.sector_rotation', lang)
        linked_suffix = t('scanner.coins_linked', lang)

        return (
            f"{direction_icon} <b>{sector_name} {sector_title}</b>\n\n"
            f"<b>{coins_label}</b>: {coins_str}\n"
            f"<b>{range_label}</b>: <code>{min_change:+.1f}%</code> ~ <code>{max_change:+.1f}%</code> (5min)\n"
            f"<b>{signal_label}</b>: {direction_text}, {rotation_hint}\n\n"
            f"<i>🔒 Ignis Pass | {len(sector_alerts)} {linked_suffix}</i>"
        )


# Singleton
_aggregator: Optional[SectorAggregator] = None


def get_sector_aggregator() -> SectorAggregator:
    """Get SectorAggregator singleton"""
    global _aggregator
    if _aggregator is None:
        _aggregator = SectorAggregator()
    return _aggregator
