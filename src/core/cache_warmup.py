"""
Cache warmup module

Preloads hot data at application startup.
"""
import logging
from typing import List

from src.core.cache import get_cache

logger = logging.getLogger(__name__)


async def warmup_membership_plans():
    """
    Warm up membership plan cache.

    Plan data changes infrequently and can be cached for a long time.
    """
    try:
        # Delayed import to avoid circular dependencies
        from src.telegram.database.membership_plan_dao import MembershipPlanDAO

        cache = get_cache()
        plan_dao = MembershipPlanDAO()

        plans = plan_dao.get_all_enabled_plans()
        for plan in plans:
            key = cache.make_key("telegram", "plan", plan['plan_code'])
            await cache.set(key, plan, ttl=86400, tags=["telegram:plans"])

        logger.info(f"Warmed up plan cache: {len(plans)} plans")
    except Exception as e:
        logger.warning(f"Plan cache warmup failed: {e}")


async def warmup_scanner_volatility(symbols: List[str] = None, limit: int = 50):
    """
    Warm up volatility cache.

    Args:
        symbols: List of symbols to warm up; defaults to active symbols
        limit: Maximum number of symbols to warm up
    """
    try:
        # Delayed import to avoid circular dependencies
        from src.scanner.alert_detector import AlertDetector

        detector = AlertDetector()

        if symbols is None:
            # Get active symbols
            symbols = await detector.get_active_symbols()

        for symbol in symbols[:limit]:
            await detector.get_volatility(symbol)

        logger.info(f"Warmed up volatility cache: {min(len(symbols), limit)} symbols")
    except Exception as e:
        logger.warning(f"Volatility cache warmup failed: {e}")


async def warmup_active_members(limit: int = 100):
    """
    Warm up active member cache.

    Args:
        limit: Maximum number of members to warm up
    """
    try:
        # Delayed import to avoid circular dependencies
        from src.telegram.services.member_service import MemberService

        cache = get_cache()
        service = MemberService()

        # Get list of active member IDs
        member_ids = service.get_active_members(min_level=0)
        cached_count = 0

        for telegram_id in member_ids[:limit]:
            member = service.get_user_membership_info(telegram_id)
            if member:
                key = cache.make_key("telegram", "member", str(telegram_id))
                await cache.set(key, member, ttl=3600, tags=["telegram:members"])
                cached_count += 1

        logger.info(f"Warmed up member cache: {cached_count} members")
    except Exception as e:
        logger.warning(f"Member cache warmup failed: {e}")


async def warmup_all():
    """
    Run all warmup tasks.

    Call after application startup, or optionally on a schedule.
    """
    logger.info("Starting cache warmup...")

    # Run warmup tasks in priority order
    warmup_tasks = [
        ("membership_plans", warmup_membership_plans),
        # scanner and member warmup may depend on external services; optional
    ]

    success_count = 0
    for name, task in warmup_tasks:
        try:
            await task()
            success_count += 1
        except Exception as e:
            logger.error(f"Warmup task '{name}' failed: {e}")

    logger.info(f"Cache warmup completed: {success_count}/{len(warmup_tasks)} tasks")


async def invalidate_all_plans():
    """
    Invalidate all membership plan cache entries.

    Call after plan configuration changes.
    """
    cache = get_cache()
    count = await cache.invalidate_by_tag("telegram:plans")
    logger.info(f"Invalidated {count} plan cache entries")
    return count


async def invalidate_all_members():
    """
    Invalidate all member cache entries.

    Call after bulk member operations.
    """
    cache = get_cache()
    count = await cache.invalidate_by_tag("telegram:members")
    logger.info(f"Invalidated {count} member cache entries")
    return count
