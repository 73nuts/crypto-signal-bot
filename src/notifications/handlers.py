"""
Event notification handlers.

Message bus-based notification handlers; subscribes to business events and sends notifications.

Priority layering system:
- CircuitBreaker: >10 messages/min triggers 5-min circuit break
- SlidingWindowLimiter: alert=20/h, spread=10/h, orderbook=10/h
- DigestManager: aggregate and send after 5 minutes or 3 messages
- P0 messages reply to Daily Pulse pinned message
"""

import logging
import os

from src.core.message_bus import on_event
from src.core.events import (
    AlertDetectedEvent,
    DailyPulseReadyEvent,
    SpreadDetectedEvent,
    OrderbookImbalanceEvent,
    SignalGeneratedEvent,
    PositionOpenedEvent,
    PositionClosedEvent,
    PaymentReceivedEvent,
)

logger = logging.getLogger(__name__)

# Dry Run mode: log only, no actual sends
DRY_RUN = os.getenv("NOTIFICATION_DRY_RUN", "").lower() in ("true", "1", "yes")


# ==========================================
# Notification component singletons (lazy init to avoid circular imports)
# ==========================================

_swing_notification_manager = None
_position_manager = None
_broadcaster = None
_wechat_sender = None


def _get_swing_notification_manager():
    """Get SwingNotificationManager singleton (lazy init)."""
    global _swing_notification_manager
    if _swing_notification_manager is None:
        from src.strategies.swing.services.notification_manager import SwingNotificationManager

        _swing_notification_manager = SwingNotificationManager()
    return _swing_notification_manager


def _get_position_manager():
    """Get PositionManager singleton (lazy init)."""
    global _position_manager
    if _position_manager is None:
        from src.trading.position_manager import PositionManager

        _position_manager = PositionManager()
    return _position_manager


def _get_broadcaster():
    """Get TelegramBroadcaster singleton (lazy init)."""
    global _broadcaster
    if _broadcaster is None:
        from src.notifications.telegram_broadcaster import get_broadcaster

        _broadcaster = get_broadcaster()
    return _broadcaster


def _get_wechat_sender():
    """Get WeChatSender singleton (lazy init)."""
    global _wechat_sender
    if _wechat_sender is None:
        from src.notifications.wechat_sender import WeChatSender

        _wechat_sender = WeChatSender()
    return _wechat_sender


# ==========================================
# Priority component singletons
# ==========================================

_priority_calculator = None
_circuit_breaker = None
_sliding_window = None
_digest_manager = None
_pinned_reply_manager = None


def _get_priority_calculator():
    """Get priority calculator singleton."""
    global _priority_calculator
    if _priority_calculator is None:
        from src.notifications.priority import get_priority_calculator

        _priority_calculator = get_priority_calculator()
    return _priority_calculator


def _get_circuit_breaker():
    """Get circuit breaker singleton."""
    global _circuit_breaker
    if _circuit_breaker is None:
        from src.notifications.priority import get_circuit_breaker

        _circuit_breaker = get_circuit_breaker()
    return _circuit_breaker


def _get_sliding_window():
    """Get sliding window rate limiter singleton."""
    global _sliding_window
    if _sliding_window is None:
        from src.notifications.priority import get_sliding_window_limiter

        _sliding_window = get_sliding_window_limiter()
    return _sliding_window


def _get_digest_manager():
    """Get digest manager singleton."""
    global _digest_manager
    if _digest_manager is None:
        from src.notifications.priority import get_digest_manager

        _digest_manager = get_digest_manager()
        # Set send callback
        _digest_manager.set_send_callback(_send_digest_message)
    return _digest_manager


def _get_pinned_reply_manager():
    """Get pinned reply manager singleton."""
    global _pinned_reply_manager
    if _pinned_reply_manager is None:
        from src.notifications.priority import get_pinned_reply_manager

        _pinned_reply_manager = get_pinned_reply_manager()
    return _pinned_reply_manager


# ==========================================
# Priority helper functions
# ==========================================


async def _send_digest_message(lang: str, message: str) -> None:
    """Send digest aggregated message (silent).

    Args:
        lang: Language (zh/en)
        message: Aggregated message content
    """
    from src.core.config import settings

    if DRY_RUN:
        logger.info(f"[DRY_RUN] Would send Digest ({lang}): {message[:100]}...")
        return

    broadcaster = _get_broadcaster()
    if not broadcaster or not broadcaster.is_ready:
        logger.warning("[Digest] Broadcaster not ready")
        return

    # Send to Premium channel (silent)
    channels = settings.get_channels_by_level("PREMIUM")
    channel_id = channels.get(lang)
    if channel_id:
        try:
            await broadcaster.bot.send_message(
                chat_id=int(channel_id),
                text=message,
                parse_mode="HTML",
                disable_notification=True,  # Silent
            )
            logger.info(f"[Digest] Sent to PREMIUM/{lang}")
        except Exception as e:
            logger.error(f"[Digest] Send failed: {e}")


async def _send_with_reply_to_pinned(
    broadcaster, chat_id: int, message: str, disable_notification: bool = False
) -> None:
    """Send message replying to the day's pinned message.

    Args:
        broadcaster: TelegramBroadcaster instance
        chat_id: Channel ID
        message: Message content
        disable_notification: Whether to send silently
    """
    pinned_mgr = _get_pinned_reply_manager()
    pinned_msg_id = await pinned_mgr.get_pinned_message_async(chat_id)

    if DRY_RUN:
        logger.info(
            f"[DRY_RUN] Would send with reply_to={pinned_msg_id}: {message[:100]}..."
        )
        return

    try:
        await broadcaster.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="HTML",
            disable_notification=disable_notification,
            reply_to_message_id=pinned_msg_id,  # Reply to pinned
        )
    except Exception as e:
        # Fall back to regular send if reply fails
        logger.warning(f"Reply to pinned failed, sending without reply: {e}")
        await broadcaster.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="HTML",
            disable_notification=disable_notification,
        )


def _add_to_digest(
    event_type: str,
    symbol: str,
    summary: str,
    lang: str = "zh",
    reason: str = "P2",
    data: dict = None,
) -> None:
    """Add message to digest queue.

    Args:
        event_type: Event type (alert/spread/orderbook)
        symbol: Asset symbol
        summary: Short description
        lang: Language
        reason: Reason (P2/RATE_LIMITED/CIRCUIT_BREAKER)
        data: Event data (for digest human-readable display)
    """
    digest_mgr = _get_digest_manager()
    digest_mgr.add(event_type, symbol, summary, lang, reason, data=data)


# ==========================================
# Scanner event handlers
# ==========================================


@on_event("scanner.alert_detected")
async def handle_alert_notification(event: AlertDetectedEvent):
    """Handle market alert notification event (priority layering).

    Flow:
    1. Calculate priority (P0/P1/P2)
    2. Circuit breaker check
    3. Handle by priority:
       - P0: Reply to pinned, always send
       - P1: Send after sliding window rate limit check
       - P2: Route to digest queue
    """
    from src.core.config import settings
    from src.notifications.priority import Priority

    logger.info(
        f"[EventHandler] Alert event | "
        f"symbol={event.symbol} | "
        f"type={event.alert_type} | "
        f"score={event.score:.1f} | "
        f"trace_id={event.trace_id}"
    )

    priority = _get_priority_calculator().calculate(event)

    # Build digest data
    alert_data = {
        "alert_type": event.alert_type,
        "change_pct": event.data.get("change_pct", 0),
        "score": event.score,
    }

    breaker = _get_circuit_breaker()
    if breaker.is_open():
        # Circuit open: all messages go to digest
        logger.info(f"[EventHandler] CircuitBreaker OPEN, routing to Digest")
        for lang in ["zh", "en"]:
            _add_to_digest(
                "alert",
                event.symbol,
                event.alert_type,
                lang,
                "CIRCUIT_BREAKER",
                data=alert_data,
            )
        return

    if not breaker.record_message():
        # Circuit tripped
        logger.warning(f"[EventHandler] CircuitBreaker TRIPPED by {event.symbol}")
        for lang in ["zh", "en"]:
            _add_to_digest(
                "alert",
                event.symbol,
                event.alert_type,
                lang,
                "CIRCUIT_BREAKER",
                data=alert_data,
            )
        return

    # Get per-language messages
    messages_by_lang = getattr(event, "messages_by_lang", None)
    if not messages_by_lang:
        messages_by_lang = {"zh": event.message, "en": event.message}

    # Short summary for digest fallback
    summary = event.alert_type

    try:
        broadcaster = _get_broadcaster()
        if not broadcaster or not broadcaster.is_ready:
            logger.warning("[EventHandler] Broadcaster not ready")
            return

        if priority == Priority.P0:
            # P0: Reply to pinned, always with sound, send to BASIC+PREMIUM
            logger.info(f"[EventHandler] P0 alert: {event.symbol}, sending with reply")
            for level in ["BASIC", "PREMIUM"]:
                channels = settings.get_channels_by_level(level)
                for lang, channel_id in channels.items():
                    if not channel_id:
                        continue
                    msg = messages_by_lang.get(lang, event.message)
                    await _send_with_reply_to_pinned(
                        broadcaster, int(channel_id), msg, disable_notification=False
                    )

        elif priority == Priority.P1:
            # P1: Sliding window rate limit check
            if not _get_sliding_window().check_and_record("alert"):
                # Over limit, route to digest
                logger.info(f"[EventHandler] P1 rate limited: {event.symbol}")
                for lang in ["zh", "en"]:
                    _add_to_digest(
                        "alert",
                        event.symbol,
                        summary,
                        lang,
                        "RATE_LIMITED",
                        data=alert_data,
                    )
                return

            # P1 normal send
            disable_notification = event.score < 90
            for level in ["BASIC", "PREMIUM"]:
                channels = settings.get_channels_by_level(level)
                for lang, channel_id in channels.items():
                    if not channel_id:
                        continue
                    msg = messages_by_lang.get(lang, event.message)
                    if DRY_RUN:
                        logger.info(f"[DRY_RUN] Would send P1 to {level}/{lang}")
                        continue
                    try:
                        await broadcaster.bot.send_message(
                            chat_id=int(channel_id),
                            text=msg,
                            parse_mode="HTML",
                            disable_notification=disable_notification,
                        )
                    except Exception as e:
                        logger.warning(f"Send to {level}/{lang} failed: {e}")

        else:  # P2
            # P2: Route directly to digest
            logger.debug(f"[EventHandler] P2 alert: {event.symbol}, routing to Digest")
            for lang in ["zh", "en"]:
                _add_to_digest(
                    "alert", event.symbol, summary, lang, "P2", data=alert_data
                )
            return

        # WeChat push removed - only Swing signals and trading error alerts use WeChat
        # Alert notifications go through Telegram only

        logger.info(
            f"[EventHandler] Alert notification sent | "
            f"priority=P{priority} | symbol={event.symbol}"
        )

    except Exception as e:
        logger.error(f"[EventHandler] Alert notification send failed: {e}", exc_info=True)


@on_event("scanner.daily_pulse_ready")
async def handle_daily_pulse_notification(event: DailyPulseReadyEvent):
    """Handle daily pulse notification event (language-timezone binding).

    Routes by target_lang to reduce noise:
    - target_lang='zh': Chinese channels only (00:00 UTC = 08:00 Beijing)
    - target_lang='en': English channels only (08:00 UTC)
    - target_lang='': All channels (backward compatible)
    """
    import time
    from src.core.config import settings

    target_lang = getattr(event, "target_lang", "")

    logger.info(
        f"[EventHandler] Daily pulse event | "
        f"target_lang={target_lang or 'ALL'} | "
        f"content_length={len(event.content)} | "
        f"trace_id={event.trace_id}"
    )

    # Get per-language content
    content_by_lang = getattr(event, "content_by_lang", None)
    content_hook_by_lang = getattr(event, "content_hook_by_lang", None)
    if not content_by_lang:
        # Backward compat: use single content
        content_by_lang = {"zh": event.content, "en": event.content}
    if not content_hook_by_lang and event.content_hook:
        content_hook_by_lang = {"zh": event.content_hook, "en": event.content_hook}

    try:
        broadcaster = _get_broadcaster()
        if broadcaster and broadcaster.is_ready:
            # Fear & Greed official image URL (cache-busted)
            cache_buster = int(time.time())
            fg_image_url = f"https://alternative.me/crypto/fear-and-greed-index.png?t={cache_buster}"

            # PREMIUM Channels: full version (by language)
            premium_channels = settings.get_channels_by_level("PREMIUM")
            for lang, channel_id in premium_channels.items():
                if not channel_id:
                    continue
                # Filter non-target language to reduce noise
                if target_lang and lang != target_lang:
                    continue
                content = content_by_lang.get(lang, event.content)
                try:
                    await _send_daily_pulse_to_channel(
                        broadcaster, int(channel_id), fg_image_url, content
                    )
                    logger.info(f"[EventHandler] Daily Pulse sent (PREMIUM/{lang})")
                except Exception as e:
                    logger.warning(f"Send to PREMIUM/{lang} failed: {e}")

            # BASIC Channels: hook version (by language)
            if content_hook_by_lang:
                basic_channels = settings.get_channels_by_level("BASIC")
                for lang, channel_id in basic_channels.items():
                    if not channel_id:
                        continue
                    # Filter non-target language to reduce noise
                    if target_lang and lang != target_lang:
                        continue
                    content = content_hook_by_lang.get(lang, event.content_hook)
                    try:
                        await _send_daily_pulse_to_channel(
                            broadcaster, int(channel_id), fg_image_url, content
                        )
                        logger.info(
                            f"[EventHandler] Daily Pulse sent (BASIC/{lang})"
                        )
                    except Exception as e:
                        logger.warning(f"Send to BASIC/{lang} failed: {e}")

        # WeChat push removed - daily pulse goes through Telegram only

        logger.info("[EventHandler] Daily pulse notification sent")

    except Exception as e:
        logger.error(f"[EventHandler] Daily pulse notification send failed: {e}", exc_info=True)


async def _send_daily_pulse_to_channel(
    broadcaster, chat_id: int, image_url: str, content: str
):
    """Send Daily Pulse to a single channel.

    Flow: send F&G image -> send pulse body -> pin pulse message -> record pinned ID
    """
    from src.telegram.utils import update_pinned_message

    # Step 1: Send F&G image as cover
    try:
        await broadcaster.bot.send_photo(
            chat_id=chat_id,
            photo=image_url,
            caption="<b>Market Sentiment Overview</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        # Image send failure does not block the flow
        logger.warning(f"[EventHandler] F&G image send failed: {e}")

    # Step 2: Send full pulse body
    msg = await broadcaster.bot.send_message(
        chat_id=chat_id, text=content, parse_mode="HTML", disable_web_page_preview=True
    )

    # Step 3: Pin management - unpin old, pin new pulse
    await update_pinned_message(broadcaster.bot, chat_id, msg.message_id)

    # Step 4: Record pinned message ID for P0 replies
    try:
        pinned_mgr = _get_pinned_reply_manager()
        await pinned_mgr.set_pinned_message_async(chat_id, msg.message_id)
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to record pinned message ID: {e}")


def _strip_html(html_text: str) -> str:
    """Strip HTML tags (fallback utility)."""
    import re

    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", html_text)
    # Replace HTML entities
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text


@on_event("scanner.spread_detected")
async def handle_spread_notification(event: SpreadDetectedEvent):
    """Handle spread alert event (priority layering).

    Flow:
    1. Calculate priority (P0/P1/P2)
    2. Circuit breaker check
    3. Handle by priority
    """
    from src.core.config import settings
    from src.notifications.priority import Priority

    logger.info(
        f"[EventHandler] Spread event | "
        f"symbol={event.symbol} | "
        f"spread={event.spread_pct:+.2f}% | "
        f"type={event.spread_type} | "
        f"trace_id={event.trace_id}"
    )

    priority = _get_priority_calculator().calculate(event)

    spread_data = {
        "spread_type": event.spread_type,
        "spread_pct": event.spread_pct,
    }

    breaker = _get_circuit_breaker()
    if breaker.is_open():
        logger.info(f"[EventHandler] CircuitBreaker OPEN, routing spread to Digest")
        for lang in ["zh", "en"]:
            _add_to_digest(
                "spread",
                event.symbol,
                event.spread_type,
                lang,
                "CIRCUIT_BREAKER",
                data=spread_data,
            )
        return

    if not breaker.record_message():
        logger.warning(
            f"[EventHandler] CircuitBreaker TRIPPED by spread {event.symbol}"
        )
        for lang in ["zh", "en"]:
            _add_to_digest(
                "spread",
                event.symbol,
                event.spread_type,
                lang,
                "CIRCUIT_BREAKER",
                data=spread_data,
            )
        return

    summary = event.spread_type

    try:
        broadcaster = _get_broadcaster()
        if not broadcaster or not broadcaster.is_ready:
            logger.warning("[EventHandler] Broadcaster not ready, skipping spread push")
            return

        if priority == Priority.P0:
            # P0: Reply to pinned (extreme spread >=10%), send to BASIC+PREMIUM
            logger.info(f"[EventHandler] P0 spread: {event.symbol}, sending with reply")
            for level in ["BASIC", "PREMIUM"]:
                channels = settings.get_channels_by_level(level)
                for lang, channel_id in channels.items():
                    if not channel_id:
                        continue
                    msg = event.messages_by_lang.get(lang)
                    if not msg:
                        continue
                    await _send_with_reply_to_pinned(
                        broadcaster, int(channel_id), msg, disable_notification=False
                    )

        elif priority == Priority.P1:
            # P1: Sliding window rate limit check
            if not _get_sliding_window().check_and_record("spread"):
                logger.info(f"[EventHandler] P1 spread rate limited: {event.symbol}")
                for lang in ["zh", "en"]:
                    _add_to_digest(
                        "spread",
                        event.symbol,
                        summary,
                        lang,
                        "RATE_LIMITED",
                        data=spread_data,
                    )
                return

            # P1 normal send (PREMIUM channel)
            channels = settings.get_channels_by_level("PREMIUM")
            for lang, channel_id in channels.items():
                if not channel_id:
                    continue
                msg = event.messages_by_lang.get(lang)
                if not msg:
                    continue
                if DRY_RUN:
                    logger.info(f"[DRY_RUN] Would send P1 spread to PREMIUM/{lang}")
                    continue
                try:
                    await broadcaster.bot.send_message(
                        chat_id=int(channel_id),
                        text=msg,
                        parse_mode="HTML",
                        disable_notification=False,
                    )
                    logger.info(f"[EventHandler] Spread alert sent (PREMIUM/{lang})")
                except Exception as e:
                    logger.warning(f"Send to PREMIUM/{lang} failed: {e}")

        else:  # P2
            # P2: Route to digest
            logger.debug(f"[EventHandler] P2 spread: {event.symbol}, routing to Digest")
            for lang in ["zh", "en"]:
                _add_to_digest(
                    "spread", event.symbol, summary, lang, "P2", data=spread_data
                )
            return

        logger.info(
            f"[EventHandler] Spread notification sent | "
            f"priority=P{priority} | symbol={event.symbol}"
        )

    except Exception as e:
        logger.error(f"[EventHandler] Spread notification send failed: {e}", exc_info=True)


@on_event("scanner.orderbook_imbalance")
async def handle_orderbook_notification(event: OrderbookImbalanceEvent):
    """Handle order book imbalance alert event (priority layering).

    Flow:
    1. Calculate priority (P0/P1/P2)
    2. Circuit breaker check
    3. Handle by priority
    """
    from src.core.config import settings
    from src.notifications.priority import Priority

    logger.info(
        f"[EventHandler] Orderbook event | "
        f"symbol={event.symbol} | "
        f"side={event.imbalance_side} | "
        f"ratio={event.imbalance_ratio:.2f} | "
        f"pct={event.imbalance_pct:.0f}% | "
        f"trace_id={event.trace_id}"
    )

    priority = _get_priority_calculator().calculate(event)

    orderbook_data = {
        "imbalance_side": event.imbalance_side,
        "imbalance_pct": event.imbalance_pct,
        "imbalance_ratio": event.imbalance_ratio,
    }

    breaker = _get_circuit_breaker()
    if breaker.is_open():
        logger.info(f"[EventHandler] CircuitBreaker OPEN, routing orderbook to Digest")
        for lang in ["zh", "en"]:
            _add_to_digest(
                "orderbook",
                event.symbol,
                event.imbalance_side,
                lang,
                "CIRCUIT_BREAKER",
                data=orderbook_data,
            )
        return

    if not breaker.record_message():
        logger.warning(
            f"[EventHandler] CircuitBreaker TRIPPED by orderbook {event.symbol}"
        )
        for lang in ["zh", "en"]:
            _add_to_digest(
                "orderbook",
                event.symbol,
                event.imbalance_side,
                lang,
                "CIRCUIT_BREAKER",
                data=orderbook_data,
            )
        return

    summary = event.imbalance_side

    try:
        broadcaster = _get_broadcaster()
        if not broadcaster or not broadcaster.is_ready:
            logger.warning("[EventHandler] Broadcaster not ready, skipping orderbook push")
            return

        if priority == Priority.P0:
            # P0: Reply to pinned (extreme imbalance >=87%), send to BASIC+PREMIUM
            logger.info(
                f"[EventHandler] P0 orderbook: {event.symbol}, sending with reply"
            )
            for level in ["BASIC", "PREMIUM"]:
                channels = settings.get_channels_by_level(level)
                for lang, channel_id in channels.items():
                    if not channel_id:
                        continue
                    msg = event.messages_by_lang.get(lang)
                    if not msg:
                        continue
                    await _send_with_reply_to_pinned(
                        broadcaster, int(channel_id), msg, disable_notification=False
                    )

        elif priority == Priority.P1:
            # P1: Sliding window rate limit check
            if not _get_sliding_window().check_and_record("orderbook"):
                logger.info(f"[EventHandler] P1 orderbook rate limited: {event.symbol}")
                for lang in ["zh", "en"]:
                    _add_to_digest(
                        "orderbook",
                        event.symbol,
                        summary,
                        lang,
                        "RATE_LIMITED",
                        data=orderbook_data,
                    )
                return

            # P1 normal send (PREMIUM channel)
            channels = settings.get_channels_by_level("PREMIUM")
            for lang, channel_id in channels.items():
                if not channel_id:
                    continue
                msg = event.messages_by_lang.get(lang)
                if not msg:
                    continue
                if DRY_RUN:
                    logger.info(f"[DRY_RUN] Would send P1 orderbook to PREMIUM/{lang}")
                    continue
                try:
                    await broadcaster.bot.send_message(
                        chat_id=int(channel_id),
                        text=msg,
                        parse_mode="HTML",
                        disable_notification=False,
                    )
                    logger.info(f"[EventHandler] Orderbook alert sent (PREMIUM/{lang})")
                except Exception as e:
                    logger.warning(f"Send to PREMIUM/{lang} failed: {e}")

        else:  # P2
            # P2: Route to digest
            logger.debug(
                f"[EventHandler] P2 orderbook: {event.symbol}, routing to Digest"
            )
            for lang in ["zh", "en"]:
                _add_to_digest(
                    "orderbook", event.symbol, summary, lang, "P2", data=orderbook_data
                )
            return

        logger.info(
            f"[EventHandler] Orderbook notification sent | "
            f"priority=P{priority} | symbol={event.symbol}"
        )

    except Exception as e:
        logger.error(f"[EventHandler] Orderbook notification send failed: {e}", exc_info=True)


# ==========================================
# Swing strategy event handlers
# ==========================================


@on_event("swing.signal_generated")
async def handle_signal_notification(event: SignalGeneratedEvent):
    """Handle trading signal notification event.

    Current behavior:
      - Log only
      - Actual push handled by SwingExecutor/NotificationManager
    """
    logger.info(
        f"[EventHandler] Trading signal | "
        f"symbol={event.symbol} | "
        f"direction={event.direction} | "
        f"entry={event.entry_price:.2f} | "
        f"trace_id={event.trace_id}"
    )


@on_event("swing.position_opened")
async def handle_position_opened_notification(event: PositionOpenedEvent):
    """Handle position opened notification event.

    - Calls SwingNotificationManager to send entry notification
    - Saves telegram_message_id to position record (for exit reply)
    """
    logger.info(
        f"[EventHandler] Position opened | "
        f"position_id={event.position_id} | "
        f"symbol={event.symbol} | "
        f"entry={event.entry_price:.2f} | "
        f"trace_id={event.trace_id}"
    )

    try:
        manager = _get_swing_notification_manager()
        result = await manager.send_signal(
            {
                "type": "ENTRY",
                "symbol": event.symbol,
                "strategy": event.strategy_name,
                "price": event.entry_price,
                "action": "LONG",
                "stop_loss": event.stop_loss,
                "current_price": event.entry_price,
            }
        )

        # Save telegram_message_id to position for exit reply
        telegram_msg_id = result.get("telegram_message_id") if result else None
        if telegram_msg_id and event.position_id:
            try:
                pm = _get_position_manager()
                pm.update_telegram_message_id(event.position_id, telegram_msg_id)
                logger.info(
                    f"[EventHandler] Entry message ID saved: "
                    f"position_id={event.position_id}, msg_id={telegram_msg_id}"
                )
            except Exception as e:
                logger.warning(f"[EventHandler] Failed to save message ID: {e}")

    except Exception as e:
        logger.error(f"[EventHandler] Position opened notification send failed: {e}", exc_info=True)


@on_event("swing.position_closed")
async def handle_position_closed_notification(event: PositionClosedEvent):
    """Handle position closed notification event.

    - Calls SwingNotificationManager to send exit notification
    - Uses telegram_message_id to reply to entry message
    """
    logger.info(
        f"[EventHandler] Position closed | "
        f"position_id={event.position_id} | "
        f"symbol={event.symbol} | "
        f"pnl={event.pnl_percent:+.2f}% | "
        f"trace_id={event.trace_id}"
    )

    try:
        manager = _get_swing_notification_manager()
        await manager.send_signal(
            {
                "type": "EXIT",
                "symbol": event.symbol,
                "entry_price": event.entry_price,
                "price": event.exit_price,
                "action": "CLOSE",
                "pnl_pct": event.pnl_percent,
                "reason": event.reason,
            },
            reply_to_message_id=event.telegram_message_id,
        )
        logger.info(
            f"[EventHandler] Position closed notification sent"
            f"{' (Reply=' + str(event.telegram_message_id) + ')' if event.telegram_message_id else ''}"
        )

    except Exception as e:
        logger.error(f"[EventHandler] Position closed notification send failed: {e}", exc_info=True)


# ==========================================
# Payment event handlers
# ==========================================


@on_event("payment.received")
async def handle_payment_received(event: PaymentReceivedEvent):
    """Handle payment success event.

    - Logs payment success (audit trail)
    - Extensible: admin notifications, stats, etc.
    """
    logger.info(
        f"[EventHandler] Payment received | "
        f"order_id={event.order_id} | "
        f"telegram_id={event.telegram_id} | "
        f"plan_code={event.plan_code} | "
        f"trace_id={event.trace_id}"
    )


# ==========================================
# Explicit registration function (prevents import side-effect removal by tools)
# ==========================================


def register_all_handlers(bus=None):
    """Explicitly register all event handlers.

    Addresses: @on_event decorators rely on import side effects,
    which may be removed by auto-formatters (ruff/isort) as unused imports.

    Usage:
        from src.notifications.handlers import register_all_handlers
        register_all_handlers(message_bus)

    Note:
        Since @on_event decorators auto-register on module import,
        this function mainly serves to:
        1. Ensure the module is correctly imported
        2. Provide an explicit call point to prevent import removal
        3. Log registration for debugging
    """
    from src.core.message_bus import get_message_bus

    bus = bus or get_message_bus()

    # Verify handlers are registered
    registered_events = list(bus._handlers.keys())

    expected_events = [
        "scanner.alert_detected",
        "scanner.daily_pulse_ready",
        "scanner.spread_detected",
        "scanner.orderbook_imbalance",
        "swing.signal_generated",
        "swing.position_opened",
        "swing.position_closed",
        "payment.received",
    ]

    missing = [e for e in expected_events if e not in registered_events]
    if missing:
        logger.warning(f"[Handlers] Missing event handlers: {missing}")
    else:
        logger.info(f"[Handlers] {len(expected_events)} event handlers registered")
