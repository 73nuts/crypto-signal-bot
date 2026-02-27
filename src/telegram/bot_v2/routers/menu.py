"""
Persistent menu click handlers.

Handles text messages from the bottom ReplyKeyboard (3x2 grid):
  - Subscribe / Performance
  - My Account / Trader Pro
  - Language / Feedback
"""

from typing import Callable, Dict, List, Optional, Tuple

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.core.cache import get_cache
from src.core.config import settings
from src.core.structured_logger import get_logger

from ..keyboards.plans import get_plans_keyboard
from ..states.feedback import FeedbackStates
from ..utils.async_wrapper import run_sync
from ..utils.formatting import escape_markdown

logger = get_logger(__name__)

# Redis lock config (prevent duplicate clicks)
PERF_LOCK_PREFIX = "perf_render_lock:"
PERF_LOCK_TTL = 30  # seconds, enough time to generate the image

router = Router(name="menu")


# ========================================
# Menu button match patterns (bilingual + emoji)
# ========================================
# Pattern list includes Chinese aliases to match Chinese-locale keyboard buttons
MENU_PATTERNS = {
    'subscribe': ['Subscribe', '订阅', '订阅套餐'],
    'performance': ['Performance', '历史表现'],
    'account': ['My Account', '我的账户', '会员状态'],
    'trader_pro': ['Trader Pro', 'Trader计划'],
    'language': ['Language', '语言'],
    'feedback': ['Feedback', '反馈'],
}


# ========================================
# 2025 Swing performance data (Realistic Backtest Engine)
# ========================================
PERFORMANCE_DATA = {
    'trades': [
        {'date': '01/09', 'symbol': 'BTC', 'entry': 67077, 'exit': 91464, 'pnl': 36.36, 'hold_days': 85, 'result': 'win'},
        {'date': '01/27', 'symbol': 'SOL', 'entry': 262, 'exit': 228, 'pnl': -12.67, 'hold_days': 8, 'result': 'loss'},
        {'date': '05/17', 'symbol': 'BNB', 'entry': 667, 'exit': 635, 'pnl': -4.76, 'hold_days': 7, 'result': 'loss'},
        {'date': '06/05', 'symbol': 'BTC', 'entry': 96471, 'exit': 100627, 'pnl': 4.31, 'hold_days': 34, 'result': 'win'},
        {'date': '06/05', 'symbol': 'ETH', 'entry': 2207, 'exit': 2449, 'pnl': 10.96, 'hold_days': 27, 'result': 'win'},
        {'date': '06/13', 'symbol': 'ETH', 'entry': 2817, 'exit': 2551, 'pnl': -9.43, 'hold_days': 2, 'result': 'loss'},
        {'date': '06/20', 'symbol': 'SOL', 'entry': 139, 'exit': 139, 'pnl': 0.08, 'hold_days': 61, 'result': 'win'},
        {'date': '08/02', 'symbol': 'ETH', 'entry': 2952, 'exit': 3427, 'pnl': 16.09, 'hold_days': 22, 'result': 'win'},
        {'date': '08/22', 'symbol': 'BTC', 'entry': 116037, 'exit': 111738, 'pnl': -3.70, 'hold_days': 42, 'result': 'loss'},
        {'date': '08/24', 'symbol': 'SOL', 'entry': 164, 'exit': 211, 'pnl': 28.44, 'hold_days': 44, 'result': 'win'},
        {'date': '09/22', 'symbol': 'ETH', 'entry': 4011, 'exit': 4265, 'pnl': 6.34, 'hold_days': 44, 'result': 'win'},
        {'date': '10/10', 'symbol': 'BNB', 'entry': 709, 'exit': 932, 'pnl': 31.39, 'hold_days': 85, 'result': 'win'},
        {'date': '10/10', 'symbol': 'SOL', 'entry': 214, 'exit': 185, 'pnl': -13.29, 'hold_days': 42, 'result': 'loss'},
    ],
    'stats': {
        'total_trades': 13,
        'winners': 8,
        'losers': 5,
        'win_rate': 61.5,
        'avg_rr': 2.23,
        'max_drawdown': 10.6
    }
}


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


def _check_membership(telegram_id: int):
    """Sync: check membership status."""
    from ..utils.db_provider import get_member_service
    return get_member_service().check_membership_valid(telegram_id)


def _count_recent_feedback(telegram_id: int) -> int:
    """Sync: count feedback submissions in the last 24 hours."""
    from ..utils.db_provider import get_feedback_dao
    return get_feedback_dao().count_recent_feedback(telegram_id, hours=24)


def _get_performance_cache():
    """Sync: get performance cache service."""
    from src.telegram.services import get_performance_cache
    return get_performance_cache()


def _get_performance_with_meta() -> Tuple[Optional[List], Optional[Dict], int, int]:
    """
    Fetch performance data and metadata (used for cache invalidation).

    Returns:
        (trades_list, stats_dict, last_trade_id, trade_count)
    """
    try:
        from src.trading.position_manager import PositionManager

        mysql_config = settings.get_mysql_config()
        pm = PositionManager(
            host=mysql_config['host'],
            port=mysql_config['port'],
            password=mysql_config['password'],
            database=mysql_config['database']
        )

        trades = pm.get_closed_trades(year=2025, limit=50)
        if not trades:
            return None, None, 0, 0

        stats = pm.get_trade_stats(year=2025)

        last_trade_id = trades[0].get('id', 0) if trades else 0
        trade_count = stats.get('total_trades', 0)

        trades_list = []
        for t in trades:
            pnl_val = t.get('realized_pnl_percent') or 0
            hold_days = 0
            if t.get('opened_at') and t.get('closed_at'):
                delta = t['closed_at'] - t['opened_at']
                hold_days = max(delta.days, 1)
            trades_list.append({
                'date': t['closed_at'].strftime('%m/%d') if t.get('closed_at') else None,
                'symbol': t.get('symbol', '').replace('USDT', ''),
                'entry': float(t.get('entry_price') or 0),
                'exit': float(t.get('exit_price') or 0),
                'pnl': float(pnl_val),
                'hold_days': hold_days,
                'result': 'win' if float(pnl_val) > 0 else 'loss',
            })

        return trades_list, stats, last_trade_id, trade_count

    except Exception as e:
        logger.warning(f"Failed to get performance with meta: {e}")
        return None, None, 0, 0


def _generate_performance_card(trades_data, stats_data):
    """Sync: generate performance card image."""
    from src.telegram.utils.performance_card import generate_performance_card
    return generate_performance_card(trades_data, stats_data)


# ========================================
# Helper functions
# ========================================

def _get_all_menu_patterns() -> list:
    """Return all menu button text patterns (used for the message filter)."""
    all_patterns = []
    for patterns in MENU_PATTERNS.values():
        all_patterns.extend(patterns)
    return all_patterns


def _match_menu(text: str, menu_key: str) -> bool:
    """Check whether text matches the given menu key (ignores emoji prefix)."""
    patterns = MENU_PATTERNS.get(menu_key, [])
    for pattern in patterns:
        if pattern in text:
            return True
    return False


def _get_subscribe_keyboard(lang: str, t: Callable):
    """Build subscribe call-to-action inline keyboard."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t('perf.unlock_prompt').replace('\\', ''),
        callback_data="show_plans"
    )
    return builder.as_markup()


def _get_performance_caption(lang: str, t: Callable) -> str:
    """Build performance image caption."""
    return (
        f"*{t('perf.title')}*\n\n"
        f"{t('perf.unlock_prompt')}"
    )


# ========================================
# Menu handlers
# ========================================

ALL_MENU_PATTERNS = _get_all_menu_patterns()


@router.message(F.text.func(lambda t: any(p in t for p in ALL_MENU_PATTERNS)))
async def handle_menu_click(message: Message, lang: str, t: Callable, bot: Bot, state: FSMContext) -> None:
    """Route persistent menu button clicks via MENU_PATTERNS (multilingual + emoji)."""
    text = message.text
    telegram_id = message.from_user.id

    logger.info(f"Menu click: telegram_id={telegram_id}, text={text}")

    if _match_menu(text, 'subscribe'):
        await show_subscribe_menu(message, lang, t)
    elif _match_menu(text, 'performance'):
        await show_performance(message, lang, t, bot)
    elif _match_menu(text, 'account'):
        await show_account(message, lang, t)
    elif _match_menu(text, 'trader_pro'):
        await show_trader_pro(message, lang, t)
    elif _match_menu(text, 'language'):
        await show_language_menu(message, lang, t)
    elif _match_menu(text, 'feedback'):
        await show_feedback_prompt(message, lang, t, state)


async def show_subscribe_menu(message: Message, lang: str, t: Callable) -> None:
    """Show subscription plan selection."""
    telegram_id = message.from_user.id

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
        logger.error(f"Show subscribe failed: {e}", exc_info=True)
        await message.answer(t('common.system_busy'))


async def show_performance(message: Message, lang: str, t: Callable, bot: Bot) -> None:
    """Show historical performance page with event-driven image cache."""
    user_id = message.from_user.id
    lock_key = f"{PERF_LOCK_PREFIX}{user_id}"
    redis_cache = get_cache()

    if not await redis_cache.setnx(lock_key, "1", ttl=PERF_LOCK_TTL):
        return

    loading_msg = None

    try:
        loading_msg = await message.answer(t('perf.loading'))

        await bot.send_chat_action(
            chat_id=message.chat.id,
            action=ChatAction.UPLOAD_PHOTO
        )

        perf_cache = await run_sync(_get_performance_cache)

        trades_data, stats_data, last_trade_id, trade_count = await run_sync(_get_performance_with_meta)

        cached_file_id = perf_cache.get_cached_file_id()
        if cached_file_id and perf_cache.is_cache_valid(last_trade_id, trade_count):
            logger.info(f"Performance cache hit: file_id={cached_file_id[:20]}...")
            keyboard = _get_subscribe_keyboard(lang, t)
            await message.answer_photo(
                photo=cached_file_id,
                caption=_get_performance_caption(lang, t),
                parse_mode='MarkdownV2',
                reply_markup=keyboard
            )
            if loading_msg:
                try:
                    await loading_msg.delete()
                except Exception:
                    pass
            return

        logger.info("Performance cache miss, generating new image")

        if not trades_data:
            trades_data = PERFORMANCE_DATA['trades']
            stats_data = PERFORMANCE_DATA['stats']

        card_buffer = await run_sync(_generate_performance_card, trades_data, stats_data)

        keyboard = _get_subscribe_keyboard(lang, t)
        card_bytes = card_buffer.read()
        photo = BufferedInputFile(card_bytes, filename="performance.png")

        sent_message = await message.answer_photo(
            photo=photo,
            caption=_get_performance_caption(lang, t),
            parse_mode='MarkdownV2',
            reply_markup=keyboard
        )

        if sent_message.photo:
            new_file_id = sent_message.photo[-1].file_id
            perf_cache.update_cache(new_file_id, last_trade_id, trade_count)
            logger.info(f"Performance cache updated: file_id={new_file_id[:20]}...")

        if loading_msg:
            try:
                await loading_msg.delete()
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Show performance failed: {e}", exc_info=True)
        if loading_msg:
            try:
                await loading_msg.edit_text(t('perf.error'))
            except Exception:
                await _show_performance_fallback(message, lang, t)
        else:
            await _show_performance_fallback(message, lang, t)

    finally:
        await redis_cache.delete(lock_key)


async def _show_performance_fallback(message: Message, lang: str, t: Callable) -> None:
    """Fallback: show performance as text when image generation fails."""
    trades_data = PERFORMANCE_DATA['trades']
    stats_data = PERFORMANCE_DATA['stats']

    recent_trades = trades_data[:5]
    trade_lines = []
    for trade in recent_trades:
        pnl = trade.get('pnl', 0)
        pnl_str = f"+{pnl:.2f}%" if pnl > 0 else f"{pnl:.2f}%"
        symbol = trade.get('symbol', 'N/A')
        trade_lines.append(f"  {symbol}: {pnl_str}")

    trades_text = "\n".join(trade_lines) if trade_lines else t('perf.no_trades')

    win_rate = stats_data.get('win_rate', 0)
    total_trades = stats_data.get('total_trades', 0)

    text = (
        f"*{t('perf.title')}*\n\n"
        f"{escape_markdown(trades_text)}\n\n"
        f"*{t('perf.win_rate')}:* {win_rate}%\n"
        f"*{t('perf.total_trades')}:* {total_trades}\n\n"
        f"{t('perf.unlock_prompt')}"
    )

    keyboard = _get_subscribe_keyboard(lang, t)
    await message.answer(text, parse_mode='MarkdownV2', reply_markup=keyboard)


async def show_account(message: Message, lang: str, t: Callable) -> None:
    """Show account / membership status."""
    telegram_id = message.from_user.id

    try:
        status = await run_sync(_check_membership, telegram_id)

        if status['active']:
            membership_type = status.get('membership_type', 'VIP')
            expire_date = status.get('expire_date')
            days = status.get('days_remaining', 0)
            is_whitelist = status.get('is_whitelist', False)

            if is_whitelist:
                expire_str = t('account.lifetime')
                days_str = t('account.unlimited')
            else:
                expire_str = expire_date.strftime('%Y\\-%m\\-%d') if expire_date else "N/A"
                days_str = str(days)

            text = (
                f"*{t('account.title')}*\n\n"
                f"*{t('account.user_id')}:* `{telegram_id}`\n"
                f"*{t('account.plan')}:* {escape_markdown(membership_type)}\n"
                f"*{t('account.expires')}:* {expire_str}\n"
                f"*{t('account.days_left')}:* {days_str}\n\n"
                f"{t('account.renew_prompt')}"
            )
        else:
            text = (
                f"*{t('account.title')}*\n\n"
                f"*{t('account.user_id')}:* `{telegram_id}`\n"
                f"*{t('account.plan')}:* {t('account.free')}\n"
                f"*{t('account.status')}:* {t('account.not_subscribed')}\n\n"
                f"{t('account.unlock_prompt')}"
            )

        await message.answer(text, parse_mode='MarkdownV2')

    except Exception as e:
        logger.error(f"Show account failed: {e}", exc_info=True)
        await message.answer(t('account.query_failed'))


async def show_trader_pro(message: Message, lang: str, t: Callable) -> None:
    """Show Trader Program entry — prompt user to use /trader."""
    text = (
        f"*{t('trader.title')}*\n\n"
        f"{t('trader.description')}\n\n"
        f"{t('trader.use_command')}: /trader"
    )
    await message.answer(text, parse_mode='MarkdownV2')


async def show_language_menu(message: Message, lang: str, t: Callable) -> None:
    """Show language selection menu."""
    from ..keyboards.inline import get_language_keyboard

    text = f"*{t('language.select')}*"
    keyboard = get_language_keyboard(lang)
    await message.answer(text, parse_mode='MarkdownV2', reply_markup=keyboard)


async def show_feedback_prompt(message: Message, lang: str, t: Callable, state: FSMContext) -> None:
    """Show feedback prompt and enter FSM waiting state."""
    telegram_id = message.from_user.id

    try:
        membership = await run_sync(_check_membership, telegram_id)
        is_premium = membership['active'] and membership.get('level', 0) >= 2

        # Rate limits
        RATE_LIMIT_BASIC = 2
        RATE_LIMIT_PREMIUM = 5

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

        text = (
            f"*{t('feedback.title')}*\n\n"
            f"{t('feedback.instruction')}\n\n"
            f"{t('feedback.remaining', remaining=remaining)}\n\n"
            f"{t('feedback.cancel_hint')}"
        )
        await message.answer(text, parse_mode='MarkdownV2')

    except Exception as e:
        logger.error(f"Feedback prompt failed: {e}", exc_info=True)
        await message.answer(t('common.system_busy'))
