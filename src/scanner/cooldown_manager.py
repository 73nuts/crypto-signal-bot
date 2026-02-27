"""
Unified Cooldown Manager

Responsibilities:
1. Centralized cooldown logic for all Scanner modules
2. Redis persistence (survives process restarts)
3. Cross-type cooldown (same coin sends at most 1 alert per 10 minutes)
4. Direction-aware (reverse signals can bypass cooldown)
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class CooldownType:
    """Cooldown type constants"""
    ALERT = 'alert'           # Anomaly radar
    SPREAD = 'spread'         # Spread monitor
    ORDERBOOK = 'orderbook'   # Orderbook monitor
    CROSS_TYPE = 'cross_type' # Cross-type cooldown
    GLOBAL = 'global'         # Global cooldown


class Direction:
    """Direction constants"""
    PUMP = 'PUMP'
    DROP = 'DROP'
    NEUTRAL = 'NEUTRAL'
    # Spread directions
    PREMIUM = 'PREMIUM'   # Futures premium
    DISCOUNT = 'DISCOUNT' # Futures discount
    # Orderbook directions
    BID_WALL = 'BID_WALL' # Bid wall
    ASK_WALL = 'ASK_WALL' # Ask wall


class CooldownManager:
    """
    Unified Cooldown Manager

    Features:
    - Per-type cooldown (alert 4h, spread 2h, orderbook 1h)
    - Cross-type cooldown (same coin sends at most 1 alert per 10 minutes)
    - Direction-aware (same direction cooldown, opposite direction bypasses)
    - Redis persistence (optional, defaults to memory)
    """

    # Cooldown time config (seconds)
    COOLDOWN_SECONDS: Dict[str, int] = {
        CooldownType.ALERT: 4 * 3600,       # Alert: 4 hours
        CooldownType.SPREAD: 2 * 3600,      # Spread: 2 hours
        CooldownType.ORDERBOOK: 60 * 60,    # Orderbook: 1 hour
        CooldownType.CROSS_TYPE: 10 * 60,   # Cross-type: 10 minutes
        CooldownType.GLOBAL: 60 * 60,       # Global: 60 minutes
    }

    def __init__(self, cache_manager=None, use_redis: bool = True):
        """
        Initialize cooldown manager

        Args:
            cache_manager: CacheManager instance, None uses memory storage
            use_redis: Whether to use Redis persistence
        """
        self._use_redis = use_redis and cache_manager is not None
        self._cache = cache_manager

        # Memory storage (fallback or when Redis disabled)
        self._memory_store: Dict[str, datetime] = {}

        # Global cooldown record
        self._last_global_push: Optional[datetime] = None

        logger.info(
            f"CooldownManager initialized, use_redis={self._use_redis}"
        )

    def _make_key(
        self,
        cooldown_type: str,
        symbol: str,
        direction: str = Direction.NEUTRAL
    ) -> str:
        """
        Generate cooldown key

        Format: cooldown:{type}:{symbol}_{direction}
        """
        return f"cooldown:{cooldown_type}:{symbol}_{direction}"

    async def _get_timestamp(self, key: str) -> Optional[datetime]:
        """Get cooldown timestamp (async)"""
        if self._use_redis:
            try:
                value = await self._cache.get(key)
                if value:
                    return datetime.fromisoformat(value)
            except Exception as e:
                logger.warning(f"Redis get failed, fallback to memory: {e}")

        # Memory fallback
        return self._memory_store.get(key)

    async def _set_timestamp(self, key: str, ts: datetime, ttl_seconds: int) -> None:
        """Set cooldown timestamp (async)"""
        if self._use_redis:
            try:
                # TTL = cooldown time + 10 minute buffer
                ttl = ttl_seconds + 600
                await self._cache.set(key, ts.isoformat(), ttl=ttl)
                return
            except Exception as e:
                logger.warning(f"Redis set failed, fallback to memory: {e}")

        # Memory fallback
        self._memory_store[key] = ts

    async def check(
        self,
        cooldown_type: str,
        symbol: str,
        direction: str = Direction.NEUTRAL
    ) -> bool:
        """
        Check if push is allowed (async)

        Args:
            cooldown_type: Cooldown type (alert/spread/orderbook)
            symbol: Coin symbol
            direction: Direction (PUMP/DROP/NEUTRAL etc.)

        Returns:
            True = can push, False = on cooldown
        """
        key = self._make_key(cooldown_type, symbol, direction)
        last_push = await self._get_timestamp(key)

        if last_push is None:
            return True

        cooldown_seconds = self.COOLDOWN_SECONDS.get(cooldown_type, 3600)
        elapsed = (datetime.now(timezone.utc) - last_push).total_seconds()

        can_push = elapsed >= cooldown_seconds

        if not can_push:
            remaining_min = (cooldown_seconds - elapsed) / 60
            logger.debug(
                f"Cooldown active: {key}, remaining={remaining_min:.1f}min"
            )

        return can_push

    async def update(
        self,
        cooldown_type: str,
        symbol: str,
        direction: str = Direction.NEUTRAL
    ) -> None:
        """
        Update cooldown record (async)

        Args:
            cooldown_type: Cooldown type
            symbol: Coin symbol
            direction: Direction
        """
        key = self._make_key(cooldown_type, symbol, direction)
        now = datetime.now(timezone.utc)
        ttl = self.COOLDOWN_SECONDS.get(cooldown_type, 3600)

        await self._set_timestamp(key, now, ttl)
        logger.debug(f"Cooldown updated: {key}")

    async def check_cross_type(self, symbol: str) -> bool:
        """
        Cross-type cooldown check (async)

        Same coin sends at most 1 alert per 10 minutes, regardless of type.

        Args:
            symbol: Coin symbol

        Returns:
            True = can push, False = on cooldown
        """
        return await self.check(CooldownType.CROSS_TYPE, symbol, Direction.NEUTRAL)

    async def update_cross_type(self, symbol: str) -> None:
        """Update cross-type cooldown (async)"""
        await self.update(CooldownType.CROSS_TYPE, symbol, Direction.NEUTRAL)

    def check_global(self) -> bool:
        """
        Global cooldown check

        Shared 60-minute cooldown across all types.

        Returns:
            True = can push, False = on cooldown
        """
        if self._last_global_push is None:
            return True

        elapsed = (datetime.now(timezone.utc) - self._last_global_push).total_seconds()
        cooldown_seconds = self.COOLDOWN_SECONDS[CooldownType.GLOBAL]

        return elapsed >= cooldown_seconds

    def update_global(self) -> None:
        """Update global cooldown"""
        self._last_global_push = datetime.now(timezone.utc)

    async def get_remaining(
        self,
        cooldown_type: str,
        symbol: str,
        direction: str = Direction.NEUTRAL
    ) -> float:
        """
        Get remaining cooldown time (minutes, async)

        Returns:
            Remaining cooldown in minutes, 0 means no cooldown
        """
        key = self._make_key(cooldown_type, symbol, direction)
        last_push = await self._get_timestamp(key)

        if last_push is None:
            return 0

        cooldown_seconds = self.COOLDOWN_SECONDS.get(cooldown_type, 3600)
        elapsed = (datetime.now(timezone.utc) - last_push).total_seconds()
        remaining = cooldown_seconds - elapsed

        return max(0, remaining / 60)

    async def clear(
        self,
        cooldown_type: str,
        symbol: str,
        direction: str = Direction.NEUTRAL
    ) -> None:
        """
        Clear specific cooldown (admin function, async)
        """
        key = self._make_key(cooldown_type, symbol, direction)

        if self._use_redis:
            try:
                await self._cache.delete(key)
            except Exception as e:
                logger.warning(f"Redis delete failed: {e}")

        # Memory clear
        self._memory_store.pop(key, None)
        logger.info(f"Cooldown cleared: {key}")

    def get_status(self) -> Dict[str, Any]:
        """
        Get cooldown status summary (for debugging)
        """
        now = datetime.now(timezone.utc)
        global_remaining = 0

        if self._last_global_push:
            elapsed = (now - self._last_global_push).total_seconds()
            global_remaining = max(
                0,
                (self.COOLDOWN_SECONDS[CooldownType.GLOBAL] - elapsed) / 60
            )

        return {
            'use_redis': self._use_redis,
            'global_cooldown_remaining_min': round(global_remaining, 1),
            'memory_entries': len(self._memory_store),
        }

    # ==========================================
    # Convenience methods: Alert radar (async)
    # ==========================================

    async def check_alert(self, symbol: str, direction: str) -> bool:
        """Check alert cooldown (async)"""
        return await self.check(CooldownType.ALERT, symbol, direction)

    async def update_alert(self, symbol: str, direction: str) -> None:
        """Update alert cooldown (async)"""
        await self.update(CooldownType.ALERT, symbol, direction)

    # ==========================================
    # Convenience methods: Spread monitor (async)
    # ==========================================

    async def check_spread(self, symbol: str, direction: str = Direction.NEUTRAL) -> bool:
        """Check spread cooldown (async)"""
        return await self.check(CooldownType.SPREAD, symbol, direction)

    async def update_spread(self, symbol: str, direction: str = Direction.NEUTRAL) -> None:
        """Update spread cooldown (async)"""
        await self.update(CooldownType.SPREAD, symbol, direction)

    # ==========================================
    # Convenience methods: Orderbook monitor (async)
    # ==========================================

    async def check_orderbook(self, symbol: str, direction: str = Direction.NEUTRAL) -> bool:
        """Check orderbook cooldown (async)"""
        return await self.check(CooldownType.ORDERBOOK, symbol, direction)

    async def update_orderbook(self, symbol: str, direction: str = Direction.NEUTRAL) -> None:
        """Update orderbook cooldown (async)"""
        await self.update(CooldownType.ORDERBOOK, symbol, direction)

    # ==========================================
    # Static helper methods
    # ==========================================

    @staticmethod
    def get_direction_from_alert_type(alert_type: str) -> str:
        """
        Extract direction from alert type

        Args:
            alert_type: Alert type (flash_pump, flash_drop, volume_spike, etc.)

        Returns:
            Direction: PUMP/DROP/NEUTRAL
        """
        alert_lower = alert_type.lower()
        if 'pump' in alert_lower:
            return Direction.PUMP
        elif 'drop' in alert_lower:
            return Direction.DROP
        else:
            return Direction.NEUTRAL

    @staticmethod
    def get_direction_from_spread(spread_pct: float) -> str:
        """
        Extract direction from spread value

        Args:
            spread_pct: Spread percentage (positive=futures premium, negative=futures discount)

        Returns:
            Direction: PREMIUM/DISCOUNT
        """
        return Direction.PREMIUM if spread_pct > 0 else Direction.DISCOUNT

    @staticmethod
    def get_direction_from_orderbook(bid_depth: float, ask_depth: float) -> str:
        """
        Extract direction from orderbook depth

        Args:
            bid_depth: Bid depth
            ask_depth: Ask depth

        Returns:
            Direction: BID_WALL/ASK_WALL
        """
        return Direction.BID_WALL if bid_depth > ask_depth else Direction.ASK_WALL


# ==========================================
# Global singleton
# ==========================================

_cooldown_manager: Optional[CooldownManager] = None


def get_cooldown_manager() -> CooldownManager:
    """
    Get global CooldownManager instance

    Initializes on first call, using CacheManager for Redis persistence.
    """
    global _cooldown_manager

    if _cooldown_manager is None:
        try:
            from src.core.cache import CacheManager
            cache = CacheManager()
            _cooldown_manager = CooldownManager(cache_manager=cache, use_redis=True)
        except Exception as e:
            logger.warning(f"CacheManager init failed, using memory mode: {e}")
            _cooldown_manager = CooldownManager(cache_manager=None, use_redis=False)

    return _cooldown_manager
