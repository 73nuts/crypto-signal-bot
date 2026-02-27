"""
Join request handler.

Handles users requesting to join a channel via invite link.
Checks membership status, plan access level, and language preference.
Non-members or plan/language mismatches are declined automatically.
"""

from typing import Optional, Tuple

from aiogram import Bot, Router
from aiogram.types import ChatJoinRequest

from src.core.config import settings
from src.core.structured_logger import get_logger

from ..utils.async_wrapper import run_sync

logger = get_logger(__name__)

router = Router(name="join_request")

# Reverse mapping: chat_id -> (level_key, lang)
_chat_id_to_key = None


def _get_chat_id_to_key() -> dict:
    """Build and cache chat_id to channel key mapping.

    Returns:
        {chat_id: (level_key, lang)}, e.g. {-1003200802449: ('PREMIUM', 'en')}
    """
    global _chat_id_to_key
    if _chat_id_to_key is None:
        _chat_id_to_key = {}

        channel_basic_zh = settings.TELEGRAM_CHANNEL_BASIC_ZH
        channel_basic_en = settings.TELEGRAM_CHANNEL_BASIC_EN
        channel_premium_zh = settings.TELEGRAM_CHANNEL_PREMIUM_ZH
        channel_premium_en = settings.TELEGRAM_CHANNEL_PREMIUM_EN

        if channel_basic_zh:
            _chat_id_to_key[int(channel_basic_zh)] = ("BASIC", "zh")
        if channel_basic_en:
            _chat_id_to_key[int(channel_basic_en)] = ("BASIC", "en")
        if channel_premium_zh:
            _chat_id_to_key[int(channel_premium_zh)] = ("PREMIUM", "zh")
        if channel_premium_en:
            _chat_id_to_key[int(channel_premium_en)] = ("PREMIUM", "en")

        logger.info(f"JoinRequest mapping loaded: {len(_chat_id_to_key)} channels")

    return _chat_id_to_key


def _get_channel_info(chat_id: int) -> Optional[Tuple[str, str]]:
    """Return (level_key, lang) for the given chat_id, or None if unknown."""
    return _get_chat_id_to_key().get(chat_id)


def _get_membership_info(user_id: int) -> Optional[dict]:
    """Sync: get user membership info."""
    from ..utils.db_provider import get_member_service

    return get_member_service().get_user_membership_info(user_id)


def _get_access_groups(plan_code: str) -> list:
    """Sync: get access groups for the given plan."""
    from ..utils.db_provider import get_membership_plan_dao

    return get_membership_plan_dao().get_access_groups_by_plan_code(plan_code)


def _get_user_language(user_id: int) -> str:
    """Sync: get user language preference — in-memory cache first, then DB."""
    from src.telegram.i18n import get_user_language

    from ..utils.db_provider import get_member_service

    cached_lang = get_user_language(user_id)
    if cached_lang:
        return cached_lang

    try:
        db_lang = get_member_service().get_language(user_id)
        if db_lang:
            return db_lang
    except Exception as e:
        logger.warning(f"Failed to get user language from DB: user_id={user_id}, error={e}")

    return "en"


@router.chat_join_request()
async def handle_join_request(request: ChatJoinRequest, bot: Bot) -> None:
    """Handle channel join request: verify membership, plan access, and language match."""
    user_id = request.from_user.id
    chat_id = request.chat.id
    chat_title = request.chat.title or "Unknown"
    username = request.from_user.username or "Unknown"

    logger.info(
        f"Join request: user_id={user_id}, username={username}, "
        f"chat_id={chat_id}, chat_title={chat_title}"
    )

    channel_info = _get_channel_info(chat_id)

    if not channel_info:
        logger.warning(f"Unknown channel ID: {chat_id}, declining")
        await request.decline()
        return

    level_key, channel_lang = channel_info
    channel_desc = f"{level_key}/{channel_lang}"

    membership = await run_sync(_get_membership_info, user_id)

    if not membership:
        logger.info(f"No membership record: user_id={user_id}, declining {channel_desc}")
        await request.decline()
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "Your join request was declined.\n\n"
                    "Please subscribe first: /subscribe"
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to send decline message: user_id={user_id}, error={e}")
        return

    status = membership.get("status")
    if status != "ACTIVE":
        logger.info(
            f"Membership not active: user_id={user_id}, status={status}, declining {channel_desc}"
        )
        await request.decline()
        return

    plan_code = membership.get("membership_type")
    access_groups = await run_sync(_get_access_groups, plan_code)

    if level_key not in access_groups:
        logger.info(
            f"Plan has no access: user_id={user_id}, plan={plan_code}, channel={channel_desc}, declining"
        )
        await request.decline()
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"Your plan ({plan_code}) doesn't include {level_key} access.\n\n"
                    "Please upgrade: /subscribe"
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to send access denied message: user_id={user_id}, error={e}")
        return

    user_lang = await run_sync(_get_user_language, user_id)
    if user_lang != channel_lang:
        logger.info(
            f"Language mismatch: user_id={user_id}, user_lang={user_lang}, channel_lang={channel_lang}, declining"
        )
        await request.decline()
        try:
            if user_lang == "zh":
                msg = (
                    "Your language is set to Chinese, but you're trying to join an English channel.\n\n"
                    "Please use /language to switch, or use the Chinese channel invite link you received."
                )
            else:
                msg = (
                    "Your language is set to English, but you're trying to join a Chinese channel.\n\n"
                    "Please use /language to switch, or use the English channel invite link you received."
                )
            await bot.send_message(chat_id=user_id, text=msg)
        except Exception as e:
            logger.warning(f"Failed to send language mismatch message: user_id={user_id}, error={e}")
        return

    is_trial = plan_code == "TRIAL_7D"

    await request.approve()

    if is_trial:
        logger.info(
            f"Trial user approved: user_id={user_id}, username={username}, channel={channel_desc}"
        )
    else:
        logger.info(
            f"Join approved: user_id={user_id}, username={username}, channel={channel_desc}, plan={plan_code}"
        )
