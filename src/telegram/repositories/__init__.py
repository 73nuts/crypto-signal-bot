"""
Telegram module repository layer.

Pure data CRUD operations; no business logic.
"""
from .membership_repository import MembershipRepository

__all__ = [
    'MembershipRepository',
]
