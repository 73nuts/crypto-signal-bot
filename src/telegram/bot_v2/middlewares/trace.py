"""
TraceMiddleware - distributed tracing middleware.

Features:
1. Generate trace_id for each Update
2. Inject into context for handler access
3. Integrates with TraceContext
"""
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Update

from src.core.tracing import TraceContext


class TraceMiddleware(BaseMiddleware):
    """trace_id injection middleware."""

    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any]
    ) -> Any:
        """
        Create TraceContext for each request.

        Injects into data:
        - trace_id: trace ID
        - trace_ctx: TraceContext instance
        """
        user_id = None
        if event.message and event.message.from_user:
            user_id = str(event.message.from_user.id)
        elif event.callback_query and event.callback_query.from_user:
            user_id = str(event.callback_query.from_user.id)

        async with TraceContext(user_id=user_id) as ctx:
            data['trace_id'] = ctx.trace_id
            data['trace_ctx'] = ctx

            return await handler(event, data)
