"""
OpenRouter AI Client (Grok 4.1 Fast)

Used to generate Quant View AI commentary for Daily Pulse

Features:
  - Based on OpenRouter unified gateway
  - Uses Grok 4.1 Fast model
  - Supports Live Search (Web + X)
  - Mover news correlation (gainer/loser + news search)
  - X/Twitter sentiment aggregation (KOL views + community sentiment)
  - Language separation: English channel pure English, Chinese channel pure Chinese
  - Comprehensive error handling

Environment variables:
  - OPENROUTER_API_KEY: OpenRouter API key
"""

import re
from typing import Any, Dict, Optional

import requests

from src.core.config import settings
from src.core.structured_logger import get_logger


class OpenRouterClient:
    """
    OpenRouter API client

    Uses Grok 4.1 Fast model to generate market commentary
    """

    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
    MODEL = "x-ai/grok-4.1-fast"

    # Limit output length to control cost
    MAX_TOKENS = 800

    # System prompts - language-separated
    # English prompt
    SYSTEM_PROMPT_EN = """# Role
You are Ignis Quant intelligence analyst, aggregating on-chain/derivatives/narrative data into concise market insights.

# Search Tasks

1. On-chain: Search "BTC whale" "exchange outflow" → extract specific data
2. Derivatives: Search "BTC liquidation" "open interest" → combine with system L/S and FR
3. Narrative: Search "Bitcoin ETF" "MicroStrategy" → extract major events
4. Movers: For top gainers/losers, search "{symbol} news" → find correlation

# Output Format (ENGLISH ONLY, strict)

Line 1 (Core View): 📍 {Bullish/Bearish/Neutral} · {conclusion} · ${price} pivot

Four dimensions:
• On-chain: {conclusion} ({data})
• Derivatives: {conclusion} ({data})
• Narrative: {impact} ({event})
• Sentiment: {bias} ({data source})

If significant movers:
↑ {SYMBOL} +{pct}%: {reason}
↓ {SYMBOL} {pct}%: {reason}

# Example Output

📍 Bullish · Bulls in control above $90k · $90k pivot

• On-chain: Selling pressure shrinking (whales +270k BTC/30d)
• Derivatives: Short squeeze brewing (L/S 2.26 / FR negative)
• Narrative: Institutional demand rising (ETF +$800M weekly)
• Sentiment: Bullish bias (social volume +40%)

↑ RNDR +18%: Nvidia partnership rumor
↓ AVAX -12%: Whale dumping detected

# Constraints

- Max 25 words per line
- Total length: 150-250 words
- FORBIDDEN: emoji (except 📍↑↓), fluff, URLs, Chinese text
- FORBIDDEN: price targets, only give pivot and conditional judgments
- REQUIRED: data-backed, clear direction
"""

    # Chinese prompt — intentionally in Chinese.
    # This LLM prompt targets the Chinese-language market analysis channel and must stay in Chinese
    # so the model outputs pure Chinese without English contamination.
    SYSTEM_PROMPT_ZH = """# 角色
你是Ignis Quant情报分析师，聚合链上/衍生品/叙事信息，输出简洁研判。

# 搜索任务

1. 链上: 搜索 "BTC whale" "exchange outflow" → 提取具体数据
2. 衍生品: 搜索 "BTC liquidation" "open interest" → 结合系统L/S和FR
3. 叙事: 搜索 "Bitcoin ETF" "MicroStrategy" → 提取重大事件
4. 涨跌榜关联: 对于领涨/领跌币种，搜索 "{币种} news" → 关联新闻原因

# 输出格式（严格遵守，纯中文）

第一行(核心观点): 📍 {看涨/看跌/中性} · {结论} · ${价位} 关键位

四个维度:
• 链上: {结论} ({数据})
• 衍生品: {结论} ({数据})
• 叙事: {影响} ({事件})
• 情绪: {倾向} ({数据来源})

如有涨跌榜异动:
↑ {币种} +{涨幅}%: {原因}
↓ {币种} {跌幅}%: {原因}

# 示例输出

📍 看涨 · 多头占优，站稳$90k看高 · $90k 关键位

• 链上: 抛压收缩 (巨鲸30天吸筹27万BTC)
• 衍生品: 轧空酝酿 (L/S 2.26 / FR负值)
• 叙事: 机构回暖 (ETF周流入$800M)
• 情绪: 偏多 (社交热度+40%)

↑ RNDR +18%: 传Nvidia合作
↓ AVAX -12%: 巨鲸抛售

# 约束

- 每行限制25字以内
- 总长度: 150-250字
- 禁止: emoji（📍↑↓除外）、废话、URL、英文内容
- 禁止: 目标价预测，只给关键位和条件判断
- 必须: 数据支撑、方向明确
"""

    # Backward compat: default to English
    SYSTEM_PROMPT_BASE = SYSTEM_PROMPT_EN

    # Timezone focus - English
    SYSTEM_PROMPT_EN_ASIA = SYSTEM_PROMPT_EN + """

# Timezone Focus (Asia Morning 08:00 UTC+8)

Analyze past 12 hours:
- Fund flows after US market close
- Overnight on-chain large transfers
- Key events during US/EU trading hours
- Factors affecting Asia market open
"""

    SYSTEM_PROMPT_EN_WEST = SYSTEM_PROMPT_EN + """

# Timezone Focus (US/EU Morning 08:00 UTC)

Analyze past 12 hours:
- Asia trading session performance
- Asia session fund flows
- Pre-US-market sentiment
- Factors affecting US market open
"""

    # Timezone focus - Chinese (intentionally in Chinese; model outputs pure Chinese)
    SYSTEM_PROMPT_ZH_ASIA = SYSTEM_PROMPT_ZH + """

# 时区侧重 (亚洲早盘 08:00 UTC+8)

重点分析过去12小时的变化:
- 美股收盘后的资金流向变化
- 隔夜链上大额转账
- 欧美交易时段的关键事件
- 亚洲开盘可能的影响因素
"""

    SYSTEM_PROMPT_ZH_WEST = SYSTEM_PROMPT_ZH + """

# 时区侧重 (欧美早盘 08:00 UTC)

重点分析过去12小时的变化:
- 亚洲交易时段的盘面表现
- 亚洲时段的资金流向
- 美股开盘前的市场情绪
- 可能影响美股开盘的因素
"""

    # Backward compat: default to English base
    SYSTEM_PROMPT = SYSTEM_PROMPT_EN
    SYSTEM_PROMPT_ASIA = SYSTEM_PROMPT_EN_ASIA
    SYSTEM_PROMPT_WEST = SYSTEM_PROMPT_EN_WEST

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize client

        Args:
            api_key: OpenRouter API key, defaults to reading from config
        """
        self.logger = get_logger(__name__)
        self.api_key = api_key or settings.get_secret('OPENROUTER_API_KEY')

        if not self.api_key:
            self.logger.warning("OPENROUTER_API_KEY not configured, AI features unavailable")

    @property
    def is_available(self) -> bool:
        """Check if API is available"""
        return bool(self.api_key)

    def _build_market_prompt(self, market_data: Dict[str, Any]) -> str:
        """
        Build market data prompt

        Args:
            market_data: Market data dict

        Returns:
            Formatted prompt string
        """
        parts = ["# System Input Data"]

        # BTC data
        if market_data.get('btc'):
            btc = market_data['btc']
            parts.append(f"- BTC: ${btc.get('price', 0):,.0f} ({btc.get('change_24h', 0):+.1f}% 24h)")

        # ETH data
        if market_data.get('eth'):
            eth = market_data['eth']
            parts.append(f"- ETH: ${eth.get('price', 0):,.0f} ({eth.get('change_24h', 0):+.1f}% 24h)")

        # Fear & Greed
        if market_data.get('fear_greed'):
            fg = market_data['fear_greed']
            parts.append(f"- Fear & Greed Index: {fg.get('value', 'N/A')} ({fg.get('classification', 'N/A')})")

        # Long/short ratio
        if market_data.get('long_short_ratio'):
            ls = market_data['long_short_ratio']
            ratio = ls.get('long_short_ratio', 1.0)
            if ratio > 1.2:
                bias = "Long dominant"
            elif ratio < 0.8:
                bias = "Short dominant"
            else:
                bias = "Balanced"
            parts.append(f"- Top trader L/S ratio: {ratio:.2f} ({bias})")

        # Funding rate
        if 'avg_funding' in market_data:
            fr = market_data['avg_funding']
            parts.append(f"- Average funding rate: {fr:+.4f}%")

        # Market sentiment
        if market_data.get('sentiment'):
            parts.append(f"- Market sentiment: {market_data['sentiment']}")

        # Gainer/loser data (for AI news correlation)
        if market_data.get('top_gainers') or market_data.get('top_losers'):
            parts.append("\n# 24h Gainers/Losers (search related news)")

            if market_data.get('top_gainers'):
                parts.append("Top gainers:")
                for coin in market_data['top_gainers'][:3]:
                    symbol = coin.get('symbol', '').replace('USDT', '')
                    change = coin.get('change', 0)
                    volume = coin.get('volume_usd', 0)
                    vol_str = f" (${volume/1e6:.0f}M)" if volume >= 1e6 else ""
                    parts.append(f"- {symbol}: {change:+.1f}%{vol_str}")

            if market_data.get('top_losers'):
                parts.append("Top losers:")
                for coin in market_data['top_losers'][:3]:
                    symbol = coin.get('symbol', '').replace('USDT', '')
                    change = coin.get('change', 0)
                    parts.append(f"- {symbol}: {change:+.1f}%")

        parts.append("\nGenerate market commentary based on the above data and real-time search results:")

        return "\n".join(parts)

    def _clean_ai_output(self, content: str) -> str:
        """
        Clean AI output

        Remove:
        - markdown links [[1]](url) or [text](url)
        - pure citation markers [1] [[1]]
        """
        # Remove [[1]](url) format
        content = re.sub(r'\[\[\d+\]\]\([^)]+\)', '', content)
        # Remove [text](url) format
        content = re.sub(r'\[[^\]]+\]\([^)]+\)', '', content)
        # Remove remaining [[1]] or [1] citation markers
        content = re.sub(r'\[\[?\d+\]?\]?', '', content)
        # Clean excess blank lines (keep single newlines)
        content = re.sub(r'\n{3,}', '\n\n', content)

        return content.strip()

    def _detect_risk_correlation(self, content: str) -> str:
        """
        Detect risk correlation signals

        When ETF outflow + whale inflow appear simultaneously, add 🚨 warning to title.
        This is a classic "institutional retreat but whale accumulation" divergence signal.

        Args:
            content: AI output content

        Returns:
            Content with possible 🚨 added
        """
        content_lower = content.lower()

        # ETF outflow keywords (institutional retreat signal)
        etf_outflow_keywords = [
            'etf流出', 'etf outflow', 'etf净流出',
            'etf撤出', 'etf withdrawal', 'etf outflows'
        ]

        # Whale inflow keywords (large holder accumulation signal)
        whale_inflow_keywords = [
            '巨鲸转入', '巨鲸吸筹', '鲸鱼吸筹', '大户吸筹',
            'whale inflow', 'whales accumulated', 'whale accumulating',
            'whales buying', '巨鲸买入'
        ]

        # Check if both exist simultaneously
        has_etf_outflow = any(kw in content_lower for kw in etf_outflow_keywords)
        has_whale_inflow = any(kw in content_lower for kw in whale_inflow_keywords)

        if has_etf_outflow and has_whale_inflow:
            self.logger.info("Risk correlation detected: ETF outflow + whale inflow -> adding 🚨 warning")
            # Add 🚨 after 📍
            content = content.replace('📍', '📍🚨', 1)

        return content

    def _get_system_prompt(self, region: str = 'asia', lang: str = 'en') -> str:
        """
        Get system prompt by timezone region and language

        Args:
            region: Timezone region (asia=Asia, west=US/EU)
            lang: Language code ('zh' | 'en')

        Returns:
            Corresponding system prompt
        """
        if lang == 'zh':
            if region == 'west':
                return self.SYSTEM_PROMPT_ZH_WEST
            elif region == 'asia':
                return self.SYSTEM_PROMPT_ZH_ASIA
            else:
                return self.SYSTEM_PROMPT_ZH
        else:
            # Default English
            if region == 'west':
                return self.SYSTEM_PROMPT_EN_WEST
            elif region == 'asia':
                return self.SYSTEM_PROMPT_EN_ASIA
            else:
                return self.SYSTEM_PROMPT_EN

    def generate_quant_view(
        self,
        market_data: Dict[str, Any],
        enable_web_search: bool = False,
        timeout: int = 30,
        region: str = 'asia',
        lang: str = 'en'
    ) -> Optional[str]:
        """
        Generate Quant View AI commentary (language-separated)

        Args:
            market_data: Market data
                - btc: {price, change_24h}
                - eth: {price, change_24h}
                - fear_greed: {value, classification}
                - long_short_ratio: {long_short_ratio}
                - avg_funding: float
                - sentiment: str
            enable_web_search: Whether to enable Live Search (increases cost)
            timeout: Request timeout (seconds)
            region: Timezone region (asia=Asia, west=US/EU)
            lang: Language code ('zh' | 'en'), determines output language

        Returns:
            AI-generated market commentary, None on failure
        """
        if not self.is_available:
            self.logger.warning("OpenRouter API unavailable, skipping AI generation")
            return None

        try:
            # Build request
            model = f"{self.MODEL}:online" if enable_web_search else self.MODEL
            user_prompt = self._build_market_prompt(market_data)

            # Select system prompt by timezone and language
            system_prompt = self._get_system_prompt(region, lang)

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "max_tokens": self.MAX_TOKENS,
                "temperature": 0.5,  # More rigorous logic, reduce hallucination risk
            }

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/73nuts/crypto-signal-bot",  # Required by OpenRouter
                "X-Title": "Ignis Daily Pulse"
            }

            self.logger.info(f"Calling OpenRouter API (model={model}, web_search={enable_web_search})")

            response = requests.post(
                self.BASE_URL,
                headers=headers,
                json=payload,
                timeout=timeout
            )
            response.raise_for_status()

            result = response.json()
            content = result['choices'][0]['message']['content']

            # Clean output
            content = self._clean_ai_output(content)

            # Detect risk correlation signals
            content = self._detect_risk_correlation(content)

            self.logger.info(f"AI generation successful: {len(content)} chars")
            return content

        except requests.exceptions.Timeout:
            self.logger.warning(f"OpenRouter API timed out ({timeout}s)")
            return None

        except requests.exceptions.HTTPError as e:
            self.logger.error(f"OpenRouter API error: {e.response.status_code} - {e.response.text}")
            return None

        except Exception as e:
            self.logger.error(f"AI generation failed: {e}")
            return None

    def generate_with_live_search(
        self,
        query: str,
        timeout: int = 60
    ) -> Optional[str]:
        """
        Generate response using Live Search (search X/Twitter and web)

        Args:
            query: Query question
            timeout: Timeout (seconds), Live Search needs more time

        Returns:
            AI response, None on failure
        """
        if not self.is_available:
            return None

        try:
            payload = {
                "model": f"{self.MODEL}:online",
                "messages": [
                    {"role": "user", "content": query}
                ],
                "max_tokens": 500,
                "temperature": 0.5,
            }

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/73nuts/ignis",
                "X-Title": "Ignis Daily Pulse"
            }

            self.logger.info(f"Live Search query: {query[:50]}...")

            response = requests.post(
                self.BASE_URL,
                headers=headers,
                json=payload,
                timeout=timeout
            )
            response.raise_for_status()

            result = response.json()
            content = result['choices'][0]['message']['content']

            return content.strip()

        except Exception as e:
            self.logger.error(f"Live Search failed: {e}")
            return None


# Convenience function
def get_ai_client() -> OpenRouterClient:
    """Get AI client singleton"""
    return OpenRouterClient()
