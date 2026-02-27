"""
I18nMiddleware - internationalization middleware.

Features:
1. Extract user ID from messages/callbacks
2. Query user language preference (from database)
3. Auto-detect Telegram client language for new users
4. Inject lang and t into handler context

Injected into data:
- lang: str - user language code ('en' or 'zh')
- t: Callable - translation function t(key, **kwargs)

Usage:
    @router.message(Command("start"))
    async def cmd_start(message: Message, lang: str, t: Callable) -> None:
        await message.answer(t('start.welcome'))
"""
from functools import partial
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import Update

from src.core.structured_logger import get_logger
from src.telegram.i18n import get_cached_language, set_user_language
from src.telegram.i18n import t as translate_func

from ..utils.async_wrapper import run_sync

logger = get_logger(__name__)

# Default language
DEFAULT_LANGUAGE = 'en'


class I18nMiddleware(BaseMiddleware):
    """Internationalization middleware."""

    def __init__(self):
        super().__init__()
        self._member_service = None

    def _get_member_service(self):
        """Lazy-load MemberService (avoid circular imports)."""
        if self._member_service is None:
            from ..utils.db_provider import get_member_service
            self._member_service = get_member_service()
        return self._member_service

    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any]
    ) -> Any:
        """
        Process request and inject language info.

        Flow:
        1. Extract user ID
        2. Query/detect language preference
        3. Inject lang and t into data
        4. Call next handler
        """
        user_id, user = self._extract_user_info(event)

        if user_id:
            lang = await self._get_or_detect_language(user_id, user)
        else:
            lang = DEFAULT_LANGUAGE

        data['lang'] = lang
        data['t'] = partial(translate_func, lang=lang)

        return await handler(event, data)

    def _extract_user_info(self, event: Update) -> tuple:
        """
        Extract user info from Update.

        Returns:
            (user_id, user_object) or (None, None)
        """
        user = None
        user_id = None

        if event.message and event.message.from_user:
            user = event.message.from_user
            user_id = user.id
        elif event.callback_query and event.callback_query.from_user:
            user = event.callback_query.from_user
            user_id = user.id
        elif event.inline_query and event.inline_query.from_user:
            user = event.inline_query.from_user
            user_id = user.id
        elif event.chat_join_request and event.chat_join_request.from_user:
            user = event.chat_join_request.from_user
            user_id = user.id

        return user_id, user

    async def _get_or_detect_language(
        self,
        telegram_id: int,
        user: Optional[Any]
    ) -> str:
        """
        Get or detect user language.

        Flow:
        1. Query database for language setting
        2. If not set, detect from Telegram client locale
        3. Save to database and in-memory cache

        Args:
            telegram_id: Telegram user ID
            user: aiogram User object

        Returns:
            Language code
        """
        try:
            # 1. Check in-memory cache first (avoids DB call on every request)
            cached = get_cached_language(telegram_id)
            if cached is not None:
                return cached

            member_service = self._get_member_service()

            # 2. Query database (only for users not yet in cache)
            existing_lang = await run_sync(member_service.get_language, telegram_id)

            if existing_lang:
                set_user_language(telegram_id, existing_lang)
                return existing_lang

            # 2. New user: detect from Telegram client locale
            detected_lang = self._detect_language_from_user(user)

            # 3. Save to database
            try:
                await run_sync(member_service.update_language, telegram_id, detected_lang)
            except Exception as e:
                # Save failure must not block flow
                logger.warning(f"Failed to save language preference: {e}")

            # 4. Sync to in-memory cache
            set_user_language(telegram_id, detected_lang)

            return detected_lang

        except Exception as e:
            logger.warning(f"Language preference fetch failed: telegram_id={telegram_id}, error={e}")
            return DEFAULT_LANGUAGE

    def _detect_language_from_user(self, user: Optional[Any]) -> str:
        """
        Detect language from Telegram user info.

        Mapping:
        - zh, zh-hans, zh-hant, zh-cn, zh-tw -> 'zh'
        - all others -> 'en'

        Args:
            user: aiogram User object

        Returns:
            Language code
        """
        if user is None:
            return DEFAULT_LANGUAGE

        lang_code = getattr(user, 'language_code', None) or 'en'
        lang_code = lang_code.lower()

        if lang_code.startswith('zh'):
            return 'zh'

        return DEFAULT_LANGUAGE
