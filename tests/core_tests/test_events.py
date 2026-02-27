"""
Event definition tests

TDD: write tests first, then implement

Run:
    pytest tests/core_tests/test_events.py -v
"""
import unittest
from datetime import datetime


class TestBaseEvent(unittest.TestCase):
    """Tests for event base class"""

    def test_event_has_required_fields(self):
        """Event must have required fields"""
        from src.core.events import BaseEvent

        event = BaseEvent()
        self.assertIsNotNone(event.event_id)
        self.assertIsNotNone(event.timestamp)
        self.assertIsInstance(event.timestamp, datetime)

    def test_event_is_immutable(self):
        """Event is immutable"""
        from src.core.events import BaseEvent

        event = BaseEvent()
        with self.assertRaises(Exception):
            event.event_id = "new_id"


class TestScannerEvents(unittest.TestCase):
    """Tests for Scanner events"""

    def test_alert_detected_event(self):
        """Alert detection event"""
        from src.core.events import AlertDetectedEvent

        event = AlertDetectedEvent(
            symbol="BTC",
            alert_type="price_surge",
            score=85.5,
            message="BTC price surge"
        )
        self.assertEqual(event.symbol, "BTC")
        self.assertEqual(event.event_type, "scanner.alert_detected")
        self.assertEqual(event.source, "scanner")

    def test_daily_pulse_ready_event(self):
        """Daily report ready event"""
        from src.core.events import DailyPulseReadyEvent

        event = DailyPulseReadyEvent(
            content="Daily report content",
            image_path="/tmp/fg.png"
        )
        self.assertEqual(event.event_type, "scanner.daily_pulse_ready")


class TestSwingEvents(unittest.TestCase):
    """Tests for Swing strategy events"""

    def test_signal_generated_event(self):
        """Trading signal event"""
        from src.core.events import SignalGeneratedEvent

        event = SignalGeneratedEvent(
            symbol="BTC",
            direction="LONG",
            entry_price=50000.0,
            stop_loss=48000.0,
            position_size=0.1,
            strategy_name="swing-ensemble"
        )
        self.assertEqual(event.event_type, "swing.signal_generated")
        self.assertEqual(event.direction, "LONG")

    def test_position_opened_event(self):
        """Position opened event"""
        from src.core.events import PositionOpenedEvent

        event = PositionOpenedEvent(
            position_id=1,
            symbol="ETH",
            entry_price=3000.0,
            quantity=1.5
        )
        self.assertEqual(event.event_type, "swing.position_opened")

    def test_position_closed_event(self):
        """Position closed event"""
        from src.core.events import PositionClosedEvent

        event = PositionClosedEvent(
            position_id=1,
            symbol="ETH",
            exit_price=3200.0,
            pnl_percent=6.67
        )
        self.assertEqual(event.event_type, "swing.position_closed")


class TestPaymentEvents(unittest.TestCase):
    """Tests for payment events"""

    def test_payment_received_event(self):
        """Payment confirmation event"""
        from src.core.events import PaymentReceivedEvent

        event = PaymentReceivedEvent(
            order_id="ORD-001",
            telegram_id=123456,
            amount=29.9,
            tx_hash="0xabc123"
        )
        self.assertEqual(event.event_type, "payment.received")
        self.assertEqual(event.order_id, "ORD-001")


class TestNotificationEvents(unittest.TestCase):
    """Tests for notification events"""

    def test_notification_request_event(self):
        """Notification request event"""
        from src.core.events import NotificationRequestEvent

        event = NotificationRequestEvent(
            channel="telegram",
            target="-100123456",
            message="Test message",
            priority=8
        )
        self.assertEqual(event.event_type, "notification.request")
        self.assertEqual(event.priority, 8)
        self.assertEqual(event.max_retries, 3)


if __name__ == '__main__':
    unittest.main()
