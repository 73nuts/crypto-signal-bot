"""
Feedback router.

Handles /feedback (FSM) and admin /reply command.
Rate limited: Basic 2/24h, Premium 5/24h.
Notifications sent via Telegram (Lisa) and email.
"""

from typing import Callable

from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from ..states.feedback import FeedbackStates
from ..filters.admin import AdminFilter
from ..utils.async_wrapper import run_sync
from src.core.config import settings
from src.core.structured_logger import get_logger

logger = get_logger(__name__)

router = Router(name="feedback")

# Rate limits (per 24 hours)
RATE_LIMIT_BASIC = 2
RATE_LIMIT_PREMIUM = 5


# ========================================
# Sync DAO wrapper functions
# ========================================

def _count_recent_feedback(telegram_id: int) -> int:
    """Sync: count feedback submissions in the last 24 hours."""
    from ..utils.db_provider import get_feedback_dao
    return get_feedback_dao().count_recent_feedback(telegram_id, hours=24)


def _check_membership(telegram_id: int) -> dict:
    """Sync: check membership status."""
    from ..utils.db_provider import get_member_service
    return get_member_service().check_membership_valid(telegram_id)


def _create_feedback(telegram_id: int, username: str, content: str) -> int:
    """Sync: create feedback record."""
    from ..utils.db_provider import get_feedback_dao
    return get_feedback_dao().create_feedback(
        telegram_id=telegram_id,
        username=username,
        content=content
    )


def _get_feedback_by_id(feedback_id: int) -> dict:
    """Sync: get feedback by ID."""
    from ..utils.db_provider import get_feedback_dao
    return get_feedback_dao().get_feedback_by_id(feedback_id)


def _mark_as_replied(feedback_id: int, reply_content: str, admin_id: int):
    """Sync: mark feedback as replied."""
    from ..utils.db_provider import get_feedback_dao
    return get_feedback_dao().mark_as_replied(feedback_id, reply_content, admin_id)


def _get_pending_feedbacks(limit: int = 20):
    """Sync: get pending (unreplied) feedbacks."""
    from ..utils.db_provider import get_feedback_dao
    return get_feedback_dao().get_pending_feedbacks(limit=limit)


def _get_user_language(telegram_id: int) -> str:
    """Sync: get user language preference."""
    from ..utils.db_provider import get_member_service
    return get_member_service().get_language(telegram_id) or 'en'


# ========================================
# User feedback flow
# ========================================

@router.message(Command("feedback"))
async def cmd_feedback(message: Message, state: FSMContext, lang: str, t: Callable) -> None:
    """/feedback: start feedback FSM flow."""
    telegram_id = message.from_user.id
    logger.info(f"/feedback: telegram_id={telegram_id}")

    try:
        membership = await run_sync(_check_membership, telegram_id)
        is_premium = membership['active'] and membership.get('level') == 2

        limit = RATE_LIMIT_PREMIUM if is_premium else RATE_LIMIT_BASIC
        recent_count = await run_sync(_count_recent_feedback, telegram_id)

        if recent_count >= limit:
            await message.answer(
                t('feedback.rate_limited', limit=limit),
                parse_mode='MarkdownV2'
            )
            return

        remaining = limit - recent_count

        await state.set_state(FeedbackStates.waiting_for_content)

        await message.answer(
            f"*{t('feedback.title')}*\n\n"
            f"{t('feedback.instruction')}\n\n"
            f"{t('feedback.remaining', remaining=remaining)}\n\n"
            f"{t('feedback.cancel_hint')}",
            parse_mode='MarkdownV2'
        )

    except Exception as e:
        logger.error(f"/feedback failed: {e}", exc_info=True)
        await message.answer(t('common.system_busy'))


@router.message(FeedbackStates.waiting_for_content, Command("cancel"))
async def cancel_feedback(message: Message, state: FSMContext, lang: str, t: Callable) -> None:
    """Cancel the feedback FSM flow."""
    await state.clear()
    await message.answer(t('feedback.cancelled'), parse_mode='MarkdownV2')


@router.message(FeedbackStates.waiting_for_content, F.text)
async def receive_feedback(message: Message, state: FSMContext, lang: str, t: Callable, bot: Bot) -> None:
    """Save feedback and send dual-channel notification."""
    telegram_id = message.from_user.id
    username = message.from_user.username or "N/A"
    feedback_text = message.text.strip()

    logger.info(f"Feedback received: telegram_id={telegram_id}, len={len(feedback_text)}")

    if len(feedback_text) < 5:
        await message.answer(t('feedback.too_short'), parse_mode='MarkdownV2')
        return  # keep FSM state

    if len(feedback_text) > 2000:
        await message.answer(t('feedback.too_long'), parse_mode='MarkdownV2')
        return  # keep FSM state

    try:
        feedback_id = await run_sync(_create_feedback, telegram_id, username, feedback_text)

        membership = await run_sync(_check_membership, telegram_id)
        member_status = "Premium" if membership['active'] and membership.get('level') == 2 else "Basic/Free"

        await _notify_feedback(
            bot=bot,
            feedback_id=feedback_id,
            telegram_id=telegram_id,
            username=username,
            content=feedback_text,
            member_status=member_status
        )

        await state.clear()

        await message.answer(
            f"*{t('feedback.submitted_title')}*\n\n"
            f"{t('feedback.submitted_msg')}\n\n"
            f"{t('feedback.ticket_id')}: `#{feedback_id}`",
            parse_mode='MarkdownV2'
        )

        logger.info(f"Feedback saved: id={feedback_id}, user={telegram_id}")

    except Exception as e:
        logger.error(f"Feedback submission failed: {e}", exc_info=True)
        await state.clear()
        await message.answer(t('common.system_busy'))


async def _notify_feedback(
    bot: Bot,
    feedback_id: int,
    telegram_id: int,
    username: str,
    content: str,
    member_status: str
) -> None:
    """Send dual-channel notification: Lisa's Telegram + Gmail."""
    lisa_telegram_id = getattr(settings, 'LISA_TELEGRAM_ID', None)
    if lisa_telegram_id:
        try:
            message_text = (
                f"<b>New Feedback #{feedback_id}</b>\n\n"
                f"<b>User:</b> @{username} (<code>{telegram_id}</code>)\n"
                f"<b>Status:</b> {member_status}\n\n"
                f"<b>Content:</b>\n{content}\n\n"
                f"Reply with: <code>/reply {feedback_id} your_message</code>"
            )

            await bot.send_message(
                chat_id=int(lisa_telegram_id),
                text=message_text,
                parse_mode='HTML'
            )
            logger.info(f"Feedback notification sent to Lisa: #{feedback_id}")

        except Exception as e:
            logger.error(f"Failed to notify Lisa via Telegram: {e}")

    await _send_feedback_email(
        feedback_id=feedback_id,
        telegram_id=telegram_id,
        username=username,
        content=content,
        member_status=member_status
    )


async def _send_feedback_email(
    feedback_id: int,
    telegram_id: int,
    username: str,
    content: str,
    member_status: str
) -> None:
    """Send feedback notification email."""
    try:
        from src.notifications.email_sender import EmailSender

        email_enabled = getattr(settings, 'EMAIL_ENABLED', False)
        if not email_enabled:
            logger.debug("Email notification disabled")
            return

        email_config = settings.get_email_config()
        sender = EmailSender(email_config)
        subject = f"[Ignis Feedback #{feedback_id}] New feedback from @{username}"
        body = f"""
New User Feedback

Ticket ID: #{feedback_id}
User: @{username} ({telegram_id})
Member Status: {member_status}

Content:
{content}

---
Ignis Quant Bot
"""
        success = sender.send(subject, body)
        if success:
            logger.info(f"Feedback email sent: #{feedback_id}")
        else:
            logger.warning(f"Feedback email send returned False: #{feedback_id}")

    except Exception as e:
        logger.error(f"Failed to send feedback email: {e}")


# ========================================
# Admin commands
# ========================================

@router.message(Command("reply"), AdminFilter())
async def cmd_reply(message: Message, lang: str, t: Callable, bot: Bot) -> None:
    """Admin /reply <feedback_id> <message>: reply to a user feedback."""
    admin_id = message.from_user.id

    text = message.text.strip()
    parts = text.split(maxsplit=2)

    if len(parts) < 3:
        await message.answer(
            "Usage: /reply <feedback_id> <message>\n"
            "Example: /reply 42 Thank you for your feedback!"
        )
        return

    try:
        feedback_id = int(parts[1])
    except ValueError:
        await message.answer("Invalid feedback_id. Must be a number.")
        return

    reply_message = parts[2]

    try:
        feedback = await run_sync(_get_feedback_by_id, feedback_id)
        if not feedback:
            await message.answer(f"Feedback #{feedback_id} not found.")
            return

        if feedback['replied']:
            await message.answer(
                f"Feedback #{feedback_id} already replied.\n"
                f"Previous reply: {feedback['reply_content']}"
            )
            return

        target_user_id = feedback['telegram_id']
        user_lang = await run_sync(_get_user_language, target_user_id)

        await run_sync(_mark_as_replied, feedback_id, reply_message, admin_id)

        from src.telegram.i18n import t as translate
        try:
            await bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"*{translate('feedback.reply_title', user_lang)}*\n\n"
                    f"{translate('feedback.ticket_id', user_lang)}: `#{feedback_id}`\n\n"
                    f"{translate('feedback.reply_from_team', user_lang)}\n\n"
                    f"{reply_message}"
                ),
                parse_mode='MarkdownV2'
            )
            logger.info(f"Reply sent: feedback={feedback_id}, user={target_user_id}")

        except Exception as e:
            logger.error(f"Failed to send reply to user: {e}")
            await message.answer(
                f"Reply saved but failed to send to user {target_user_id}: {e}"
            )
            return

        await message.answer(
            f"Reply sent to user {target_user_id}.\n"
            f"Feedback #{feedback_id} marked as replied."
        )

    except Exception as e:
        logger.error(f"/reply failed: {e}", exc_info=True)
        await message.answer(f"Error: {e}")


@router.message(Command("pending_feedbacks"), AdminFilter())
async def cmd_pending_feedbacks(message: Message) -> None:
    """Admin /pending_feedbacks: list unreplied feedbacks."""
    try:
        pending = await run_sync(_get_pending_feedbacks, 20)

        if not pending:
            await message.answer("No pending feedbacks.")
            return

        lines = ["<b>Pending Feedbacks</b>\n"]
        for fb in pending:
            created = fb['created_at'].strftime('%m/%d %H:%M')
            content_preview = fb['content'][:50] + '...' if len(fb['content']) > 50 else fb['content']
            lines.append(
                f"#{fb['id']} | @{fb['username'] or 'N/A'} | {created}\n"
                f"  {content_preview}\n"
            )

        lines.append("\nReply with: /reply <id> <message>")

        await message.answer('\n'.join(lines), parse_mode='HTML')

    except Exception as e:
        logger.error(f"/pending_feedbacks failed: {e}", exc_info=True)
        await message.answer(f"Error: {e}")
