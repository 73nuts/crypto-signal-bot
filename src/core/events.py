"""
Event definitions module

Base class and concrete event definitions for all business events.
Uses Pydantic to ensure type safety and immutability.
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    """
    Base event class

    All events must inherit from this class, providing:
    - event_id: unique identifier
    - event_type: event type
    - timestamp: creation time
    - trace_id: distributed trace ID
    - source: event source
    """

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)
    trace_id: Optional[str] = None
    source: str = ""

    model_config = {"frozen": True}  # immutable


# ==========================================
# Scanner events
# ==========================================

class AlertDetectedEvent(BaseEvent):
    """Market alert detected event"""

    event_type: str = "scanner.alert_detected"
    source: str = "scanner"

    symbol: str
    alert_type: str  # price_surge, volume_spike, funding_extreme
    score: float
    message: str
    data: dict = Field(default_factory=dict)
    # Language-separated messages: {'zh': '...', 'en': '...'}
    messages_by_lang: dict = Field(default_factory=dict)


class DailyPulseReadyEvent(BaseEvent):
    """Daily pulse report ready event"""

    event_type: str = "scanner.daily_pulse_ready"
    source: str = "scanner"

    content: str  # Premium version (full content) - backward compatible
    image_path: Optional[str] = None
    # Multi-channel differentiated content
    content_hook: Optional[str] = None  # Basic version (partial content hidden) - backward compatible
    content_text: Optional[str] = None  # WeChat version (plain text)
    # Language-separated content: {'zh': '...', 'en': '...'}
    content_by_lang: dict = Field(default_factory=dict)  # Premium version
    content_hook_by_lang: dict = Field(default_factory=dict)  # Basic version
    # target language (for filtering push channels)
    target_lang: str = ''  # 'zh' | 'en' | '' (empty = all, backward compatible)


class SpreadDetectedEvent(BaseEvent):
    """
    Spread detected event

    Spot-futures spread monitoring for arbitrage opportunity detection.
    """

    event_type: str = "scanner.spread_detected"
    source: str = "scanner"

    symbol: str
    spot_price: float
    futures_price: float
    spread_pct: float       # (futures-spot)/spot*100
    spread_type: str        # 'PREMIUM' | 'DISCOUNT'

    # Language-separated messages
    messages_by_lang: dict = Field(default_factory=dict)       # Premium version
    messages_basic_by_lang: dict = Field(default_factory=dict)  # Basic version (with FOMO)

    # Additional data (e.g. threshold config)
    data: dict = Field(default_factory=dict)


class OrderbookImbalanceEvent(BaseEvent):
    """
    Order book imbalance event (D.1.2)

    Detects severe imbalance between bid/ask depth, which may signal large order pressure or support.
    """

    event_type: str = "scanner.orderbook_imbalance"
    source: str = "scanner"

    symbol: str
    imbalance_ratio: float      # bid_depth / ask_depth
    imbalance_side: str         # 'BID_HEAVY' | 'ASK_HEAVY'
    imbalance_pct: float        # imbalance percentage (0-100)
    bid_depth_usd: float        # bid depth (USD)
    ask_depth_usd: float        # ask depth (USD)

    # Language-separated messages
    messages_by_lang: dict = Field(default_factory=dict)        # Premium version
    messages_basic_by_lang: dict = Field(default_factory=dict)  # Basic version (with FOMO)

    # Additional data (e.g. threshold config)
    data: dict = Field(default_factory=dict)


# ==========================================
# Swing strategy events
# ==========================================

class SignalGeneratedEvent(BaseEvent):
    """Trading signal generated event"""

    event_type: str = "swing.signal_generated"
    source: str = "swing"

    symbol: str
    direction: str  # LONG, SHORT
    entry_price: float
    stop_loss: float
    position_size: float
    strategy_name: str


class PositionOpenedEvent(BaseEvent):
    """Position opened event"""

    event_type: str = "swing.position_opened"
    source: str = "swing"

    position_id: int
    symbol: str
    entry_price: float
    quantity: float
    # Fields required by notification handlers
    strategy_name: str = ""
    stop_loss: float = 0.0
    leverage: float = 1.0


class PositionClosedEvent(BaseEvent):
    """Position closed event"""

    event_type: str = "swing.position_closed"
    source: str = "swing"

    position_id: int
    symbol: str
    exit_price: float
    pnl_percent: float
    # Fields required by notification handlers
    entry_price: float = 0.0
    reason: str = ""
    telegram_message_id: Optional[int] = None  # Used to reply to entry message


class StopLossMovedEvent(BaseEvent):
    """Stop loss moved event"""

    event_type: str = "swing.stop_loss_moved"
    source: str = "swing"

    position_id: int
    symbol: str
    old_stop: float
    new_stop: float


# ==========================================
# Payment events
# ==========================================

class PaymentReceivedEvent(BaseEvent):
    """Payment confirmed event"""

    event_type: str = "payment.received"
    source: str = "payment"

    order_id: str
    telegram_id: int
    amount: float
    tx_hash: str
    # Fields required by notification handlers
    plan_code: str = ""
    duration_days: int = 0


class MembershipActivatedEvent(BaseEvent):
    """Membership activated event"""

    event_type: str = "membership.activated"
    source: str = "membership"

    telegram_id: int
    plan_code: str
    expire_date: datetime


# ==========================================
# Notification events
# ==========================================

class NotificationRequestEvent(BaseEvent):
    """Notification request event"""

    event_type: str = "notification.request"
    source: str = "system"

    channel: str  # telegram, email, wechat
    target: str   # chat_id, email, etc.
    message: str
    priority: int = 5  # 1-10, 10 is highest
    retry_count: int = 0
    max_retries: int = 3
