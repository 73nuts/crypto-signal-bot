"""
Telegram database access layer.

Provides unified DAO interfaces:
- OrderDAO: payment order management
- MembershipPlanDAO: plan config management
- SignalPushDAO: VIP push records
- WalletDAO: HD wallet state management (address index)
- AuditDAO: audit log management
- FeedbackDAO: feedback management

For membership data access, use:
- MembershipRepository (CRUD)
- MemberService (business logic)

All DAOs use transactions for data consistency and support optimistic locking.
"""

from .base import DatabaseManager
from .order_dao import OrderDAO
from .membership_plan_dao import MembershipPlanDAO
from .signal_push_dao import SignalPushDAO
from .wallet_dao import WalletDAO
from .audit_dao import AuditDAO
from .feedback_dao import FeedbackDAO

__all__ = [
    'DatabaseManager',
    'OrderDAO',
    'MembershipPlanDAO',
    'SignalPushDAO',
    'WalletDAO',
    'AuditDAO',
    'FeedbackDAO',
]
