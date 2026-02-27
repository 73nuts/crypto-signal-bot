"""
Business constants for the Telegram module.

Centralized constant management to avoid scattering across files.

Categories:
- Membership management
- Signal push
- Payment
- Group config
- Orders
"""

from decimal import Decimal


# ============================================================
# Membership management (MemberService)
# ============================================================

# Membership cache TTL (seconds)
MEMBERSHIP_CACHE_TTL_SECONDS = 300  # 5 minutes

# Grace period after membership expiry before kicking (hours)
# T+1 kick strategy: 24-hour grace period for users
MEMBERSHIP_GRACE_PERIOD_HOURS = 24

# Max cache entries (prevent memory leak)
MEMBERSHIP_CACHE_MAX_SIZE = 1000


# ============================================================
# VIP management (Admin commands)
# ============================================================

# Max days for manual VIP extension
MAX_VIP_EXTENSION_DAYS = 365

# Default days for manual VIP extension
DEFAULT_VIP_EXTENSION_DAYS = 30


# ============================================================
# Signal push (VipSignalSender)
# ============================================================

# Signal dedup window (seconds) - same signal within this window is a duplicate
SIGNAL_DEDUP_WINDOW_SECONDS = 60

# Rate limit window (seconds) - minimum push interval per symbol
SIGNAL_RATE_LIMIT_SECONDS = 60

# Max signal tracking dict size (prevent memory leak)
SIGNAL_TRACKING_MAX_SIZE = 500


# ============================================================
# Payment (PaymentMonitor, OrderGenerator)
# ============================================================

# Payment amount tolerance (USDT)
# BEP20 transfers are exact; gas fees deduct from BNB, not USDT.
# 0.05U tolerance handles rounding display issues in some wallets.
PAYMENT_AMOUNT_TOLERANCE = Decimal("0.05")

# BSCScan API retry config
BSCSCAN_RETRY_MAX_ATTEMPTS = 3
BSCSCAN_RETRY_BASE_DELAY = 1.0  # seconds
BSCSCAN_RETRY_MAX_DELAY = 10.0  # seconds

# Block confirmations required
BLOCK_CONFIRMATIONS = 12

# BSCScan API rate limit delay (seconds) - free tier: 5 req/s
BSCSCAN_RATE_LIMIT_DELAY = 0.25


# ============================================================
# Orders (OrderGenerator)
# ============================================================

# Order ID character set (excludes ambiguous chars: 0O1IL)
ORDER_ID_CHARS = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'

# Order ID random suffix length
ORDER_ID_RANDOM_LENGTH = 4

# Order ID date format
ORDER_ID_DATE_FORMAT = '%Y%m%d'

# Pending order query limit
PENDING_ORDER_FETCH_LIMIT = 1


# ============================================================
# Callback retry (CallbackRetryTask)
# ============================================================

# Max callback retry attempts
CALLBACK_MAX_RETRY_ATTEMPTS = 3

# Callback retry base delay (seconds)
CALLBACK_RETRY_BASE_DELAY = 2.0

# Periodic scan interval (seconds)
CALLBACK_SCAN_INTERVAL = 3600  # 1 hour


# ============================================================
# BSC chain config (FundCollector)
# ============================================================

# Gas price (gwei)
BSC_GAS_PRICE_GWEI = 3

# Minimum BNB required on sub-address for gas
BSC_MIN_BNB_FOR_GAS = 0.0005

# USDT balance tolerance for amount comparison
BSC_USDT_BALANCE_TOLERANCE = Decimal("0.05")


# ============================================================
# User-facing messages
# ============================================================

# Payment confirming wait message
MSG_PAYMENT_CONFIRMING = (
    "Payment confirming, may take 1-2 minutes due to network latency..."
)

# Address allocation failure message
MSG_ADDRESS_ALLOCATION_FAILED = (
    "Payment channel busy, please retry in 30 seconds."
)

# Order creation failure message
MSG_ORDER_CREATION_FAILED = (
    "Order creation failed, please retry later."
)
