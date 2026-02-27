"""
ReplyKeyboard builders.

Covers: main menu (6-button persistent bar), language selection.
"""
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from src.telegram.i18n import t


def get_main_menu_keyboard(lang: str = 'en') -> ReplyKeyboardMarkup:
    """
    Build the persistent main menu keyboard (3x2 grid).

    Layout:
    +-----------------+-----------------+
    | Subscribe       | Performance     |
    +-----------------+-----------------+
    | My Account      | Trader Pro      |
    +-----------------+-----------------+
    | Language        | Feedback        |
    +-----------------+-----------------+
    """
    keyboard = [
        [
            KeyboardButton(text=t('menu.subscribe', lang)),
            KeyboardButton(text=t('menu.performance', lang))
        ],
        [
            KeyboardButton(text=t('menu.my_account', lang)),
            KeyboardButton(text=t('menu.trader_pro', lang))
        ],
        [
            KeyboardButton(text=t('menu.language', lang)),
            KeyboardButton(text=t('menu.feedback', lang))
        ]
    ]

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )


def get_language_keyboard() -> ReplyKeyboardMarkup:
    """Build language selection reply keyboard (fallback; InlineKeyboard is preferred)."""
    builder = ReplyKeyboardBuilder()

    builder.button(text="English")
    builder.button(text="Chinese")
    builder.row()
    builder.button(text="Back")

    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)
