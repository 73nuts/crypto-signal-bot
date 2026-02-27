"""
Sector mapping admin command handler.

Commands:
- /sector_check: check new coins and generate classification proposals

Callbacks:
- sector_approve_{id}: approve update proposal
- sector_reject_{id}: reject update proposal
"""

from typing import Callable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.core.structured_logger import get_logger

from ..filters.admin import AdminFilter, is_admin_sync
from ..utils.async_wrapper import run_sync
from ..utils.formatting import safe_answer_callback

logger = get_logger(__name__)

router = Router(name="sector_admin")


def _escape_markdown_v2(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    special_chars = [
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]
    for char in special_chars:
        text = text.replace(char, f"\\{char}")
    return text


def _check_sectors():
    """Sync: run sector check."""
    from src.scanner.sector_updater import get_sector_updater

    updater = get_sector_updater()
    return updater.check_and_notify()


def _format_proposal(proposal: dict) -> str:
    """Sync: format proposal message."""
    from src.scanner.sector_updater import get_sector_updater

    updater = get_sector_updater()
    return updater.format_proposal_message(proposal)


def _apply_proposal(proposal_id: str):
    """Sync: apply proposal."""
    from src.scanner.sector_updater import get_sector_updater

    updater = get_sector_updater()
    return updater.apply_proposal(proposal_id)


def _reject_proposal(proposal_id: str):
    """Sync: reject proposal."""
    from src.scanner.sector_updater import get_sector_updater

    updater = get_sector_updater()
    return updater.reject_proposal(proposal_id)


@router.message(Command("sector_check"), AdminFilter())
async def cmd_sector_check(message: Message, lang: str, t: Callable) -> None:
    """/sector_check: check new coins in Top 200 and generate AI classification proposals."""
    logger.info(f"/sector_check: admin_id={message.from_user.id}")

    status_msg = await message.answer(
        "Checking for new coins...\nThis may take 10-30 seconds."
    )

    try:
        proposal = await run_sync(_check_sectors)

        if not proposal:
            await status_msg.edit_text(
                "No new coins found in Top 200.\nAll coins are already classified."
            )
            return

        message_text = await run_sync(_format_proposal, proposal)

        builder = InlineKeyboardBuilder()
        builder.button(text="Approve", callback_data=f"sector_approve_{proposal['id']}")
        builder.button(text="Reject", callback_data=f"sector_reject_{proposal['id']}")
        builder.adjust(2)

        await status_msg.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=builder.as_markup()
        )

    except Exception as e:
        logger.error(f"/sector_check failed: {e}", exc_info=True)
        await status_msg.edit_text(f"Check failed: {e}")


@router.callback_query(F.data.startswith("sector_approve_"))
async def sector_approve_callback(callback: CallbackQuery) -> None:
    """Handle sector_approve_{proposal_id} callback."""
    is_admin = await run_sync(is_admin_sync, callback.from_user.id)
    if not is_admin:
        await callback.answer("Unauthorized", show_alert=True)
        return

    await safe_answer_callback(callback)

    callback_data = callback.data
    proposal_id = callback_data.replace("sector_approve_", "")

    logger.info(
        f"Sector approve: proposal_id={proposal_id}, admin={callback.from_user.id}"
    )

    try:
        success, result_message = await run_sync(_apply_proposal, proposal_id)

        if success:
            escaped_msg = _escape_markdown_v2(result_message)
            await callback.message.edit_text(
                f"*Approved*\n\n{escaped_msg}\n\n"
                f"sector\\_mapping\\.json has been updated\\.",
                parse_mode="MarkdownV2",
            )
        else:
            escaped_msg = _escape_markdown_v2(result_message)
            await callback.message.edit_text(
                f"*Failed*\n\n{escaped_msg}", parse_mode="MarkdownV2"
            )

    except Exception as e:
        logger.error(f"Sector approve failed: {e}", exc_info=True)
        await callback.message.edit_text(f"Error: {e}")


@router.callback_query(F.data.startswith("sector_reject_"))
async def sector_reject_callback(callback: CallbackQuery) -> None:
    """Handle sector_reject_{proposal_id} callback."""
    is_admin = await run_sync(is_admin_sync, callback.from_user.id)
    if not is_admin:
        await callback.answer("Unauthorized", show_alert=True)
        return

    await safe_answer_callback(callback)

    callback_data = callback.data
    proposal_id = callback_data.replace("sector_reject_", "")

    logger.info(
        f"Sector reject: proposal_id={proposal_id}, admin={callback.from_user.id}"
    )

    try:
        success, result_message = await run_sync(_reject_proposal, proposal_id)

        if success:
            await callback.message.edit_text(
                "*Rejected*\n\nThe update proposal has been discarded\\.",
                parse_mode="MarkdownV2",
            )
        else:
            escaped_msg = _escape_markdown_v2(result_message)
            await callback.message.edit_text(
                f"*Failed*\n\n{escaped_msg}", parse_mode="MarkdownV2"
            )

    except Exception as e:
        logger.error(f"Sector reject failed: {e}", exc_info=True)
        await callback.message.edit_text(f"Error: {e}")
