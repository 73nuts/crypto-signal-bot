"""
Internationalization (i18n) module.

Language is controlled via environment variables:
- LANGUAGE: Language code (zh_CN/en_US), default zh_CN
- BILINGUAL: Enable bilingual mode (true/false), default false

Usage:
    from src.i18n import t, tb, get_language, is_bilingual

    # Single-language translation
    text = t('status.oversold')  # returns 'Oversold' or '超卖区'

    # Bilingual translation
    text = tb('status.oversold')  # returns '超卖区 / Oversold'

    # Check bilingual mode
    if is_bilingual():
        text = tb(key)
    else:
        text = t(key)
"""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# Supported languages
SUPPORTED_LANGUAGES = ['zh_CN', 'en_US']
DEFAULT_LANGUAGE = 'zh_CN'
BILINGUAL_SEPARATOR = ' / '

# Translation cache
_translations: dict = {}
_translations_all: Dict[str, dict] = {}  # All-language cache
_current_language: str = ''
_bilingual_mode: Optional[bool] = None


def _load_language(lang: str) -> dict:
    """Load a language pack.

    Args:
        lang: Language code (zh_CN, en_US)

    Returns:
        Translations dict
    """
    i18n_dir = Path(__file__).parent
    lang_file = i18n_dir / f'{lang}.yaml'

    if not lang_file.exists():
        # Fall back to default language
        lang_file = i18n_dir / f'{DEFAULT_LANGUAGE}.yaml'

    if not lang_file.exists():
        return {}

    with open(lang_file, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def get_language() -> str:
    """Get current language setting.

    Returns:
        Language code
    """
    global _current_language

    if not _current_language:
        # Deferred import to avoid circular dependency
        from src.core.config import settings
        _current_language = settings.LANGUAGE
        if _current_language not in SUPPORTED_LANGUAGES:
            _current_language = DEFAULT_LANGUAGE

    return _current_language


def set_language(lang: str) -> None:
    """Set current language.

    Args:
        lang: Language code
    """
    global _current_language, _translations

    if lang in SUPPORTED_LANGUAGES:
        _current_language = lang
        _translations = {}  # Clear cache to force reload


def _get_translations() -> dict:
    """Get translations dict for the current language.

    Returns:
        Translations dict
    """
    global _translations

    lang = get_language()

    if not _translations:
        _translations = _load_language(lang)

    return _translations


def t(key: str, default: Optional[str] = None, **kwargs) -> str:
    """Get translated text.

    Args:
        key: Translation key, supports dot notation (e.g. 'status.oversold')
        default: Fallback value if key is not found
        **kwargs: Format parameters

    Returns:
        Translated text
    """
    translations = _get_translations()

    # Support dot-separated keys
    keys = key.split('.')
    value: Any = translations

    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            # Key not found; return default or the key itself
            return default if default is not None else key

    if not isinstance(value, str):
        return default if default is not None else key

    # Support format parameters
    if kwargs:
        try:
            value = value.format(**kwargs)
        except (KeyError, ValueError):
            pass

    return value


def reload_translations() -> None:
    """Reload translations (call after switching language)."""
    global _translations, _translations_all
    _translations = {}
    _translations_all = {}


def is_bilingual() -> bool:
    """Check whether bilingual mode is enabled.

    Returns:
        True if bilingual mode is enabled
    """
    global _bilingual_mode

    if _bilingual_mode is None:
        # Deferred import to avoid circular dependency
        from src.core.config import settings
        _bilingual_mode = settings.BILINGUAL

    return _bilingual_mode


def set_bilingual(enabled: bool) -> None:
    """Set bilingual mode.

    Args:
        enabled: Whether to enable bilingual output
    """
    global _bilingual_mode
    _bilingual_mode = enabled


def _get_all_translations() -> Dict[str, dict]:
    """Get translations dict for all languages.

    Returns:
        {lang_code: translations_dict}
    """
    global _translations_all

    if not _translations_all:
        for lang in SUPPORTED_LANGUAGES:
            _translations_all[lang] = _load_language(lang)

    return _translations_all


def _get_value_by_key(translations: dict, key: str) -> Optional[str]:
    """Look up a value in a translations dict.

    Args:
        translations: Translations dict
        key: Dot-separated key

    Returns:
        Translation value or None
    """
    keys = key.split('.')
    value: Any = translations

    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            return None

    return value if isinstance(value, str) else None


def tb(key: str, default: Optional[str] = None, **kwargs) -> str:
    """Get bilingual translation text (Chinese / English).

    Args:
        key: Translation key, supports dot notation (e.g. 'status.oversold')
        default: Fallback value
        **kwargs: Format parameters

    Returns:
        Bilingual text, e.g. '超卖区 / Oversold'
    """
    all_trans = _get_all_translations()

    zh_value = _get_value_by_key(all_trans.get('zh_CN', {}), key)
    en_value = _get_value_by_key(all_trans.get('en_US', {}), key)

    # Apply format parameters
    if kwargs:
        try:
            if zh_value:
                zh_value = zh_value.format(**kwargs)
            if en_value:
                en_value = en_value.format(**kwargs)
        except (KeyError, ValueError):
            pass

    # Build bilingual text
    if zh_value and en_value:
        if zh_value == en_value:
            return zh_value  # Skip duplication when both sides are identical
        return f"{zh_value}{BILINGUAL_SEPARATOR}{en_value}"
    elif zh_value:
        return zh_value
    elif en_value:
        return en_value
    else:
        return default if default is not None else key


def tt(key: str, default: Optional[str] = None, **kwargs) -> str:
    """Smart translate: automatically picks single-language or bilingual based on BILINGUAL env var.

    Args:
        key: Translation key
        default: Fallback value
        **kwargs: Format parameters

    Returns:
        Translated text (single-language or bilingual)
    """
    if is_bilingual():
        return tb(key, default, **kwargs)
    else:
        return t(key, default, **kwargs)
