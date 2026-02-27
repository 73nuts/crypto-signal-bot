"""
i18n package. Provides multi-language support (en / zh).
"""

from .translator import SUPPORTED_LANGUAGES, get_cached_language, get_user_language, set_user_language, t

__all__ = [
    't',
    'get_user_language',
    'get_cached_language',
    'set_user_language',
    'SUPPORTED_LANGUAGES',
]
