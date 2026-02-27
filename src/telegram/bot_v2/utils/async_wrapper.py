"""
Async wrapper utilities.

Wraps synchronous DAO calls for aiogram's async event loop.

Usage:
    from src.telegram.bot_v2.utils import run_sync

    result = await run_sync(member_service.check_membership_valid, telegram_id)
    result = await run_sync(dao.create_order, telegram_id=123, plan_code='BASIC_M')
"""
import asyncio
from functools import partial
from typing import Any, Callable, TypeVar

from src.core.structured_logger import get_logger

logger = get_logger(__name__)

T = TypeVar('T')


async def run_sync(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    Execute a sync function in the thread pool.

    Uses asyncio.to_thread to offload blocking DAO calls,
    preventing aiogram's async event loop from stalling.

    Args:
        func: synchronous callable
        *args: positional arguments
        **kwargs: keyword arguments

    Returns:
        return value of func

    Example:
        status = await run_sync(member_service.check_membership_valid, telegram_id)
        order = await run_sync(
            order_generator.create_order,
            telegram_id=user_id,
            plan_code='PREMIUM_M'
        )
    """
    if kwargs:
        func = partial(func, **kwargs)
        return await asyncio.to_thread(func, *args)
    else:
        return await asyncio.to_thread(func, *args)


async def run_sync_with_timeout(
    func: Callable[..., T],
    *args: Any,
    timeout: float = 30.0,
    **kwargs: Any
) -> T:
    """
    Execute a sync function in the thread pool with a timeout.

    Args:
        func: synchronous callable
        *args: positional arguments
        timeout: timeout in seconds (default 30)
        **kwargs: keyword arguments

    Returns:
        return value of func

    Raises:
        asyncio.TimeoutError: if execution exceeds timeout

    Example:
        try:
            result = await run_sync_with_timeout(
                slow_dao_method,
                user_id,
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.error("DAO call timed out")
    """
    return await asyncio.wait_for(
        run_sync(func, *args, **kwargs),
        timeout=timeout
    )
