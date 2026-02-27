"""
Message bus tests

TDD: write tests first, then implement

Run:
    pytest tests/core_tests/test_message_bus.py -v
"""
import asyncio
import unittest


class TestMessageBus(unittest.TestCase):
    """Tests for message bus"""

    def test_singleton(self):
        """Message bus is a singleton"""
        from src.core.message_bus import get_message_bus

        bus1 = get_message_bus()
        bus2 = get_message_bus()
        self.assertIs(bus1, bus2)

    def test_subscribe_and_publish(self):
        """Subscribe and publish events"""
        from src.core.events import AlertDetectedEvent
        from src.core.message_bus import MessageBus

        # Create new instance (avoid singleton interference)
        bus = MessageBus.__new__(MessageBus)
        bus._initialized = False
        bus.__init__()

        received_events = []

        async def handler(event):
            received_events.append(event)

        bus.subscribe("scanner.alert_detected", handler)

        event = AlertDetectedEvent(
            symbol="BTC",
            alert_type="price_surge",
            score=90.0,
            message="Test"
        )

        asyncio.run(bus.publish(event))

        # Wait for event processing
        asyncio.run(asyncio.sleep(0.1))

        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0].symbol, "BTC")

    def test_trace_id_propagation(self):
        """trace_id propagation"""
        from src.core.events import AlertDetectedEvent
        from src.core.message_bus import MessageBus

        bus = MessageBus.__new__(MessageBus)
        bus._initialized = False
        bus.__init__()

        received_trace_id = []

        async def handler(event):
            received_trace_id.append(event.trace_id)

        bus.subscribe("scanner.alert_detected", handler)

        event = AlertDetectedEvent(
            symbol="BTC",
            alert_type="price_surge",
            score=90.0,
            message="Test"
        )

        asyncio.run(bus.publish(event, trace_id="test-trace-123"))
        asyncio.run(asyncio.sleep(0.1))

        self.assertEqual(received_trace_id[0], "test-trace-123")


class TestOnEventDecorator(unittest.TestCase):
    """Tests for event subscription decorator"""

    def test_decorator_registers_handler(self):
        """Decorator registers handler"""
        from src.core.events import AlertDetectedEvent
        from src.core.message_bus import get_message_bus, on_event

        call_count = [0]

        @on_event("scanner.alert_detected")
        async def test_handler(event):
            call_count[0] += 1

        bus = get_message_bus()
        event = AlertDetectedEvent(
            symbol="ETH",
            alert_type="volume_spike",
            score=75.0,
            message="Volume test"
        )

        asyncio.run(bus.publish(event))
        asyncio.run(asyncio.sleep(0.1))

        self.assertGreaterEqual(call_count[0], 1)


if __name__ == '__main__':
    unittest.main()
