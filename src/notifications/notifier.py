"""
Notifier module.
Unified management of email, WeChat, and Telegram VIP push notifications.
"""

import logging
from src.notifications.email_sender import EmailSender
from src.notifications.wechat_sender import WeChatSender
from src.notifications.telegram_sender import TelegramSender


class Notifier:
    """Notifier - unified management of email, WeChat, and Telegram push."""

    def __init__(self, email_config):
        """
        Args:
            email_config: Email config dict
        """
        self.logger = logging.getLogger(__name__)
        self.email_sender = EmailSender(email_config)
        self.wechat_sender = WeChatSender()
        self.telegram_sender = TelegramSender()

    def send(self, subject, message, brief_message=None, signal_data=None):
        """Send notification (email + WeChat + Telegram VIP).

        Args:
            subject: Notification subject
            message: Notification body (detailed, for email and WeChat)
            brief_message: Simplified content (optional, kept for compatibility)
            signal_data: Raw signal dict (optional, for Telegram VIP push)

        Returns:
            bool: Whether at least one channel succeeded
        """
        email_sent = self.email_sender.send(subject, message)
        wechat_sent = self.wechat_sender.send(subject, message)

        telegram_sent = False
        if signal_data and isinstance(signal_data, dict):
            telegram_sent = self.telegram_sender.send_signal(signal_data)

        if email_sent or wechat_sent or telegram_sent:
            self.logger.info("Notification sent")
            return True
        else:
            self.logger.error("All notification channels failed")
            return False

    def send_email_wechat_only(self, subject, message):
        """Send notification via email and WeChat only.

        Used for internal system alerts (SR updates, realtime alerts, etc.).

        Args:
            subject: Notification subject
            message: Notification body

        Returns:
            bool: Whether at least one channel succeeded
        """
        email_sent = self.email_sender.send(subject, message)
        wechat_sent = self.wechat_sender.send(subject, message)

        if email_sent or wechat_sent:
            self.logger.info("Notification sent (email + WeChat only)")
            return True
        else:
            self.logger.error("Email + WeChat notification failed")
            return False

    def send_tp_sl_notification(
        self,
        symbol: str,
        event_type: str,
        price: float,
        pnl: float,
        pnl_percent: float,
        signal_id: int = None,
        position_id: int = None,
        signal_type: str = 'SWING'
    ) -> bool:
        """
        Send take-profit/stop-loss notification.

        Sends to Telegram only, supports replying to the original signal message.

        Args:
            symbol: Asset symbol
            event_type: Event type 'TP1' | 'TP2' | 'SL'
            price: Trigger price
            pnl: Realized PnL (USD)
            pnl_percent: Realized PnL percentage
            signal_id: Original signal ID (for reply)
            position_id: Position ID
            signal_type: Signal type 'SWING'

        Returns:
            bool: Whether send succeeded
        """
        return self.telegram_sender.send_tp_sl_notification(
            symbol=symbol,
            event_type=event_type,
            price=price,
            pnl=pnl,
            pnl_percent=pnl_percent,
            signal_id=signal_id,
            position_id=position_id,
            signal_type=signal_type
        )
