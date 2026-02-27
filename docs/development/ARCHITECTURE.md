# System Architecture

**Version**: v2.1 | **Updated**: 2026-01-15

---

## System Overview

Ignis is a cryptocurrency quantitative trading system built on a **scheduler pattern** with **modern infrastructure**:

```
┌─────────────────────────────────────────────────────────┐
│                    Application Layer                     │
├─────────────┬─────────────┬─────────────┬───────────────┤
│   Swing     │   Scanner   │  Telegram   │    Payment    │
│  Scheduler  │  Scheduler  │   Bot v2    │    Monitor    │
├─────────────┴─────────────┴─────────────┴───────────────┤
│                    Core Infrastructure                   │
├──────────┬──────────┬──────────┬──────────┬─────────────┤
│    DI    │ Message  │   Saga   │  Cache   │  Database   │
│Container │   Bus    │Orchestr. │ Manager  │    Pool     │
├──────────┴──────────┴──────────┴──────────┴─────────────┤
│                    External Services                     │
├─────────────┬─────────────┬─────────────┬───────────────┤
│   Binance   │   MySQL     │    Redis    │   Telegram    │
└─────────────┴─────────────┴─────────────┴───────────────┘
```

---

## Core Infrastructure (src/core/)

| Module | File | Responsibility |
|--------|------|----------------|
| **DI Container** | container.py, bootstrap.py | Dependency injection, lifecycle management |
| **Message Bus** | message_bus.py, events.py | Event publish/subscribe, module decoupling |
| **Saga** | saga.py | Distributed transaction orchestration, compensation |
| **Cache** | cache.py | Unified Redis cache interface |
| **Database** | database.py | Connection pool, transaction management |
| **Protocols** | protocols.py | Interface definitions, dependency abstractions |
| **Config** | config.py | Configuration management, credential tiering |

### DI Bootstrap Flow

```python
# Application startup
from src.core.bootstrap import bootstrap_production
container = bootstrap_production()

# Resolve dependencies
from src.core.container import inject
service = inject(MemberServiceProtocol)
```

---

## Swing Strategy Layer

**Run schedule**: Daily at UTC 00:01

```
src/strategies/swing/
├── scheduler.py           # Scheduler main entry
├── executor.py            # Signal execution
├── ensemble_strategy.py   # BTC/ETH/BNB strategy
├── breakout_strategy.py   # SOL strategy
└── notification_manager.py
```

| Symbol | Strategy | Exit Method |
|--------|----------|-------------|
| BTC | swing-ensemble | Trailing stop (25-day low) |
| ETH/BNB | swing-ensemble | Trailing stop (15-day low) |
| SOL | swing-breakout | Fixed take-profit / stop-loss |

---

## Scanner Intelligence Layer

**Run schedule**: Every 3 minutes

```
src/scanner/
├── scheduler.py        # Scheduler
├── market_scanner.py   # Full market scan
├── alert_detector.py   # Movement detection
├── formatter.py        # Message formatting
└── ai_client.py        # AI interpretation (Grok)
```

| Feature | Trigger Condition |
|---------|-------------------|
| Movement Radar | Price >3%/5min, Volume >3x |
| Daily Pulse | Daily at 08:00 UTC+8 |

---

## Telegram Bot (aiogram 3.x)

```
src/telegram/bot_v2/
├── main.py             # Entry point, Dispatcher setup
├── middlewares/
│   ├── auth.py         # Auth middleware (user info injection)
│   └── i18n.py         # Internationalization middleware
├── routers/
│   ├── user.py         # User commands
│   ├── subscription.py # Subscription + payment flow
│   ├── admin.py        # Admin commands
│   └── ...
├── keyboards/          # Button layouts
├── states/             # FSM state definitions
└── utils/              # Utility functions
```

---

## Payment System

```
src/telegram/payment/
├── payment_monitor.py  # BSC on-chain listener
└── ...

src/sagas/
└── payment_saga.py     # Payment Saga orchestration
```

**Payment flow**: Order creation -> Address assignment -> On-chain monitoring -> Membership activation -> Group invitation

---

## Saga Orchestration

Distributed transaction orchestration engine providing retry, recovery, and compensation capabilities.

```
src/core/saga.py           # Orchestration engine
src/sagas/
├── trading_saga.py        # Trading flow Saga
└── payment_saga.py        # Payment flow Saga
```

### Trading Saga (Entry Flow)

```
validate_signal ──▶ create_order ──▶ update_position ──▶ notify
      │                  │                  │               │
  param validation    place order       DB record        Telegram
  position check      + stop-loss       verification      push
                      (retry ×2)
```

| Feature | Description |
|---------|-------------|
| Retry | Exponential backoff, per-step configuration (1–3 attempts) |
| Recovery | Auto-recovers RUNNING sagas on startup |
| Compensation | Executes in reverse on failure (close position / cancel order / rollback DB) |
| Idempotency | Deduplicated by signal_id (`{symbol}:{date}`) |
| Timeout | 120s overall, 10–60s per step |

### State Transitions

```
RUNNING ──success──▶ COMPLETED
    │
    └──failure──▶ COMPENSATING ──▶ COMPENSATED
```

### Database Tables

| Table | Purpose |
|-------|---------|
| saga_instances | Saga execution state |
| saga_steps | Step execution state |
| idempotency_keys | Idempotency deduplication |

---

## Trading Module

```
src/trading/
├── binance_trading_client.py  # Binance API
├── position_manager.py        # Position CRUD
└── trailing_stop_manager.py   # Trailing stop-loss
```

---

## Data Layer

| Table | Purpose |
|-------|---------|
| positions | Position records (single source of truth) |
| signals | Trading signals |
| orders | Order records |
| members | Member information |
| payment_addresses | Payment address pool |

---

## Deployment Architecture

| Service | Container | Description |
|---------|-----------|-------------|
| swing | ignis_swing | Strategy scheduler |
| scanner | ignis_scanner | Intelligence scanner |
| tg-bot | ignis_tg_bot | Telegram Bot |
| tg-payment | ignis_tg_payment | Payment listener |
| mysql | ignis_mysql | Database |
| redis | ignis_redis | Cache |

```bash
# Start core services
docker-compose up -d swing scanner tg-bot tg-payment
```

---

## Design Principles

1. **Protocol-driven** - Depend on abstract interfaces for testability
2. **Event decoupling** - MessageBus publishes events; subscribers handle independently
3. **Saga orchestration** - Distributed transactions with automatic compensation
4. **Single source of truth** - The `positions` table is the authoritative state for all trades
