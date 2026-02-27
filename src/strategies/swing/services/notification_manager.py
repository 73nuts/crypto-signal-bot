"""
Swing notification manager (daily trend following)

Responsibilities:
  1. Unified multi-channel push (Telegram/email/WeChat)
  2. Format Swing signals for each channel
  3. Personal notifications (separate from VIP group)

Channels:
  - Telegram: VIP group push + personal push
  - Email: SMTP push
  - WeChat: Server Chan push
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from src.core.config import settings
from src.notifications.telegram_app import get_bot
from src.telegram.i18n import t


class SwingNotificationManager:
    """Swing notification manager (daily trend following)"""

    def __init__(self):
        """Initialize the notification manager."""
        self.logger = logging.getLogger(__name__)

        # Channel senders (lazy initialized)
        self._telegram_sender = None
        self._email_sender = None
        self._wechat_sender = None

        # Config cache (using unified config manager)
        self._env_prefix = settings.ENVIRONMENT_PREFIX
        self._telegram_bot_token = settings.get_secret('TELEGRAM_BOT_TOKEN')
        self._telegram_premium_channels = settings.get_channels_by_level('PREMIUM')
        self._personal_chat_id = settings.TELEGRAM_PERSONAL_CHAT_ID

        # Personal Telegram (direct send)
        self._personal_telegram_enabled = False

        # Initialization state
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Ensure all channels are initialized."""
        if self._initialized:
            return

        self._init_telegram()
        self._init_email()
        self._init_wechat()

        self._initialized = True
        self._log_status()

    def _init_telegram(self) -> None:
        """Initialize Telegram."""
        try:
            from src.notifications.telegram_sender import TelegramSender
            self._telegram_sender = TelegramSender()

            # Personal Telegram
            if self._personal_chat_id:
                self._personal_telegram_enabled = True
                self.logger.info(f"Telegram personal push enabled: {self._personal_chat_id}")
        except Exception as e:
            self.logger.warning(f"Telegram init failed: {e}")

    def _init_email(self) -> None:
        """Initialize email."""
        try:
            email_config = self._build_email_config()
            if email_config.get('enabled'):
                from src.notifications.email_sender import EmailSender
                self._email_sender = EmailSender(email_config)
                self.logger.info("Email push enabled")
        except Exception as e:
            self.logger.warning(f"Email init failed: {e}")

    def _init_wechat(self) -> None:
        """Initialize WeChat."""
        try:
            from src.notifications.wechat_sender import WeChatSender
            self._wechat_sender = WeChatSender()
            if self._wechat_sender.enabled:
                self.logger.info("WeChat push enabled")
        except Exception as e:
            self.logger.warning(f"WeChat init failed: {e}")

    @staticmethod
    def _build_email_config() -> Dict[str, Any]:
        """Build email config from unified settings."""
        if not settings.EMAIL_ENABLED:
            return {'enabled': False}

        return {
            'enabled': True,
            'smtp_server': settings.EMAIL_SMTP_SERVER,
            'smtp_ports': [settings.EMAIL_SMTP_PORT, 465],
            'username': settings.EMAIL_USERNAME or '',
            'password': settings.get_secret('EMAIL_PASSWORD', ''),
            'recipients': [
                {'email': settings.EMAIL_TO or '', 'enabled': True}
            ]
        }

    def _log_status(self) -> None:
        """Log initialization status."""
        status = []
        if self._telegram_sender and self._telegram_sender.is_enabled():
            status.append("Telegram(VIP)")
        if self._personal_telegram_enabled:
            status.append("Telegram(personal)")
        if self._email_sender:
            status.append("Email")
        if self._wechat_sender and self._wechat_sender.enabled:
            status.append("WeChat")

        if status:
            self.logger.info(f"Swing notification channels: {', '.join(status)}")
        else:
            self.logger.warning("Swing notifications: no channels available")

    # ========================================
    # Signal push
    # ========================================

    async def send_signal(
        self,
        signal: Dict[str, Any],
        reply_to_message_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send a signal to all channels.

        Channel strategy:
        - ENTRY/EXIT: all channels (Telegram VIP + WeChat/email)
        - Stop-loss move: Telegram VIP only

        Args:
            signal: Signal dict containing:
                - type: ENTRY/EXIT
                - symbol: asset symbol
                - strategy: strategy name
                - price: price
                - action: LONG/SELL
                - stop_loss: stop-loss price
                - take_profit: take-profit price list
                - reason: reason
                - pnl_pct: P/L percent (on exit)
            reply_to_message_id: Message ID to reply to (for reply thread).

        Returns:
            {
                'success': Dict[str, bool],  # per-channel send result
                'telegram_message_id': Optional[int]
            }
        """
        self._ensure_initialized()

        results = {
            'success': {
                'telegram_vip': False,
                'email': False,
                'wechat': False
            },
            'telegram_message_id': None
        }

        signal_type = signal.get('type', 'ENTRY')
        symbol = signal.get('symbol', 'UNKNOWN')

        # Format messages
        title = self._format_title(signal)
        text_message = self._format_text_message(signal)

        # 1. Telegram VIP group (all signal types)
        telegram_msg_id = await self._send_telegram_vip_signal(signal, reply_to_message_id)
        if telegram_msg_id:
            results['success']['telegram_vip'] = True
            results['telegram_message_id'] = telegram_msg_id

        # 2. WeChat/email (ENTRY and EXIT only)
        if signal_type in ('ENTRY', 'EXIT'):
            # Email
            if self._email_sender:
                try:
                    results['success']['email'] = self._email_sender.send(title, text_message)
                except Exception as e:
                    self.logger.error(f"Email push failed: {e}")

            # WeChat
            if self._wechat_sender and self._wechat_sender.enabled:
                try:
                    results['success']['wechat'] = self._wechat_sender.send(title, text_message)
                except Exception as e:
                    self.logger.error(f"WeChat push failed: {e}")

        success_count = sum(1 for v in results['success'].values() if v)
        self.logger.info(f"[{symbol}] {signal_type} signal push done: {success_count} channel(s) succeeded")

        return results

    async def _send_telegram_vip_signal(
        self,
        signal: Dict[str, Any],
        reply_to_message_id: Optional[int] = None
    ) -> Optional[int]:
        """
        Send signal to Telegram Premium channels.

        Args:
            signal: Signal data.
            reply_to_message_id: Message ID to reply to.

        Returns:
            First message_id on success, None on failure.
        """
        try:
            if not self._telegram_premium_channels:
                self.logger.warning("Telegram Premium channels not configured")
                return None

            bot = get_bot()
            if not bot:
                self.logger.warning("Telegram Bot unavailable")
                return None

            # Format Telegram message
            message = self._format_telegram_signal(signal)
            signal_type = signal.get('type', 'ENTRY')

            first_msg_id = None
            for lang, channel_id in self._telegram_premium_channels.items():
                try:
                    msg = await bot.send_message(
                        chat_id=int(channel_id),
                        text=message,
                        parse_mode='HTML',
                        reply_to_message_id=reply_to_message_id
                    )
                    if first_msg_id is None:
                        first_msg_id = msg.message_id
                    self.logger.info(
                        f"Telegram signal sent ({lang}): msg_id={msg.message_id}"
                    )

                    # ENTRY: pin with notification
                    if signal_type == 'ENTRY':
                        await self._pin_entry_signal(bot, int(channel_id), msg.message_id)
                    # EXIT: restore daily pinned message
                    elif signal_type == 'EXIT':
                        await self._restore_daily_pinned(bot, int(channel_id))

                except Exception as e:
                    self.logger.error(f"Telegram signal send failed ({lang}): {e}")

            return first_msg_id

        except Exception as e:
            self.logger.error(f"Telegram VIP push failed: {e}")
            return None

    async def _pin_entry_signal(self, bot, channel_id: int, message_id: int) -> None:
        """
        Pin the ENTRY signal message (with notification).

        Args:
            bot: Telegram Bot instance.
            channel_id: Channel ID.
            message_id: Message ID.
        """
        try:
            from src.telegram.utils.pinning import update_pinned_message
            await update_pinned_message(
                bot=bot,
                chat_id=channel_id,
                message_id=message_id,
                silent=False  # send notification (phone vibration)
            )
            self.logger.info(f"ENTRY signal pinned with notification: channel={channel_id}, msg={message_id}")
        except Exception as e:
            self.logger.warning(f"ENTRY signal pin failed: {e}")

    async def _restore_daily_pinned(self, bot, channel_id: int) -> None:
        """
        Restore the Daily Pulse pinned message (after EXIT).

        Args:
            bot: Telegram Bot instance.
            channel_id: Channel ID.
        """
        try:
            from src.notifications.priority.pinned_reply import get_pinned_reply_manager
            from src.telegram.utils.pinning import update_pinned_message

            manager = get_pinned_reply_manager()
            daily_msg_id = await manager.get_pinned_message_async(channel_id)

            if daily_msg_id:
                await update_pinned_message(
                    bot=bot,
                    chat_id=channel_id,
                    message_id=daily_msg_id,
                    silent=True  # silent restore, no notification
                )
                self.logger.info(f"Daily Pulse pinned restored: channel={channel_id}, msg={daily_msg_id}")
            else:
                self.logger.debug(f"No Daily Pulse pin record, skipping restore: channel={channel_id}")
        except Exception as e:
            self.logger.warning(f"Daily Pulse pin restore failed: {e}")

    def _format_telegram_signal(self, signal: Dict[str, Any]) -> str:
        """
        Format the Telegram signal message (Wall Street terminal style).

        Design principles:
        - Do not expose strategy name or entry reason
        - Show stop-loss as percentage only, not absolute price
        - Use <code> monospace for prices
        """
        signal_type = signal.get('type', 'ENTRY')
        symbol = signal.get('symbol', 'UNKNOWN')
        price = signal.get('price', 0)

        if signal_type == 'ENTRY':
            action = signal.get('action', 'LONG')
            stop_loss = signal.get('stop_loss', 0)
            take_profit = signal.get('take_profit', [])

            # Direction icon
            side_icon = "🟢" if action == "LONG" else "🔴"

            # Stop-loss percentage
            if price > 0 and stop_loss > 0:
                sl_pct = ((stop_loss - price) / price) * 100
            else:
                sl_pct = -5.0  # default

            lines = [
                f"⚡️ <b>Ignis Signal</b> | #{symbol}",
                "",
                f"{side_icon} <b>{action}</b>",
                "",
                "🎯 <b>Entry</b>",
                f"<code>${price:,.2f}</code>",
                "",
                "🛡 <b>Stop Loss</b>",
                f"<code>{sl_pct:+.1f}%</code>",
            ]

            # Take-profit targets
            if take_profit:
                lines.extend(["", "💰 <b>Take Profit</b>"])
                if isinstance(take_profit, list):
                    for i, tp in enumerate(take_profit[:2], 1):
                        if tp:
                            lines.append(f"{i}. <code>${tp:,.2f}</code>")
                elif take_profit:
                    lines.append(f"1. <code>${take_profit:,.2f}</code>")

            return "\n".join(lines)

        elif signal_type == 'EXIT':
            entry_price = signal.get('entry_price', 0)
            pnl_pct = signal.get('pnl_pct', 0)

            # P/L icon
            pnl_icon = "🟢" if pnl_pct >= 0 else "🔴"
            pnl_sign = "+" if pnl_pct >= 0 else ""

            return (
                f"⚡️ <b>Ignis Signal</b> | #{symbol}\n"
                f"\n"
                f"{pnl_icon} <b>EXIT</b>\n"
                f"\n"
                f"📥 <b>Entry</b>\n"
                f"<code>${entry_price:,.2f}</code>\n"
                f"\n"
                f"📤 <b>Exit</b>\n"
                f"<code>${price:,.2f}</code>\n"
                f"\n"
                f"💰 <b>P/L</b>\n"
                f"<code>{pnl_sign}{pnl_pct:.2f}%</code>"
            )

        else:
            return f"⚡️ <b>Ignis Signal</b> | #{symbol}"

    async def _send_personal_telegram(self, title: str, message: str) -> bool:
        """Send a personal Telegram message."""
        if not self._personal_chat_id:
            return False

        try:
            bot = get_bot()
            if not bot:
                return False

            full_message = f"<b>{title}</b>\n\n{message}"

            await bot.send_message(
                chat_id=self._personal_chat_id,
                text=full_message,
                parse_mode='HTML'
            )
            self.logger.info("Telegram personal push succeeded")
            return True

        except Exception as e:
            self.logger.error(f"Telegram personal push failed: {e}")
            return False

    # ========================================
    # Message formatting
    # ========================================

    def _format_title(self, signal: Dict[str, Any]) -> str:
        """Format notification title."""
        signal_type = signal.get('type', 'SIGNAL')
        symbol = signal.get('symbol', 'UNKNOWN')
        action = signal.get('action', '')

        prefix = self._env_prefix

        if signal_type == 'ENTRY':
            return f"{prefix} {action} {symbol}"
        elif signal_type == 'EXIT':
            pnl = signal.get('pnl_pct', 0)
            pnl_str = f"+{pnl:.1f}%" if pnl >= 0 else f"{pnl:.1f}%"
            return f"{prefix} EXIT {symbol} ({pnl_str})"
        else:
            return f"{prefix} {symbol} {action}"

    def _format_text_message(self, signal: Dict[str, Any]) -> str:
        """Format plain-text message (for email/WeChat)."""
        signal_type = signal.get('type', 'SIGNAL')
        symbol = signal.get('symbol', 'UNKNOWN')
        strategy = signal.get('strategy', '')
        price = signal.get('price', 0)
        reason = signal.get('reason', '')
        timestamp = signal.get('timestamp', datetime.now())

        if isinstance(timestamp, datetime):
            time_str = timestamp.strftime('%Y-%m-%d %H:%M')
        else:
            time_str = str(timestamp)

        lines = [
            f"## {time_str}",
            "",
            "### Summary",
            f"- Symbol: {symbol}USDT",
            f"- Strategy: {strategy}",
            f"- Price: ${price:.2f}",
        ]

        if signal_type == 'ENTRY':
            action = signal.get('action', 'LONG')
            stop_loss = signal.get('stop_loss')
            take_profit = signal.get('take_profit', [])

            lines.extend([
                f"- Direction: {action}",
                "",
                "### Trade Parameters",
            ])

            if stop_loss:
                lines.append(f"- Stop Loss: ${stop_loss:.2f}")
            if take_profit:
                if isinstance(take_profit, list):
                    for i, tp in enumerate(take_profit, 1):
                        if tp:
                            lines.append(f"- Take Profit {i}: ${tp:.2f}")
                else:
                    lines.append(f"- Take Profit: ${take_profit:.2f}")

        elif signal_type == 'EXIT':
            entry_price = signal.get('entry_price', 0)
            pnl_pct = signal.get('pnl_pct', 0)
            exit_reason = signal.get('reason', '')

            lines.extend([
                f"- Entry Price: ${entry_price:.2f}",
                f"- P/L: {pnl_pct:+.2f}%",
                "",
                "### Exit Reason",
                f"{exit_reason}",
            ])

        if reason and signal_type == 'ENTRY':
            lines.extend([
                "",
                "### Entry Reason",
                f"{reason}",
            ])

        return "\n".join(lines)

    # ========================================
    # Trailing stop notifications
    # ========================================

    async def send_trailing_stop_update(
        self,
        update_data: Dict[str, Any],
        current_price: float,
        reply_to_message_id: Optional[int] = None
    ) -> bool:
        """
        Send trailing stop update notification (Telegram Premium only).

        Args:
            update_data: Update details:
                - symbol: asset symbol
                - old_stop: old stop price
                - new_stop: new stop price
                - entry_price: entry price
                - strategy: strategy name
            current_price: Current market price.
            reply_to_message_id: Entry message ID to reply to.

        Returns:
            True if at least one channel succeeded.
        """
        self._ensure_initialized()

        symbol = update_data.get('symbol', 'UNKNOWN')
        old_stop = update_data.get('old_stop', 0)
        new_stop = update_data.get('new_stop', 0)
        entry_price = update_data.get('entry_price', 0)

        # Locked P/L based on new stop
        locked_pnl_pct = (new_stop - entry_price) / entry_price * 100 if entry_price > 0 else 0
        # Current floating P/L
        current_pnl_pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

        any_success = False
        for lang, channel_id in self._telegram_premium_channels.items():
            telegram_msg = self._format_trailing_stop_telegram(
                symbol, entry_price, old_stop, new_stop,
                current_price, locked_pnl_pct, current_pnl_pct,
                lang=lang
            )
            success = await self._send_telegram_to_channel(
                channel_id, telegram_msg, reply_to_message_id
            )
            if success:
                any_success = True
                self.logger.info(
                    f"[{symbol}] Trailing stop notification sent ({lang}, reply={reply_to_message_id})"
                )

        if not any_success:
            self.logger.warning(f"[{symbol}] Trailing stop notification failed")

        return any_success

    async def _send_telegram_to_channel(
        self,
        channel_id: str,
        message: str,
        reply_to_message_id: Optional[int] = None
    ) -> bool:
        """
        Send a Telegram message to a specific channel (HTML format).

        Args:
            channel_id: Channel ID.
            message: Message content.
            reply_to_message_id: Message ID to reply to.

        Returns:
            True on success.
        """
        try:
            if not channel_id:
                return False

            bot = get_bot()
            if not bot:
                return False

            await bot.send_message(
                chat_id=int(channel_id),
                text=message,
                parse_mode='HTML',
                reply_to_message_id=reply_to_message_id
            )
            return True

        except Exception as e:
            self.logger.error(f"Telegram message send failed: {e}")
            return False

    async def _send_telegram_message(
        self,
        message: str,
        reply_to_message_id: Optional[int] = None
    ) -> bool:
        """
        Send a Telegram message to the default Premium channel (HTML format).

        Deprecated: use _send_telegram_to_channel instead.

        Args:
            message: Message content.
            reply_to_message_id: Message ID to reply to.

        Returns:
            True on success.
        """
        if not self._telegram_premium_channels:
            return False

        default_channel = list(self._telegram_premium_channels.values())[0]
        return await self._send_telegram_to_channel(default_channel, message, reply_to_message_id)

    def _format_trailing_stop_telegram(
        self,
        symbol: str,
        entry_price: float,
        old_stop: float,
        new_stop: float,
        current_price: float,
        locked_pnl_pct: float,
        current_pnl_pct: float,
        lang: str = 'en'
    ) -> str:
        """
        Format the trailing stop Telegram message (Wall Street terminal style).

        Design: show stop as percentage only, not absolute price.

        Args:
            lang: Language ('zh' / 'en').
        """
        old_sl_pct = ((old_stop - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        new_sl_pct = ((new_stop - entry_price) / entry_price) * 100 if entry_price > 0 else 0

        title_updated = t('scanner.trailing_stop_updated', lang)
        label_adjustment = t('scanner.adjustment', lang)
        label_locked = t('scanner.locked_pl', lang)
        label_current = t('scanner.current_pl', lang)

        return (
            f"🛡 <b>Ignis Signal</b> | #{symbol}\n"
            f"\n"
            f"🔄 <b>{title_updated}</b>\n"
            f"\n"
            f"📊 <b>{label_adjustment}</b>\n"
            f"<code>{old_sl_pct:+.1f}%</code> → <code>{new_sl_pct:+.1f}%</code>\n"
            f"\n"
            f"🔒 <b>{label_locked}</b>\n"
            f"<code>{locked_pnl_pct:+.2f}%</code>\n"
            f"\n"
            f"📈 <b>{label_current}</b>\n"
            f"<code>{current_pnl_pct:+.2f}%</code>"
        )

    # ========================================
    # Status query
    # ========================================

    def get_status(self) -> Dict[str, Any]:
        """Return notification manager status."""
        self._ensure_initialized()

        return {
            'telegram_vip': self._telegram_sender.is_enabled() if self._telegram_sender else False,
            'telegram_personal': self._personal_telegram_enabled,
            'email': self._email_sender is not None,
            'wechat': bool(self._wechat_sender and self._wechat_sender.enabled),
        }

    async def test_all_channels(self) -> Dict[str, bool]:
        """
        Test all channels by sending a test message.

        Returns:
            Per-channel test results.
        """
        test_signal = {
            'type': 'ENTRY',
            'symbol': 'TEST',
            'strategy': 'swing-test',
            'price': 100000.0,
            'action': 'LONG',
            'stop_loss': 95000.0,
            'take_profit': [105000.0, 110000.0],
            'reason': 'Swing notification test',
            'technical_reason': 'Multi-period breakout confirmed, strong momentum',
            'timestamp': datetime.now(),
        }

        self.logger.info("Testing all notification channels...")
        return await self.send_signal(test_signal)
