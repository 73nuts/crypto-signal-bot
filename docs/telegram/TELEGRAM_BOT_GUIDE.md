# Telegram Bot Guide

**Framework**: aiogram 3.x (Python) | **Languages**: English / Chinese

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Bot Commands](#3-bot-commands)
4. [Channel Setup](#4-channel-setup)
5. [Payment Flow](#5-payment-flow)
6. [Background Tasks](#6-background-tasks)
7. [State Machines](#7-state-machines)
8. [i18n System](#8-i18n-system)
9. [Adding New Routers](#9-adding-new-routers)
10. [Configuration Reference](#10-configuration-reference)
11. [File Structure](#11-file-structure)

---

## 1. Overview

The Ignis Telegram Bot delivers crypto signal alerts and manages member subscriptions. It supports two membership tiers:

| Tier | Access |
|------|--------|
| **Basic** | Market scanner alerts, Daily Pulse (summary version) |
| **Premium** | VIP strategy signals, full scanner, Daily Pulse (full version) |

Guests can interact with the bot but receive no signal content.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  Telegram Channels (4 channels)              │
├──────────────┬──────────────┬──────────────┬────────────────┤
│  Basic (ZH)  │  Basic (EN)  │ Premium (ZH) │ Premium (EN)   │
├──────────────┴──────────────┴──────────────┴────────────────┤
│                        Bot Core                              │
├───────────┬─────────────┬────────────┬───────────┬──────────┤
│  Routers  │ Middlewares │   States   │ Keyboards │  Utils   │
│  (10)     │  (4)        │   (FSM)    │ (dynamic) │          │
├───────────┴─────────────┴────────────┴───────────┴──────────┤
│                      Service Layer                           │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│ Payment  │  Signal  │ Scanner  │  Member  │    Feedback     │
│ Monitor  │  Sender  │Scheduler │ Service  │    Service      │
├──────────┴──────────┴──────────┴──────────┴─────────────────┤
│                       Data Layer                             │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│  MySQL   │  Redis   │  BSC RPC │ BSCScan  │   OpenRouter    │
└──────────┴──────────┴──────────┴──────────┴─────────────────┘
```

### Routers

| Router | File | Responsibility |
|--------|------|----------------|
| user | `routers/user.py` | `/start`, `/help`, `/status` |
| subscription | `routers/subscription.py` | Subscription flow, payment |
| menu | `routers/menu.py` | Main menu (6-button keyboard) |
| admin | `routers/admin.py` | Admin-only commands |
| feedback | `routers/feedback.py` | User feedback submission |
| trader | `routers/trader.py` | Referral program |
| language | `routers/language.py` | Language switching |
| join_request | `routers/join_request.py` | Channel join approval |
| sector_admin | `routers/sector_admin.py` | Sector classification management |
| errors | `routers/errors.py` | Global error handler |

### Middleware Pipeline

```
Request -> I18nMiddleware -> AuthMiddleware -> ThrottleMiddleware -> Router
                |                  |                  |
           inject lang        inject user          rate limit
```

| Middleware | Responsibility |
|------------|----------------|
| `I18nMiddleware` | Injects user language and `t()` translation function into handlers |
| `AuthMiddleware` | Injects user info into handlers |
| `ThrottleMiddleware` | Prevents API abuse |
| `TraceMiddleware` | Injects distributed trace context |

---

## 3. Bot Commands

### User Commands

| Command | Description | Visibility |
|---------|-------------|------------|
| `/start` | Smart welcome (shows expiry for members, feature overview for guests) | All users |
| `/help` | Help documentation | All users |
| `/status` | View membership status and expiry date | All users |
| `/subscribe` | View subscription plans | All users |
| `/feedback` | Submit feedback | All users |
| `/language` | Switch language | All users |
| `/trader` | Referral program info | All users |

### Main Menu (6-button ReplyKeyboard, 3x2 layout)

| Button | Action |
|--------|--------|
| Subscribe | Show subscription plans |
| Performance | Show strategy backtest results (image card) |
| My Account | Show membership details |
| Trader Pro | Referral program |
| Language | Language selection |
| Feedback | Submit feedback |

### Admin Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/admin` | Stats dashboard (total members, active, new, expiring soon) | -- |
| `/add_vip <user_id> <days>` | Manually activate membership | `/add_vip 123456 30` |
| `/remove_vip <user_id>` | Remove membership | `/remove_vip 123456` |
| `/referral_pending` | View pending trader applications | -- |
| `/referral_approve <uid>` | Approve trader | `/referral_approve ABC123` |
| `/referral_reject <uid>` | Reject trader | `/referral_reject ABC123` |
| `/referral_stats` | Trader statistics | -- |
| `/sector_check` | Check sector classification updates | -- |

Admin commands use `AdminFilter`. Non-admins receive no response. Admin IDs are configured via `ADMIN_TELEGRAM_ID`.

---

## 4. Channel Setup

The bot manages four Telegram channels split by tier and language:

| Type | Language | Env Variable | Content |
|------|----------|-------------|---------|
| Basic | Chinese | `TELEGRAM_CHANNEL_BASIC_ZH` | Scanner alerts (ZH) |
| Basic | English | `TELEGRAM_CHANNEL_BASIC_EN` | Scanner alerts (EN) |
| Premium | Chinese | `TELEGRAM_CHANNEL_PREMIUM_ZH` | VIP signals + alerts (ZH) |
| Premium | English | `TELEGRAM_CHANNEL_PREMIUM_EN` | VIP signals + alerts (EN) |

Each channel has Discussion enabled so members can comment directly under posts.

### Join Request Flow

When a user applies to join via invite link, the bot handles `ChatJoinRequest`:

```
User clicks invite link
      |
Bot receives ChatJoinRequest event
      |
Check membership status (ACTIVE?)
      | yes
Check plan tier (BASIC/PREMIUM?)
      | matches
Check language preference (channel must match)
      | matches
Approve join
```

Rejection reasons:
- Non-member: prompt to subscribe
- Wrong tier: prompt to upgrade
- Language mismatch: prompt to switch language or use correct link

After activation, invite links are generated based on the user's language preference:

```python
# User language = zh -> invite to Chinese channel
send_invites(user_id, plan_code='PREMIUM', lang='zh')

# User language = en -> invite to English channel
send_invites(user_id, plan_code='PREMIUM', lang='en')
```

---

## 5. Payment Flow

Payments use on-chain USDT (BEP20) on BSC.

```
+-------------+     +-------------+     +--------------+
|    Order    |     |   Payment   |     |    Member    |
|  Generator  |---->|   Monitor   |---->|  Activation  |
+-------------+     +-------------+     +--------------+
      |                    |                    |
  create order       watch on-chain        activate + invite
  assign address     confirm transfer
```

### Key Components

| Component | File | Responsibility |
|-----------|------|----------------|
| `OrderGenerator` | `payment/order_generator.py` | Order creation, address assignment, HMAC signing |
| `PaymentMonitor` | `payment/payment_monitor.py` | BSC on-chain event listener |
| `HDWalletManager` | `payment/hd_wallet_manager.py` | HD wallet address derivation |
| `FundCollector` | `payment/fund_collector.py` | Sweep funds to master wallet |
| `PaymentSaga` | `sagas/payment_saga.py` | Distributed transaction orchestration |

### Order Rules

| Item | Rule |
|------|------|
| Order ID format | `YYYYMMDD-XXXX` (e.g. `20251231-A1B2`) |
| Expiry | 30 minutes |
| Idempotency | If a PENDING order exists for the same user + plan, return it instead of creating a new one |
| Signature | HMAC-SHA256 |
| Confirmations required | 12 blocks |

### Order State Machine

```
            +---------+
            | PENDING |  (initial)
            +----+----+
                 |
     +-----------+-----------+
     |           |           |
+---------+ +---------+ +--------+
|CONFIRMED| | EXPIRED | | FAILED |
+---------+ +---------+ +--------+
 terminal    terminal    terminal
```

PENDING is the only mutable state. All transitions are irreversible.

### Saga Compensation

If post-payment steps fail, the saga alerts an admin for manual intervention:

```python
# payment_saga.py
async def compensate_membership(context):
    alert_manager.sync_alert_critical(
        f"Saga compensation: membership activation needs manual check\n"
        f"order_id={order_id}\n"
        f"telegram_id={telegram_id}"
    )
```

On-chain payment is irreversible, so automatic rollback is unsafe. Admin recovers by running `/add_vip <user_id> <days>`.

### Orphan Order Detection

Every 30 minutes a background job checks for orders with status `CONFIRMED` but no corresponding `ACTIVE` membership. These are alerted to the admin for manual resolution:

```sql
SELECT order_id, telegram_id, membership_type
FROM payment_orders po
LEFT JOIN memberships m ON po.telegram_id = m.telegram_id AND m.status = 'ACTIVE'
WHERE po.status = 'CONFIRMED'
  AND po.confirmed_at < NOW() - INTERVAL 10 MINUTE
  AND m.id IS NULL
```

---

## 6. Background Tasks

| Task | Frequency | Responsibility |
|------|-----------|----------------|
| `check_expired_members` | Hourly | Kick expired members from channels (with retry, max 3 attempts) |
| `send_renewal_reminders` | Daily 10:00 UTC | Renewal reminders at T-3, T-1, T+0 |
| `detect_orphan_orders` | Every 30 min | Alert on confirmed orders with no active membership |
| `scanner_scheduler` | Every 3 min | Market scanner + cooldown control |
| `spread_scan` | Every 1 min | Spot-futures spread monitoring |
| `orderbook_scan` | Every 5 min | Order book depth imbalance detection |
| `daily_pulse` (Asia) | 00:00 UTC | Chinese channel daily report |
| `daily_pulse` (West) | 08:00 UTC | English channel daily report |
| `trend_heartbeat` | Hourly | Detect coins near breakout |
| `sector_check` | Mon 02:00 UTC | Check sector mapping updates |

### Membership State Machine

```
    +--------+
    | ACTIVE | <-----------------+
    +---+----+                   |
        |  expires          renew / admin activate
        v                        |
    +---------+                  |
    | EXPIRED | -----------------+
    +---+-----+
        |  admin removes
        v
    +-----------+
    | CANCELLED |  (terminal)
    +-----------+
```

### Renewal Stacking

```python
if current_membership.is_expired():
    new_expiry = today + purchased_days
else:
    new_expiry = current_expiry + purchased_days  # stacks, does not overwrite
```

Upgrading from Basic to Premium requires paying the full Premium price (no partial credit). Days from the existing subscription are stacked onto the new expiry.

---

## 7. State Machines

### FSM States

| State Group | State | Trigger |
|-------------|-------|---------|
| `TraderStates` | `waiting_for_uid` | User clicks submit UID |
| `FeedbackStates` | `waiting_for_content` | User sends `/feedback` |

### Keyboard Types

**ReplyKeyboard (main menu)**:
- 3x2 layout, 6 buttons
- Dynamically generated per user language

**InlineKeyboard** usage:
- Plan selection
- Payment confirmation
- Language selection
- Admin approval flows
- Sector classification approval

### Message Format Conventions

| Context | Format | Reason |
|---------|--------|--------|
| Bot default | HTML | `main.py` sets `parse_mode=HTML` |
| User interactions | MarkdownV2 | Cleaner code blocks |
| Signal pushes | HTML | Better compatibility, no escaping needed |
| Admin commands | HTML | Convenient `<code>` tag rendering |

When embedding user input in MarkdownV2, escape all special characters:

```python
SPECIAL_CHARS = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']

def escape_markdown(text: str) -> str:
    for char in SPECIAL_CHARS:
        text = text.replace(char, f'\\{char}')
    return text
```

---

## 8. i18n System

### File Structure

```
src/telegram/i18n/
├── translator.py       # Translation engine
├── __init__.py         # Public interface
├── en.json             # English strings
└── zh.json             # Chinese strings
```

### Language Detection

```
First user interaction
      |
I18nMiddleware reads Telegram client language
      |
zh / zh-hans / zh-cn / zh-tw -> 'zh'
anything else                -> 'en'  (default)
      |
Persisted to DB (members.language column)
      |
Synced to in-memory cache (_user_languages)
```

### Translation API

```python
# In handlers -- t() is injected by I18nMiddleware
async def cmd_start(message: Message, lang: str, t: Callable):
    await message.answer(t('start.welcome'))

# Manual call
from src.telegram.i18n import t
message = t('days_left', 'zh', days=3)  # "还剩3天"
```

### Supported Features

- Nested keys: `t('menu.subscribe')` resolves `{"menu": {"subscribe": "..."}}`
- Parameter substitution: `t('renewal.days', days=3)` -> "3 days remaining"
- Fallback: missing key returns the key string itself
- Hot reload: `reload_translations()` re-reads JSON files without restart

### Signal i18n Keys (sample)

| Key | Chinese | English |
|-----|---------|---------|
| `signal.action_long` | 做多 | LONG |
| `signal.action_short` | 做空 | SHORT |
| `signal.label_entry` | 入场 | Entry |
| `signal.label_targets` | 止盈 | Targets |
| `signal.label_stop` | 止损 | Stop |
| `signal.tp_title_tp1` | 止盈1达成 | Target 1 Achieved |

### Language Switch Side Effects

When a user switches language, the following update immediately:
- In-memory cache
- Database record
- Bot command menu (hamburger menu)
- ReplyKeyboard button labels

Already-joined channels are **not** affected. Future invite links will point to the new language's channel.

---

## 9. Adding New Routers

1. Create `src/telegram/bot_v2/routers/your_router.py`:

```python
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()

@router.message(Command("yourcommand"))
async def cmd_yourcommand(message: Message, lang: str, t):
    await message.answer(t('yourcommand.response'))
```

2. Register the router in `src/telegram/bot_v2/main.py`:

```python
from routers import your_router
dp.include_router(your_router.router)
```

3. Add translation keys to `src/telegram/i18n/en.json` and `zh.json`.

4. If the command needs membership gating, use the injected user object provided by `AuthMiddleware` to check the membership tier.

For admin-only commands, add `AdminFilter`:

```python
from filters import AdminFilter

@router.message(Command("adminonly"), AdminFilter())
async def cmd_adminonly(message: Message):
    ...
```

---

## 10. Configuration Reference

```bash
# Bot
TELEGRAM_BOT_TOKEN=
ADMIN_TELEGRAM_ID=

# Channels (4-channel architecture)
TELEGRAM_CHANNEL_BASIC_ZH=
TELEGRAM_CHANNEL_BASIC_EN=
TELEGRAM_CHANNEL_PREMIUM_ZH=
TELEGRAM_CHANNEL_PREMIUM_EN=

# Payment -- BSC / USDT (BEP20)
BSCSCAN_API_KEY=
BSC_USDT_CONTRACT=0x55d398326f99059fF775485246999027B3197955
BSC_RPC_URL=https://bsc-dataseed.binance.org/
HD_MASTER_ADDRESS=
HD_WALLET_MNEMONIC=          # store securely, never commit
ORDER_SECRET_KEY=             # HMAC signing key
ORDER_EXPIRE_MINUTES=30

# Database / Cache
MYSQL_DSN=
REDIS_URL=

# AI (for scanner analysis)
OPENROUTER_API_KEY=
```

---

## 11. File Structure

```
src/telegram/
├── bot_v2/
│   ├── main.py              # Bot entry point
│   ├── routers/             # 10 router modules
│   ├── middlewares/         # 4 middleware classes
│   ├── states/              # FSM state groups
│   ├── keyboards/           # Keyboard builders
│   └── utils/               # Utility helpers
├── payment/
│   ├── payment_monitor.py   # On-chain payment listener
│   ├── order_generator.py   # Order creation + signing
│   └── hd_wallet_manager.py # HD wallet address derivation
├── i18n/                    # Translation files + engine
├── database/                # DAO layer
├── vip_signal_sender.py     # VIP signal push (per-language)
├── access_controller.py     # Permission gating
├── group_controller.py      # Channel management
└── notification_service.py  # Notification dispatcher

src/scanner/
├── scheduler.py             # Scan scheduler
├── alert_detector.py        # Price/volume/funding detection
├── spread_detector.py       # Spot-futures spread detection
├── orderbook_detector.py    # Order book imbalance detection
├── formatter.py             # Message formatting (per-language)
├── sector_aggregator.py     # Sector-level aggregation
└── ai_client.py             # AI insight generation

src/sagas/
└── payment_saga.py          # Payment distributed transaction
```
