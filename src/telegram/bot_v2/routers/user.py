"""
User router.

Handles /start, /help, /status commands and menu button callbacks.
"""

from typing import Callable

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from src.core.structured_logger import get_logger

from ..keyboards.reply import get_main_menu_keyboard
from ..utils.async_wrapper import run_sync
from ..utils.formatting import escape_markdown, format_date, format_days_remaining

logger = get_logger(__name__)

router = Router(name="user")


@router.message(CommandStart())
async def cmd_start(message: Message, lang: str, t: Callable) -> None:
    """/start: smart entry — show VIP status or welcome for guests."""
    user = message.from_user
    telegram_id = user.id
    username = user.username or user.first_name or "User"

    logger.info(f"/start: telegram_id={telegram_id}, username={username}, lang={lang}")

    try:
        from ..utils.db_provider import get_member_service

        member_service = get_member_service()

        # First-time user: show language selection only (blocking)
        # Trial activation happens in the language selection callback
        existing_lang = await run_sync(member_service.get_language, telegram_id)
        if not existing_lang:
            from ..keyboards.inline import get_language_keyboard

            await message.answer(
                "Select language / 选择语言:",
                reply_markup=get_language_keyboard(lang),
            )
            return

        status = await run_sync(member_service.check_membership_valid, telegram_id)

        menu_keyboard = get_main_menu_keyboard(lang)

        if status["active"]:
            days = status.get("days_remaining", 0)
            membership_type = status.get("membership_type", "VIP")
            expire_date = status.get("expire_date")
            is_whitelist = status.get("is_whitelist", False)

            plan_display = membership_type
            if membership_type == "TRIAL_7D":
                plan_display = f"Basic {t('trial.plan_suffix')}"

            expire_str = format_date(expire_date) if expire_date else "N/A"
            days_str = format_days_remaining(days)

            if is_whitelist:
                expire_str = t("account.never")
                days_str = t("account.never")

            text = (
                f"{t('start.welcome_back', username=escape_markdown(username))}\n\n"
                f"*{t('account.plan')}:* {escape_markdown(plan_display)}\n"
                f"*{t('account.expires')}:* {expire_str}\n"
                f"*{t('start.days_left')}:* {days_str}\n\n"
                f"{t('start.use_menu')}"
            )

            await message.answer(
                text, parse_mode="MarkdownV2", reply_markup=menu_keyboard
            )
        else:
            text = (
                f"*{t('start.welcome_title')}*\n\n"
                f"{t('start.hi', username=escape_markdown(username))}\n\n"
                f"*{t('start.what_we_offer')}*\n"
                f"  {t('start.feature_scanner')}\n"
                f"  {t('start.feature_pulse')}\n"
                f"  {t('start.feature_signals')}\n\n"
                f"{t('start.get_started')}"
            )

            await message.answer(
                text, parse_mode="MarkdownV2", reply_markup=menu_keyboard
            )

    except Exception as e:
        logger.error(f"/start failed: {e}", exc_info=True)
        await message.answer(
            t("common.system_busy") + "\n" + t("common.contact_support")
        )


@router.message(Command("help"))
async def cmd_help(message: Message, lang: str, t: Callable) -> None:
    """/help: show command reference and payment instructions."""
    telegram_id = message.from_user.id
    logger.info(f"/help: telegram_id={telegram_id}, lang={lang}")

    text = (
        f"*{t('help.title')}*\n\n"
        f"*{t('help.commands_title')}*\n"
        f"  {t('help.cmd_start')}\n"
        f"  {t('help.cmd_subscribe')}\n"
        f"  {t('help.cmd_status')}\n"
        f"  {t('help.cmd_help')}\n\n"
        f"*{t('help.payment_title')}*\n"
        f"  {t('help.payment_method')}\n"
        f"  {t('help.payment_exact')}\n"
        f"  {t('help.payment_auto')}\n"
        f"  {t('help.payment_expires')}\n\n"
        f"*{t('help.support_title')}*\n"
        f"  {t('help.support_contact')}"
    )

    await message.answer(text, parse_mode="MarkdownV2")


@router.message(Command("status"))
async def cmd_status(message: Message, lang: str, t: Callable) -> None:
    """/status: show membership status details."""
    telegram_id = message.from_user.id
    logger.info(f"/status: telegram_id={telegram_id}, lang={lang}")

    try:
        from ..utils.db_provider import get_member_service

        status = await run_sync(
            get_member_service().check_membership_valid, telegram_id
        )

        if status["active"]:
            membership_type = status.get("membership_type", "VIP")
            expire_date = status.get("expire_date")
            days = status.get("days_remaining", 0)
            is_whitelist = status.get("is_whitelist", False)

            plan_display = membership_type
            if membership_type == "TRIAL_7D":
                plan_display = f"Basic {t('trial.plan_suffix')}"

            expire_str = format_date(expire_date) if expire_date else "N/A"
            days_str = format_days_remaining(days)

            if is_whitelist:
                expire_str = t("account.never")
                days_str = t("account.never")

            text = (
                f"*{t('status.title')}*\n\n"
                f"*{t('status.user_id')}:* `{telegram_id}`\n"
                f"*{t('status.plan')}:* {escape_markdown(plan_display)}\n"
                f"*{t('status.expires')}:* {expire_str}\n"
                f"*{t('status.days_left')}:* {days_str}\n\n"
                f"{t('status.renew_prompt')}"
            )
        else:
            text = (
                f"*{t('status.title')}*\n\n"
                f"*{t('status.user_id')}:* `{telegram_id}`\n"
                f"*{t('status.plan')}:* {t('status.none')}\n"
                f"*{t('account.status')}:* {t('status.not_subscribed')}\n\n"
                f"{t('status.get_started_prompt')}"
            )

        from ..keyboards.inline import get_subscribe_keyboard

        keyboard = get_subscribe_keyboard(lang)
        await message.answer(text, parse_mode="MarkdownV2", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"/status failed: {e}", exc_info=True)
        await message.answer(t("status.query_failed"))
