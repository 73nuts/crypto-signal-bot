"""
MarkdownV2 formatting utilities and Telegram helpers (aiogram 3.x).

All special characters in Telegram MarkdownV2 must be backslash-escaped:
_ * [ ] ( ) ~ ` > # + - = | { } . !

Reference: https://core.telegram.org/bots/api#markdownv2-style
"""
import re
from datetime import datetime
from decimal import Decimal
from typing import Union

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery


async def safe_answer_callback(callback: CallbackQuery) -> None:
    """Answer a callback query, silently ignoring TelegramBadRequest (e.g. query too old)."""
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

SPECIAL_CHARS = r'_*[]()~`>#+-=|{}.!'


def escape_markdown(text: str) -> str:
    """Escape all MarkdownV2 special characters in text."""
    if not text:
        return ''

    pattern = f'([{re.escape(SPECIAL_CHARS)}])'
    return re.sub(pattern, r'\\\1', str(text))


def format_amount(amount: Union[Decimal, float, str]) -> str:
    """Format a numeric amount for display: 2 decimal places, trailing zeros stripped, escaped."""
    if isinstance(amount, str):
        amount = Decimal(amount)
    elif isinstance(amount, float):
        amount = Decimal(str(amount))

    formatted = f"{amount:.2f}".rstrip('0').rstrip('.')
    return escape_markdown(formatted)


def format_address(address: str) -> str:
    """Wrap a wallet address in backticks (tap-to-copy; no escaping needed inside)."""
    return f"`{address}`"


def format_expire_time(expire_at: datetime) -> str:
    """Return a human-readable expiry description (e.g. '45 min', '2h 15min')."""
    now = datetime.now()
    delta = expire_at - now

    if delta.total_seconds() <= 0:
        return escape_markdown("Expired")

    minutes = int(delta.total_seconds() / 60)
    if minutes < 60:
        return escape_markdown(f"{minutes}min")

    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes > 0:
        return escape_markdown(f"{hours}h {remaining_minutes}min")
    return escape_markdown(f"{hours}h")


def format_date(dt: datetime) -> str:
    """Format a datetime as 'YYYY-MM-DD HH:MM' (MarkdownV2-escaped)."""
    return escape_markdown(dt.strftime('%Y-%m-%d %H:%M'))


def format_days_remaining(days: int) -> str:
    """Return human-readable days remaining. 99999 = permanent whitelist."""
    if days is None or days <= 0:
        return escape_markdown("Expired")
    elif days >= 99999:
        return escape_markdown("Permanent")
    elif days == 1:
        return escape_markdown("1 day")
    else:
        return escape_markdown(f"{days} days")
