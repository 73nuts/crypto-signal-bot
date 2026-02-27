"""
Subscription router.

Handles /subscribe command and plan selection callbacks.
Flow: select plan -> generate order -> show payment info (address + QR code).
"""

from typing import Callable

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from src.core.config import settings
from src.core.structured_logger import get_logger

from ..keyboards.plans import (
    BACK_TO_PLANS_CALLBACK,
    CHECK_PAYMENT_CALLBACK,
    PLAN_CALLBACK_PREFIX,
    TRADER_CALLBACK,
    get_payment_keyboard,
    get_plan_display_name,
    get_plans_keyboard,
    parse_plan_callback,
)
from ..utils.async_wrapper import run_sync
from ..utils.formatting import escape_markdown, format_address, format_expire_time, safe_answer_callback

logger = get_logger(__name__)

router = Router(name="subscription")


# ========================================
# Sync DAO wrapper functions
# ========================================

def _get_all_plans():
    """Sync: get all enabled plans."""
    from ..utils.db_provider import get_membership_plan_dao
    return get_membership_plan_dao().get_all_enabled_plans()


def _get_alpha_remaining():
    """Sync: get alpha phase remaining slots."""
    from src.telegram.pricing import PricingEngine
    engine = PricingEngine()
    return engine.get_alpha_remaining()


def _create_order(telegram_id: int, plan_code: str, username: str = None):
    """Sync: create payment order."""
    from src.telegram.order_generator import OrderGenerator
    order_generator = OrderGenerator()
    return order_generator.create_order(
        telegram_id=telegram_id,
        plan_code=plan_code,
        telegram_username=username
    )


def _generate_qr(address: str):
    """Sync: generate payment QR code."""
    from src.telegram.utils.qr_generator import generate_payment_qr
    return generate_payment_qr(address)


def _get_order_by_id(order_id: str):
    """Sync: get order by ID."""
    from ..utils.db_provider import get_order_dao
    return get_order_dao().get_order_by_id(order_id)


# ========================================
# Command and callback handlers
# ========================================

@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, lang: str, t: Callable) -> None:
    """/subscribe: show plan selection."""
    telegram_id = message.from_user.id
    logger.info(f"/subscribe: telegram_id={telegram_id}, lang={lang}")

    try:
        plans = await run_sync(_get_all_plans)

        if not plans:
            await message.answer(t('subscribe.no_plans'))
            return

        remaining = await run_sync(_get_alpha_remaining)
        alpha_limit = settings.ALPHA_LIMIT

        text = (
            f"*{t('subscribe.title')}*\n\n"
            f"*{t('subscribe.basic_title')}* \\- {t('subscribe.basic_subtitle')}\n"
            f"  {t('subscribe.basic_feature1')}\n"
            f"  {t('subscribe.basic_feature2')}\n\n"
            f"*{t('subscribe.premium_title')}* \\- {t('subscribe.premium_subtitle')}\n"
            f"  {t('subscribe.premium_feature1')}\n"
            f"  {t('subscribe.premium_feature2')}\n"
            f"  {t('subscribe.premium_feature3')}\n\n"
            f"*{t('subscribe.early_bird').format(remaining=remaining, limit=alpha_limit)}*\n"
            f"{t('subscribe.premium_offer').format(days=settings.ALPHA_BONUS_DAYS)}"
        )

        keyboard = get_plans_keyboard(plans, telegram_id=telegram_id, lang=lang)
        await message.answer(text, parse_mode='MarkdownV2', reply_markup=keyboard)

    except Exception as e:
        logger.error(f"/subscribe failed: {e}", exc_info=True)
        await message.answer(t('common.system_busy'))


@router.callback_query(F.data.startswith(PLAN_CALLBACK_PREFIX))
async def plan_callback(callback: CallbackQuery, lang: str, t: Callable, bot: Bot) -> None:
    """Handle plan selection: create order, generate QR, send payment info."""
    await safe_answer_callback(callback)

    telegram_id = callback.from_user.id
    username = callback.from_user.username
    callback_data = callback.data

    plan_code = parse_plan_callback(callback_data)
    if not plan_code:
        logger.warning(f"Invalid plan callback: {callback_data}")
        await callback.message.edit_text(t('checkout.invalid_plan'))
        return

    logger.info(f"Plan selected: telegram_id={telegram_id}, plan={plan_code}, lang={lang}")

    await callback.message.edit_text(t('checkout.generating'))

    try:
        order_info = await run_sync(_create_order, telegram_id, plan_code, username)

        order_id = order_info['order_id']
        payment_address = order_info['payment_address']
        amount = order_info['amount']
        expire_at = order_info['expire_at']
        plan_name = get_plan_display_name(plan_code, lang)
        is_existing = order_info.get('is_existing', False)

        qr_buffer = await run_sync(_generate_qr, payment_address)

        expire_str = format_expire_time(expire_at)

        caption = (
            f"*{t('checkout.usdt_payment')}*\n\n"
            f"*{escape_markdown(plan_name)}*\n"
            f"`{amount}` USDT\n\n"
            f"*{t('checkout.address_label')}*\n"
            f"{format_address(payment_address)}\n"
            f"{t('checkout.tap_to_copy')}\n\n"
            f"{t('checkout.expires')}: {expire_str}\n"
            f"{t('checkout.order_id')}: `{escape_markdown(order_id)}`\n\n"
            f"{t('checkout.auto_activate')}"
        )

        if is_existing:
            caption = f"*{t('checkout.existing_order')}*\n\n" + caption

        keyboard = get_payment_keyboard(order_id, lang)

        qr_bytes = qr_buffer.read()
        photo = BufferedInputFile(qr_bytes, filename="payment_qr.png")

        await bot.send_photo(
            chat_id=callback.message.chat.id,
            photo=photo,
            caption=caption,
            parse_mode='MarkdownV2',
            reply_markup=keyboard
        )

        logger.info(f"Order created: order_id={order_id}, address={payment_address}")

        try:
            await callback.message.delete()
        except Exception:
            pass

    except ValueError as e:
        logger.warning(f"Order creation failed (business error): {e}")
        await callback.message.edit_text(
            f"{escape_markdown(str(e))}",
            parse_mode='MarkdownV2'
        )
    except RuntimeError as e:
        logger.error(f"Order creation failed (system error): {e}")
        await callback.message.edit_text(t('checkout.channel_unavailable'))
    except Exception as e:
        logger.error(f"Order creation failed (unknown error): {e}", exc_info=True)
        await callback.message.edit_text(t('common.system_busy'))


@router.callback_query(F.data.startswith(f"{CHECK_PAYMENT_CALLBACK}:"))
async def check_payment_callback(callback: CallbackQuery, lang: str, t: Callable) -> None:
    """Handle 'check payment status' callback — query order and respond."""
    await safe_answer_callback(callback)

    telegram_id = callback.from_user.id
    callback_data = callback.data

    order_id = callback_data.split(':', 1)[1]
    logger.info(f"Check payment: telegram_id={telegram_id}, order_id={order_id}")

    try:
        order = await run_sync(_get_order_by_id, order_id)

        if not order:
            await callback.answer(t('checkout.invalid_plan'), show_alert=True)
            return

        status = order['status']

        if status == 'CONFIRMED':
            await callback.answer(t('checkout.confirmed'), show_alert=True)
        elif status == 'PENDING':
            await callback.answer(t('checkout.pending'), show_alert=True)
        elif status == 'EXPIRED':
            await callback.answer(t('checkout.expired'), show_alert=True)
        else:
            await callback.answer(
                f"{t('checkout.status_error')}: {status}",
                show_alert=True
            )

    except Exception as e:
        logger.error(f"Check payment failed: {e}", exc_info=True)
        await callback.answer(t('checkout.query_failed'), show_alert=True)


@router.callback_query(F.data == BACK_TO_PLANS_CALLBACK)
async def back_to_plans_callback(callback: CallbackQuery, lang: str, t: Callable) -> None:
    """Handle 'back to plans' callback — delete QR image and resend plan menu."""
    await safe_answer_callback(callback)

    telegram_id = callback.from_user.id
    logger.info(f"Back to plans: telegram_id={telegram_id}")

    try:
        plans = await run_sync(_get_all_plans)

        if not plans:
            await callback.message.reply_text(t('subscribe.no_plans'))
            return

        remaining = await run_sync(_get_alpha_remaining)
        alpha_limit = settings.ALPHA_LIMIT

        text = (
            f"*{t('subscribe.title')}*\n\n"
            f"*{t('subscribe.basic_title')}* \\- {t('subscribe.basic_subtitle')}\n"
            f"  {t('subscribe.basic_feature1')}\n"
            f"  {t('subscribe.basic_feature2')}\n\n"
            f"*{t('subscribe.premium_title')}* \\- {t('subscribe.premium_subtitle')}\n"
            f"  {t('subscribe.premium_feature1')}\n"
            f"  {t('subscribe.premium_feature2')}\n"
            f"  {t('subscribe.premium_feature3')}\n\n"
            f"*{t('subscribe.early_bird').format(remaining=remaining, limit=alpha_limit)}*\n"
            f"{t('subscribe.premium_offer').format(days=settings.ALPHA_BONUS_DAYS)}"
        )

        keyboard = get_plans_keyboard(plans, telegram_id=telegram_id, lang=lang)

        await callback.message.answer(text, parse_mode='MarkdownV2', reply_markup=keyboard)

        try:
            await callback.message.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Back to plans failed: {e}", exc_info=True)
        await callback.message.reply_text(t('common.system_busy'))


@router.callback_query(F.data == "show_plans")
async def show_plans_callback(callback: CallbackQuery, lang: str, t: Callable) -> None:
    """Handle 'show plans' callback (from /start welcome message)."""
    await back_to_plans_callback(callback, lang, t)


@router.callback_query(F.data == TRADER_CALLBACK)
async def trader_program_callback(callback: CallbackQuery, lang: str, t: Callable) -> None:
    """Handle Trader Program button — redirect user to /trader command."""
    await safe_answer_callback(callback)

    text = (
        f"*{t('trader.title')}*\n\n"
        f"{t('trader.description')}\n\n"
        f"{t('trader.use_command')}: /trader"
    )

    await callback.message.edit_text(text, parse_mode='MarkdownV2')
