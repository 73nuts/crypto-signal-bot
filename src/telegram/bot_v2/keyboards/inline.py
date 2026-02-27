"""
InlineKeyboard builders.

Covers: action confirmation, subscribe entry, language selection.
Plan and payment keyboards are in plans.py.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_confirm_keyboard() -> InlineKeyboardMarkup:
    """Build confirm/cancel keyboard."""
    builder = InlineKeyboardBuilder()

    builder.button(text="Confirm", callback_data="confirm_feedback")
    builder.button(text="Cancel", callback_data="cancel_feedback")

    builder.adjust(2)
    return builder.as_markup()


def get_subscribe_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Build subscribe entry keyboard (shown to non-members in /status)."""
    from src.telegram.i18n import t

    builder = InlineKeyboardBuilder()
    builder.button(
        text=t("payment.btn_subscribe_now", lang), callback_data="show_plans"
    )
    builder.adjust(1)
    return builder.as_markup()


def get_language_keyboard(current_lang: str = "en") -> InlineKeyboardMarkup:
    """Build language selection keyboard with checkmark on current language."""
    builder = InlineKeyboardBuilder()

    languages = [
        ("en", "English"),
        ("zh", "中文"),
    ]

    for code, name in languages:
        display = f"{name} ✓" if code == current_lang else name
        builder.button(text=display, callback_data=f"set_lang:{code}")

    builder.adjust(2)
    return builder.as_markup()
