"""
Mock notifier.

Mock notification implementations for testing.
"""
from typing import Any, Dict, List


class MockNotifier:
    """Mock single-user notifier."""

    def __init__(self):
        self._sent_messages: List[Dict[str, Any]] = []

    async def send(
        self,
        user_id: int,
        message: str,
        **kwargs
    ) -> bool:
        """Send a notification."""
        self._sent_messages.append({
            'user_id': user_id,
            'message': message,
            **kwargs
        })
        return True

    # Test helper methods
    def get_sent_messages(self, user_id: int = None) -> List[Dict[str, Any]]:
        """Get sent messages (for testing)."""
        if user_id:
            return [m for m in self._sent_messages if m['user_id'] == user_id]
        return self._sent_messages

    def get_message_count(self, user_id: int = None) -> int:
        """Get message count (for testing)."""
        return len(self.get_sent_messages(user_id))

    def clear(self):
        """Clear messages (for testing)."""
        self._sent_messages.clear()


class MockBroadcaster:
    """Mock broadcaster."""

    def __init__(self):
        self._broadcasts: List[Dict[str, Any]] = []

    async def broadcast(
        self,
        group_id: int,
        message: str,
        **kwargs
    ) -> bool:
        """Broadcast a message."""
        self._broadcasts.append({
            'group_id': group_id,
            'message': message,
            **kwargs
        })
        return True

    # Test helper methods
    def get_broadcasts(self, group_id: int = None) -> List[Dict[str, Any]]:
        """Get broadcast messages (for testing)."""
        if group_id:
            return [b for b in self._broadcasts if b['group_id'] == group_id]
        return self._broadcasts

    def get_broadcast_count(self, group_id: int = None) -> int:
        """Get broadcast count (for testing)."""
        return len(self.get_broadcasts(group_id))

    def clear(self):
        """Clear broadcasts (for testing)."""
        self._broadcasts.clear()
