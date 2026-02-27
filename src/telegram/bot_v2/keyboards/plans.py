"""
Plan selection keyboards.

2x2 grid layout with unified pricing engine and i18n support.

Layout:
  [ Basic $29.9/mo ]  [ Basic $299/yr ]
  [ Prem $39.9/mo ]   [ Prem $399/yr ]
  [      Trader Program - 30% OFF      ]
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.telegram.i18n import t
from src.telegram.pricing import PricingEngine

PLAN_CALLBACK_PREFIX = 'plan_'
TRADER_CALLBACK = 'trader_program'
CHECK_PAYMENT_CALLBACK = 'check_payment'
BACK_TO_PLANS_CALLBACK = 'back_to_plans'

PLAN_I18N_MAP = {
    'BASIC_M': 'btn_basic_m',
    'BASIC_Y': 'btn_basic_y',
    'PREMIUM_M': 'btn_prem_m',
    'PREMIUM_Y': 'btn_prem_y',
}

PLAN_LEVEL = {
    'BASIC_M': 1,
    'BASIC_Y': 1,
    'PREMIUM_M': 2,
    'PREMIUM_Y': 2,
}


def _format_price(price: Decimal) -> str:
    """Round price to nearest integer and format as '$N'."""
    rounded = int(price.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return f"${rounded}"


def _build_button_text(
    plan_code: str,
    price: Decimal,
    lang: str
) -> str:
    """Build button label from i18n template with price substituted."""
    i18n_key = PLAN_I18N_MAP.get(plan_code)
    if not i18n_key:
        return f"{plan_code} - ${price}"

    template = t(f'plans.{i18n_key}', lang)
    price_str = _format_price(price)
    text = template.replace('${price}', price_str)

    return text


def get_plans_keyboard(
    plans: List[Dict[str, Any]],
    telegram_id: Optional[int] = None,
    lang: str = 'en'
) -> InlineKeyboardMarkup:
    """
    Build plan selection keyboard (2x2 grid + Trader CTA row).

    Row 1: [ Basic Monthly ] [ Basic Yearly ]
    Row 2: [ Premium Monthly ] [ Premium Yearly ]
    Row 3: [ Trader Program CTA ]
    """
    engine = PricingEngine()
    builder = InlineKeyboardBuilder()

    plan_map = {p['plan_code']: p for p in plans}

    def add_button(code: str) -> bool:
        plan = plan_map.get(code)
        if not plan:
            return False

        original_price = Decimal(str(plan['price_usdt']))
        level = PLAN_LEVEL.get(code, 1)

        result = engine.calculate(original_price, level, telegram_id)

        text = _build_button_text(code, result.final_price, lang)
        builder.button(text=text, callback_data=f"{PLAN_CALLBACK_PREFIX}{code}")
        return True

    add_button('BASIC_M')
    add_button('BASIC_Y')
    add_button('PREMIUM_M')
    add_button('PREMIUM_Y')

    builder.adjust(2, 2)

    trader_text = t('plans.trader_cta', lang)
    builder.button(text=trader_text, callback_data=TRADER_CALLBACK)

    builder.adjust(2, 2, 1)

    return builder.as_markup()


def get_plan_callback_data(plan_code: str) -> str:
    """Return callback_data string for a plan code."""
    return f"{PLAN_CALLBACK_PREFIX}{plan_code}"


def parse_plan_callback(callback_data: str) -> str:
    """Extract plan code from callback_data. Returns empty string if invalid."""
    if callback_data and callback_data.startswith(PLAN_CALLBACK_PREFIX):
        return callback_data[len(PLAN_CALLBACK_PREFIX):]
    return ''


def get_plan_display_name(plan_code: str, lang: str = 'en') -> str:
    """Return the localized display name for a plan code."""
    key_map = {
        'BASIC_M': 'basic_monthly',
        'BASIC_Y': 'basic_yearly',
        'PREMIUM_M': 'premium_monthly',
        'PREMIUM_Y': 'premium_yearly',
    }
    i18n_key = key_map.get(plan_code)
    if i18n_key:
        return t(f'plans.{i18n_key}', lang)
    return plan_code


def get_payment_keyboard(order_id: str, lang: str = 'en') -> InlineKeyboardMarkup:
    """Build payment page keyboard (check status + back to plans)."""
    builder = InlineKeyboardBuilder()

    builder.button(
        text=t('payment.btn_check_status', lang),
        callback_data=f"{CHECK_PAYMENT_CALLBACK}:{order_id}"
    )
    builder.button(
        text=t('payment.btn_back_to_plans', lang),
        callback_data=BACK_TO_PLANS_CALLBACK
    )

    builder.adjust(1)
    return builder.as_markup()
