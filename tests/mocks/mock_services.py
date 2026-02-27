"""
Mock service implementations.

Mock services for testing.
"""
from typing import Dict, Any
from datetime import datetime, timedelta


class MockMemberService:
    """Mock member service."""

    def __init__(self):
        self._members: Dict[int, Dict[str, Any]] = {}

    def activate_or_renew(
        self,
        telegram_id: int,
        plan_code: str,
        days: int
    ) -> Dict[str, Any]:
        """Activate or renew a membership."""
        now = datetime.now()
        expire_date = now + timedelta(days=days)

        if telegram_id in self._members:
            # Renewal
            member = self._members[telegram_id]
            member['plan_code'] = plan_code
            member['expire_date'] = expire_date
            member['status'] = 'ACTIVE'
        else:
            # New activation
            member = {
                'telegram_id': telegram_id,
                'plan_code': plan_code,
                'status': 'ACTIVE',
                'expire_date': expire_date,
                'created_at': now
            }
            self._members[telegram_id] = member

        return {
            'success': True,
            'telegram_id': telegram_id,
            'expire_date': expire_date
        }

    def check_valid(self, telegram_id: int) -> bool:
        """Check if a membership is valid."""
        member = self._members.get(telegram_id)
        if not member:
            return False
        if member.get('status') != 'ACTIVE':
            return False
        expire_date = member.get('expire_date')
        if expire_date and expire_date < datetime.now():
            return False
        return True

    def get_member_status(self, telegram_id: int) -> Dict[str, Any]:
        """Get member status."""
        member = self._members.get(telegram_id)
        if not member:
            return {
                'valid': False,
                'status': 'NOT_FOUND'
            }
        return {
            'valid': self.check_valid(telegram_id),
            'status': member.get('status'),
            'plan_code': member.get('plan_code'),
            'expire_date': member.get('expire_date')
        }

    def force_expire_membership(self, telegram_id: int, reason: str) -> bool:
        """Force-expire a membership."""
        if telegram_id in self._members:
            self._members[telegram_id]['status'] = 'EXPIRED'
            self._members[telegram_id]['expire_reason'] = reason
            return True
        return False

    # Test helper methods
    def add_member(
        self,
        telegram_id: int,
        plan_code: str = 'PREMIUM_M',
        status: str = 'ACTIVE',
        days_remaining: int = 30
    ):
        """Add a member (for testing)."""
        self._members[telegram_id] = {
            'telegram_id': telegram_id,
            'plan_code': plan_code,
            'status': status,
            'expire_date': datetime.now() + timedelta(days=days_remaining),
            'created_at': datetime.now()
        }

    def clear(self):
        """Clear all data (for testing)."""
        self._members.clear()
