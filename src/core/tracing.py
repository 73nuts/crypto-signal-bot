"""
Distributed tracing context management

Propagates trace_id across async boundaries using contextvars.
"""
import contextvars
import functools
import uuid
from typing import Any, Dict

# Context Variables
_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar('trace_id', default='')
_span_id: contextvars.ContextVar[str] = contextvars.ContextVar('span_id', default='')
_user_id: contextvars.ContextVar[str] = contextvars.ContextVar('user_id', default='')
_extra_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    'extra_context', default={}
)


def generate_trace_id() -> str:
    """Generate a trace_id (UUID v4 short format, 16 chars)"""
    return uuid.uuid4().hex[:16]


def generate_span_id() -> str:
    """Generate a span_id (UUID v4 short format, 8 chars)"""
    return uuid.uuid4().hex[:8]


def get_trace_id() -> str:
    """Get current trace_id"""
    return _trace_id.get()


def get_span_id() -> str:
    """Get current span_id"""
    return _span_id.get()


def get_user_id() -> str:
    """Get current user_id"""
    return _user_id.get()


def get_context() -> Dict[str, Any]:
    """Get the full tracing context"""
    ctx = {
        'trace_id': _trace_id.get(),
        'span_id': _span_id.get(),
    }
    if _user_id.get():
        ctx['user_id'] = _user_id.get()
    ctx.update(_extra_context.get())
    return ctx


def set_trace_id(trace_id: str) -> contextvars.Token:
    """Set trace_id"""
    return _trace_id.set(trace_id)


def set_user_id(user_id: str) -> contextvars.Token:
    """Set user_id"""
    return _user_id.set(str(user_id))


def set_extra(key: str, value: Any) -> None:
    """Set additional context"""
    current = _extra_context.get().copy()
    current[key] = value
    _extra_context.set(current)


class TraceContext:
    """
    Tracing context manager

    Usage:
        async with TraceContext(user_id=12345):
            # All logs within this scope automatically carry trace_id
            logger.info("Processing started")
            await some_async_operation()
            logger.info("Processing complete")
    """

    def __init__(
        self,
        trace_id: str = None,
        user_id: str = None,
        **extra
    ):
        self.trace_id = trace_id or generate_trace_id()
        self.span_id = generate_span_id()
        self.user_id = str(user_id) if user_id else None
        self.extra = extra
        self._tokens = []

    def __enter__(self) -> 'TraceContext':
        self._tokens.append(_trace_id.set(self.trace_id))
        self._tokens.append(_span_id.set(self.span_id))
        if self.user_id:
            self._tokens.append(_user_id.set(self.user_id))
        if self.extra:
            current = _extra_context.get().copy()
            current.update(self.extra)
            self._tokens.append(_extra_context.set(current))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # contextvars restores automatically on context exit via Token mechanism
        pass

    async def __aenter__(self) -> 'TraceContext':
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return self.__exit__(exc_type, exc_val, exc_tb)


def trace_context(
    trace_id: str = None,
    user_id: str = None,
    user_id_arg: str = None,
    **extra
):
    """
    Decorator: add tracing context to a function.

    Usage:
        @trace_context(user_id_arg='telegram_id')
        async def handle_payment(telegram_id: int, amount: float):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract user_id from arguments
            uid = user_id
            if user_id_arg:
                uid = kwargs.get(user_id_arg)
                if uid is None and args:
                    # Try to get from positional arguments
                    import inspect
                    sig = inspect.signature(func)
                    params = list(sig.parameters.keys())
                    if user_id_arg in params:
                        idx = params.index(user_id_arg)
                        if idx < len(args):
                            uid = args[idx]

            async with TraceContext(trace_id=trace_id, user_id=uid, **extra):
                return await func(*args, **kwargs)

        return wrapper
    return decorator
