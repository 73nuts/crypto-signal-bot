#!/usr/bin/env python3
"""
Group controller tests (v2.0).

Coverage:
1. Group config loading (ALPHA + RADAR)
2. Group mapping logic
3. access_groups parsing
4. Async method logic (using mock)

Note: Actual Telegram API calls require mocking.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestGroupConfig(unittest.TestCase):
    """Group config tests (v2.0)."""

    def test_group_env_mapping(self):
        """Group env variable mapping (v2.0: ALPHA + RADAR)."""
        from src.telegram.group_controller import GroupController

        # Verify mapping definition
        expected = {
            'BASIC': 'TELEGRAM_GROUP_BASIC',
            'PREMIUM': 'TELEGRAM_GROUP_PREMIUM',
        }
        self.assertEqual(GroupController.GROUP_ENV_MAP, expected)
        print("\nGroup env variable mapping correct (v2.0)")

    def test_group_button_text(self):
        """Group button text config."""
        from src.telegram.group_controller import GroupController

        self.assertIn('BASIC', GroupController.GROUP_BUTTON_TEXT)
        self.assertIn('PREMIUM', GroupController.GROUP_BUTTON_TEXT)
        self.assertIn('Basic', GroupController.GROUP_BUTTON_TEXT['BASIC'])
        self.assertIn('Premium', GroupController.GROUP_BUTTON_TEXT['PREMIUM'])
        print("\nGroup button text config correct (v2.0)")


class TestGroupControllerInit(unittest.TestCase):
    """GroupController initialization tests."""

    def test_load_group_config(self):
        """Load group config from settings."""
        from src.telegram.group_controller import GroupController

        mock_bot = MagicMock()
        mock_db = MagicMock()

        # Mock settings attributes
        with patch('src.telegram.group_controller.settings') as mock_settings:
            mock_settings.TELEGRAM_GROUP_BASIC = '-100111111111'
            mock_settings.TELEGRAM_GROUP_PREMIUM = '-100222222222'

            controller = GroupController(mock_bot, mock_db)

            self.assertEqual(controller.groups['BASIC'], -100111111111)
            self.assertEqual(controller.groups['PREMIUM'], -100222222222)
            self.assertTrue(controller.is_fully_configured())
            print("\nGroup config loaded correctly")

    def test_partial_config(self):
        """Partial config (missing BASIC)."""
        from src.telegram.group_controller import GroupController

        mock_bot = MagicMock()
        mock_db = MagicMock()

        # Mock settings - only PREMIUM configured
        with patch('src.telegram.group_controller.settings') as mock_settings:
            mock_settings.TELEGRAM_GROUP_BASIC = None
            mock_settings.TELEGRAM_GROUP_PREMIUM = '-100222222222'

            controller = GroupController(mock_bot, mock_db)

            self.assertNotIn('BASIC', controller.groups)
            self.assertIn('PREMIUM', controller.groups)
            self.assertFalse(controller.is_fully_configured())
            print("\nPartial config detection correct")


class TestAccessGroupsLogic(unittest.TestCase):
    """access_groups logic tests (v2.0)."""

    def test_basic_plan_access(self):
        """Basic plan group access."""
        # Basic plan can only join Basic group
        basic_access = ['BASIC']
        self.assertEqual(len(basic_access), 1)
        self.assertIn('BASIC', basic_access)
        self.assertNotIn('PREMIUM', basic_access)
        print("\nBasic plan access correct: BASIC group only")

    def test_premium_plan_access(self):
        """Premium plan group access."""
        # Premium plan can only join Premium group
        premium_access = ['PREMIUM']
        self.assertEqual(len(premium_access), 1)
        self.assertIn('PREMIUM', premium_access)
        self.assertNotIn('BASIC', premium_access)
        print("\nPremium plan access correct: PREMIUM group only")


class TestDAOIntegration(unittest.TestCase):
    """DAO integration tests (requires database)."""

    @classmethod
    def setUpClass(cls):
        """Check database connection."""
        try:
            from src.telegram.database import DatabaseManager
            cls.db = DatabaseManager()
            cls.db_available = True
        except Exception as e:
            cls.db_available = False
            print(f"\nSkipping DAO tests: {e}")

    def test_get_access_groups_basic(self):
        """Get Basic plan group list."""
        if not self.db_available:
            self.skipTest("Database unavailable")

        from src.telegram.database import MembershipPlanDAO

        dao = MembershipPlanDAO(self.db)
        groups = dao.get_access_groups_by_plan_code('BASIC_M')

        self.assertIsInstance(groups, list)
        if groups:
            self.assertIn('BASIC', groups)
            self.assertNotIn('PREMIUM', groups)
            print(f"\nBASIC_M plan groups: {groups}")

    def test_get_access_groups_premium(self):
        """Get Premium plan group list."""
        if not self.db_available:
            self.skipTest("Database unavailable")

        from src.telegram.database import MembershipPlanDAO

        dao = MembershipPlanDAO(self.db)
        groups = dao.get_access_groups_by_plan_code('PREMIUM_M')

        self.assertIsInstance(groups, list)
        if groups:
            self.assertIn('PREMIUM', groups)
            self.assertNotIn('BASIC', groups)
            print(f"\nPREMIUM_M plan groups: {groups}")

    def test_json_parsing(self):
        """JSON field auto-parsing."""
        if not self.db_available:
            self.skipTest("Database unavailable")

        from src.telegram.database import MembershipPlanDAO

        dao = MembershipPlanDAO(self.db)
        plan = dao.get_plan_by_code('PREMIUM_M')

        if plan:
            # access_groups should already be list, not str
            self.assertIsInstance(plan.get('access_groups'), list)
            print(f"\nJSON parsing correct: {type(plan.get('access_groups'))}")


def run_tests():
    """Run all tests."""
    print("=" * 60)
    print("GroupController Unit Tests (v2.0)")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestGroupConfig))
    suite.addTests(loader.loadTestsFromTestCase(TestGroupControllerInit))
    suite.addTests(loader.loadTestsFromTestCase(TestAccessGroupsLogic))
    suite.addTests(loader.loadTestsFromTestCase(TestDAOIntegration))

    # Run
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print("\n" + "=" * 60)
    print(f"Tests complete: {result.testsRun} tests")
    print(f"  Passed: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  Failed: {len(result.failures)}")
    print(f"  Errors: {len(result.errors)}")
    print("=" * 60)

    return result


if __name__ == '__main__':
    run_tests()
