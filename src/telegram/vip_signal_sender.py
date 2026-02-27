"""
VIP signal sender.

Responsibilities:
1. Format signals into Telegram HTML (language-separated, v5.0).
2. Route to all PREMIUM channels, one message per language.
3. Two-layer message architecture: main signal card + analysis reply.
4. Deduplication (hash-based, 60s window).
5. Push audit log (signal_push_history).
6. Anti-spam: muted delivery for same-symbol signals within 60s.
7. Pin management: new signal replaces pinned message in each channel.
"""

import hashlib
import logging
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from src.core.config import settings

from .database.base import DatabaseManager
from .i18n import t
from .utils import update_pinned_message


class VipSignalSender:
    """VIP signal sender (v5.0: language-separated channels)."""

    REQUIRED_LEVEL = 'PREMIUM'

    TYPE_LABELS = {
        'SWING': 'Swing',
    }

    ACTION_CONFIG = {
        'LONG': {'emoji': '🚀', 'label': 'LONG', 'color': '🟢'},
        'SHORT': {'emoji': '🔻', 'label': 'SHORT', 'color': '🔴'},
    }

    DEDUP_WINDOW = 60      # deduplication window (seconds)
    ANTI_SPAM_WINDOW = 60  # muted-delivery window for same-symbol signals (seconds)

    def __init__(
        self,
        bot: Bot,
        db_manager: Optional[DatabaseManager] = None
    ):
        self.bot = bot
        self.db = db_manager or DatabaseManager()
        self.logger = logging.getLogger(__name__)

        self.premium_channels: Dict[str, int] = {}
        self._load_group_config()

        self._last_signal_time: Dict[str, datetime] = {}
        self._signal_lock = Lock()

    def _load_group_config(self):
        """Load PREMIUM channel config from settings (one entry per language)."""
        lang_targets = settings.get_signal_targets_by_level(self.REQUIRED_LEVEL)

        for lang, target_id in lang_targets.items():
            if target_id:
                try:
                    self.premium_channels[lang] = int(target_id)
                    self.logger.info(
                        f"Signal target: PREMIUM/{lang} -> Channel ({target_id})"
                    )
                except ValueError:
                    self.logger.error(f"Invalid target ID: {lang}={target_id}")
            else:
                self.logger.warning(f"Signal target not configured: PREMIUM/{lang}")

    async def send_signal(
        self,
        raw_data: Dict[str, Any],
        signal_type: str
    ) -> bool:
        """
        Send a VIP signal to all PREMIUM channels.

        Returns True if at least one channel succeeds; False if all fail or signal is skipped.
        """
        if not self.premium_channels:
            self.logger.error("No PREMIUM channels configured, cannot send signal")
            return False

        signal_hash = self._calculate_hash(raw_data)

        if self._is_duplicate(signal_hash):
            self.logger.warning(f"Duplicate signal, skipped: hash={signal_hash[:16]}...")
            return False

        symbol = raw_data.get('symbol', 'UNKNOWN')
        should_mute = self._should_mute_notification(symbol)
        has_indicators = self._has_indicators(raw_data)

        success_count = 0
        first_msg_id = None
        first_main_text = None

        for lang, channel_id in self.premium_channels.items():
            try:
                main_text = self._format_main_signal(raw_data, signal_type, lang=lang)
                if first_main_text is None:
                    first_main_text = main_text

                main_msg = await self.bot.send_message(
                    chat_id=channel_id,
                    text=main_text,
                    parse_mode='HTML',
                    disable_notification=should_mute
                )

                mute_status = " (muted)" if should_mute else ""
                self.logger.info(
                    f"Signal sent{mute_status}: type={signal_type}, "
                    f"channel={lang}/{channel_id}, msg_id={main_msg.message_id}"
                )

                await update_pinned_message(self.bot, channel_id, main_msg.message_id)

                analysis_msg_id = None
                if has_indicators:
                    analysis_text = self._format_analysis(raw_data, lang=lang)
                    try:
                        analysis_msg = await self.bot.send_message(
                            chat_id=channel_id,
                            text=analysis_text,
                            parse_mode='HTML',
                            reply_to_message_id=main_msg.message_id,
                            disable_notification=should_mute
                        )
                        analysis_msg_id = analysis_msg.message_id
                        self.logger.info(
                            f"Analysis sent: channel={lang}, msg_id={analysis_msg_id}"
                        )
                    except TelegramAPIError as e:
                        self.logger.warning(f"Analysis send failed [{lang}]: {e}")

                if first_msg_id is None:
                    first_msg_id = main_msg.message_id

                success_count += 1

            except TelegramAPIError as e:
                self.logger.error(f"Signal send failed [{lang}]: {e}")

        if success_count > 0:
            self._update_last_signal_time(symbol)

            first_channel = next(iter(self.premium_channels.values()))
            self._record_push(
                signal_hash=signal_hash,
                signal_type=signal_type,
                group_id=first_channel,
                content=first_main_text or '',
                message_id=first_msg_id,
                analysis_msg_id=None,
                signal_id=raw_data.get('id')
            )

            self.logger.info(
                f"Signal delivery done: {success_count}/{len(self.premium_channels)} channels succeeded"
            )
            return True
        else:
            self._record_push(
                signal_hash=signal_hash,
                signal_type=signal_type,
                group_id=0,
                content=first_main_text or '',
                status='FAILED',
                error_message="All channels failed"
            )
            return False

    def _should_mute_notification(self, symbol: str) -> bool:
        """Return True if a signal for this symbol was sent within the anti-spam window."""
        with self._signal_lock:
            last_time = self._last_signal_time.get(symbol)
            if not last_time:
                return False

            elapsed = (datetime.now() - last_time).total_seconds()
            if elapsed < self.ANTI_SPAM_WINDOW:
                self.logger.info(
                    f"Anti-spam: {symbol} already sent {elapsed:.0f}s ago, muting"
                )
                return True

            return False

    def _update_last_signal_time(self, symbol: str):
        """Record the current time as the last send time for this symbol."""
        with self._signal_lock:
            self._last_signal_time[symbol] = datetime.now()

    def _format_main_signal(
        self,
        data: Dict[str, Any],
        signal_type: str,
        lang: str = 'en'
    ) -> str:
        """Format the main signal card (HTML). Language determined by lang parameter."""
        action = data.get('action', 'LONG')
        symbol = data.get('symbol', 'UNKNOWN')
        config = self.ACTION_CONFIG.get(action, self.ACTION_CONFIG['LONG'])

        emoji = config['emoji']
        action_label = t(f'signal.action_{action.lower()}', lang)
        type_label = t('signal.type_swing', lang)

        long_range = data.get('long_range', {})
        if isinstance(long_range, dict) and long_range:
            entry_min = long_range.get('min')
            entry_max = long_range.get('max')
        else:
            entry_min = data.get('short_entry_price') or data.get('current_price')
            entry_max = entry_min
        entry_str = f"<code>{entry_min}</code>" if entry_min == entry_max else f"<code>{entry_min} - {entry_max}</code>"

        tp = data.get('take_profit')
        na_str = f"<i>{t('signal.na', lang)}</i>"
        if isinstance(tp, list) and len(tp) >= 2:
            tp1, tp2 = tp[0], tp[1]
            tp_str = f"1️⃣ <code>{tp1}</code>\n2️⃣ <code>{tp2}</code>"
        elif isinstance(tp, list) and len(tp) == 1:
            tp_str = f"<code>{tp[0]}</code>"
        elif tp is not None:
            tp_str = f"<code>{tp}</code>"
        else:
            tp_str = na_str

        sl = data.get('stop_loss')
        sl_str = f"<code>{sl}</code>" if sl else na_str

        position_size = data.get('position_size', 20)
        leverage_str = f"Cross {position_size}x"

        created_at = data.get('created_at')
        if isinstance(created_at, datetime):
            time_str = created_at.strftime('%Y-%m-%d %H:%M')
        else:
            time_str = datetime.now().strftime('%Y-%m-%d %H:%M')

        msg = f"""{emoji} <b>{action_label}</b> #{symbol}USDT
📊 <b>{type_label}</b>

🛒 <b>{t('signal.label_entry', lang)}:</b>
{entry_str}

🎯 <b>{t('signal.label_targets', lang)}:</b>
{tp_str}

🛑 <b>{t('signal.label_stop', lang)}:</b>
{sl_str}

⚙️ <b>{t('signal.label_leverage', lang)}:</b>
{leverage_str}

📅 <i>{time_str}</i>
👇 <i>{t('signal.label_see_analysis', lang)}</i>"""

        return msg

    def _format_analysis(self, data: Dict[str, Any], lang: str = 'en') -> str:
        """
        Format the analysis reply (HTML). Three sections:
        1. Logic: parsed confidence dimensions
        2. Smart Money: top-trader ratio + OI + OBV + CMF
        3. Technical: RSI + Bollinger Bands
        """
        symbol = data.get('symbol', 'UNKNOWN')
        action = data.get('action', 'LONG')
        action_type = 'Long' if 'LONG' in action else 'Short'

        logic_parts = []
        breakdown = data.get('confidence_breakdown', {})

        if isinstance(breakdown, dict):
            dimensions = breakdown.get('dimensions', [])
            for dim in dimensions:
                if isinstance(dim, dict):
                    name = dim.get('name', '')
                    details = dim.get('details', '')
                    score = dim.get('score', 0)
                    max_score = dim.get('max_score', 0)

                    # Show only high-scoring dimensions (>60%)
                    if max_score > 0 and score / max_score >= 0.6:
                        if ('volume' in name.lower() or '成交量' in name) and details:
                            logic_parts.append(f"📊 {details}")
                        elif ('bollinger' in name.lower() or '布林带' in name) and details:
                            logic_parts.append(f"📉 {details}")
                        elif 'MA25' in name and details:
                            logic_parts.append(f"📈 {details}")

        if not logic_parts:
            reason = data.get('technical_reason', '')
            if reason:
                logic_parts.append(reason)
            else:
                logic_parts.append(t('signal.default_reason', lang))

        logic_str = "\n".join(logic_parts)

        smart_money = []
        enhanced = data.get('enhanced_indicators', {})
        if isinstance(enhanced, dict):
            # Top trader long/short ratio
            top_trader = enhanced.get('top_trader', {})
            if isinstance(top_trader, dict) and top_trader.get('ratio') is not None:
                ratio = top_trader['ratio']
                if ratio > 1:
                    whale_trend = t('signal.whale_bullish', lang)
                else:
                    whale_trend = t('signal.whale_bearish', lang)
                smart_money.append(f"• <b>{t('signal.analysis_smart_money', lang)}:</b> {ratio:.2f} ({whale_trend})")

            # Open interest
            oi_data = enhanced.get('open_interest', {})
            if isinstance(oi_data, dict) and oi_data.get('change_5m_pct') is not None:
                oi_change = oi_data['change_5m_pct']
                oi_trend_key = 'signal.oi_inflow' if oi_change > 0 else 'signal.oi_outflow'
                oi_emoji = "📈" if oi_change > 0 else "📉"
                smart_money.append(f"• <b>OI:</b> {oi_change:+.2f}% {oi_emoji} {t(oi_trend_key, lang)}")

            # OBV money flow
            obv_data = enhanced.get('obv', {})
            if isinstance(obv_data, dict) and obv_data.get('change_5d_pct') is not None:
                obv_change = obv_data['change_5d_pct']
                if obv_change > 1:
                    obv_trend = t('signal.obv_inflow', lang)
                elif obv_change < -1:
                    obv_trend = t('signal.obv_outflow', lang)
                else:
                    obv_trend = t('signal.obv_neutral', lang)
                smart_money.append(f"• <b>OBV:</b> {obv_change:+.1f}% ({obv_trend})")

            # CMF money flow
            cmf_data = enhanced.get('cmf', {})
            if isinstance(cmf_data, dict) and cmf_data.get('value') is not None:
                cmf_val = cmf_data['value']
                cmf_pressure = cmf_data.get('pressure', 'neutral')
                pressure_key = f'signal.cmf_{cmf_pressure}'
                smart_money.append(f"• <b>CMF:</b> {cmf_val:.3f} ({t(pressure_key, lang)})")

        smart_money_str = "\n".join(smart_money) if smart_money else f"<i>{t('signal.data_loading', lang)}</i>"

        indicators = []

        rsi = data.get('rsi')
        if rsi:
            if rsi < 30:
                rsi_status = f"🟢 {t('signal.rsi_oversold', lang)}"
            elif rsi > 70:
                rsi_status = f"🔴 {t('signal.rsi_overbought', lang)}"
            elif rsi < 45:
                rsi_status = f"🟡 {t('signal.rsi_low', lang)}"
            elif rsi > 55:
                rsi_status = f"🟡 {t('signal.rsi_high', lang)}"
            else:
                rsi_status = f"⚪ {t('signal.rsi_neutral', lang)}"
            indicators.append(f"• <b>RSI:</b> {rsi:.1f} {rsi_status}")

        if isinstance(enhanced, dict):
            bb_data = enhanced.get('bollinger_bands', {})
            if isinstance(bb_data, dict) and bb_data.get('position'):
                position = bb_data['position']
                pos_key_map = {
                    'oversold': 'signal.bb_lower',
                    'overbought': 'signal.bb_upper',
                    'neutral': 'signal.bb_middle'
                }
                pos_emoji = {'oversold': '🟢', 'overbought': '🔴', 'neutral': '⚪'}
                pos_text = f"{pos_emoji.get(position, '⚪')} {t(pos_key_map.get(position, 'signal.bb_middle'), lang)}"
                dist_lower = bb_data.get('distance_to_lower_pct', 0)
                dist_upper = bb_data.get('distance_to_upper_pct', 0)
                indicators.append(f"• <b>BB:</b> {pos_text} (↓{dist_lower:.1f}% ↑{dist_upper:.1f}%)")

        indicators_str = "\n".join(indicators) if indicators else f"<i>{t('signal.na', lang)}</i>"

        confidence = data.get('confidence', 0)

        msg = f"""📝 <b>{t('signal.analysis_title', lang)}</b>
<i>Re: #{symbol}USDT {action_type}</i>

💡 <b>{t('signal.analysis_logic', lang)}:</b>
{logic_str}

💰 <b>{t('signal.analysis_smart_money', lang)}:</b>
{smart_money_str}

📈 <b>{t('signal.analysis_technical', lang)}:</b>
{indicators_str}

📊 <b>{t('signal.analysis_confidence', lang)}:</b>
{confidence}%

⚠️ <i>{t('signal.dyor', lang)}</i>"""

        return msg

    def _calculate_hash(self, data: Dict[str, Any]) -> str:
        """Compute a deduplication hash from key signal fields."""
        key_fields = [
            str(data.get('symbol', '')),
            str(data.get('action', '')),
            str(data.get('price', '')),
            str(data.get('stop_loss', '')),
        ]
        content = '|'.join(key_fields)
        return hashlib.sha256(content.encode()).hexdigest()

    def _is_duplicate(self, signal_hash: str) -> bool:
        """Return True if an identical signal was successfully sent within DEDUP_WINDOW."""
        try:
            sql = """
                SELECT COUNT(*) as cnt
                FROM signal_push_history
                WHERE signal_hash = %s
                  AND created_at > %s
                  AND status = 'SUCCESS'
            """
            cutoff = datetime.now() - timedelta(seconds=self.DEDUP_WINDOW)
            result = self.db.execute_query(sql, (signal_hash, cutoff), fetch_one=True)
            return result and result['cnt'] > 0
        except Exception as e:
            self.logger.warning(f"Dedup check failed: {e}")
            return False

    def _has_indicators(self, data: Dict[str, Any]) -> bool:
        """Return True if the signal data contains any technical indicator fields."""
        indicator_keys = ['rsi', 'macd', 'adx', 'volatility', 'technical_reason']
        return any(data.get(k) is not None for k in indicator_keys)

    def _record_push(
        self,
        signal_hash: str,
        signal_type: str,
        group_id: int,
        content: str,
        message_id: Optional[int] = None,
        analysis_msg_id: Optional[int] = None,
        signal_id: Optional[int] = None,
        status: str = 'SUCCESS',
        error_message: Optional[str] = None
    ):
        """Write a push audit record to signal_push_history."""
        try:
            sql = """
                INSERT INTO signal_push_history
                (signal_id, signal_hash, signal_type, target_group_id,
                 message_content, message_id, analysis_msg_id, status, error_message)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            self.db.execute_update(sql, (
                signal_id, signal_hash, signal_type, group_id,
                content, message_id, analysis_msg_id, status, error_message
            ))
            self.logger.debug(f"Push record saved: hash={signal_hash[:16]}...")
        except Exception as e:
            self.logger.error(f"Failed to save push record: {e}")

    def get_configured_groups(self) -> Dict[str, int]:
        """Return a copy of configured language -> channel_id mappings."""
        return self.premium_channels.copy()

    def is_fully_configured(self) -> bool:
        """Return True if both zh and en channels are configured."""
        return len(self.premium_channels) >= 2

    async def send_tp_sl_notification(
        self,
        symbol: str,
        event_type: str,
        price: float,
        pnl: float,
        pnl_percent: float,
        signal_id: Optional[int] = None,
        position_id: Optional[int] = None,
        signal_type: str = 'SWING'
    ) -> bool:
        """
        Send a TP/SL notification to all PREMIUM channels.

        Returns True if at least one channel succeeds.
        Reply is not used (unreliable in multi-channel scenarios).
        """
        if not self.premium_channels:
            self.logger.error("No PREMIUM channels configured, cannot send TP/SL notification")
            return False

        success_count = 0

        for lang, channel_id in self.premium_channels.items():
            try:
                msg_text = self._format_tp_sl_message(
                    symbol=symbol,
                    event_type=event_type,
                    price=price,
                    pnl=pnl,
                    pnl_percent=pnl_percent,
                    position_id=position_id,
                    lang=lang
                )

                await self.bot.send_message(
                    chat_id=channel_id,
                    text=msg_text,
                    parse_mode='HTML'
                )
                self.logger.info(
                    f"TP/SL notification sent: {event_type} {symbol}, channel={lang}"
                )
                success_count += 1

            except TelegramAPIError as e:
                self.logger.error(f"TP/SL notification failed [{lang}]: {e}")

        if success_count > 0:
            self.logger.info(
                f"TP/SL delivery done: {success_count}/{len(self.premium_channels)} channels succeeded"
            )
            return True
        else:
            return False

    def _get_signal_telegram_msg_id(self, signal_id: int) -> Optional[int]:
        """Look up telegram_message_id for a signal. Returns None if not found."""
        try:
            sql = """
                SELECT telegram_message_id
                FROM signals
                WHERE id = %s
            """
            result = self.db.execute_query(sql, (signal_id,), fetch_one=True)
            if result and result.get('telegram_message_id'):
                return int(result['telegram_message_id'])
            return None
        except Exception as e:
            self.logger.warning(f"Failed to query telegram_message_id: {e}")
            return None

    def _format_tp_sl_message(
        self,
        symbol: str,
        event_type: str,
        price: float,
        pnl: float,
        pnl_percent: float,
        position_id: Optional[int] = None,
        lang: str = 'en'
    ) -> str:
        """Format a TP/SL notification message (HTML). Language determined by lang parameter."""
        event_config = {
            'TP1': {
                'emoji': '🎯',
                'title_key': 'signal.tp_title_tp1',
                'action_key': 'signal.tp_action_tp1'
            },
            'TP2': {
                'emoji': '🏆',
                'title_key': 'signal.tp_title_tp2',
                'action_key': 'signal.tp_action_tp2'
            },
            'SL': {
                'emoji': '🛑',
                'title_key': 'signal.tp_title_sl',
                'action_key': 'signal.tp_action_sl'
            }
        }

        config = event_config.get(event_type, event_config['SL'])

        pnl_emoji = '💰' if pnl >= 0 else '💸'
        pnl_sign = '+' if pnl >= 0 else ''

        msg = f"""{config['emoji']} <b>{t(config['title_key'], lang)}</b>
#{symbol}USDT

💵 <b>{t('signal.label_price', lang)}:</b> <code>{price:.2f}</code>

{pnl_emoji} <b>{t('signal.label_pnl', lang)}:</b>
{pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_percent:.2f}%)

📋 <b>{t('signal.label_action', lang)}:</b>
{t(config['action_key'], lang)}"""

        if position_id:
            msg += f"\n\n<i>Position ID: {position_id}</i>"

        return msg
