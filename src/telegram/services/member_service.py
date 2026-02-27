"""
Member service layer.

Responsibilities: membership business logic
- validity checks
- activation / renewal
- Trader Program management
- expiry processing

Low-level CRUD is delegated to MembershipRepository.
"""

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from src.telegram.repositories.membership_repository import MembershipRepository


class RenewalReminder(str, Enum):
    """Renewal reminder trigger points."""

    T_MINUS_3 = "T-3"    # 3 days before expiry
    T_MINUS_1 = "T-1"    # 1 day before expiry
    T_ZERO = "T+0"       # expiry day
    ALPHA_CLOSING = "ALPHA_CLOSING"  # Alpha renewal window closing (T+25)


class MemberService:
    """
    Membership service.

    Encapsulates membership business logic; Repository handles CRUD only.
    """

    def __init__(self, repository: MembershipRepository = None):
        self.repository = repository or MembershipRepository()
        self.logger = logging.getLogger(self.__class__.__name__)

    # ========================================
    # Membership validity checks
    # ========================================

    def check_membership_valid(self, telegram_id: int) -> Dict[str, Any]:
        """
        Check whether a user's membership is active.

        Returns:
            {
                'active': bool,
                'membership_type': str or None,
                'level': int or None,
                'expire_date': datetime or None,
                'days_remaining': int or None,
                'is_whitelist': bool
            }
        """
        member = self.repository.find_by_telegram_id(telegram_id)

        if not member:
            return {
                "active": False,
                "membership_type": None,
                "level": None,
                "expire_date": None,
                "days_remaining": None,
                "is_whitelist": False,
            }

        now = datetime.now()
        expire_date = member["expire_date"]
        is_whitelist = member.get("is_whitelist", False)

        # Whitelist users are permanently active
        is_active = member["status"] == "ACTIVE" and (is_whitelist or expire_date > now)

        days_remaining = None
        if is_active:
            if is_whitelist:
                days_remaining = 99999
            else:
                delta = expire_date - now
                days_remaining = delta.days

        return {
            "active": is_active,
            "membership_type": member["membership_type"],
            "level": member.get("level", 1),
            "expire_date": expire_date,
            "days_remaining": days_remaining,
            "is_whitelist": is_whitelist,
        }

    # ========================================
    # Activation / renewal
    # ========================================

    def activate_or_renew(
        self,
        telegram_id: int,
        membership_type: str,
        duration_days: int,
        level: int,
        order_id: str,
        telegram_username: Optional[str] = None,
    ) -> Optional[int]:
        """
        Activate or renew a membership.

        New users get a fresh record; existing users have their expiry extended.

        Returns:
            membership record ID, or None on failure
        """
        existing = self.repository.find_by_telegram_id(telegram_id)

        if existing:
            # Guard: never downgrade an active membership
            existing_level = existing.get("level") or 0
            is_active = existing.get("status") == "ACTIVE"
            if is_active and level < existing_level:
                self.logger.warning(
                    f"Blocked membership downgrade: telegram_id={telegram_id}, "
                    f"current_level={existing_level}, attempted_level={level}, "
                    f"order_id={order_id}"
                )
                return None

            # Whitelist users should not be modified by normal flows
            if existing.get("is_whitelist"):
                self.logger.warning(
                    f"Blocked renewal for whitelist user: telegram_id={telegram_id}, "
                    f"order_id={order_id}"
                )
                return existing["id"]

            # Renewal: extend from current expiry (or now if already expired)
            current_expire = existing["expire_date"]
            now = datetime.now()

            base_date = max(current_expire, now)
            new_expire = base_date + timedelta(days=duration_days)

            success = self.repository.update_expiry(
                telegram_id=telegram_id,
                new_expire=new_expire,
                membership_type=membership_type,
                level=level,
                order_id=order_id,
                version=existing["version"],
            )
            if success:
                self.logger.info(
                    f"Membership renewed: telegram_id={telegram_id}, "
                    f"type={membership_type}, days={duration_days}"
                )
            return existing["id"] if success else None
        else:
            member_id = self.repository.create(
                telegram_id=telegram_id,
                membership_type=membership_type,
                duration_days=duration_days,
                level=level,
                activated_by_order_id=order_id,
                telegram_username=telegram_username,
            )
            if member_id:
                self.logger.info(
                    f"Membership activated: telegram_id={telegram_id}, "
                    f"type={membership_type}, days={duration_days}"
                )
            return member_id

    # ========================================
    # Trial
    # ========================================

    def is_trial_eligible(self, telegram_id: int) -> bool:
        """
        Check whether a user may activate the free trial.

        Rules:
        1. No existing membership record → eligible
        2. Already active (any plan) → ineligible
        3. Previously used Trial → ineligible
        4. Previously paid → ineligible
        """
        member = self.repository.find_by_telegram_id(telegram_id)

        if not member:
            return True

        # Active membership of any kind → never downgrade to trial
        if member.get("status") == "ACTIVE":
            return False

        if member.get("membership_type") == "TRIAL_7D":
            return False

        # Check payment history
        sql = """
            SELECT COUNT(*) as cnt FROM payment_orders
            WHERE telegram_id = %s AND status = 'CONFIRMED'
        """
        result = self.repository._db.execute(sql, (telegram_id,), fetch="one")
        if result and result["cnt"] > 0:
            return False

        # Non-trial order_id means a manually activated paid user
        if member.get("activated_by_order_id") and not member[
            "activated_by_order_id"
        ].startswith("TRIAL-"):
            return False

        return True

    def activate_trial(
        self, telegram_id: int, telegram_username: Optional[str] = None
    ) -> bool:
        """Activate the 7-day free trial for an eligible user. Returns True on success."""
        if not self.is_trial_eligible(telegram_id):
            self.logger.warning(f"Trial not eligible: telegram_id={telegram_id}")
            return False

        from datetime import datetime

        order_id = f"TRIAL-{telegram_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        member_id = self.activate_or_renew(
            telegram_id=telegram_id,
            membership_type="TRIAL_7D",
            duration_days=7,
            level=1,
            order_id=order_id,
            telegram_username=telegram_username,
        )

        if member_id:
            self.logger.info(
                f"Trial activated: telegram_id={telegram_id}, order_id={order_id}"
            )
            return True

        self.logger.error(f"Trial activation failed: telegram_id={telegram_id}")
        return False

    def get_trial_expiring_tomorrow(self) -> List[Dict[str, Any]]:
        """Return Trial users expiring tomorrow (Day 6 reminder)."""
        sql = """
            SELECT id, telegram_id, telegram_username, membership_type,
                   level, expire_date, language
            FROM memberships
            WHERE status = 'ACTIVE'
              AND membership_type = 'TRIAL_7D'
              AND expire_date > NOW()
              AND expire_date <= DATE_ADD(NOW(), INTERVAL 1 DAY)
              AND is_whitelist = 0
            ORDER BY expire_date ASC
        """
        result = self.repository._db.execute(sql, fetch="all")
        return result if result else []

    # ========================================
    # Expiry processing
    # ========================================

    def expire_membership(self, telegram_id: int) -> bool:
        """Manually expire a membership. Returns True on success."""
        member = self.repository.find_by_telegram_id(telegram_id)
        if not member:
            return False

        success = self.repository.update_status(
            telegram_id=telegram_id, status="EXPIRED", version=member["version"]
        )
        if success:
            self.logger.info(f"Membership expired: telegram_id={telegram_id}")
        return success

    def batch_expire_memberships(self) -> int:
        """Batch-expire all overdue active memberships. Returns count expired."""
        expired_list = self.repository.find_expired_active()
        count = 0

        for member in expired_list:
            success = self.repository.update_status(
                telegram_id=member["telegram_id"],
                status="EXPIRED",
                version=member["version"],
            )
            if success:
                count += 1
                self.logger.info(f"Batch expire: telegram_id={member['telegram_id']}")

        return count

    # ========================================
    # Trader Program
    # ========================================

    def is_trader_verified(self, telegram_id: int) -> bool:
        """Return True if user has a verified Trader Program status."""
        member = self.repository.find_by_telegram_id(telegram_id)
        if not member:
            return False
        return bool(member.get("is_referral_verified", False))

    def submit_binance_uid(self, telegram_id: int, uid: str) -> bool:
        """Save a submitted Binance UID. Returns True on success."""
        member = self.repository.find_by_telegram_id(telegram_id)
        if not member:
            return False

        success = self.repository.update_binance_uid(
            telegram_id=telegram_id, binance_uid=uid, version=member["version"]
        )
        if success:
            self.logger.info(f"UID submitted: telegram_id={telegram_id}, uid={uid}")
        return success

    def approve_trader(self, telegram_id: int) -> bool:
        """Approve a Trader application. Returns True on success."""
        member = self.repository.find_by_telegram_id(telegram_id)
        if not member:
            return False

        success = self.repository.update_referral_status(
            telegram_id=telegram_id, is_verified=True, version=member["version"]
        )
        if success:
            self.logger.info(f"Trader approved: telegram_id={telegram_id}")
        return success

    # ========================================
    # Language settings
    # ========================================

    def update_language(self, telegram_id: int, language: str) -> bool:
        """Update user language preference. Returns True on success."""
        return self.repository.update_language(telegram_id, language)

    def get_language(self, telegram_id: int) -> str:
        """Return user language preference, defaulting to 'en'."""
        member = self.repository.find_by_telegram_id(telegram_id)
        if not member:
            return "en"
        return member.get("language") or "en"

    # ========================================
    # Statistics queries
    # ========================================

    def get_expiring_soon(self, days: int = 7) -> List[Dict[str, Any]]:
        """Return members expiring within the given number of days."""
        return self.repository.find_expiring(days)

    def count_by_level(self) -> Dict[str, int]:
        """Return active member counts by level: {'basic': N, 'premium': M}."""
        return {
            "basic": self.repository.count_by_level(1),
            "premium": self.repository.count_by_level(2),
        }

    def get_pending_traders(self) -> List[Dict[str, Any]]:
        """Return Trader applications pending review."""
        return self.repository.find_pending_referrals()

    def reject_trader(self, telegram_id: int) -> bool:
        """Reject a Trader application (clears UID and verification status). Returns True on success."""
        member = self.repository.find_by_telegram_id(telegram_id)
        if not member:
            return False

        success = self.repository.update_binance_uid(
            telegram_id=telegram_id, binance_uid=None, version=member["version"]
        )
        if success:
            self.logger.info(f"Trader rejected: telegram_id={telegram_id}")
        return success

    def force_expire_membership(self, telegram_id: int) -> bool:
        """Force-expire a membership (admin operation, bypasses version check). Returns True on success."""
        success = self.repository.force_update_status(
            telegram_id=telegram_id, status="EXPIRED"
        )
        if success:
            self.logger.info(f"Membership force-expired: telegram_id={telegram_id}")
        return success

    def get_referral_stats(self) -> Dict[str, int]:
        """
        Return Trader Program statistics.

        Returns:
            {
                'total_applications': int,
                'verified_count': int,
                'pending_count': int
            }
        """
        sql_total = f"""
            SELECT COUNT(*) as cnt FROM {self.repository.table_name}
            WHERE binance_uid IS NOT NULL
        """
        total = self.repository._db.execute(sql_total, fetch="one")

        sql_verified = f"""
            SELECT COUNT(*) as cnt FROM {self.repository.table_name}
            WHERE binance_uid IS NOT NULL AND is_referral_verified = 1
        """
        verified = self.repository._db.execute(sql_verified, fetch="one")

        sql_pending = f"""
            SELECT COUNT(*) as cnt FROM {self.repository.table_name}
            WHERE binance_uid IS NOT NULL AND is_referral_verified = 0
        """
        pending = self.repository._db.execute(sql_pending, fetch="one")

        return {
            "total_applications": total["cnt"] if total else 0,
            "verified_count": verified["cnt"] if verified else 0,
            "pending_count": pending["cnt"] if pending else 0,
        }

    def get_referral_stats_extended(self) -> Dict[str, Any]:
        """
        Return extended Trader Program statistics.

        Includes: basic counts, revenue stats, and top-5 active traders.
        """
        basic_stats = self.get_referral_stats()

        sql_orders = """
            SELECT
                COUNT(*) as order_count,
                COALESCE(SUM(po.actual_amount), 0) as total_revenue
            FROM payment_orders po
            INNER JOIN memberships m ON po.telegram_id = m.telegram_id
            WHERE m.is_referral_verified = 1
              AND po.status = 'CONFIRMED'
        """
        order_stats = self.repository._db.execute(sql_orders, fetch="one")

        sql_top = """
            SELECT
                m.telegram_username,
                m.telegram_id,
                COUNT(po.id) as order_count,
                COALESCE(SUM(po.actual_amount), 0) as total_amount
            FROM memberships m
            LEFT JOIN payment_orders po ON m.telegram_id = po.telegram_id
                AND po.status = 'CONFIRMED'
            WHERE m.is_referral_verified = 1
            GROUP BY m.telegram_id, m.telegram_username
            HAVING order_count > 0
            ORDER BY order_count DESC
            LIMIT 5
        """
        top_traders = self.repository._db.execute(sql_top, fetch="all") or []

        total_revenue = float(order_stats["total_revenue"]) if order_stats else 0
        est_rebate = total_revenue * 0.30  # estimated 30% rebate

        return {
            **basic_stats,
            "order_count": order_stats["order_count"] if order_stats else 0,
            "total_revenue": total_revenue,
            "est_rebate": est_rebate,
            "top_traders": top_traders,
        }

    def is_uid_available(self, uid: str, telegram_id: int) -> tuple:
        """
        Check whether a Binance UID is available for a given user.

        Returns:
            (True, 'new')      - new UID, requires manual review
            (True, 'auto')     - verified UID belonging to this user, auto-approve
            (False, 'occupied')- UID claimed by another user
        """
        status = self.repository.find_verified_uid(uid)

        if not status["exists"]:
            return (True, "new")

        if status["telegram_id"] == telegram_id:
            return (True, "auto")

        return (False, "occupied")

    def add_verified_uid(
        self, uid: str, telegram_id: int, verified_by: str = "admin"
    ) -> bool:
        """Add a verified UID to the verified pool. Returns True on success."""
        return self.repository.add_verified_uid(uid, telegram_id, verified_by)

    # ========================================
    # Additional helper methods
    # ========================================

    def count_premium_users(self) -> int:
        """Return the count of active Premium users."""
        return self.count_by_level()["premium"]

    def get_active_members(self, min_level: int = 0) -> List[int]:
        """
        Return telegram_ids of all active members.

        min_level: 0=all, 1=Basic and above, 2=Premium only
        """
        members = self.repository.find_all_active()

        if min_level > 0:
            return [m["telegram_id"] for m in members if m.get("level", 1) >= min_level]

        return [m["telegram_id"] for m in members]

    def get_user_membership_info(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Return full membership record, or None if not found."""
        return self.repository.find_by_telegram_id(telegram_id)

    def process_expired_members(self) -> Dict[str, int]:
        """
        Process expired memberships (scheduled task).

        Strategy: stop signals immediately on expiry; remove from group after 24h (T+1 kick).

        Returns:
            {'marked_expired': int, 'to_kick': int}
        """
        result = {"marked_expired": 0, "to_kick": 0}

        result["marked_expired"] = self.batch_expire_memberships()

        to_kick = self.get_members_to_kick()
        result["to_kick"] = len(to_kick)

        if result["marked_expired"] > 0 or result["to_kick"] > 0:
            self.logger.info(
                f"Expiry processed: marked_expired={result['marked_expired']}, "
                f"to_kick={result['to_kick']}"
            )

        return result

    def get_expiring_members(self, reminder_type: str) -> List[Dict[str, Any]]:
        """Return members matching the given reminder_type ('T-3', 'T-1', 'T+0', 'ALPHA_CLOSING')."""
        if reminder_type == "T-3":
            return self._get_members_expiring_in_range(2, 3)
        elif reminder_type == "T-1":
            return self._get_members_expiring_in_range(0, 1)
        elif reminder_type == "T+0":
            return self._get_recently_expired_members(hours=24)
        elif reminder_type == "ALPHA_CLOSING":
            return self._get_alpha_window_closing_members()
        else:
            return []

    def _get_members_expiring_in_range(
        self, min_days: int, max_days: int
    ) -> List[Dict[str, Any]]:
        """Return non-whitelist members expiring within [min_days, max_days]."""
        sql = """
            SELECT id, telegram_id, telegram_username, membership_type,
                   level, expire_date
            FROM memberships
            WHERE status = 'ACTIVE'
              AND expire_date > DATE_ADD(NOW(), INTERVAL %s DAY)
              AND expire_date <= DATE_ADD(NOW(), INTERVAL %s DAY)
              AND is_whitelist = 0
            ORDER BY expire_date ASC
        """
        result = self.repository._db.execute(sql, (min_days, max_days), fetch="all")
        return result if result else []

    def _get_recently_expired_members(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Return non-whitelist members that expired within the last N hours."""
        sql = """
            SELECT id, telegram_id, telegram_username, membership_type,
                   level, expire_date
            FROM memberships
            WHERE status = 'ACTIVE'
              AND expire_date <= NOW()
              AND expire_date > DATE_SUB(NOW(), INTERVAL %s HOUR)
              AND is_whitelist = 0
            ORDER BY expire_date DESC
        """
        result = self.repository._db.execute(sql, (hours,), fetch="all")
        return result if result else []

    def _get_alpha_window_closing_members(self) -> List[Dict[str, Any]]:
        """
        Return members whose Alpha renewal window is about to close.

        Criteria:
        1. Status EXPIRED
        2. Expired 25-26 days ago (5-day window remaining)
        3. Last confirmed order used ALPHA discount
        """
        sql = """
            SELECT m.id, m.telegram_id, m.telegram_username, m.membership_type,
                   m.level, m.expire_date
            FROM memberships m
            INNER JOIN (
                SELECT telegram_id, discount_type
                FROM payment_orders
                WHERE status = 'CONFIRMED'
                  AND (telegram_id, confirmed_at) IN (
                      SELECT telegram_id, MAX(confirmed_at)
                      FROM payment_orders
                      WHERE status = 'CONFIRMED'
                      GROUP BY telegram_id
                  )
            ) last_order ON m.telegram_id = last_order.telegram_id
            WHERE m.status = 'EXPIRED'
              AND m.expire_date <= DATE_SUB(NOW(), INTERVAL 25 DAY)
              AND m.expire_date > DATE_SUB(NOW(), INTERVAL 26 DAY)
              AND m.is_whitelist = 0
              AND last_order.discount_type = 'ALPHA'
            ORDER BY m.expire_date ASC
        """
        result = self.repository._db.execute(sql, fetch="all")
        return result if result else []

    def get_members_to_kick(self, grace_hours: int = 24) -> List[Dict[str, Any]]:
        """Return EXPIRED non-whitelist members past the grace period (default 24h)."""
        sql = """
            SELECT id, telegram_id, telegram_username, membership_type,
                   expire_date
            FROM memberships
            WHERE status = 'EXPIRED'
              AND expire_date < DATE_SUB(NOW(), INTERVAL %s HOUR)
              AND is_whitelist = 0
        """
        result = self.repository._db.execute(sql, (grace_hours,), fetch="all")
        return result if result else []

    def get_membership_stats(self) -> Dict[str, Any]:
        """
        Return membership statistics.

        Returns:
            {
                'total_active': int,
                'by_type': {'BASIC_M': x, 'BASIC_Y': y, 'PREMIUM_M': z, 'PREMIUM_Y': w},
                'by_level': {1: basic_count, 2: premium_count},
                'expiring_soon': int  # expiring within 3 days
            }
        """
        sql_by_type = """
            SELECT membership_type, level, COUNT(*) as count
            FROM memberships
            WHERE status = 'ACTIVE'
              AND (expire_date > NOW() OR is_whitelist = 1)
            GROUP BY membership_type, level
        """
        results = self.repository._db.execute(sql_by_type, fetch="all") or []

        stats = {
            "BASIC_M": 0,
            "BASIC_Y": 0,
            "PREMIUM_M": 0,
            "PREMIUM_Y": 0,
            "total": 0,
            "by_level": {1: 0, 2: 0},
        }

        for row in results:
            m_type = row.get("membership_type")
            level = row.get("level", 1)
            count = row.get("count", 0)

            if m_type in stats:
                stats[m_type] = count
            stats["total"] += count
            stats["by_level"][level] = stats["by_level"].get(level, 0) + count

        expiring = self.repository.find_expiring(days=3)

        return {
            "total_active": stats["total"],
            "by_type": {
                "BASIC_M": stats["BASIC_M"],
                "BASIC_Y": stats["BASIC_Y"],
                "PREMIUM_M": stats["PREMIUM_M"],
                "PREMIUM_Y": stats["PREMIUM_Y"],
            },
            "by_level": stats["by_level"],
            "expiring_soon": len(expiring),
        }
