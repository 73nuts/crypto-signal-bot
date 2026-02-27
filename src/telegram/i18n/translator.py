"""
i18n translator.

Loads language JSON files and provides t(key, lang) for string lookup.
Supports nested keys (e.g. 'menu.subscribe') and format kwargs.
Falls back to English when a key is missing in the requested language.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ['en', 'zh']
DEFAULT_LANGUAGE = 'en'

I18N_DIR = Path(__file__).parent

_translations: Dict[str, Dict[str, Any]] = {}
_user_languages: Dict[int, str] = {}


def _load_translations() -> None:
    """Load all language files."""
    global _translations

    for lang in SUPPORTED_LANGUAGES:
        file_path = I18N_DIR / f"{lang}.json"
        try:
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    _translations[lang] = json.load(f)
                logger.debug(f"Loaded language file: {lang}.json")
            else:
                logger.warning(f"Language file not found: {file_path}")
                _translations[lang] = {}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse language file: {file_path}, error={e}")
            _translations[lang] = {}


def _get_nested_value(data: Dict[str, Any], key: str) -> Optional[str]:
    """Resolve a dot-separated key in a nested dict. Returns the string value or None."""
    keys = key.split('.')
    current = data

    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            return None

    return current if isinstance(current, str) else None


def t(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """
    Look up a translation key.

    Falls back to English when the key is missing in the requested language.
    Returns the key itself if not found in any language.

    Examples:
        t('menu.subscribe', 'en')  -> "Subscribe"
        t('menu.subscribe', 'zh')  -> "订阅"
        t('renewal.days_left', 'en', days=3)  -> "3 days left"
    """
    if not _translations:
        _load_translations()

    lang = lang.lower()[:2] if lang else DEFAULT_LANGUAGE
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE

    translations = _translations.get(lang, {})
    value = _get_nested_value(translations, key)

    if value is None and lang != DEFAULT_LANGUAGE:
        translations = _translations.get(DEFAULT_LANGUAGE, {})
        value = _get_nested_value(translations, key)

    if value is None:
        logger.warning(f"Translation key not found: {key} (lang={lang})")
        return key

    if kwargs:
        try:
            value = value.format(**kwargs)
        except KeyError as e:
            logger.warning(f"Translation parameter missing: key={key}, missing={e}")

    return value


def get_user_language(telegram_id: int) -> str:
    """Return the cached language preference for a user. Defaults to 'en'."""
    return _user_languages.get(telegram_id, DEFAULT_LANGUAGE)


def get_cached_language(telegram_id: int) -> Optional[str]:
    """Return cached language if present, None if not yet cached."""
    return _user_languages.get(telegram_id)


def set_user_language(telegram_id: int, lang: str) -> None:
    """Cache a user's language preference in memory. Persist via DAO for durability."""
    lang = lang.lower()[:2] if lang else DEFAULT_LANGUAGE
    if lang in SUPPORTED_LANGUAGES:
        _user_languages[telegram_id] = lang
        logger.debug(f"User language set: telegram_id={telegram_id}, lang={lang}")


def reload_translations() -> None:
    """Reload translation files (hot reload)."""
    global _translations
    _translations = {}
    _load_translations()
    logger.info("Translation files reloaded")
