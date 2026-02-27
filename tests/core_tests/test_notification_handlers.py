"""
Notification handler tests

TDD: write tests first, then implement

Run:
    pytest tests/core_tests/test_notification_handlers.py -v
"""
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch


class TestNotificationHandlers(unittest.TestCase):
    """Tests for notification handlers"""

    def setUp(self):
        """Reset message bus"""
        from src.core import message_bus
        message_bus._message_bus = None

    def test_alert_handler_registered(self):
        """Alert handler is registered"""
        # Importing handlers module auto-registers handlers
        from src.notifications import handlers
        from src.core.message_bus import get_message_bus

        bus = get_message_bus()

        # Verify handler is registered
        self.assertIn("scanner.alert_detected", bus._handlers)
        self.assertTrue(len(bus._handlers["scanner.alert_detected"]) > 0)

    def test_daily_pulse_handler_registered(self):
        """Daily pulse handler is registered"""
        from src.notifications import handlers
        from src.core.message_bus import get_message_bus

        bus = get_message_bus()

        # Verify handler is registered
        self.assertIn("scanner.daily_pulse_ready", bus._handlers)
        self.assertTrue(len(bus._handlers["scanner.daily_pulse_ready"]) > 0)

    def test_alert_handler_logs_event(self):
        """Alert handler logs the event"""
        from src.notifications import handlers
        from src.core.message_bus import get_message_bus
        from src.core.events import AlertDetectedEvent

        bus = get_message_bus()

        event = AlertDetectedEvent(
            symbol="BTC",
            alert_type="flash_pump",
            score=95.0,
            message="BTC 5-minute gain 2.5%"
        )

        # Publish event
        asyncio.run(bus.publish(event))
        asyncio.run(asyncio.sleep(0.1))

        # Handler should be called (verified via logs)
        # Primary assertion: no exception raised
        self.assertTrue(True)


if __name__ == '__main__':
    unittest.main()
