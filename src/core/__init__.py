"""
Ignis core module

Includes:
- config: unified configuration management (SecretsManager)
- logger: global log sanitization
- database: MySQL connection pool
- repository: base Repository class
- events: event definitions
- message_bus: message bus
"""

from .config import get_settings, init_settings, settings
from .database import DatabasePool, get_db
from .logger import SensitiveFilter, audit_logger, get_logger, setup_logging
from .message_bus import MessageBus, get_message_bus, on_event
from .repository import BaseRepository

__all__ = [
    # configuration management
    'settings',
    'get_settings',
    'init_settings',
    # logging
    'setup_logging',
    'get_logger',
    'SensitiveFilter',
    'audit_logger',
    # database connection pool
    'DatabasePool',
    'get_db',
    # base Repository
    'BaseRepository',
    # message bus
    'MessageBus',
    'get_message_bus',
    'on_event',
]
