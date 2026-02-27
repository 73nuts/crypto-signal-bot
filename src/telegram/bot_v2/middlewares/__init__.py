"""
Middleware module.

Provides:
- TraceMiddleware: trace_id injection (Phase 7 integration)
- AuthMiddleware: permission checks
- ThrottleMiddleware: rate limiting
- I18nMiddleware: internationalization (Phase 10.F)
"""
from .auth import AuthMiddleware
from .i18n import I18nMiddleware
from .throttle import ThrottleMiddleware
from .trace import TraceMiddleware

__all__ = ['TraceMiddleware', 'AuthMiddleware', 'ThrottleMiddleware', 'I18nMiddleware']
