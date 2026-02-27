"""
Membership repository.

Pure CRUD on the memberships table; no business logic, no cross-table queries.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

from src.core.repository import BaseRepository


class MembershipRepository(BaseRepository):
    """Membership data repository. CRUD only — use MemberService for business logic."""

    @property
    def table_name(self) -> str:
        return 'memberships'

    # ========================================
    # CREATE
    # ========================================

    def create(
        self,
        telegram_id: int,
        membership_type: str,
        duration_days: int,
        level: int,
        activated_by_order_id: str,
        telegram_username: Optional[str] = None
    ) -> Optional[int]:
        """Create a new membership record. Returns the record ID, or None on failure."""
        now = datetime.now()
        expire_date = now + timedelta(days=duration_days)

        sql = f"""
            INSERT INTO {self.table_name} (
                telegram_id, telegram_username, membership_type,
                level, start_date, expire_date,
                status, activated_by_order_id, renewal_count,
                version, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, 'ACTIVE', %s, 0, 1, NOW(6), NOW(6)
            )
        """
        try:
            member_id = self._db.execute_insert(sql, (
                telegram_id,
                telegram_username,
                membership_type,
                level,
                now,
                expire_date,
                activated_by_order_id
            ))
            self.logger.info(
                f"Membership created: telegram_id={telegram_id}, type={membership_type}"
            )
            return member_id
        except Exception as e:
            self.logger.error(f"Membership creation failed: {e}")
            return None

    # ========================================
    # READ
    # ========================================

    def find_by_telegram_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Return membership record by Telegram ID, or None if not found."""
        sql = f"""
            SELECT id, telegram_id, telegram_username, membership_type,
                   level, start_date, expire_date, status, is_whitelist,
                   activated_by_order_id, renewal_count,
                   version, created_at, updated_at,
                   is_referral_verified, binance_uid, language
            FROM {self.table_name}
            WHERE telegram_id = %s
        """
        return self._db.execute(sql, (telegram_id,), fetch='one')

    def find_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Return membership record by username (@ prefix stripped). None if not found."""
        username = username.lstrip('@')
        sql = f"""
            SELECT id, telegram_id, telegram_username, membership_type,
                   level, start_date, expire_date, status, is_whitelist,
                   activated_by_order_id, renewal_count,
                   version, created_at, updated_at
            FROM {self.table_name}
            WHERE telegram_username = %s
        """
        return self._db.execute(sql, (username,), fetch='one')

    def find_by_binance_uid(self, uid: str) -> Optional[Dict[str, Any]]:
        """Return membership record by Binance UID, or None if not found."""
        sql = f"""
            SELECT id, telegram_id, telegram_username, membership_type,
                   level, start_date, expire_date, status, is_whitelist,
                   binance_uid, is_referral_verified,
                   version, created_at, updated_at
            FROM {self.table_name}
            WHERE binance_uid = %s
        """
        return self._db.execute(sql, (uid,), fetch='one')

    def find_all_active(self) -> List[Dict[str, Any]]:
        """Return all active membership records (including whitelist)."""
        sql = f"""
            SELECT id, telegram_id, telegram_username, membership_type,
                   level, start_date, expire_date, status, is_whitelist,
                   version, created_at, updated_at
            FROM {self.table_name}
            WHERE status = 'ACTIVE'
              AND (expire_date > NOW() OR is_whitelist = 1)
            ORDER BY expire_date ASC
        """
        result = self._db.execute(sql, fetch='all')
        return result if result else []

    def find_expiring(self, days: int = 3) -> List[Dict[str, Any]]:
        """Return non-whitelist active members expiring within the given number of days."""
        sql = f"""
            SELECT id, telegram_id, telegram_username, membership_type,
                   level, start_date, expire_date, status,
                   version, created_at, updated_at
            FROM {self.table_name}
            WHERE status = 'ACTIVE'
              AND expire_date > NOW()
              AND expire_date <= DATE_ADD(NOW(), INTERVAL %s DAY)
              AND is_whitelist = 0
        """
        result = self._db.execute(sql, (days,), fetch='all')
        return result if result else []

    def count_by_level(self, level: int) -> int:
        """Return the count of active members at the given level (1=Basic, 2=Premium)."""
        sql = f"""
            SELECT COUNT(*) as cnt FROM {self.table_name}
            WHERE status = 'ACTIVE' AND level = %s
              AND (expire_date > NOW() OR is_whitelist = 1)
        """
        result = self._db.execute(sql, (level,), fetch='one')
        return result['cnt'] if result else 0

    # ========================================
    # UPDATE
    # ========================================

    def update_status(
        self,
        telegram_id: int,
        status: str,
        version: int
    ) -> bool:
        """Update membership status with optimistic lock. Returns True on success."""
        sql = f"""
            UPDATE {self.table_name}
            SET status = %s,
                version = version + 1,
                updated_at = NOW(6)
            WHERE telegram_id = %s AND version = %s
        """
        affected = self._db.execute(sql, (status, telegram_id, version), fetch=None)
        return affected > 0

    def update_expiry(
        self,
        telegram_id: int,
        new_expire: datetime,
        membership_type: str,
        level: int,
        order_id: str,
        version: int
    ) -> bool:
        """Update expiry date on renewal with optimistic lock. Returns True on success."""
        sql = f"""
            UPDATE {self.table_name}
            SET expire_date = %s,
                membership_type = %s,
                level = %s,
                activated_by_order_id = %s,
                renewal_count = renewal_count + 1,
                version = version + 1,
                status = 'ACTIVE',
                updated_at = NOW(6)
            WHERE telegram_id = %s AND version = %s
        """
        affected = self._db.execute(sql, (
            new_expire, membership_type, level, order_id, telegram_id, version
        ), fetch=None)
        return affected > 0

    def update_language(self, telegram_id: int, language: str) -> bool:
        """Update language preference. Returns True on success."""
        sql = f"""
            UPDATE {self.table_name}
            SET language = %s, updated_at = NOW()
            WHERE telegram_id = %s
        """
        affected = self._db.execute(sql, (language, telegram_id), fetch=None)
        return affected > 0

    def update_binance_uid(
        self,
        telegram_id: int,
        binance_uid: Optional[str],
        version: int
    ) -> bool:
        """Update Binance UID (pass None to clear). Returns True on success."""
        sql = f"""
            UPDATE {self.table_name}
            SET binance_uid = %s,
                is_referral_verified = 0,
                version = version + 1,
                updated_at = NOW(6)
            WHERE telegram_id = %s AND version = %s
        """
        params = (binance_uid, telegram_id, version)
        affected = self._db.execute(sql, params, fetch=None)
        return affected > 0

    def update_referral_status(
        self,
        telegram_id: int,
        is_verified: bool,
        version: int
    ) -> bool:
        """Update Trader verification status with optimistic lock. Returns True on success."""
        sql = f"""
            UPDATE {self.table_name}
            SET is_referral_verified = %s,
                version = version + 1,
                updated_at = NOW(6)
            WHERE telegram_id = %s AND version = %s
        """
        affected = self._db.execute(sql, (
            1 if is_verified else 0, telegram_id, version
        ), fetch=None)
        return affected > 0

    # ========================================
    # Query helpers
    # ========================================

    def find_pending_referrals(self) -> List[Dict[str, Any]]:
        """Return active members with a submitted but unverified Binance UID."""
        sql = f"""
            SELECT id, telegram_id, telegram_username, binance_uid,
                   membership_type, level, status, is_referral_verified,
                   version, created_at, updated_at
            FROM {self.table_name}
            WHERE binance_uid IS NOT NULL
              AND is_referral_verified = 0
              AND status = 'ACTIVE'
            ORDER BY updated_at DESC
        """
        result = self._db.execute(sql, fetch='all')
        return result if result else []

    def find_expired_active(self) -> List[Dict[str, Any]]:
        """Return non-whitelist members whose expire_date has passed but status is still ACTIVE."""
        sql = f"""
            SELECT id, telegram_id, version
            FROM {self.table_name}
            WHERE status = 'ACTIVE'
              AND expire_date <= NOW()
              AND is_whitelist = 0
        """
        result = self._db.execute(sql, fetch='all')
        return result if result else []

    def force_update_status(self, telegram_id: int, status: str) -> bool:
        """Force-update membership status (admin only, bypasses version check). Returns True on success."""
        sql = f"""
            UPDATE {self.table_name}
            SET status = %s,
                version = version + 1,
                updated_at = NOW(6)
            WHERE telegram_id = %s AND status = 'ACTIVE'
        """
        affected = self._db.execute(sql, (status, telegram_id), fetch=None)
        return affected > 0

    # ========================================
    # Verified UIDs management
    # ========================================

    def find_verified_uid(self, uid: str) -> Optional[Dict[str, Any]]:
        """
        Look up a UID in the verified pool.

        Returns:
            {
                'exists': bool,
                'telegram_id': int or None,
                'verified_at': datetime or None,
                'verified_by': str or None
            }
        """
        sql = """
            SELECT telegram_id, verified_at, verified_by
            FROM verified_uids
            WHERE uid = %s
        """
        result = self._db.execute(sql, (uid,), fetch='one')

        if result:
            return {
                'exists': True,
                'telegram_id': result['telegram_id'],
                'verified_at': result['verified_at'],
                'verified_by': result.get('verified_by')
            }
        return {
            'exists': False,
            'telegram_id': None,
            'verified_at': None,
            'verified_by': None
        }

    def add_verified_uid(
        self,
        uid: str,
        telegram_id: int,
        verified_by: str = 'admin'
    ) -> bool:
        """
        Add a verified UID to the pool.

        Args:
            uid: Binance UID
            telegram_id: Telegram user ID
            verified_by: verification method ('admin', 'system', 'migration')

        Returns:
            True if added successfully, False otherwise
        """
        sql = """
            INSERT INTO verified_uids (uid, telegram_id, verified_by)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                telegram_id = VALUES(telegram_id),
                verified_at = NOW(),
                verified_by = VALUES(verified_by)
        """
        try:
            self._db.execute(sql, (uid, telegram_id, verified_by), fetch=None)
            self.logger.info(
                f"UID verified: uid={uid}, telegram_id={telegram_id}, by={verified_by}"
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to add verified UID: {e}")
            return False
