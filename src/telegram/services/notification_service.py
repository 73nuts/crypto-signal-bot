"""
User notification service.

Responsibilities:
1. Send renewal reminders (DMs)
2. Send system notifications (DMs)
3. Complements VipSignalSender (group broadcast vs DM)

Design:
- All methods are async, consistent with aiogram usage
- Failures are logged but not raised (notifications must not block the main flow)
- Lang parameter reserved for future i18n expansion
"""

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .member_service import RenewalReminder


class NotificationService:
    """User DM notification service."""

    PLAN_DISPLAY_NAMES = {
        "BASIC_M": "Basic Monthly",
        "BASIC_Y": "Basic Yearly",
        "PREMIUM_M": "Premium Monthly",
        "PREMIUM_Y": "Premium Yearly",
    }

    def __init__(self, bot: Bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)

    async def send_renewal_reminder(
        self,
        telegram_id: int,
        reminder_type: RenewalReminder,
        expire_date: datetime,
        plan_code: str,
        lang: str = "en",
    ) -> bool:
        """Send a renewal reminder DM. Returns True on success."""
        days_left = (expire_date.date() - datetime.now().date()).days
        if days_left < 0:
            days_left = 0

        message = self._format_renewal_message(
            plan_code=plan_code,
            days_left=days_left,
            expire_date=expire_date,
            reminder_type=reminder_type,
            lang=lang,
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Renew Now / 立即续费", callback_data="show_plans"
                    )
                ]
            ]
        )

        try:
            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            self.logger.info(
                f"Renewal reminder sent: telegram_id={telegram_id}, "
                f"type={reminder_type.value}, days_left={days_left}"
            )
            return True

        except TelegramAPIError as e:
            # User may have blocked the bot or deleted the conversation
            self.logger.warning(
                f"Renewal reminder failed: telegram_id={telegram_id}, error={e}"
            )
            return False

    def _format_renewal_message(
        self,
        plan_code: str,
        days_left: int,
        expire_date: datetime,
        reminder_type: RenewalReminder,
        lang: str = "en",
    ) -> str:
        """Format a renewal reminder message as HTML.

        Strings are intentionally bilingual (English + Chinese) to serve
        both language groups from a single message template.
        """
        plan_name = self.PLAN_DISPLAY_NAMES.get(plan_code, plan_code)
        expire_str = expire_date.strftime("%Y-%m-%d")

        if reminder_type == RenewalReminder.ALPHA_CLOSING:
            # Alpha window closing (T+25, 5 days remaining)
            days_remaining = 5
            title = "Alpha Pricing Expires Soon / Alpha定价即将失效"
            urgency = (
                f"Your Alpha pricing expires in <b>{days_remaining} days</b>!\n"
                f"您的Alpha定价将在<b>{days_remaining}天</b>后失效！\n\n"
                "Renew now to keep <b>50% OFF</b>.\n"
                "立即续费可保持<b>5折</b>优惠。"
            )
        elif reminder_type == RenewalReminder.T_ZERO:
            title = "Membership Expired Today / 会员今日到期"
            urgency = "Your membership expires <b>TODAY</b>!\n您的会员<b>今天</b>到期！"
        elif reminder_type == RenewalReminder.T_MINUS_1:
            title = "Membership Expiring Tomorrow / 会员明天到期"
            urgency = (
                "Your membership expires <b>TOMORROW</b>!\n您的会员<b>明天</b>到期！"
            )
        else:  # T_MINUS_3
            title = "Membership Expiring Soon / 会员即将到期"
            urgency = f"Your membership expires in <b>{days_left} days</b>.\n您的会员将在<b>{days_left}天</b>后到期。"

        message = f"""📅 <b>{title}</b>

{urgency}

📋 <b>Plan / 套餐:</b> {plan_name}
📆 <b>Expire Date / 到期日:</b> {expire_str}

Renew now to continue receiving signals.
立即续费以继续接收信号。

👇 Click below to renew / 点击下方续费"""

        return message

    async def send_expiry_notice(
        self, telegram_id: int, plan_code: str, lang: str = "en"
    ) -> bool:
        """Send a post-expiry notice prompting the user to renew. Returns True on success."""
        plan_name = self.PLAN_DISPLAY_NAMES.get(plan_code, plan_code)

        message = f"""⏰ <b>Membership Expired / 会员已过期</b>

Your <b>{plan_name}</b> subscription has expired.
您的<b>{plan_name}</b>会员已过期。

You will be removed from the signal group in 24 hours.
您将在24小时后被移出信号群。

Renew now to continue!
立即续费以继续使用！"""

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Renew Now / 立即续费", callback_data="show_plans"
                    )
                ]
            ]
        )

        try:
            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            self.logger.info(f"Expiry notice sent: telegram_id={telegram_id}")
            return True

        except TelegramAPIError as e:
            self.logger.warning(
                f"Expiry notice failed: telegram_id={telegram_id}, error={e}"
            )
            return False

    async def send_trial_expiry_reminder(
        self, telegram_id: int, lang: str = "en"
    ) -> bool:
        """Send Trial expiry reminder (Day 6). Returns True on success."""
        from src.telegram.i18n import t

        message = f"<b>{t('trial.expiring_tomorrow', lang)}</b>"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("payment.btn_subscribe_now", lang),
                        callback_data="show_plans",
                    )
                ]
            ]
        )

        try:
            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            self.logger.info(f"Trial expiry reminder sent: telegram_id={telegram_id}")
            return True

        except TelegramAPIError as e:
            self.logger.warning(
                f"Trial expiry reminder failed: telegram_id={telegram_id}, error={e}"
            )
            return False

    async def send_admin_alert(self, message: str) -> bool:
        """Send an alert message to the configured admin. Returns True on success."""
        from src.core.config import settings

        admin_id = getattr(settings, "TELEGRAM_ADMIN_ID", None)
        if not admin_id:
            self.logger.warning("TELEGRAM_ADMIN_ID not configured, skipping admin alert")
            return False

        try:
            await self.bot.send_message(
                chat_id=int(admin_id), text=message, parse_mode="Markdown"
            )
            return True
        except TelegramAPIError as e:
            self.logger.error(f"Admin alert failed: {e}")
            return False
