"""
Language switch router (aiogram 3.x).

Provides /language command. On language change, updates:
1. In-memory cache
2. Database
3. ReplyKeyboard (bottom 6 buttons)
4. Command menu (hamburger menu)
"""

from typing import Callable

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.structured_logger import get_logger
from src.telegram.i18n import SUPPORTED_LANGUAGES, set_user_language

from ..middlewares.auth import invalidate_auth_cache
from ..utils.async_wrapper import run_sync
from ..utils.bot_commands import set_user_commands

logger = get_logger(__name__)

router = Router(name="language")

LANGUAGE_NAMES = {
    "en": "English",
    "zh": "中文",
}


def get_language_keyboard() -> InlineKeyboardMarkup:
    """Build language selection inline keyboard."""
    buttons = []
    for lang_code in SUPPORTED_LANGUAGES:
        display_name = LANGUAGE_NAMES.get(lang_code, lang_code)
        buttons.append(
            InlineKeyboardButton(
                text=display_name, callback_data=f"set_lang:{lang_code}"
            )
        )

    return InlineKeyboardMarkup(inline_keyboard=[buttons])


@router.message(Command("language"))
async def cmd_language(message: Message, lang: str, t: Callable) -> None:
    """/language: show language selection menu."""
    lang_name = LANGUAGE_NAMES.get(lang, lang)
    keyboard = get_language_keyboard()

    text = (
        f"<b>{t('language.title')}</b>\n\n"
        f"{t('language.current', language=lang_name)}\n\n"
        f"{t('language.select')}"
    )

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data.startswith("set_lang:"))
async def handle_set_language(
    callback: CallbackQuery, bot: Bot, lang: str, t: Callable
) -> None:
    """Handle language selection callback. Activates trial for first-time users."""
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    telegram_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.first_name
    new_lang = callback.data.split(":")[1]

    if new_lang not in SUPPORTED_LANGUAGES:
        return

    set_user_language(telegram_id, new_lang)

    from ..utils.db_provider import get_member_service

    member_service = get_member_service()

    try:
        await run_sync(member_service.update_language, telegram_id, new_lang)
    except Exception as e:
        logger.warning(f"Failed to save language preference: telegram_id={telegram_id}, error={e}")

    try:
        await set_user_commands(bot, telegram_id, new_lang)
    except Exception as e:
        logger.warning(f"Failed to update command menu: telegram_id={telegram_id}, error={e}")

    from src.telegram.i18n import t as translate_func

    def new_t(key, **kw):
        return translate_func(key, new_lang, **kw)

    try:
        is_trial_eligible = await run_sync(member_service.is_trial_eligible, telegram_id)
    except Exception as e:
        logger.warning(f"Trial eligibility check failed: telegram_id={telegram_id}, error={e}")
        is_trial_eligible = False

    if is_trial_eligible:
        try:
            trial_success = await run_sync(
                member_service.activate_trial, telegram_id, username
            )
        except Exception as e:
            logger.error(f"Trial activation error: telegram_id={telegram_id}, error={e}")
            trial_success = False

        if trial_success:
            invalidate_auth_cache(telegram_id)
            trial_text = (
                f"<b>{new_t('trial.activated')}</b>\n\n"
                f"{new_t('trial.what_you_get')}\n"
                f"  {new_t('trial.feature_scanner')}\n"
                f"  {new_t('trial.feature_pulse')}\n"
            )
            await callback.message.edit_text(trial_text, parse_mode="HTML")

            from src.telegram.access_controller import AccessController

            access_controller = AccessController(bot)
            await access_controller.send_invites(
                user_id=telegram_id,
                plan_code="TRIAL_7D",
                lang=new_lang,
                username=username,
            )

            from ..keyboards.reply import get_main_menu_keyboard

            new_keyboard = get_main_menu_keyboard(new_lang)
            await bot.send_message(
                chat_id=telegram_id,
                text=new_t("start.use_menu"),
                reply_markup=new_keyboard,
            )

            logger.info(f"Trial activated: telegram_id={telegram_id}, lang={new_lang}")
            return

    lang_name = LANGUAGE_NAMES.get(new_lang, new_lang)
    text = (
        f"<b>{new_t('language.title')}</b>\n\n"
        f"{new_t('language.changed', language=lang_name)}"
    )
    await callback.message.edit_text(text, parse_mode="HTML")

    from ..keyboards.reply import get_main_menu_keyboard

    new_keyboard = get_main_menu_keyboard(new_lang)
    await bot.send_message(
        chat_id=telegram_id, text=new_t("start.use_menu"), reply_markup=new_keyboard
    )

    logger.info(f"Language changed: telegram_id={telegram_id}, lang={new_lang}")
