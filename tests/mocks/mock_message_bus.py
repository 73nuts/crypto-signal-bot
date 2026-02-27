"""
Mock message bus.

In-memory message bus for testing.
"""
from typing import Dict, Any, List, Callable, Awaitable
import asyncio


class MockMessageBus:
    """Mock message bus."""

    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._published_events: List[Dict[str, Any]] = []

    async def publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Publish an event."""
        self._published_events.append({
            'event_type': event_type,
            'payload': payload
        })

        # Call all subscribed handlers
        if event_type in self._handlers:
            for handler in self._handlers[event_type]:
                if asyncio.iscoroutinefunction(handler):
                    await handler(payload)
                else:
                    handler(payload)

    def subscribe(
        self,
        event_type: str,
        handler: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Subscribe to an event."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    # Test helper methods
    def get_published_events(self, event_type: str = None) -> List[Dict[str, Any]]:
        """Get published events (for testing)."""
        if event_type:
            return [e for e in self._published_events if e['event_type'] == event_type]
        return self._published_events

    def get_event_count(self, event_type: str = None) -> int:
        """Get event count (for testing)."""
        return len(self.get_published_events(event_type))

    def clear(self):
        """Clear all events and handlers (for testing)."""
        self._published_events.clear()
        self._handlers.clear()

    def clear_events(self):
        """Clear events only, keep handlers (for testing)."""
        self._published_events.clear()
