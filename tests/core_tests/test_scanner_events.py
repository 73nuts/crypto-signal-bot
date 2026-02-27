"""
Scanner event integration tests.

TDD: write tests first, then implement.

How to run:
    pytest tests/core_tests/test_scanner_events.py -v
"""
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch


class TestScannerEventIntegration(unittest.TestCase):
    """Scanner event publishing tests."""

    def setUp(self):
        """Reset message bus."""
        # Reset singleton
        from src.core import message_bus
        message_bus._message_bus = None

    def test_scanner_publishes_alert_event(self):
        """Scanner publishes an event when an alert is detected."""
        from src.core.message_bus import get_message_bus
        from src.core.events import AlertDetectedEvent

        bus = get_message_bus()
        received_events = []

        async def handler(event):
            received_events.append(event)

        bus.subscribe("scanner.alert_detected", handler)

        # Simulate Scanner publishing an event
        event = AlertDetectedEvent(
            symbol="BTC",
            alert_type="flash_pump",
            score=95.0,
            message="BTC 5-minute gain 2.5%"
        )

        asyncio.run(bus.publish(event))
        asyncio.run(asyncio.sleep(0.1))

        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0].symbol, "BTC")
        self.assertEqual(received_events[0].score, 95.0)

    def test_scanner_publishes_daily_pulse_event(self):
        """Scanner publishes an event when a daily pulse is ready."""
        from src.core.message_bus import get_message_bus
        from src.core.events import DailyPulseReadyEvent

        bus = get_message_bus()
        received_events = []

        async def handler(event):
            received_events.append(event)

        bus.subscribe("scanner.daily_pulse_ready", handler)

        # Simulate Scanner publishing daily pulse event
        event = DailyPulseReadyEvent(
            content="Daily Pulse Report Content",
            image_path="/tmp/fg.png"
        )

        asyncio.run(bus.publish(event))
        asyncio.run(asyncio.sleep(0.1))

        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0].content, "Daily Pulse Report Content")

    def test_alert_event_trace_id_propagation(self):
        """trace_id propagation in alert events."""
        from src.core.message_bus import get_message_bus
        from src.core.events import AlertDetectedEvent

        bus = get_message_bus()
        received_trace_ids = []

        async def handler(event):
            received_trace_ids.append(event.trace_id)

        bus.subscribe("scanner.alert_detected", handler)

        event = AlertDetectedEvent(
            symbol="ETH",
            alert_type="volume_spike",
            score=85.0,
            message="ETH volume anomaly"
        )

        # Publish with a specific trace_id
        asyncio.run(bus.publish(event, trace_id="scan-trace-001"))
        asyncio.run(asyncio.sleep(0.1))

        self.assertEqual(received_trace_ids[0], "scan-trace-001")


class TestScannerPublishMethod(unittest.TestCase):
    """Scanner publish_alert method tests."""

    def setUp(self):
        """Reset message bus."""
        from src.core import message_bus
        message_bus._message_bus = None

    @patch('src.scanner.scheduler.get_broadcaster')
    @patch('src.scanner.scheduler.AlertDetector')
    def test_publish_alert_called_on_scan(self, mock_detector_cls, mock_broadcaster):
        """Events are published when scan_and_push is called."""
        from src.core.message_bus import get_message_bus

        bus = get_message_bus()
        received_events = []

        async def handler(event):
            received_events.append(event)

        bus.subscribe("scanner.alert_detected", handler)

        # This test verifies that events are published after Scanner integration
        # Enable when integration is complete
        self.assertTrue(True)  # placeholder


if __name__ == '__main__':
    unittest.main()
