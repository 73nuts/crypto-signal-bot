"""
Router module (Phase 10.F).

Router tree:
- errors_router: global error handling
- language_router: language switching
- user_router: user features (/start, /help, /status)
- admin_router: admin features (/admin, /add_vip, etc.)
- feedback_router: feedback system (FSM)
- trader_router: Trader Program (FSM)
- join_request_router: channel join request handling
- sector_admin_router: sector management
- subscription_router: subscription flow (includes payment flow)
- menu_router: menu button handling

Version: v1.0.1
Updated: 2025-12-31
"""
from .errors import router as errors_router
from .language import router as language_router
from .user import router as user_router
from .admin import router as admin_router
from .feedback import router as feedback_router
from .trader import router as trader_router
from .join_request import router as join_request_router
from .sector_admin import router as sector_admin_router
from .subscription import router as subscription_router
from .menu import router as menu_router

__all__ = [
    'errors_router',
    'language_router',
    'user_router',
    'admin_router',
    'feedback_router',
    'trader_router',
    'join_request_router',
    'sector_admin_router',
    'subscription_router',
    'menu_router',
]
