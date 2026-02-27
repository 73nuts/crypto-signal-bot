"""
Trader Program router (aiogram 3.x).

Flow: /trader -> [Submit UID] -> user sends UID -> admin approval buttons.
Smart approval: new UID = manual review; verified UID + same user = auto-approve;
verified UID + other user = reject (occupied).
"""

from typing import Callable

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.core.config import settings
from src.core.structured_logger import get_logger

from ..filters.admin import AdminFilter
from ..states.trader import TraderStates
from ..utils.async_wrapper import run_sync
from ..utils.formatting import escape_markdown, safe_answer_callback

logger = get_logger(__name__)

router = Router(name="trader")

# Callback prefixes
SUBMIT_UID_CALLBACK = "trader_submit_uid"
APPROVE_CALLBACK = "trader_approve"
REJECT_CALLBACK = "trader_reject"
CANCEL_CALLBACK = "trader_cancel"


# ========================================
# Sync DAO wrapper functions
# ========================================

def _is_trader_verified(telegram_id: int) -> bool:
    """Sync: check if user is a verified Trader."""
    from ..utils.db_provider import get_member_service
    return get_member_service().is_trader_verified(telegram_id)


def _get_member_info(telegram_id: int) -> dict:
    """Sync: get member info."""
    from ..utils.db_provider import get_member_service
    return get_member_service().repository.find_by_telegram_id(telegram_id)


def _is_uid_available(uid: str, telegram_id: int) -> tuple:
    """Sync: check UID availability."""
    from ..utils.db_provider import get_member_service
    return get_member_service().is_uid_available(uid, telegram_id)


def _submit_binance_uid(telegram_id: int, uid: str) -> bool:
    """Sync: submit Binance UID."""
    from ..utils.db_provider import get_member_service
    return get_member_service().submit_binance_uid(telegram_id, uid)


def _approve_trader(telegram_id: int) -> bool:
    """Sync: approve trader application."""
    from ..utils.db_provider import get_member_service
    return get_member_service().approve_trader(telegram_id)


def _add_verified_uid(uid: str, telegram_id: int, verified_by: str) -> None:
    """Sync: add UID to verified pool."""
    from ..utils.db_provider import get_member_service
    get_member_service().add_verified_uid(uid, telegram_id, verified_by=verified_by)


def _reject_trader(telegram_id: int) -> bool:
    """Sync: reject trader application."""
    from ..utils.db_provider import get_member_service
    return get_member_service().reject_trader(telegram_id)


def _get_user_language(telegram_id: int) -> str:
    """Sync: get user language preference."""
    from ..utils.db_provider import get_member_service
    return get_member_service().get_language(telegram_id) or 'en'


def _get_all_admins() -> list:
    """Sync: get all admin telegram IDs."""
    from ..utils.db_provider import get_db
    result = get_db().execute_query(
        "SELECT telegram_id FROM memberships WHERE is_admin = 1"
    )
    return result or []


# ========================================
# User commands
# ========================================

@router.message(Command("trader"))
async def cmd_trader(message: Message, state: FSMContext, lang: str, t: Callable) -> None:
    """/trader: show Trader Program description and 30% discount offer."""
    telegram_id = message.from_user.id
    logger.info(f"/trader: telegram_id={telegram_id}, lang={lang}")

    try:
        is_verified = await run_sync(_is_trader_verified, telegram_id)
        if is_verified:
            await message.answer(
                f"*{t('trader.title')}*\n\n"
                f"{t('trader.already_verified')}\n\n"
                f"{t('trader.see_prices')}",
                parse_mode='MarkdownV2'
            )
            return

        existing = await run_sync(_get_member_info, telegram_id)
        if existing and existing.get('binance_uid') and not existing.get('is_referral_verified'):
            uid = existing.get('binance_uid')
            await message.answer(
                f"*{t('trader.title')}*\n\n"
                f"{t('trader.pending_review')}\n\n"
                f"{t('trader.submitted_uid')}: `{escape_markdown(uid)}`",
                parse_mode='MarkdownV2'
            )
            return

        referral_url = getattr(settings, 'BINANCE_REFERRAL_URL', 'https://www.binance.com/register')
        referral_url_escaped = escape_markdown(referral_url)

        message_text = (
            f"*{t('trader.title')}*\n\n"
            f"*{t('trader.how_it_works')}*\n"
            f"{t('trader.step1')}\n"
            f"{t('trader.step2')}\n"
            f"{t('trader.step3')}\n\n"
            f"*{t('trader.reg_link')}*\n{referral_url_escaped}\n\n"
            f"*{t('trader.already_registered')}*\n"
            f"{t('trader.submit_below')}"
        )

        builder = InlineKeyboardBuilder()
        builder.button(text=t('trader.submit_uid_btn'), callback_data=SUBMIT_UID_CALLBACK)
        builder.button(text=t('trader.cancel_btn'), callback_data=CANCEL_CALLBACK)
        builder.adjust(1)

        await message.answer(
            message_text,
            parse_mode='MarkdownV2',
            reply_markup=builder.as_markup(),
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.error(f"/trader failed: {e}", exc_info=True)
        await message.answer(t('common.system_busy'))


@router.callback_query(F.data == SUBMIT_UID_CALLBACK)
async def submit_uid_callback(callback: CallbackQuery, state: FSMContext, lang: str, t: Callable) -> None:
    """Handle [Submit UID] button — enter FSM waiting-for-UID state."""
    await safe_answer_callback(callback)

    telegram_id = callback.from_user.id
    logger.info(f"Trader submit UID started: telegram_id={telegram_id}")

    await state.set_state(TraderStates.waiting_for_uid)

    await callback.message.edit_text(
        f"*{t('trader.submit_title')}*\n\n"
        f"{t('trader.send_uid')}\n\n"
        f"*{t('trader.how_to_find')}*\n"
        f"{t('trader.find_instructions')}\n\n"
        f"{t('trader.type_cancel')}",
        parse_mode='MarkdownV2'
    )


@router.callback_query(F.data == CANCEL_CALLBACK)
async def cancel_callback(callback: CallbackQuery, state: FSMContext, lang: str, t: Callable) -> None:
    """Handle cancel button."""
    await safe_answer_callback(callback)
    await state.clear()
    await callback.message.edit_text(t('trader.cancelled'), parse_mode='MarkdownV2')


@router.message(TraderStates.waiting_for_uid, Command("cancel"))
async def cancel_command(message: Message, state: FSMContext, lang: str, t: Callable) -> None:
    """Handle /cancel command."""
    await state.clear()
    await message.answer(t('trader.cancelled'), parse_mode='MarkdownV2')


@router.message(TraderStates.waiting_for_uid, F.text)
async def receive_uid(message: Message, state: FSMContext, lang: str, t: Callable, bot: Bot) -> None:
    """Receive user-submitted Binance UID and route to manual or auto approval."""
    telegram_id = message.from_user.id
    username = message.from_user.username or "N/A"
    uid_text = message.text.strip()

    logger.info(f"Trader UID received: telegram_id={telegram_id}, uid={uid_text}")

    # Validate UID format (8-10 digits)
    if not uid_text.isdigit() or len(uid_text) < 8 or len(uid_text) > 10:
        await message.answer(
            f"{t('trader.invalid_format')}\n\n{t('trader.type_cancel')}",
            parse_mode='MarkdownV2'
        )
        return  # keep FSM state

    try:
        available, reason = await run_sync(_is_uid_available, uid_text, telegram_id)

        if not available:
            # UID occupied by another user
            await state.clear()
            await message.answer(
                t('trader.uid_occupied'),
                parse_mode='MarkdownV2'
            )
            logger.warning(f"UID occupied: uid={uid_text}, attempted by telegram_id={telegram_id}")
            return

        if reason == 'auto':
            # Verified UID — auto-approve returning user
            await run_sync(_submit_binance_uid, telegram_id, uid_text)
            await run_sync(_approve_trader, telegram_id)

            await state.clear()
            await message.answer(
                f"*{t('trader.auto_approved_title')}*\n\n"
                f"{t('trader.your_uid')}: `{escape_markdown(uid_text)}`\n\n"
                f"{t('trader.auto_approved_msg')}",
                parse_mode='MarkdownV2'
            )
            logger.info(f"Trader auto-approved (returning user): telegram_id={telegram_id}, uid={uid_text}")
            return

        # New UID — manual approval flow
        success = await run_sync(_submit_binance_uid, telegram_id, uid_text)
        if not success:
            await state.clear()
            await message.answer(
                t('trader.save_failed'),
                parse_mode='MarkdownV2'
            )
            return

        await _notify_admin_for_approval(
            bot=bot,
            telegram_id=telegram_id,
            username=username,
            binance_uid=uid_text
        )

        await state.clear()
        await message.answer(
            f"*{t('trader.submitted_title')}*\n\n"
            f"{t('trader.your_uid')}: `{escape_markdown(uid_text)}`\n\n"
            f"{t('trader.review_notice')}\n"
            f"{t('trader.review_time')}",
            parse_mode='MarkdownV2'
        )

        logger.info(f"Trader UID submitted (new): telegram_id={telegram_id}, uid={uid_text}")

    except Exception as e:
        logger.error(f"Trader UID submission failed: {e}", exc_info=True)
        await state.clear()
        await message.answer(t('common.system_busy'))


async def _notify_admin_for_approval(
    bot: Bot,
    telegram_id: int,
    username: str,
    binance_uid: str
) -> None:
    """Send approval request with inline buttons to all admins."""
    admins = await run_sync(_get_all_admins)

    if not admins:
        logger.warning("No admin found for Trader approval notification")
        return

    message_text = (
        f"<b>Trader Program - New Submission</b>\n\n"
        f"<b>User:</b> @{username} (<code>{telegram_id}</code>)\n"
        f"<b>Binance UID:</b> <code>{binance_uid}</code>\n\n"
        f"Please verify and approve/reject:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="Approve", callback_data=f"{APPROVE_CALLBACK}:{telegram_id}")
    builder.button(text="Reject", callback_data=f"{REJECT_CALLBACK}:{telegram_id}")
    builder.adjust(2)

    for admin in admins:
        admin_id = admin['telegram_id']
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=message_text,
                parse_mode='HTML',
                reply_markup=builder.as_markup()
            )
            logger.info(f"Trader approval notification sent to admin: {admin_id}")
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

    try:
        from src.notifications.wechat_sender import WeChatSender
        wechat = WeChatSender()
        if wechat.enabled:
            wechat_msg = (
                f"Trader application\n\n"
                f"User: @{username} ({telegram_id})\n"
                f"UID: {binance_uid}\n\n"
                f"Please approve in Telegram"
            )
            wechat.send("Ignis Trader Application", wechat_msg)
            logger.info("Trader approval WeChat notification sent")
    except Exception as e:
        logger.warning(f"Failed to send WeChat notification: {e}")


# ========================================
# Admin approval callbacks
# ========================================

@router.callback_query(F.data.startswith(f"{APPROVE_CALLBACK}:"), AdminFilter())
async def approve_callback(callback: CallbackQuery, bot: Bot) -> None:
    """Handle admin approve — update DB and notify user."""
    await safe_answer_callback(callback)

    admin_id = callback.from_user.id

    callback_data = callback.data
    if ':' not in callback_data:
        await callback.message.edit_text("Invalid callback data")
        return

    target_user_id = int(callback_data.split(':', 1)[1])
    logger.info(f"Trader approval: admin={admin_id}, user={target_user_id}")

    try:
        user_lang = await run_sync(_get_user_language, target_user_id)
        user_info = await run_sync(_get_member_info, target_user_id)
        binance_uid = user_info.get('binance_uid') if user_info else None

        success = await run_sync(_approve_trader, target_user_id)
        if not success:
            await callback.message.edit_text(
                f"Failed to approve user {target_user_id}. User may not exist."
            )
            return

        if binance_uid:
            await run_sync(_add_verified_uid, binance_uid, target_user_id, 'admin')

        await callback.message.edit_text(
            f"<b>Approved</b>\n\n"
            f"User <code>{target_user_id}</code> is now a verified Trader.\n"
            f"UID: <code>{binance_uid or 'N/A'}</code>\n"
            f"Approved by: {admin_id}",
            parse_mode='HTML'
        )

        from src.telegram.i18n import t as translate
        try:
            await bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"*{translate('trader.approved_title', user_lang)}*\n\n"
                    f"{translate('trader.approved_msg', user_lang)}\n\n"
                    f"{translate('trader.discount_info', user_lang)}\n"
                    f"{translate('trader.see_prices', user_lang)}"
                ),
                parse_mode='MarkdownV2'
            )
        except Exception as e:
            logger.warning(f"Failed to notify user {target_user_id}: {e}")

        logger.info(f"Trader approved: user={target_user_id}, by_admin={admin_id}")

    except Exception as e:
        logger.error(f"Trader approval failed: {e}", exc_info=True)
        await callback.message.edit_text(f"Error: {e}")


@router.callback_query(F.data.startswith(f"{REJECT_CALLBACK}:"), AdminFilter())
async def reject_callback(callback: CallbackQuery, bot: Bot) -> None:
    """Handle admin reject — update DB and notify user."""
    await safe_answer_callback(callback)

    admin_id = callback.from_user.id

    callback_data = callback.data
    if ':' not in callback_data:
        await callback.message.edit_text("Invalid callback data")
        return

    target_user_id = int(callback_data.split(':', 1)[1])
    logger.info(f"Trader rejection: admin={admin_id}, user={target_user_id}")

    try:
        user_lang = await run_sync(_get_user_language, target_user_id)

        success = await run_sync(_reject_trader, target_user_id)
        if not success:
            await callback.message.edit_text(
                f"Failed to reject user {target_user_id}. User may not exist."
            )
            return

        await callback.message.edit_text(
            f"<b>Rejected</b>\n\n"
            f"User <code>{target_user_id}</code> application rejected.\n"
            f"Rejected by: {admin_id}",
            parse_mode='HTML'
        )

        from src.telegram.i18n import t as translate
        try:
            await bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"*{translate('trader.rejected_title', user_lang)}*\n\n"
                    f"{translate('trader.rejected_msg', user_lang)}\n\n"
                    f"{translate('trader.rejected_hint', user_lang)}\n"
                    f"{translate('trader.try_again', user_lang)}"
                ),
                parse_mode='MarkdownV2'
            )
        except Exception as e:
            logger.warning(f"Failed to notify user {target_user_id}: {e}")

        logger.info(f"Trader rejected: user={target_user_id}, by_admin={admin_id}")

    except Exception as e:
        logger.error(f"Trader rejection failed: {e}", exc_info=True)
        await callback.message.edit_text(f"Error: {e}")
