"""
Message bus module

EventBus wrapper based on bubus, providing:
1. Unified event publish/subscribe interface
2. Automatic trace_id propagation
3. Singleton pattern
4. Decorator syntax sugar
"""
import asyncio
import logging
from typing import Callable, Optional, Dict, List
from functools import wraps
from uuid import uuid4

from bubus import EventBus as BubusEventBus

from src.core.events import BaseEvent

logger = logging.getLogger(__name__)


class MessageBus:
    """
    Message bus (singleton)

    Wraps bubus EventBus with a unified event publish/subscribe interface.
    """

    _instance: Optional['MessageBus'] = None

    def __new__(cls) -> 'MessageBus':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._bus = BubusEventBus()
        self._handlers: Dict[str, List[Callable]] = {}
        self._initialized = True

        logger.info("MessageBus initialized")

    def subscribe(
        self,
        event_type: str,
        handler: Callable,
        retry_on_failure: bool = True
    ):
        """
        Subscribe to an event.

        Args:
            event_type: Event type (e.g. "scanner.alert_detected")
            handler: Handler function (async or sync)
            retry_on_failure: Whether to retry on failure
        """
        @wraps(handler)
        async def wrapped_handler(event: BaseEvent):
            try:
                if asyncio.iscoroutinefunction(handler):
                    return await handler(event)
                else:
                    return handler(event)
            except Exception as e:
                logger.error(
                    f"Event handler failed: {event_type} | "
                    f"event_id={event.event_id} | "
                    f"trace_id={event.trace_id} | "
                    f"error={e}"
                )
                if not retry_on_failure:
                    raise

        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(wrapped_handler)

        logger.debug(f"Subscribed: {event_type} -> {handler.__name__}")

    async def publish(
        self,
        event: BaseEvent,
        trace_id: Optional[str] = None
    ):
        """
        Publish an event.

        Args:
            event: Event object
            trace_id: Distributed trace ID
        """
        # Set trace_id
        if trace_id:
            event = event.model_copy(update={"trace_id": trace_id})
        elif not event.trace_id:
            event = event.model_copy(update={"trace_id": str(uuid4())})

        logger.info(
            f"Publishing: {event.event_type} | "
            f"event_id={event.event_id} | "
            f"trace_id={event.trace_id}"
        )

        # Call all subscribed handlers
        handlers = self._handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(f"Handler error: {e}")

    def clear_handlers(self):
        """Clear all handlers (for testing)"""
        self._handlers.clear()


# Global singleton
_message_bus: Optional[MessageBus] = None


def get_message_bus() -> MessageBus:
    """Get the message bus singleton"""
    global _message_bus
    if _message_bus is None:
        _message_bus = MessageBus()
    return _message_bus


def on_event(event_type: str, **kwargs):
    """
    Event subscription decorator.

    Usage:
        @on_event("scanner.alert_detected")
        async def handle_alert(event):
            ...
    """
    def decorator(func: Callable):
        bus = get_message_bus()
        bus.subscribe(event_type, func, **kwargs)
        return func
    return decorator
