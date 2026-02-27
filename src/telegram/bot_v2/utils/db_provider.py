"""
Database and service providers.

Provides shared DatabaseManager and Service instances to avoid
reconnecting on every call. DI container is preferred; module-level
singletons are the fallback.
"""
from typing import Optional

from src.telegram.database.base import DatabaseManager

_db_instance: Optional[DatabaseManager] = None


def get_db() -> DatabaseManager:
    """Return the shared DatabaseManager instance (module-level singleton)."""
    global _db_instance
    if _db_instance is None:
        _db_instance = DatabaseManager()
    return _db_instance


def get_member_service():
    """Return MemberService — DI container first, direct instantiation as fallback."""
    try:
        from src.core.container import inject
        from src.core.protocols import MemberServiceProtocol
        return inject(MemberServiceProtocol)
    except (KeyError, ImportError):
        from src.telegram.services.member_service import MemberService
        return MemberService()


def get_membership_plan_dao():
    """Return MembershipPlanDAO instance."""
    from src.telegram.database.membership_plan_dao import MembershipPlanDAO
    return MembershipPlanDAO(get_db())


def get_order_dao():
    """Return OrderDAO instance."""
    from src.telegram.database import OrderDAO
    return OrderDAO(get_db())


def get_feedback_dao():
    """Return FeedbackDAO instance."""
    from src.telegram.database import FeedbackDAO
    return FeedbackDAO(get_db())


def get_audit_dao():
    """Return AuditDAO instance."""
    from src.telegram.database.audit_dao import AuditDAO
    return AuditDAO(get_db())


def reset_db():
    """Reset shared instance (for tests only)."""
    global _db_instance
    _db_instance = None
