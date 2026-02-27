"""
Unified configuration manager (SecretsManager)

Security hardening module based on pydantic-settings:
1. Single source of truth: all config loaded from .env
2. Tiered management: L1 (funds) / L2 (channels) / L3 (general)
3. Sensitive masking: SecretStr hides values when printed
4. Startup validation: fails to start when L1 credentials are missing

Usage:
    from src.core.config import settings

    # Get regular config
    host = settings.MYSQL_HOST

    # Get sensitive config (requires explicit call)
    api_key = settings.BINANCE_API_KEY.get_secret_value()
"""

import logging
from functools import lru_cache
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    Ignis system unified configuration

    Credential tiers:
    - L1 (Critical): funds-related; missing causes startup failure
    - L2 (Sensitive): channel credentials; missing causes feature degradation
    - L3 (Config): general config with defaults
    """

    model_config = SettingsConfigDict(
        # Load order: .env -> .env.telegram -> .env.local (later overrides earlier)
        # .env.local is for local dev overrides and should not be committed to git
        env_file=('.env', '.env.telegram', '.env.local'),
        env_file_encoding='utf-8',
        extra='ignore',  # ignore undefined variables in .env
        case_sensitive=True,
    )

    # ========================================
    # L1 - Critical: funds-related credentials
    # ========================================

    # Binance API (mainnet)
    BINANCE_API_KEY: Optional[SecretStr] = Field(
        default=None,
        description="Binance mainnet API Key"
    )
    BINANCE_API_SECRET: Optional[SecretStr] = Field(
        default=None,
        description="Binance mainnet API Secret"
    )

    # Binance API (testnet)
    BINANCE_TESTNET_API_KEY: Optional[SecretStr] = Field(
        default=None,
        description="Binance testnet API Key"
    )
    BINANCE_TESTNET_API_SECRET: Optional[SecretStr] = Field(
        default=None,
        description="Binance testnet API Secret"
    )

    # HD wallet (BSC payments)
    HD_WALLET_MNEMONIC: Optional[SecretStr] = Field(
        default=None,
        description="HD wallet mnemonic (12 words)"
    )
    HD_MASTER_ADDRESS: Optional[str] = Field(
        default=None,
        description="Master wallet address (for verification)"
    )

    # Order signing key
    ORDER_SECRET_KEY: Optional[SecretStr] = Field(
        default=None,
        description="Order HMAC signing key"
    )

    # ========================================
    # L2 - Sensitive: channel credentials
    # ========================================

    # Telegram
    TELEGRAM_BOT_TOKEN: Optional[SecretStr] = Field(
        default=None,
        description="Telegram Bot Token"
    )
    ADMIN_TELEGRAM_ID: Optional[int] = Field(
        default=None,
        description="Admin Telegram ID"
    )

    # VIP channels (language-separated, 4 channels)
    # Legacy (backward compatible)
    TELEGRAM_CHANNEL_BASIC: Optional[str] = Field(
        default=None,
        description="[Legacy] Basic signal channel ID"
    )
    TELEGRAM_CHANNEL_PREMIUM: Optional[str] = Field(
        default=None,
        description="[Legacy] Premium signal channel ID"
    )

    # Language-specific channels
    TELEGRAM_CHANNEL_BASIC_ZH: Optional[str] = Field(
        default=None,
        description="Basic Chinese signal channel ID"
    )
    TELEGRAM_CHANNEL_BASIC_EN: Optional[str] = Field(
        default=None,
        description="Basic English signal channel ID"
    )
    TELEGRAM_CHANNEL_PREMIUM_ZH: Optional[str] = Field(
        default=None,
        description="Premium Chinese signal channel ID"
    )
    TELEGRAM_CHANNEL_PREMIUM_EN: Optional[str] = Field(
        default=None,
        description="Premium English signal channel ID"
    )

    TELEGRAM_PERSONAL_CHAT_ID: Optional[str] = Field(
        default=None,
        description="Personal chat ID"
    )
    LISA_TELEGRAM_ID: Optional[int] = Field(
        default=None,
        description="Support Lisa's Telegram ID (for feedback notifications)"
    )

    # BSCScan
    BSCSCAN_API_KEY: Optional[SecretStr] = Field(
        default=None,
        description="BSCScan API Key"
    )

    # OpenRouter (AI)
    OPENROUTER_API_KEY: Optional[SecretStr] = Field(
        default=None,
        description="OpenRouter API Key"
    )

    # Email
    EMAIL_ENABLED: bool = Field(default=False)
    EMAIL_SMTP_SERVER: str = Field(default="smtp.gmail.com")
    EMAIL_SMTP_PORT: int = Field(default=587)
    EMAIL_USERNAME: Optional[str] = Field(default=None)
    EMAIL_PASSWORD: Optional[SecretStr] = Field(default=None)
    EMAIL_TO: Optional[str] = Field(default=None)

    # WeChat (Server Chan)
    WECHAT_ENABLED: bool = Field(default=False)
    SERVER_CHAN_KEY: Optional[SecretStr] = Field(default=None)

    # Webhook
    WEBHOOK_SECRET_KEY: Optional[SecretStr] = Field(
        default=None,
        description="Webhook verification key"
    )

    # Gmail (for policy monitoring)
    GMAIL_ADDRESS: Optional[str] = Field(default=None)
    GMAIL_APP_PASSWORD: Optional[SecretStr] = Field(default=None)

    # Grok AI (backup)
    GROK_API_KEY: Optional[SecretStr] = Field(default=None)
    GROK_MODEL: str = Field(default="grok-4-fast")

    # ========================================
    # L3 - Config: general config (with defaults)
    # ========================================

    # MySQL
    MYSQL_HOST: str = Field(default="localhost")
    MYSQL_PORT: int = Field(default=3306)
    MYSQL_USER: str = Field(default="root")
    MYSQL_PASSWORD: Optional[SecretStr] = Field(default=None)
    MYSQL_DATABASE: str = Field(default="crypto_signals")

    # Redis
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379)
    REDIS_PASSWORD: Optional[SecretStr] = Field(default=None)

    # BSC RPC
    BSC_RPC_URL: str = Field(
        default="https://bsc-dataseed.binance.org/",
        description="BSC RPC node URL"
    )
    BSC_USDT_CONTRACT: str = Field(
        default="0x55d398326f99059fF775485246999027B3197955",
        description="BSC USDT contract address"
    )

    # Flask Dashboard
    FLASK_SECRET_KEY: Optional[SecretStr] = Field(default=None)
    DASHBOARD_API_KEY: Optional[SecretStr] = Field(default=None)
    DASHBOARD_HOST: str = Field(default="0.0.0.0")
    DASHBOARD_PORT: int = Field(default=5000)
    FLASK_DEBUG: bool = Field(default=False)

    # System identity
    ENVIRONMENT_PREFIX: str = Field(default="[Ignis]")
    SIGNAL_NAME: str = Field(default="Signal")

    # Internationalization
    LANGUAGE: str = Field(default="zh_CN")
    BILINGUAL: bool = Field(default=False)

    # Order config
    ORDER_EXPIRE_MINUTES: int = Field(default=30)

    # ========================================
    # Trader Program & Alpha pricing
    # ========================================

    # Alpha pricing toggle
    ALPHA_PRICING_ENABLED: bool = Field(
        default=True,
        description="Whether to enable Alpha early-bird pricing"
    )
    ALPHA_LIMIT: int = Field(
        default=50,
        description="Alpha slot limit"
    )
    ALPHA_DISCOUNT: float = Field(
        default=0.5,
        description="Alpha discount (0.5 = 50% off)"
    )
    ALPHA_BONUS_DAYS: int = Field(
        default=7,
        description="Bonus days for first 50 Alpha Premium members"
    )
    ALPHA_RENEWAL_WINDOW_DAYS: int = Field(
        default=30,
        description="Alpha renewal window in days (days after expiry that Alpha price still applies)"
    )

    # Trader Program discount
    TRADER_DISCOUNT: float = Field(
        default=0.7,
        description="Trader Program discount (0.7 = 30% off)"
    )

    # Binance referral link
    BINANCE_REFERRAL_URL: str = Field(
        default="",
        description="Binance referral registration link"
    )

    # ========================================
    # Helper methods
    # ========================================

    def get_secret(self, key: str, default: str = '') -> str:
        """
        Safely retrieve a sensitive config value.

        Args:
            key: Config key name
            default: Default value

        Returns:
            Config value string
        """
        value = getattr(self, key, None)
        if value is None:
            return default
        if isinstance(value, SecretStr):
            return value.get_secret_value()
        return str(value)

    def get_mysql_config(self) -> dict:
        """Get MySQL connection config"""
        return {
            'host': self.MYSQL_HOST,
            'port': self.MYSQL_PORT,
            'user': self.MYSQL_USER,
            'password': self.get_secret('MYSQL_PASSWORD', ''),
            'database': self.MYSQL_DATABASE,
        }

    def get_redis_config(self) -> dict:
        """Get Redis connection config"""
        config = {
            'host': self.REDIS_HOST,
            'port': self.REDIS_PORT,
        }
        if self.REDIS_PASSWORD:
            config['password'] = self.REDIS_PASSWORD.get_secret_value()
        return config

    def get_email_config(self) -> dict:
        """Get email config (compatible with Notifier/EmailSender)"""
        return {
            'enabled': self.EMAIL_ENABLED,
            'smtp_server': self.EMAIL_SMTP_SERVER,
            'smtp_port': self.EMAIL_SMTP_PORT,
            'username': self.EMAIL_USERNAME,
            'password': self.get_secret('EMAIL_PASSWORD', ''),
            'to_email': self.EMAIL_TO,
        }

    def get_binance_config(self, testnet: bool = True) -> dict:
        """Get Binance config (compatible with ExchangeClient)"""
        api_key, api_secret = self.get_binance_keys(testnet)
        return {
            'api_key': api_key,
            'api_secret': api_secret,
            'default_type': 'future',
            'testnet': testnet,
        }

    def get_binance_keys(self, testnet: bool = True) -> tuple:
        """
        Get Binance API key pair.

        Args:
            testnet: Whether to use testnet

        Returns:
            (api_key, api_secret) tuple
        """
        if testnet:
            key = self.get_secret('BINANCE_TESTNET_API_KEY')
            secret = self.get_secret('BINANCE_TESTNET_API_SECRET')
        else:
            key = self.get_secret('BINANCE_API_KEY')
            secret = self.get_secret('BINANCE_API_SECRET')
        return (key, secret)

    def validate_l1_credentials(self, mode: str = 'trading') -> list:
        """
        Validate L1 credential completeness.

        Args:
            mode: Operation mode
                - 'trading': requires Binance API
                - 'payment': requires HD wallet
                - 'all': validate all

        Returns:
            List of missing credentials
        """
        missing = []

        if mode in ('trading', 'all'):
            # At least one of testnet or mainnet must be configured
            has_testnet = (
                self.BINANCE_TESTNET_API_KEY and
                self.BINANCE_TESTNET_API_SECRET
            )
            has_mainnet = (
                self.BINANCE_API_KEY and
                self.BINANCE_API_SECRET
            )
            if not has_testnet and not has_mainnet:
                missing.append('BINANCE_API_KEY/SECRET (configure at least testnet or mainnet)')

        if mode in ('payment', 'all'):
            if not self.HD_WALLET_MNEMONIC:
                missing.append('HD_WALLET_MNEMONIC')
            if not self.ORDER_SECRET_KEY:
                missing.append('ORDER_SECRET_KEY')

        return missing

    def validate_l2_credentials(self) -> dict:
        """
        Validate L2 credential status.

        Returns:
            Dict of channel availability status
        """
        return {
            'telegram': bool(self.TELEGRAM_BOT_TOKEN),
            'bscscan': bool(self.BSCSCAN_API_KEY),
            'email': self.EMAIL_ENABLED and bool(self.EMAIL_PASSWORD),
            'wechat': self.WECHAT_ENABLED and bool(self.SERVER_CHAN_KEY),
            'openrouter': bool(self.OPENROUTER_API_KEY),
        }

    def get_telegram_channel(self, level: str, lang: str = None) -> Optional[str]:
        """
        Get Telegram channel ID for a given level and language.

        Args:
            level: 'BASIC' | 'PREMIUM'
            lang: 'zh' | 'en' (optional; returns legacy config if not specified)

        Returns:
            Channel ID string, or None if not configured
        """
        if lang:
            # Language-specific channels
            channel_map = {
                ('BASIC', 'zh'): self.TELEGRAM_CHANNEL_BASIC_ZH,
                ('BASIC', 'en'): self.TELEGRAM_CHANNEL_BASIC_EN,
                ('PREMIUM', 'zh'): self.TELEGRAM_CHANNEL_PREMIUM_ZH,
                ('PREMIUM', 'en'): self.TELEGRAM_CHANNEL_PREMIUM_EN,
            }
            return channel_map.get((level, lang))

        # Legacy fallback
        if level == 'BASIC':
            return self.TELEGRAM_CHANNEL_BASIC
        elif level == 'PREMIUM':
            return self.TELEGRAM_CHANNEL_PREMIUM
        return None

    def get_all_telegram_channels(self) -> dict:
        """
        Get all VIP channel configs (4 language channels).

        Returns:
            {
                'BASIC_ZH': channel ID or None,
                'BASIC_EN': channel ID or None,
                'PREMIUM_ZH': channel ID or None,
                'PREMIUM_EN': channel ID or None,
            }
        """
        return {
            'BASIC_ZH': self.TELEGRAM_CHANNEL_BASIC_ZH,
            'BASIC_EN': self.TELEGRAM_CHANNEL_BASIC_EN,
            'PREMIUM_ZH': self.TELEGRAM_CHANNEL_PREMIUM_ZH,
            'PREMIUM_EN': self.TELEGRAM_CHANNEL_PREMIUM_EN,
        }

    def get_channels_by_level(self, level: str) -> dict:
        """
        Get all language channels for a given level.

        Args:
            level: 'BASIC' | 'PREMIUM'

        Returns:
            {'zh': channel ID or None, 'en': channel ID or None}
        """
        if level == 'BASIC':
            return {
                'zh': self.TELEGRAM_CHANNEL_BASIC_ZH,
                'en': self.TELEGRAM_CHANNEL_BASIC_EN,
            }
        elif level == 'PREMIUM':
            return {
                'zh': self.TELEGRAM_CHANNEL_PREMIUM_ZH,
                'en': self.TELEGRAM_CHANNEL_PREMIUM_EN,
            }
        return {}

    def get_signal_target(self, level: str, lang: str = None) -> Optional[str]:
        """
        Get signal push target (supports language dimension).

        Args:
            level: 'BASIC' | 'PREMIUM'
            lang: 'zh' | 'en' (optional)

        Returns:
            Target ID; prefers language channel, falls back to legacy
        """
        if lang:
            channel = self.get_telegram_channel(level, lang)
            if channel:
                return channel
        # Legacy fallback
        return self.get_telegram_channel(level)

    def get_all_signal_targets(self) -> dict:
        """
        Get all signal push targets (4 language channels).

        Returns:
            {
                'BASIC_ZH': target ID or None,
                'BASIC_EN': target ID or None,
                'PREMIUM_ZH': target ID or None,
                'PREMIUM_EN': target ID or None,
            }
        """
        return {
            'BASIC_ZH': self.get_signal_target('BASIC', 'zh'),
            'BASIC_EN': self.get_signal_target('BASIC', 'en'),
            'PREMIUM_ZH': self.get_signal_target('PREMIUM', 'zh'),
            'PREMIUM_EN': self.get_signal_target('PREMIUM', 'en'),
        }

    def get_signal_targets_by_level(self, level: str) -> dict:
        """
        Get all language signal push targets for a given level.

        Args:
            level: 'BASIC' | 'PREMIUM'

        Returns:
            {'zh': target ID or None, 'en': target ID or None}
        """
        return {
            'zh': self.get_signal_target(level, 'zh'),
            'en': self.get_signal_target(level, 'en'),
        }


@lru_cache()
def get_settings() -> Settings:
    """
    Get settings singleton (cached).

    Returns:
        Settings instance
    """
    return Settings()


# Global settings singleton
settings = get_settings()


def init_settings(validate_mode: Optional[str] = None) -> Settings:
    """
    Initialize and validate settings.

    Args:
        validate_mode: Validation mode ('trading', 'payment', 'all', None)

    Returns:
        Settings instance

    Raises:
        RuntimeError: When L1 credentials are missing
    """
    s = get_settings()

    if validate_mode:
        missing = s.validate_l1_credentials(validate_mode)
        if missing:
            raise RuntimeError(
                f"L1 credentials missing, system cannot start: {', '.join(missing)}"
            )

    # Log L2 credential status
    l2_status = s.validate_l2_credentials()
    enabled = [k for k, v in l2_status.items() if v]
    disabled = [k for k, v in l2_status.items() if not v]

    logger.info(f"Config loaded - enabled channels: {enabled}")
    if disabled:
        logger.warning(f"Unconfigured channels: {disabled}")

    return s
