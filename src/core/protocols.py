"""
Core Protocol definitions

Uses Python Protocol (PEP 544) for structural subtyping.
Supports both static type checking and runtime isinstance checks.
"""
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, runtime_checkable

# ========================================
# Trading Protocols
# ========================================

@runtime_checkable
class TradingClientProtocol(Protocol):
    """
    Trading client Protocol

    Defines the standard interface for exchange interaction.
    """

    def get_balance(self) -> Dict[str, float]:
        """Get account balance"""
        ...

    def invalidate_cache(self) -> None:
        """Clear cache (call after placing orders)"""
        ...

    def create_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float
    ) -> Dict[str, Any]:
        """Create a market order"""
        ...

    def create_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float
    ) -> Dict[str, Any]:
        """Create a stop order"""
        ...

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an order"""
        ...

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get position info"""
        ...


@runtime_checkable
class TradingClientFactoryProtocol(Protocol):
    """
    Trading client factory Protocol

    Used to create trading client instances per symbol.
    Supports lazy initialization and DI testing.
    """

    def create(self, symbol: str, testnet: bool = True) -> TradingClientProtocol:
        """
        Create a trading client.

        Args:
            symbol: Trading pair (BTC, ETH, etc.)
            testnet: Whether to use testnet

        Returns:
            TradingClientProtocol instance
        """
        ...


# ========================================
# Repository Protocols
# ========================================

@runtime_checkable
class PositionRepositoryProtocol(Protocol):
    """
    Position repository Protocol

    Defines the standard interface for position data access.
    """

    def find_by_id(self, position_id: int) -> Optional[Dict[str, Any]]:
        """Find position by ID"""
        ...

    def find_open_positions(self, symbol: str = None) -> List[Dict[str, Any]]:
        """Find open positions"""
        ...

    def create(self, position: Dict[str, Any]) -> int:
        """Create a position, returns ID"""
        ...

    def update_status(self, position_id: int, status: str) -> bool:
        """Update position status"""
        ...


@runtime_checkable
class MembershipRepositoryProtocol(Protocol):
    """
    Membership repository Protocol

    Defines the standard interface for member data access.
    """

    def find_by_telegram_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Find member by Telegram ID"""
        ...

    def find_by_binance_uid(self, binance_uid: str) -> Optional[Dict[str, Any]]:
        """Find member by Binance UID"""
        ...

    def create(self, member: Dict[str, Any]) -> int:
        """Create a member, returns ID"""
        ...

    def find_active_members(self) -> List[Dict[str, Any]]:
        """Find active members"""
        ...


# ========================================
# Service Protocols
# ========================================

@runtime_checkable
class MemberServiceProtocol(Protocol):
    """
    Member service Protocol

    Defines the standard interface for member business logic.
    (Aligned with the actual MemberService implementation)
    """

    def activate_or_renew(
        self,
        telegram_id: int,
        plan_code: str,
        days: int
    ) -> Dict[str, Any]:
        """Activate or renew membership"""
        ...

    def check_membership_valid(self, telegram_id: int) -> Dict[str, Any]:
        """Check if membership is valid; returns detailed status"""
        ...

    def get_user_membership_info(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Get full membership info"""
        ...

    def force_expire_membership(self, telegram_id: int) -> bool:
        """Force-expire membership"""
        ...

    def get_active_members(self, min_level: int = 0) -> List[int]:
        """Get list of active member IDs"""
        ...

    def count_premium_users(self) -> int:
        """Count premium users"""
        ...


# ========================================
# Notification Protocols
# ========================================

@runtime_checkable
class NotifierProtocol(Protocol):
    """
    Notifier Protocol

    Defines the standard interface for single-user notifications.
    """

    async def send(
        self,
        user_id: int,
        message: str,
        **kwargs
    ) -> bool:
        """Send a notification to a single user"""
        ...


@runtime_checkable
class BroadcasterProtocol(Protocol):
    """
    Broadcaster Protocol

    Defines the standard interface for group broadcasts.
    """

    async def broadcast(
        self,
        group_id: int,
        message: str,
        **kwargs
    ) -> bool:
        """Broadcast a message to a group"""
        ...


# ========================================
# Infrastructure Protocols
# ========================================

@runtime_checkable
class MessageBusProtocol(Protocol):
    """
    Message bus Protocol

    Defines the standard interface for event publish/subscribe.
    """

    async def publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Publish an event"""
        ...

    def subscribe(
        self,
        event_type: str,
        handler: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Subscribe to an event"""
        ...


@runtime_checkable
class DatabasePoolProtocol(Protocol):
    """
    Database connection pool Protocol

    Defines the standard interface for database access.
    """

    def get_connection(self):
        """Get a database connection"""
        ...

    async def execute(self, query: str, params: tuple = None) -> int:
        """Execute SQL, returns affected row count"""
        ...

    async def fetch_one(self, query: str, params: tuple = None) -> Optional[Dict[str, Any]]:
        """Query a single record"""
        ...

    async def fetch_all(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        """Query multiple records"""
        ...

    @asynccontextmanager
    async def transaction(self):
        """Transaction context manager"""
        ...

    def health_check(self) -> bool:
        """Health check"""
        ...


@runtime_checkable
class CacheProtocol(Protocol):
    """
    Cache service Protocol

    Defines the standard interface for unified cache access.
    """

    def make_key(self, service: str, entity: str, identifier: str = "") -> str:
        """Generate a normalized cache key"""
        ...

    async def get(self, key: str) -> Optional[Any]:
        """Get a cached value"""
        ...

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int = 300,
        tags: List[str] = None
    ) -> bool:
        """Set a cached value"""
        ...

    async def delete(self, key: str) -> bool:
        """Delete a cached value"""
        ...

    async def invalidate_by_tag(self, tag: str) -> int:
        """Bulk invalidate by tag"""
        ...

    async def health_check(self) -> bool:
        """Health check"""
        ...


# ========================================
# Strategy Service Protocols
# ========================================

@runtime_checkable
class StopLossServiceProtocol(Protocol):
    """
    Stop loss service Protocol

    Defines the standard interface for stop loss management.
    """

    def calculate_initial_stop(
        self,
        price: float,
        atr: float,
        stop_type: str,
        trailing_mult: Optional[float] = None
    ) -> float:
        """Calculate initial stop loss price"""
        ...

    def create_stop_loss_order(
        self,
        client: TradingClientProtocol,
        symbol: str,
        quantity: float,
        stop_price: float,
        position_id: int
    ) -> Optional[str]:
        """Create a stop loss order, returns order ID"""
        ...

    def update_trailing_stop(
        self,
        client: TradingClientProtocol,
        symbol: str,
        position: Dict[str, Any],
        new_stop: float
    ) -> Optional[Dict[str, Any]]:
        """Update trailing stop"""
        ...

    def attach_sl_tp_after_fill(
        self,
        client: TradingClientProtocol,
        symbol: str,
        position_id: int,
        quantity: float,
        entry_price: float,
        stop_loss: float,
        entry_atr: float,
        strategy_name: str,
        stop_type: str,
        max_leverage: int
    ) -> Dict[str, Any]:
        """Attach stop loss and take profit after limit order fill"""
        ...


# ========================================
# Type aliases
# ========================================

# Event handler type
EventHandler = Callable[[Dict[str, Any]], Awaitable[None]]
