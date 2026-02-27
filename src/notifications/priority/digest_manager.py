"""
Digest manager.

Aggregates P2-level messages and sends them in batches to reduce notification volume.

Trigger condition: 5 minutes OR 3 messages (whichever comes first)
Behavior: Aggregate into summary message, send silently
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Callable, Awaitable
from dataclasses import dataclass, field

from src.telegram.i18n import t

logger = logging.getLogger(__name__)


# Event type mapping (for human-readable Digest)
EVENT_MAP = {
    'flash_drop': {'icon': '📉', 'label_key': 'digest.flash_drop', 'suffix_key': 'digest.suffix_bounce'},
    'flash_pump': {'icon': '🚀', 'label_key': 'digest.flash_pump', 'suffix_key': 'digest.suffix_resistance'},
    'volume_spike': {'icon': '🔥', 'label_key': 'digest.volume_spike', 'suffix_key': 'digest.suffix_direction'},
    'funding_high': {'icon': '💰', 'label_key': 'digest.funding_high', 'suffix_key': 'digest.suffix_crowded_long'},
    'funding_low': {'icon': '💸', 'label_key': 'digest.funding_low', 'suffix_key': 'digest.suffix_crowded_short'},
}

ORDERBOOK_MAP = {
    'BID_HEAVY': {'icon': '🟢', 'label_key': 'digest.bid_heavy', 'suffix_key': 'digest.suffix_support'},
    'ASK_HEAVY': {'icon': '🔴', 'label_key': 'digest.ask_heavy', 'suffix_key': 'digest.suffix_resistance'},
}


@dataclass
class DigestItem:
    """Digest queue item."""
    event_type: str          # alert / spread / orderbook
    symbol: str
    summary: str             # Short description
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ''         # Reason for entering Digest (CIRCUIT_BREAKER / RATE_LIMITED / P2)
    data: Dict[str, Any] = field(default_factory=dict)


class DigestManager:
    """Digest manager.

    - Collects P2-level and rate-limited messages
    - Triggers aggregated send at 5 minutes or 3 messages
    - Supports forced flush on graceful shutdown
    - Sends per language
    """

    # Config
    MAX_WAIT_SECONDS = 5 * 60   # 5 minutes
    MAX_BATCH_SIZE = 3          # Trigger at 3 messages

    def __init__(self):
        # Queues: {lang: [DigestItem, ...]}
        self._queues: Dict[str, List[DigestItem]] = {
            'zh': [],
            'en': [],
        }

        # Timer task
        self._timer_task: Optional[asyncio.Task] = None
        self._last_flush_time: datetime = datetime.now(timezone.utc)

        # Send callback (set by handlers.py)
        self._send_callback: Optional[Callable[[str, str], Awaitable[None]]] = None

        logger.info(
            f"DigestManager initialized, "
            f"max_wait={self.MAX_WAIT_SECONDS}s, "
            f"batch_size={self.MAX_BATCH_SIZE}"
        )

    def set_send_callback(
        self,
        callback: Callable[[str, str], Awaitable[None]]
    ) -> None:
        """Set send callback.

        Args:
            callback: async def callback(lang: str, message: str)
        """
        self._send_callback = callback
        logger.debug("DigestManager send callback set")

    def add(
        self,
        event_type: str,
        symbol: str,
        summary: str,
        lang: str = 'zh',
        reason: str = 'P2',
        data: Optional[Dict[str, Any]] = None
    ) -> None:
        """Add message to Digest queue.

        Args:
            event_type: Event type (alert/spread/orderbook)
            symbol: Asset symbol
            summary: Short description
            lang: Language (zh/en)
            reason: Reason for entering Digest
            data: Additional data
        """
        if lang not in self._queues:
            lang = 'zh'  # fallback

        item = DigestItem(
            event_type=event_type,
            symbol=symbol,
            summary=summary,
            reason=reason,
            data=data or {},
        )

        self._queues[lang].append(item)

        logger.debug(
            f"[Digest] Added: {event_type}/{symbol} ({lang}) reason={reason}, "
            f"queue_size={len(self._queues[lang])}"
        )

        # Check if batch threshold reached
        if len(self._queues[lang]) >= self.MAX_BATCH_SIZE:
            logger.info(
                f"[Digest] Batch threshold reached ({lang}), scheduling flush"
            )
            asyncio.create_task(self._flush_lang(lang))

        # Start timer if not running
        self._ensure_timer_started()

    def _ensure_timer_started(self) -> None:
        """Ensure the timer is running."""
        if self._timer_task is None or self._timer_task.done():
            self._timer_task = asyncio.create_task(self._timer_loop())

    async def _timer_loop(self) -> None:
        """Periodically check whether a flush is needed."""
        while True:
            await asyncio.sleep(60)  # Check every minute

            now = datetime.now(timezone.utc)
            elapsed = (now - self._last_flush_time).total_seconds()

            if elapsed >= self.MAX_WAIT_SECONDS:
                has_items = any(len(q) > 0 for q in self._queues.values())
                if has_items:
                    logger.info(
                        f"[Digest] Timer triggered flush after {elapsed:.0f}s"
                    )
                    await self.flush_all()

    async def _flush_lang(self, lang: str) -> None:
        """Flush queue for a specific language.

        Args:
            lang: Language
        """
        if lang not in self._queues or not self._queues[lang]:
            return

        items = self._queues[lang]
        self._queues[lang] = []  # Clear queue
        self._last_flush_time = datetime.now(timezone.utc)

        # Generate aggregated message
        message = self._format_digest(items, lang)

        if self._send_callback:
            try:
                await self._send_callback(lang, message)
                logger.info(
                    f"[Digest] Flushed {len(items)} items ({lang})"
                )
            except Exception as e:
                logger.error(f"[Digest] Send failed ({lang}): {e}")
                # Re-queue on failure
                self._queues[lang].extend(items)
        else:
            logger.warning(
                f"[Digest] No send callback set, {len(items)} items discarded"
            )

    async def flush_all(self) -> None:
        """Flush all language queues.

        Called during graceful shutdown.
        """
        for lang in list(self._queues.keys()):
            await self._flush_lang(lang)

    def _format_digest(self, items: List[DigestItem], lang: str) -> str:
        """Format digest message.

        Args:
            items: List of DigestItem
            lang: Language

        Returns:
            Formatted HTML message
        """
        header = "Market Digest"

        lines = [f"<b>{header}</b>"]
        lines.append("")

        for item in items[:8]:  # Show at most 8 items
            formatted = self._humanize_item(item, lang)
            lines.append(formatted)

        if len(items) > 8:
            remaining = len(items) - 8
            lines.append(f"<i>...and {remaining} more</i>")

        return "\n".join(lines)

    def _humanize_item(self, item: DigestItem, lang: str) -> str:
        """Convert DigestItem to human-readable format.

        Args:
            item: DigestItem
            lang: Language

        Returns:
            Human-readable line, e.g. "📉 BTC | Flash drop -5.2%"
        """
        symbol = item.symbol
        data = item.data

        if item.event_type == 'alert':
            alert_type = data.get('alert_type', '')
            mapping = EVENT_MAP.get(alert_type)
            if mapping:
                icon = mapping['icon']
                label = t(mapping['label_key'], lang)
                change_pct = data.get('change_pct', 0)
                sign = '+' if change_pct >= 0 else ''
                return f"{icon} {symbol} | {label} {sign}{change_pct:.1f}%"
            else:
                return f"📊 {symbol} | {item.summary}"

        elif item.event_type == 'orderbook':
            side = data.get('imbalance_side', '')
            mapping = ORDERBOOK_MAP.get(side)
            if mapping:
                icon = mapping['icon']
                label = t(mapping['label_key'], lang)
                pct = data.get('imbalance_pct', 0)
                return f"{icon} {symbol} | {label} {pct:.0f}%"
            else:
                return f"📊 {symbol} | {item.summary}"

        elif item.event_type == 'spread':
            spread_type = data.get('spread_type', '')
            spread_pct = data.get('spread_pct', 0)
            if spread_type == 'PREMIUM':
                type_label = 'Premium'
                icon = '📈'
            else:
                type_label = 'Discount'
                icon = '📉'
            return f"{icon} {symbol} | {type_label} {abs(spread_pct):.1f}%"

        else:
            return f"📊 {symbol} | {item.summary}"

    def get_status(self) -> Dict[str, Any]:
        """Get Digest status (for debugging).

        Returns:
            Status dict
        """
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_flush_time).total_seconds()

        return {
            'queue_sizes': {lang: len(q) for lang, q in self._queues.items()},
            'last_flush_seconds_ago': round(elapsed, 1),
            'max_wait_seconds': self.MAX_WAIT_SECONDS,
            'max_batch_size': self.MAX_BATCH_SIZE,
            'timer_running': self._timer_task is not None and not self._timer_task.done(),
        }

    def stop_timer(self) -> None:
        """Stop the timer."""
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
            logger.debug("[Digest] Timer stopped")


# ==========================================
# Global singleton
# ==========================================

_digest_manager: Optional[DigestManager] = None


def get_digest_manager() -> DigestManager:
    """Get global DigestManager instance."""
    global _digest_manager
    if _digest_manager is None:
        _digest_manager = DigestManager()
    return _digest_manager
