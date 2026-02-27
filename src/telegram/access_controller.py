"""
Unified access controller.

Responsibilities:
1. Generate Channel invite links (requires Bot approval)
2. Invite users to the appropriate Channel based on plan permissions and language
3. Kick users from all Channels when membership expires
4. Send new access invites on plan upgrade

Architecture (pure Channel mode):
- Channel: signal source + comment discussion
- Language-separated: zh/en independent
- Uses Telegram native Discussion feature

Security:
- creates_join_request=True: invite link leaks still require Bot approval
- Plan permission check: even with a link, users cannot join unauthorized targets
- Language match: users can only join Channels matching their language setting
"""

import logging
from typing import Dict, Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.core.config import settings

from .database.base import DatabaseManager
from .database.membership_plan_dao import MembershipPlanDAO


class AccessController:
    """Telegram unified access controller: pure Channel mode."""

    # Target type
    TARGET_TYPE_CHANNEL = 'CHANNEL'

    # Supported languages
    SUPPORTED_LANGUAGES = ['zh', 'en']

    # Target config map (4 language channels)
    # Structure: {(level_key, lang): config_attr_name}
    TARGET_CONFIG = {
        ('BASIC', 'zh'): 'TELEGRAM_CHANNEL_BASIC_ZH',
        ('BASIC', 'en'): 'TELEGRAM_CHANNEL_BASIC_EN',
        ('PREMIUM', 'zh'): 'TELEGRAM_CHANNEL_PREMIUM_ZH',
        ('PREMIUM', 'en'): 'TELEGRAM_CHANNEL_PREMIUM_EN',
    }

    # Button labels (language-specific)
    BUTTON_TEXT = {
        # Chinese channel buttons (shown in Chinese UI)
        ('BASIC', 'zh'): '📢 Basic Signal Channel',
        ('PREMIUM', 'zh'): '📢 Premium Signal Channel',
        # English channel buttons
        ('BASIC', 'en'): '📢 Basic Signal Channel',
        ('PREMIUM', 'en'): '📢 Premium Signal Channel',
    }

    def __init__(self, bot: Bot, db_manager: Optional[DatabaseManager] = None):
        """
        Initialize the access controller.

        Args:
            bot: Telegram Bot instance
            db_manager: Database manager (optional)
        """
        self.bot = bot
        self.db = db_manager or DatabaseManager()
        self.plan_dao = MembershipPlanDAO(self.db)
        self.logger = logging.getLogger(__name__)

        # Load target config (4 language channels)
        # Structure: {(level_key, lang): chat_id}
        self.targets: Dict[Tuple[str, str], int] = {}
        self._load_target_config()

    def _load_target_config(self):
        """Load Channel config from settings (pure Channel mode)."""
        for key, attr_name in self.TARGET_CONFIG.items():
            value = getattr(settings, attr_name, None)
            if value:
                try:
                    self.targets[key] = int(value)
                    self.logger.info(f"Channel config loaded: {key} -> {value}")
                except ValueError:
                    self.logger.error(f"Invalid Channel ID: {key}={value}")
            else:
                self.logger.debug(f"Channel not configured: {key}")

    def get_chat_id(self, level_key: str, lang: str) -> Optional[int]:
        """
        Get the Channel chat_id for the given level and language.

        Args:
            level_key: 'BASIC' or 'PREMIUM'
            lang: 'zh' | 'en'

        Returns:
            chat_id, or None if not configured
        """
        return self.targets.get((level_key, lang))

    def get_all_targets(self) -> Dict[Tuple[str, str], int]:
        """Return all configured Channels."""
        return self.targets.copy()

    def get_target_key_by_chat_id(self, chat_id: int) -> Optional[Tuple[str, str]]:
        """
        Reverse-lookup the target key for a given chat_id.

        Args:
            chat_id: Telegram chat ID

        Returns:
            (level_key, lang) tuple, or None if not found
        """
        for key, cid in self.targets.items():
            if cid == chat_id:
                return key
        return None

    async def send_invites(
        self,
        user_id: int,
        plan_code: str,
        lang: str = 'en',
        username: Optional[str] = None
    ) -> bool:
        """
        Send Channel invite links (pure Channel mode).

        Sends invites to the appropriate Channel based on plan permissions and language.
        Invite links use Join Request mode and require Bot approval.

        Args:
            user_id: Telegram user ID
            plan_code: Plan code (BASIC_M/BASIC_Y/PREMIUM_M/PREMIUM_Y)
            lang: User language preference ('zh' | 'en')
            username: Username (optional, for logging)

        Returns:
            True on success, False on failure
        """
        access_groups = self.plan_dao.get_access_groups_by_plan_code(plan_code)
        if not access_groups:
            self.logger.error(f"Cannot get plan permissions: plan_code={plan_code}")
            return False

        buttons = []

        for level_key in access_groups:
            button = await self._create_invite_button(user_id, level_key, plan_code, lang)
            if button:
                buttons.append([button])

        if not buttons:
            self.logger.error(f"Failed to generate any invite links: user_id={user_id}")
            return False

        try:
            if lang == 'zh':
                text = (
                    "✅ <b>Ignis Pass 已激活！</b>\n\n"
                    "点击下方按钮加入信号频道：\n\n"
                    "<i>你可以在频道中评论和讨论</i>"
                )
            else:
                text = (
                    "✅ <b>Ignis Pass Activated!</b>\n\n"
                    "Click to join signal channel:\n\n"
                    "<i>You can comment and discuss in the channel</i>"
                )

            await self.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )

            self.logger.info(
                f"Invite sent: user_id={user_id}, "
                f"plan_code={plan_code}, levels={access_groups}, lang={lang}"
            )
            return True

        except TelegramAPIError as e:
            self.logger.error(f"Failed to send invite: user_id={user_id}, error={e}")
            return False

    async def _create_invite_button(
        self,
        user_id: int,
        level_key: str,
        plan_code: str,
        lang: str
    ) -> Optional[InlineKeyboardButton]:
        """
        Create a Channel invite button.

        Args:
            user_id: User ID
            level_key: 'BASIC' or 'PREMIUM'
            plan_code: Plan code
            lang: Language ('zh' | 'en')

        Returns:
            InlineKeyboardButton, or None on failure
        """
        chat_id = self.get_chat_id(level_key, lang)
        if not chat_id:
            self.logger.warning(f"Channel not configured: {level_key}/{lang}")
            return None

        try:
            link = await self.bot.create_chat_invite_link(
                chat_id=chat_id,
                creates_join_request=True,
                name=f"C_{user_id}_{plan_code}_{lang}"
            )

            button_text = self.BUTTON_TEXT.get(
                (level_key, lang),
                f"Join {level_key} Channel"
            )

            self.logger.info(f"Invite link created: user_id={user_id}, channel={level_key}/{lang}")

            return InlineKeyboardButton(button_text, url=link.invite_link)

        except TelegramAPIError as e:
            self.logger.error(f"Failed to create invite link: user_id={user_id}, channel={level_key}/{lang}, error={e}")
            return None

    async def kick_user(self, user_id: int) -> Dict[str, bool]:
        """
        Kick user from all Channels.

        Uses ban + unban strategy:
        - ban: removes the user
        - unban: allows re-joining (e.g. after renewal)

        Args:
            user_id: Telegram user ID

        Returns:
            Per-channel kick results, e.g. {"PREMIUM_zh": True}
        """
        results = {}

        for (level_key, lang), chat_id in self.targets.items():
            key = f"{level_key}_{lang}"
            try:
                await self.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await self.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)

                results[key] = True
                self.logger.info(f"User kicked: user_id={user_id}, channel={key}")

            except TelegramAPIError as e:
                if 'user is not a member' in str(e).lower():
                    results[key] = True
                    self.logger.debug(f"User not in channel: user_id={user_id}, channel={key}")
                else:
                    results[key] = False
                    self.logger.warning(f"Kick failed: user_id={user_id}, channel={key}, error={e}")

        return results

    async def check_user_membership(self, user_id: int, level_key: str, lang: str) -> bool:
        """
        Check if a user is in the specified Channel.

        Args:
            user_id: Telegram user ID
            level_key: 'BASIC' or 'PREMIUM'
            lang: 'zh' | 'en'

        Returns:
            True if user is in Channel, False otherwise
        """
        chat_id = self.get_chat_id(level_key, lang)
        if not chat_id:
            return False

        try:
            member = await self.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            return member.status in ['member', 'administrator', 'creator']
        except TelegramAPIError:
            return False

    async def upgrade_access(
        self,
        user_id: int,
        new_plan_code: str,
        lang: str = 'en'
    ) -> bool:
        """
        Send new access invites when a user upgrades their plan.

        Args:
            user_id: Telegram user ID
            new_plan_code: New plan code
            lang: User language preference ('zh' | 'en')

        Returns:
            True on success, False on failure
        """
        new_levels = set(self.plan_dao.get_access_groups_by_plan_code(new_plan_code))
        missing_channels = []

        for level_key in new_levels:
            is_member = await self.check_user_membership(user_id, level_key, lang)
            if not is_member:
                missing_channels.append(level_key)

        if not missing_channels:
            self.logger.info(f"User already in all channels: user_id={user_id}")
            return True

        buttons = []
        for level_key in missing_channels:
            button = await self._create_invite_button(user_id, level_key, new_plan_code, lang)
            if button:
                buttons.append([button])

        if not buttons:
            return False

        try:
            if lang == 'zh':
                text = "🎉 <b>已升级！</b>\n\n新频道已解锁："
            else:
                text = "🎉 <b>Upgraded!</b>\n\nNew channel unlocked:"

            await self.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )
            return True

        except TelegramAPIError as e:
            self.logger.error(f"Failed to send upgrade invite: {e}")
            return False


# Backward-compatible alias
GroupController = AccessController
