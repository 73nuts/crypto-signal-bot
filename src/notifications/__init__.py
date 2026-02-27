"""
Notifications module

Includes:
- handlers: event notification handlers
- telegram_broadcaster: Telegram push
- email_sender: email sending
- wechat_sender: WeChat push
"""

# Importing handlers auto-registers event handlers
from . import handlers

__all__ = ['handlers']
