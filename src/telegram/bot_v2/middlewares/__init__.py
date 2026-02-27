"""
Middleware module.

Provides:
- TraceMiddleware: trace_id injection (Phase 7 integration)
- AuthMiddleware: permission checks
- ThrottleMiddleware: rate limiting
- I18nMiddleware: internationalization (Phase 10.F)
"""
from .trace import TraceMiddleware
from .auth import AuthMiddleware
from .throttle import ThrottleMiddleware
from .i18n import I18nMiddleware

__all__ = ['TraceMiddleware', 'AuthMiddleware', 'ThrottleMiddleware', 'I18nMiddleware']
