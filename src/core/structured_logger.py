"""
Structured logging module

Based on structlog:
1. Automatic trace_id injection
2. JSON format (production) / colored (development)
3. Integrated SensitiveFilter sanitization
"""
import logging
import sys
import os
from typing import Any, Dict

import structlog
from structlog.types import Processor

from src.core.tracing import get_context
from src.core.logger import SensitiveFilter


# Global SensitiveFilter instance
_sensitive_filter = SensitiveFilter()


def add_trace_context(
    logger: logging.Logger,
    method_name: str,
    event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Processor: automatically inject trace context"""
    ctx = get_context()
    # Only add non-empty values
    for key, value in ctx.items():
        if value and key not in event_dict:
            event_dict[key] = value
    return event_dict


def sanitize_event(
    logger: logging.Logger,
    method_name: str,
    event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Processor: sanitize sensitive information"""
    # Sanitize event message
    if 'event' in event_dict and isinstance(event_dict['event'], str):
        event_dict['event'] = _sensitive_filter._sanitize(event_dict['event'])

    # Sanitize other fields
    for key, value in list(event_dict.items()):
        if isinstance(value, str) and key not in ('timestamp', 'level', 'logger'):
            event_dict[key] = _sensitive_filter._sanitize(value)

    return event_dict


def setup_structured_logging(
    level: str = "INFO",
    json_format: bool = None,
    enable_colors: bool = None,
):
    """
    Configure structured logging.

    Args:
        level: Log level
        json_format: Whether to use JSON format (None = auto-detect)
        enable_colors: Whether to enable colored output (None = auto-detect)
    """
    # Auto-detect environment
    environment = os.getenv('ENVIRONMENT', 'development')
    is_production = environment in ('prod', 'production')
    is_tty = sys.stdout.isatty()

    if json_format is None:
        json_format = is_production

    if enable_colors is None:
        enable_colors = is_tty and not is_production

    # Build processor chain
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_trace_context,
        sanitize_event,
    ]

    if json_format:
        # Production: JSON output
        processors.extend([
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ])
    else:
        # Development: colored console
        processors.extend([
            structlog.dev.ConsoleRenderer(colors=enable_colors),
        ])

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,  # Must be disabled during testing
    )

    # Also configure standard logging (for compatibility with existing code)
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )


def get_logger(name: str = None) -> structlog.BoundLogger:
    """
    Get a structured logger.

    Usage:
        logger = get_logger(__name__)
        logger.info("User logged in", user_id=12345, ip="1.2.3.4")
    """
    return structlog.get_logger(name)


# Convenience alias
logger = get_logger()
