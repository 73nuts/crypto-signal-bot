"""
Message formatter module.
Formats signal data into human-readable email messages.
Supports i18n via environment variables:
- LANGUAGE: language code (zh_CN/en_US)
- BILINGUAL: bilingual mode (true/false)
"""

from datetime import datetime

from src.core.config import settings
from src.i18n import t, tt

# Decimal precision config
DECIMAL_PRECISION = {
    'price': 2,      # price: 2 decimal places
    'percent': 1,    # percent: 1 decimal place
    'ratio': 2,      # ratio: 2 decimal places
    'cmf': 3,        # CMF: 3 decimal places
}


def translate_status(value: str) -> str:
    """Translate status value (i18n, supports bilingual).

    Args:
        value: Raw status value

    Returns:
        Translated status value (mono or bilingual)
    """
    if value is None:
        return tt('status.na', 'N/A')
    return tt(f'status.{value}', str(value))


class SignalFormatter:
    """Signal message formatter."""

    def __init__(self, symbol):
        """
        Args:
            symbol: Asset symbol (e.g. 'ETH', 'SOL')
        """
        self.symbol = symbol
        # Environment prefix for email subjects etc.
        self.env_prefix = settings.ENVIRONMENT_PREFIX

    def format_subject(self, signal):
        """Format email subject.

        Args:
            signal: Signal dict

        Returns:
            str: Email subject
        """
        action_map = {
            'LONG': 'Long',
            'SHORT': 'Short',
            'SELL': 'Reduce',
        }
        action_str = action_map.get(signal['action'], signal['action'])

        signal_name = settings.SIGNAL_NAME
        return f"{signal_name}-{self.symbol} {action_str}"

    def format_ai_subject(self, enhanced_signal, signal):
        """Format AI-enhanced email subject.

        Args:
            enhanced_signal: Grok AI enhanced signal
            signal: Raw technical signal

        Returns:
            str: AI-enhanced email subject
        """
        action = enhanced_signal['final_action']
        price = signal['current_price']
        confidence = enhanced_signal['adjusted_confidence']
        risk = enhanced_signal['risk_level']

        prefix = f"{self.env_prefix} " if self.env_prefix else ""
        return f"{prefix}{self.symbol} Signal [AI] - {action} (${price:.0f}) [{confidence}%] Risk:{risk}"

    def format_enhanced(self, signal, market_env=None, use_detailed_indicators=True):
        """Format enhanced signal message.

        Args:
            signal: Signal dict
            market_env: Market environment info (optional)
            use_detailed_indicators: Whether to include detailed technical indicators.
                True: include enhanced indicators (email/WeChat)
                False: exclude enhanced indicators

        Returns:
            str: Formatted complete message
        """
        date_str = signal['timestamp'].strftime('%Y-%m-%d')

        strategy_overview = self._format_strategy_overview(signal)
        operation_info = self.format_operation(signal)
        judgment_basis = self.format_judgment(signal)

        enhanced_indicators_section = ""
        if use_detailed_indicators:
            enhanced_indicators_text = self._format_enhanced_indicators(signal)
            if enhanced_indicators_text:
                enhanced_indicators_section = f"\n\n### Detailed Technical Indicators\n{enhanced_indicators_text}"

        auxiliary_info = self._format_auxiliary_info(signal, market_env)

        message = f"""## {date_str}

### Strategy Signal
{strategy_overview}

### Operation Details
{operation_info}

### Decision Basis
{judgment_basis}{enhanced_indicators_section}

### Auxiliary Info
{auxiliary_info}"""

        return message

    def format_ai_enhanced(self, signal, enhanced_signal, policy_event):
        """Format AI-enhanced signal message (Trump policy triggered).

        Args:
            signal: Raw technical signal dict
            enhanced_signal: Grok AI enhanced signal dict
            policy_event: Trump policy event dict

        Returns:
            str: Formatted AI-enhanced message
        """
        date_str = signal['timestamp'].strftime('%Y-%m-%d')

        # Operation info (using AI-adjusted parameters)
        operation_info = self._format_ai_operation(signal, enhanced_signal)

        # Trump policy details
        policy_detail = self._format_policy_detail(policy_event, enhanced_signal)

        # AI composite decision
        ai_decision = self._format_ai_decision_only(signal, enhanced_signal)

        message = f"""## {date_str}

### Operation Details
{operation_info}

### Trump Policy Details
{policy_detail}

### AI Decision Summary
{ai_decision}"""

        return message

    def _format_ai_operation(self, signal, enhanced_signal):
        """Format AI-enhanced operation info.

        Args:
            signal: Raw technical signal
            enhanced_signal: Grok AI enhanced signal

        Returns:
            str: Operation info text
        """
        action = enhanced_signal['final_action']
        position = enhanced_signal['position_percentage']

        if action == 'LONG':
            return f"""Suggested Long
- Limit buy: {signal['long_range']['min']}-{signal['long_range']['max']}
- TP1: {signal['take_profit'][0]}
- TP2: {signal['take_profit'][1]}
- SL: {signal['stop_loss']}
- Position: {position}%"""

        elif action == 'SHORT':
            # Short entry: use signal TP/SL
            return f"""Suggested Short
- Entry price: {signal.get('short_entry_price', round(signal['current_price'], 2))}
- Take profit: {signal['take_profit']}
- Stop loss: {signal['stop_loss']}
- Position: {position}%"""

        elif action == 'SELL':
            return f"""Suggested Reduce/TP
- Current price: {signal['current_price']:.2f}
- Target price: {signal.get('target_price', round(signal['current_price'], 2))}
- Position: {position}%"""

        else:  # WAIT
            return """Suggested Wait
Market conditions unclear, entry not recommended"""

    def _format_policy_detail(self, policy_event, enhanced_signal):
        """Format Trump policy details.

        Args:
            policy_event: Trump policy event
            enhanced_signal: Grok AI enhanced signal

        Returns:
            str: Policy detail text
        """
        affected_markets_str = ', '.join(policy_event.affected_markets) if policy_event.affected_markets else 'Unidentified'

        policy_detail = f"""**Published:** {policy_event.timestamp}
**Category:** {policy_event.category}
**Severity:** {policy_event.severity}
**Affected markets:** {affected_markets_str}

**Trump original text**
{policy_event.original_text}"""

        # Append AI policy impact analysis if available
        if enhanced_signal.get('policy_impact'):
            policy_detail += f"\n\n**AI Policy Impact Analysis**\n{enhanced_signal['policy_impact']}"

        return policy_detail

    def _format_ai_decision_only(self, signal, enhanced_signal):
        """Format AI composite decision content.

        Args:
            signal: Raw technical signal
            enhanced_signal: Grok AI enhanced signal

        Returns:
            str: AI decision text
        """
        price = signal['current_price']
        ma25 = signal['ma25']
        macd = signal['macd']

        # Price deviation from MA25 percentage
        price_deviation = ((price - ma25) / ma25) * 100
        price_vs_ma25_detail = f"{price_deviation:+.1f}%"

        # MACD status description
        if macd < -50:
            macd_desc = "strong bearish cross"
        elif macd < 0:
            macd_desc = "bearish cross" if abs(macd) > 5 else "just turned negative"
        elif macd > 50:
            macd_desc = "strong bullish cross"
        else:
            macd_desc = "bullish cross" if macd > 5 else "just turned positive"

        # Technical summary
        price_rel = 'above' if price > ma25 else 'well below' if price < ma25 * 0.95 else 'below'
        technical_summary = f"Price {price:.2f} {price_rel} MA25({ma25:.2f}) {price_vs_ma25_detail}, MACD {macd:.2f}({macd_desc})"

        # AI composite decision
        decision = f"""**Technical:** {technical_summary}
**Confidence:** {enhanced_signal['adjusted_confidence']}% | Risk level: {enhanced_signal['risk_level']}

**AI Decision Reasoning**
{enhanced_signal['reasoning']}"""

        # Append AI key points if available
        if enhanced_signal.get('key_points'):
            key_points_text = '\n'.join([f"- {point}" for point in enhanced_signal['key_points']])
            decision += f"\n\n**AI Key Points**\n{key_points_text}"

        decision += f"\n\n**Entry timing:** {enhanced_signal['entry_timing']} | Time horizon: {enhanced_signal['time_horizon']}"

        return decision

    def format_basic(self, signal):
        """Format basic signal message.

        Args:
            signal: Signal dict

        Returns:
            str: Formatted basic message
        """
        date_str = signal['timestamp'].strftime('%Y-%m-%d')

        operation_info = self.format_operation(signal)
        judgment_basis = self.format_judgment(signal)
        news = self.get_market_news()
        auxiliary_info = " | ".join(news[:2]) if news else "No significant news impact"

        message = f"""## {date_str}

### Operation Details
{operation_info}

### Decision Basis
{judgment_basis}

### Auxiliary Info
{auxiliary_info}"""

        return message

    def format_operation(self, signal):
        """Format operation info.

        Args:
            signal: Signal dict

        Returns:
            str: Operation info text
        """
        if signal['action'] == 'LONG':
            return f"""Suggested Long
- Limit buy: {signal['long_range']['min']}-{signal['long_range']['max']}
- TP1: {signal['take_profit'][0]}
- TP2: {signal['take_profit'][1]}
- SL: {signal['stop_loss']}
- Position: {signal['position_size']}%"""

        elif signal['action'] == 'SHORT':
            # Short entry range (±0.5%, consistent with long entry)
            entry_price = signal['short_entry_price']
            short_range_min = round(entry_price * 0.995, 2)
            short_range_max = round(entry_price * 1.005, 2)
            return f"""Suggested Short
- Entry range: {short_range_min}-{short_range_max}
- Take profit: {signal['take_profit']}
- Stop loss: {signal['stop_loss']}
- Position: {signal['position_size']}%"""

        elif signal['action'] == 'SELL':
            return f"""Suggested Reduce/TP
- Current price: {signal['current_price']:.0f}
- Target price: {signal['target_price']}
- Action: {signal['action_suggestion']}"""

        else:  # WAIT
            return f"""Suggested Wait
- Current price: {signal['current_price']:.2f}
- Watch level: {signal.get('wait_for_price', round(signal['current_price'], 2))} area
- Watch for: {signal.get('watch_for', 'MA25 support or breakout')}"""

    def format_judgment(self, signal):
        """Format decision basis.

        Args:
            signal: Signal dict

        Returns:
            str: Decision basis text
        """
        price = signal['current_price']
        ma25 = signal['ma25']
        macd = signal['macd']

        strategy_name = signal.get('strategy_name', 'Unknown strategy')

        price_vs_ma25 = "above" if price > ma25 else "below"
        macd_status = "bullish cross" if macd > 0 else "bearish cross"

        basis = f"""**Strategy**: {strategy_name}
**Technical**: Price {price:.0f} {price_vs_ma25} MA25({ma25:.0f}), MACD {macd:.1f}({macd_status})
**Reason**: {signal['technical_reason']}"""

        return basis

    def format_judgment_enhanced(self, signal):
        """Format enhanced decision basis.

        Args:
            signal: Signal dict

        Returns:
            str: Enhanced decision basis text
        """
        price = signal['current_price']
        ma25 = signal['ma25']
        macd = signal['macd']

        price_vs_ma25 = "above" if price > ma25 else "below"
        macd_status = "bullish cross" if macd > 0 else "bearish cross"

        basis = f"Price {price:.0f} {price_vs_ma25} MA25({ma25:.0f}), MACD {macd:.1f}({macd_status})\n"
        basis += f"{signal['technical_reason']}\n"
        basis += f"Signal confidence: {signal['confidence']}%"

        # Multi-timeframe info
        if signal.get('mtf_confirmed') is not None:
            mtf_status = "confirmed" if signal['mtf_confirmed'] else "not confirmed"
            basis += f"\nMulti-timeframe: {mtf_status} (confidence: {signal.get('mtf_confidence', 0):.1%})"

        return basis

    def format_market_env(self, signal):
        """Format market environment info (detailed).

        Args:
            signal: Signal dict

        Returns:
            str: Market environment info text
        """
        if 'market_environment' not in signal:
            return "Market environment: insufficient data"

        env = signal['market_environment']

        info = f"Market regime: {env['regime']}\n"
        info += f"Trend strength: {env['trend_strength']} ({env['trend_direction']})\n"
        info += f"Volatility level: {env['volatility_level']}\n"
        info += f"Characteristics: {env['characteristics']}"

        return info

    def format_market_env_brief(self, signal):
        """Format market environment info (brief).

        Args:
            signal: Signal dict

        Returns:
            str or None: Brief market environment info
        """
        if 'market_environment' not in signal:
            return None

        env = signal['market_environment']

        regime_map = {
            'BULL_TREND': 'Bull trend',
            'BEAR_TREND': 'Bear trend',
            'HIGH_VOLATILITY': 'High volatility',
            'LOW_VOLATILITY': 'Low volatility',
            'NEUTRAL': 'Sideways',
            'STRONG_SIDEWAYS': 'Strong sideways'
        }

        regime_desc = regime_map.get(env['regime'], 'Unknown regime')
        return f"Market regime: {regime_desc}"

    def get_market_news(self):
        """Get market news (simplified).

        Returns:
            List[str]: News item list
        """
        news_items = []

        # Check for key market hours
        now = datetime.now()
        if now.hour in [8, 16, 20]:
            news_items.append("Watch US market open window")

        # Weekend caution
        if now.weekday() >= 5:
            news_items.append("Weekend: lower liquidity, manage risk")

        if not news_items:
            news_items = ["No significant news impact"]

        return news_items

    def _format_strategy_overview(self, signal):
        """Format strategy signal overview.

        Args:
            signal: Signal dict

        Returns:
            str: Strategy overview text
        """
        action_map = {
            'LONG': 'LONG (long)',
            'SHORT': 'SHORT (short)',
            'SELL': 'SELL (reduce)',
        }
        action_desc = action_map.get(signal['action'], signal['action'])

        strategy_name = signal.get('strategy_name', 'Unknown strategy')

        # Multi-timeframe confirmation status
        if signal.get('mtf_confirmed') is not None:
            mtf_status = "confirmed" if signal['mtf_confirmed'] else "not confirmed"
            mtf_info = f" | Multi-TF: {mtf_status}"
        else:
            mtf_info = ""

        overview = f"""**Strategy**: {strategy_name}
**Signal type**: {action_desc}
**Confidence**: {signal['confidence']}%{mtf_info}"""

        return overview

    def _format_auxiliary_info(self, signal, market_env=None):
        """Format auxiliary info.

        Args:
            signal: Signal dict
            market_env: Market environment info

        Returns:
            str: Auxiliary info text
        """
        news = self.get_market_news()
        env_info = self.format_market_env_brief(signal) if 'market_environment' in signal else None

        auxiliary_items = []
        if news:
            auxiliary_items.extend(news[:2])
        if env_info:
            auxiliary_items.append(env_info)

        return " | ".join(auxiliary_items) if auxiliary_items else "No significant news impact"

    def _format_enhanced_indicators(self, signal):
        """Format enhanced indicators for detailed technical display in email/WeChat.

        Args:
            signal: Signal dict (must contain enhanced_indicators field)

        Returns:
            str or None: Formatted enhanced indicators text, None if no data
        """
        if 'enhanced_indicators' not in signal:
            return None

        indicators = signal['enhanced_indicators']
        if not indicators:
            return None

        return self._format_trend_indicators(indicators)

    def _format_trend_indicators(self, indicators):
        """Format trend signal enhanced indicators.

        Args:
            indicators: Enhanced indicators dict (bollinger, obv, cmf, orderbook)

        Returns:
            str: Formatted text
        """
        sections = []
        sep = t('format.separator', '  |  ')
        p_price = DECIMAL_PRECISION['price']
        p_pct = DECIMAL_PRECISION['percent']
        p_ratio = DECIMAL_PRECISION['ratio']
        p_cmf = DECIMAL_PRECISION['cmf']

        # 1. Bollinger Bands position
        if indicators.get('bollinger_bands'):
            bb = indicators['bollinger_bands']
            position_text = translate_status(bb['position'])
            sections.append(
                f"📊 **{tt('indicator.bollinger.title')}**\n"
                f"   {tt('indicator.bollinger.upper')}: {bb['upper']:.{p_price}f}{sep}"
                f"{tt('indicator.bollinger.middle')}: {bb.get('middle', 0):.{p_price}f}{sep}"
                f"{tt('indicator.bollinger.lower')}: {bb['lower']:.{p_price}f}\n"
                f"   {tt('indicator.bollinger.distance_upper')}: {bb['distance_to_upper_pct']:.{p_pct}f}%{sep}"
                f"{tt('indicator.bollinger.distance_lower')}: {bb['distance_to_lower_pct']:.{p_pct}f}%\n"
                f"   {tt('indicator.bollinger.position')}: {position_text}"
            )

        # 2. OBV money flow
        if indicators.get('obv'):
            obv = indicators['obv']
            trend_text = translate_status(obv['trend'])
            change = obv['change_5d_pct']
            change_sign = '+' if change > 0 else ''
            sections.append(
                f"💰 **{tt('indicator.obv.title')}**\n"
                f"   {tt('indicator.obv.current')}: {obv['current']:,.0f}{sep}"
                f"{tt('indicator.obv.change_5d')}: {change_sign}{change:.{p_pct}f}%\n"
                f"   {tt('indicator.obv.trend')}: {trend_text}"
            )

        # 3. CMF Chaikin Money Flow
        if indicators.get('cmf'):
            cmf = indicators['cmf']
            pressure_text = translate_status(cmf['pressure'])
            sections.append(
                f"📈 **{tt('indicator.cmf.title')}**\n"
                f"   {tt('indicator.cmf.value')}: {cmf['value']:.{p_cmf}f}{sep}"
                f"{tt('indicator.cmf.pressure')}: {pressure_text}"
            )

        # 4. Order book depth
        if indicators.get('orderbook'):
            ob = indicators['orderbook']
            ratio = ob.get('bid_ask_ratio')
            ratio_str = f"{ratio:.{p_ratio}f}" if isinstance(ratio, (int, float)) else tt('status.na')
            walls = ob.get('large_walls', tt('status.na'))
            sections.append(
                f"📋 **{tt('indicator.orderbook.title')}**\n"
                f"   {tt('indicator.orderbook.bid_ask_ratio')}: {ratio_str}{sep}"
                f"{tt('indicator.orderbook.large_walls')}: {walls}"
            )

        # 5. Large trader sentiment
        if indicators.get('top_trader'):
            top_t = indicators['top_trader']
            ratio = top_t.get('position_ratio')
            ratio_str = f"{ratio:.{p_ratio}f}" if isinstance(ratio, (int, float)) else tt('status.na')
            display = top_t.get('display', tt('status.na'))
            sections.append(
                f"🐋 **{tt('indicator.top_trader.title')}**\n"
                f"   {tt('indicator.top_trader.position_ratio')}: {ratio_str}{sep}"
                f"{tt('indicator.top_trader.sentiment')}: {display}"
            )

        if not sections:
            return None

        return "\n\n".join(sections)

