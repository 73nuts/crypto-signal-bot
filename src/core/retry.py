"""
General-purpose retry utilities

Provides:
- Exponential backoff retry decorator
- Sync/async support
- Configurable retry strategies

Usage:
    @retry(max_attempts=3, base_delay=1.0, exceptions=(requests.RequestException,))
    def call_api():
        ...

    @async_retry(max_attempts=3, base_delay=1.0)
    async def async_call():
        ...
"""

import asyncio
import functools
import logging
import time
from typing import Any, Callable, Optional, Tuple, Type

logger = logging.getLogger(__name__)


class RetryExhausted(Exception):
    """Raised when all retry attempts are exhausted"""

    def __init__(self, attempts: int, last_exception: Exception):
        self.attempts = attempts
        self.last_exception = last_exception
        super().__init__(
            f"Failed after {attempts} retries: {last_exception}"
        )


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[int, Exception], None]] = None
):
    """
    Synchronous retry decorator

    Args:
        max_attempts: Maximum attempt count (including first attempt)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        exponential: Whether to use exponential backoff
        exceptions: Exception types that trigger a retry
        on_retry: Callback on retry (attempt, exception) -> None

    Returns:
        Decorator function
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt >= max_attempts:
                        logger.error(
                            f"[Retry] {func.__name__} failed after {attempt} attempts: {e}"
                        )
                        raise RetryExhausted(attempt, e) from e

                    # Calculate delay
                    if exponential:
                        delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    else:
                        delay = base_delay

                    logger.warning(
                        f"[Retry] {func.__name__} attempt {attempt} failed: {e}, "
                        f"retrying in {delay:.1f}s..."
                    )

                    # Trigger callback
                    if on_retry:
                        try:
                            on_retry(attempt, e)
                        except Exception as cb_err:
                            logger.error(f"[Retry] Callback failed: {cb_err}")

                    time.sleep(delay)

            raise RetryExhausted(max_attempts, last_exception)

        return wrapper
    return decorator


def async_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[int, Exception], None]] = None
):
    """
    Asynchronous retry decorator

    Args:
        max_attempts: Maximum attempt count (including first attempt)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        exponential: Whether to use exponential backoff
        exceptions: Exception types that trigger a retry
        on_retry: Callback on retry (attempt, exception) -> None

    Returns:
        Decorator function
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt >= max_attempts:
                        logger.error(
                            f"[AsyncRetry] {func.__name__} failed after {attempt} attempts: {e}"
                        )
                        raise RetryExhausted(attempt, e) from e

                    # Calculate delay
                    if exponential:
                        delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    else:
                        delay = base_delay

                    logger.warning(
                        f"[AsyncRetry] {func.__name__} attempt {attempt} failed: {e}, "
                        f"retrying in {delay:.1f}s..."
                    )

                    # Trigger callback
                    if on_retry:
                        try:
                            on_retry(attempt, e)
                        except Exception as cb_err:
                            logger.error(f"[AsyncRetry] Callback failed: {cb_err}")

                    await asyncio.sleep(delay)

            raise RetryExhausted(max_attempts, last_exception)

        return wrapper
    return decorator


def retry_call(
    func: Callable,
    args: tuple = (),
    kwargs: dict = None,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
) -> Any:
    """
    Functional retry call (non-decorator style)

    Args:
        func: Function to call
        args: Positional arguments
        kwargs: Keyword arguments
        max_attempts: Maximum attempt count
        base_delay: Base delay in seconds
        exceptions: Exception types that trigger a retry

    Returns:
        Function return value

    Raises:
        RetryExhausted: All retries exhausted
    """
    kwargs = kwargs or {}

    @retry(max_attempts=max_attempts, base_delay=base_delay, exceptions=exceptions)
    def _call():
        return func(*args, **kwargs)

    return _call()
