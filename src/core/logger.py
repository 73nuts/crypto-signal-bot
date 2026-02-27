"""
Global log sanitization module

Security hardening module that implements:
1. Global logging Filter - automatically sanitizes sensitive information
2. Coverage: API keys, passwords, tokens, mnemonics, private keys, addresses, etc.
3. Zero-intrusion - takes effect automatically after mount; no changes to business code needed

Usage:
    from src.core.logger import setup_logging, get_logger

    # Initialize at application startup
    setup_logging()

    # Get a logger with sanitization
    logger = get_logger(__name__)
"""

import logging
import re
from typing import Callable, Dict, Union


class SensitiveFilter(logging.Filter):
    """
    Sensitive information sanitization filter

    Automatically detects and replaces sensitive information in logs:
    - API keys, passwords, tokens
    - Mnemonics, private keys
    - Phone numbers, email addresses
    - Blockchain addresses (preserves prefix and suffix)
    """

    # Sanitization rules: (regex pattern, replacement string or function)
    # Order matters: more specific rules come first
    PATTERNS: Dict[str, Union[str, Callable]] = {
        # API keys and tokens (various formats)
        r'(api[_-]?key\s*[=:]\s*)["\']?([a-zA-Z0-9\-_]{20,})["\']?': r'\1******',
        r'(api[_-]?secret\s*[=:]\s*)["\']?([a-zA-Z0-9\-_]{20,})["\']?': r'\1******',
        r'(token\s*[=:]\s*)["\']?([a-zA-Z0-9\-_:]{20,})["\']?': r'\1******',
        r'(secret\s*[=:]\s*)["\']?([a-zA-Z0-9\-_]{16,})["\']?': r'\1******',

        # Bearer token
        r'(Bearer\s+)[a-zA-Z0-9\-_.]+': r'\1******',

        # Password fields
        r'(password\s*[=:]\s*)["\']?[^"\'\s&]+["\']?': r'\1******',
        r'(passwd\s*[=:]\s*)["\']?[^"\'\s&]+["\']?': r'\1******',
        r'(pwd\s*[=:]\s*)["\']?[^"\'\s&]+["\']?': r'\1******',

        # Mnemonics (12-24 English words)
        r'(mnemonic\s*[=:]\s*)["\']?([a-z]+\s+){11,23}[a-z]+["\']?': r'\1******',

        # Private key (64-char hex without 0x prefix; 0x prefix handled by PRIVATE_KEY_PATTERN)
        r'(private[_-]?key\s*[=:]\s*)["\']?[a-fA-F0-9]{64}["\']?': r'\1******',

        # Telegram Bot Token format: digits:alphanumeric
        r'\d{8,12}:[a-zA-Z0-9_-]{35}': '******:******[BOT_TOKEN]',

        # BSCScan API Key format
        r'[A-Z0-9]{25,35}(?=.*[A-Z])(?=.*[0-9])': '******[API_KEY]',

        # URL key parameters
        r'(apikey=)[^&\s]+': r'\1******',
        r'(key=)[^&\s]+': r'\1******',
        r'(secret=)[^&\s]+': r'\1******',

        # Chinese mainland phone numbers
        r'1[3-9]\d{9}': lambda m: m.group()[:3] + '****' + m.group()[-4:],

        # Email addresses
        r'([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)':
            lambda m: m.group(1)[:2] + '***@' + m.group(2),

        # Sensitive fields in JSON
        r'"(api_key|apiKey|secret|password|token|mnemonic)"\s*:\s*"[^"]+?"':
            r'"\1": "******"',
    }

    # Private key special handling (64-char hex, fully sanitized)
    PRIVATE_KEY_PATTERN = re.compile(r'0x[a-fA-F0-9]{64}')

    # Blockchain address special handling (40-char hex, preserves prefix and suffix for debugging)
    ADDRESS_PATTERN = re.compile(r'0x[a-fA-F0-9]{40}')

    def __init__(self, name: str = ''):
        super().__init__(name)
        # Pre-compile regex patterns
        self._compiled_patterns = [
            (re.compile(pattern, re.IGNORECASE), replacement)
            for pattern, replacement in self.PATTERNS.items()
        ]

    def _mask_address(self, match: re.Match) -> str:
        """Blockchain address masking: keep first 6 and last 4 characters"""
        addr = match.group()
        return f"{addr[:6]}...{addr[-4:]}"

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Filter log records, replacing sensitive information.

        Args:
            record: Log record

        Returns:
            True (always allows the log through; only modifies content)
        """
        # Process message
        if record.msg:
            record.msg = self._sanitize(str(record.msg))

        # Process args
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._sanitize(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._sanitize(str(arg)) if isinstance(arg, str) else arg
                    for arg in record.args
                )

        return True

    def _sanitize(self, text: str) -> str:
        """
        Sanitize text by replacing sensitive information.

        Args:
            text: Original text

        Returns:
            Sanitized text
        """
        if not text:
            return text

        result = text

        # Handle private keys first (64-char hex, fully sanitized)
        result = self.PRIVATE_KEY_PATTERN.sub('0x******[PRIVATE_KEY]', result)

        # Handle blockchain addresses (40-char hex, preserve prefix/suffix for debugging)
        result = self.ADDRESS_PATTERN.sub(self._mask_address, result)

        # Apply other sanitization rules
        for pattern, replacement in self._compiled_patterns:
            if callable(replacement):
                result = pattern.sub(replacement, result)
            else:
                result = pattern.sub(replacement, result)

        return result


class AuditLogger:
    """
    Audit logger

    Records security-related operation audit logs, separate from business logs.
    Outputs in JSON format for downstream analysis.
    """

    def __init__(self, log_file: str = 'logs/audit.log'):
        import json
        from datetime import datetime, timezone

        self.log_file = log_file
        self._json = json
        self._datetime = datetime
        self._timezone = timezone

        # Ensure log directory exists
        import os
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

    def log(
        self,
        action: str,
        user_id: str = None,
        details: dict = None,
        result: str = 'success'
    ):
        """
        Record an audit log entry.

        Args:
            action: Operation type (e.g. 'login', 'payment', 'api_call')
            user_id: User identifier
            details: Detailed information
            result: Operation result
        """
        entry = {
            'timestamp': self._datetime.now(self._timezone.utc).isoformat().replace('+00:00', 'Z'),
            'action': action,
            'user_id': user_id,
            'result': result,
            'details': details or {},
        }

        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(self._json.dumps(entry, ensure_ascii=False) + '\n')


def setup_logging(
    level: int = logging.INFO,
    format_string: str = None,
    enable_sanitizer: bool = True
):
    """
    Initialize global logging configuration.

    Args:
        level: Log level
        format_string: Log format string
        enable_sanitizer: Whether to enable the sanitization filter
    """
    if format_string is None:
        format_string = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Configure root logger
    logging.basicConfig(
        level=level,
        format=format_string,
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Mount sanitization filter on all handlers
    if enable_sanitizer:
        sanitizer = SensitiveFilter()
        root_logger = logging.getLogger()

        for handler in root_logger.handlers:
            handler.addFilter(sanitizer)

        # Also add to the root logger for future handlers
        root_logger.addFilter(sanitizer)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with sanitization enabled.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)

    # Ensure sanitization filter is present
    has_sanitizer = any(
        isinstance(f, SensitiveFilter)
        for f in logger.filters
    )

    if not has_sanitizer:
        logger.addFilter(SensitiveFilter())

    return logger


# Module-level audit logger
audit_logger = AuditLogger()
