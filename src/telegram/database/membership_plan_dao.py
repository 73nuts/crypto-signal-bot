"""
Membership plan config DAO.

Manages membership_plans table with hot-reload support for prices and config.

Schema:
- plan_code: plan code (BASIC_M/BASIC_Y/PREMIUM_M/PREMIUM_Y)
- plan_name: plan display name
- price_usdt: USDT price
- duration_days: validity days
- level: access level (1=Basic, 2=Premium)
- access_groups: JSON array of accessible groups, e.g. ["BASIC"] or ["PREMIUM"]
- enabled: whether plan is active
- version: optimistic lock version
"""

import json
from typing import Optional, List, Dict, Any
from decimal import Decimal

from .base import BaseDAO


class MembershipPlanDAO(BaseDAO):
    """Membership plan config data access object."""

    TABLE = 'membership_plans'

    def get_plan_by_code(self, plan_code: str) -> Optional[Dict[str, Any]]:
        """
        Look up plan config by plan code.

        Args:
            plan_code: Plan code (BASIC_M/BASIC_Y/PREMIUM_M/PREMIUM_Y)

        Returns:
            Plan config dict (access_groups auto-parsed to list), or None
        """
        sql = f"""
            SELECT id, plan_code, plan_name, price_usdt, duration_days,
                   level, access_groups, enabled, version,
                   created_at, updated_at
            FROM {self.TABLE}
            WHERE plan_code = %s
        """
        result = self.db.execute_query(sql, (plan_code,), fetch_one=True)
        return self._parse_access_groups(result) if result else None

    def get_all_enabled_plans(self) -> List[Dict[str, Any]]:
        """
        Query all enabled plans, sorted by price ascending.

        Returns:
            List of enabled plans (access_groups auto-parsed to list)
        """
        sql = f"""
            SELECT id, plan_code, plan_name, price_usdt, duration_days,
                   level, access_groups, enabled, version,
                   created_at, updated_at
            FROM {self.TABLE}
            WHERE enabled = TRUE
            ORDER BY price_usdt ASC
        """
        results = self.db.execute_query(sql) or []
        return [self._parse_access_groups(r) for r in results]

    def get_all_plans(self) -> List[Dict[str, Any]]:
        """
        Query all plans including disabled ones.

        Returns:
            All plans (access_groups auto-parsed to list)
        """
        sql = f"""
            SELECT id, plan_code, plan_name, price_usdt, duration_days,
                   level, access_groups, enabled, version,
                   created_at, updated_at
            FROM {self.TABLE}
            ORDER BY price_usdt ASC
        """
        results = self.db.execute_query(sql) or []
        return [self._parse_access_groups(r) for r in results]

    def _parse_access_groups(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse access_groups JSON field to Python list.

        Args:
            row: Database row record

        Returns:
            Row with access_groups as a list
        """
        if row and 'access_groups' in row:
            access_groups = row['access_groups']
            if access_groups is None:
                row['access_groups'] = ['ALPHA']
            elif isinstance(access_groups, str):
                try:
                    row['access_groups'] = json.loads(access_groups)
                except json.JSONDecodeError:
                    self.logger.warning(f"Cannot parse access_groups: {access_groups}")
                    row['access_groups'] = ['ALPHA']
            # already a list: leave unchanged
        return row

    def update_plan_price(
        self,
        plan_code: str,
        new_price: Decimal,
        version: int
    ) -> bool:
        """
        Update plan price with optimistic locking.

        Args:
            plan_code: Plan code
            new_price: New price
            version: Current version

        Returns:
            True: updated successfully
            False: version conflict or plan not found
        """
        sql = f"""
            UPDATE {self.TABLE}
            SET price_usdt = %s,
                version = version + 1,
                updated_at = NOW(6)
            WHERE plan_code = %s AND version = %s
        """
        affected = self.db.execute_update(sql, (new_price, plan_code, version))

        if affected == 0:
            self.logger.warning(
                f"Plan price update failed: plan_code={plan_code}, "
                f"expected_version={version}"
            )
            return False

        self.logger.info(
            f"Plan price updated: plan_code={plan_code}, "
            f"new_price={new_price}"
        )
        return True

    def update_plan_status(
        self,
        plan_code: str,
        enabled: bool,
        version: int
    ) -> bool:
        """
        Update plan enabled status with optimistic locking.

        Args:
            plan_code: Plan code
            enabled: Whether to enable
            version: Current version

        Returns:
            True: updated successfully
            False: version conflict or plan not found
        """
        sql = f"""
            UPDATE {self.TABLE}
            SET enabled = %s,
                version = version + 1,
                updated_at = NOW(6)
            WHERE plan_code = %s AND version = %s
        """
        affected = self.db.execute_update(sql, (enabled, plan_code, version))

        if affected == 0:
            self.logger.warning(
                f"Plan status update failed: plan_code={plan_code}, "
                f"expected_version={version}"
            )
            return False

        self.logger.info(
            f"Plan status updated: plan_code={plan_code}, enabled={enabled}"
        )
        return True

    def get_price_by_plan_code(self, plan_code: str) -> Optional[Decimal]:
        """
        Get plan price by plan code.

        Args:
            plan_code: Plan code

        Returns:
            USDT price, or None if not found or disabled
        """
        sql = f"""
            SELECT price_usdt
            FROM {self.TABLE}
            WHERE plan_code = %s AND enabled = TRUE
        """
        result = self.db.execute_query(sql, (plan_code,), fetch_one=True)
        return result['price_usdt'] if result else None

    def get_access_groups_by_plan_code(self, plan_code: str) -> List[str]:
        """
        Get accessible group list for a plan.

        Args:
            plan_code: Plan code

        Returns:
            Group key list e.g. ["ALPHA"] or ["RADAR"].
            Empty list if plan not found or disabled.
        """
        plan = self.get_plan_by_code(plan_code)
        if not plan or not plan.get('enabled'):
            return []
        return plan.get('access_groups', ['ALPHA'])

    def get_level_by_plan_code(self, plan_code: str) -> Optional[int]:
        """
        Get access level for a plan.

        Args:
            plan_code: Plan code

        Returns:
            Access level: 1=Alpha, 2=Radar.
            None if plan not found or disabled.
        """
        plan = self.get_plan_by_code(plan_code)
        if not plan or not plan.get('enabled'):
            return None
        return plan.get('level', 1)
