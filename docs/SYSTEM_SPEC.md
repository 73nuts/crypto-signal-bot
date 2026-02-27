# System Specification

> A 30-minute overview of the entire system

**Version**: v1.0 | **Updated**: 2026-01-15

---

## 1. System Purpose

**Ignis** is a cryptocurrency quantitative signal system based on academically-researched trend-following strategies. It delivers trading signals to subscribers via a Telegram Bot.

| Dimension | Description |
|-----------|-------------|
| Core value | Data-driven, disciplined execution, controlled risk |
| Target users | Experienced traders who value data and reject hype-based calls |
| Business model | Subscription |

---

## 2. Core Capabilities

### 2.1 Swing Strategy (Daily Trend Following)

An automated quantitative trading strategy that checks for signals daily at UTC 00:01.

| Metric | Value |
|--------|-------|
| Instruments | BTC, ETH, BNB, SOL |
| Historical win rate | 50% |
| Profit/loss ratio | 3.18:1 |
| Annualized drawdown | 10.6% |

**Detailed spec**: [SWING_STRATEGY.md](analysis/SWING_STRATEGY.md)

### 2.2 Scanner Intelligence (Market Monitoring)

Real-time monitoring of 200+ symbols to identify movement opportunities.

| Module | Trigger Condition |
|--------|-------------------|
| Movement Radar | Z-Score > 2.5 |
| Spread Monitor | Premium ≥3% / Basic ≥10% |
| Order Book Monitor | Buy/sell wall imbalance |
| Daily Pulse | Daily market summary |

**Detailed spec**: [SCANNER.md](analysis/SCANNER.md)

### 2.3 Telegram Bot (User Interface)

The entry point for users to subscribe, pay, and receive signals.

| Feature | Description |
|---------|-------------|
| Channel structure | Basic/Premium × Chinese/English (4 channels) |
| Payment method | BSC on-chain USDT |
| Signal delivery | Entry / exit / stop-loss updates / market movements |

**Detailed spec**: [TELEGRAM_BOT_GUIDE.md](telegram/TELEGRAM_BOT_GUIDE.md)

---

## 3. Trading Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Scheduler  │────▶│    Saga     │────▶│  Executor   │────▶│  Binance    │
│ Daily check │     │ Orchestrate │     │ Order/Stop  │     │  Exchange   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
       │                   │                   │                   │
       ▼                   ▼                   ▼                   ▼
  Strategy signal     Retry/compensate    Position mgmt      Order filled
```

### 3.1 Entry Flow (Saga Orchestration)

```
validate_signal ──▶ create_order ──▶ update_position ──▶ notify
      │                  │                  │               │
      ▼                  ▼                  ▼               ▼
  param validation   market/limit order  DB record update  Telegram push
  position check     stop-loss setup
```

**Saga features**:
- Automatic retry (exponential backoff)
- State persistence (crash-recoverable)
- Automatic failure compensation (cancel order / close position)
- Idempotency (signal_id deduplication)

### 3.2 Position State Machine

```
PENDING ──filled──▶ OPEN ──closed──▶ CLOSED
    │                  │
    └──timeout/cancel──▶ CANCELLED
```

| State | Description |
|-------|-------------|
| PENDING | Limit order awaiting fill |
| OPEN | Position active, stop-loss order placed |
| CLOSED | Position closed (take-profit / stop-loss / manual) |
| CANCELLED | Limit order expired without fill |

---

## 4. Risk Management Rules

| Rule | Parameter | Description |
|------|-----------|-------------|
| Per-trade risk | 2% | Loss capped at 2% of account |
| Dynamic leverage | 1–10x | Determined by stop distance to prevent liquidation |
| Stop-loss method | ATR × 2 | Set at entry, then trailed |
| Trailing stop | Trailing Lowest | Tracks lowest point + ATR × 0.3 |
| Position limit | One position per symbol | Prevents duplicate entries |

---

## 5. Technical Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Application Layer                        │
├───────────────┬───────────────┬───────────────┬─────────────┤
│    Swing      │    Scanner    │  Telegram Bot │   Payment   │
│   Scheduler   │   Scheduler   │    (aiogram)  │   Monitor   │
├───────────────┴───────────────┴───────────────┴─────────────┤
│                   Core Infrastructure                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │    DI    │  │ Message  │  │   Saga   │  │  Cache   │    │
│  │Container │  │   Bus    │  │Orchestor │  │  Redis   │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
├─────────────────────────────────────────────────────────────┤
│                      External Services                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ Binance  │  │  MySQL   │  │  Redis   │  │ Telegram │    │
│  │  API     │  │          │  │          │  │   API    │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
└─────────────────────────────────────────────────────────────┘
```

**Detailed architecture**: [ARCHITECTURE.md](development/ARCHITECTURE.md)

### 5.1 Deployment Topology

| Component | Container | Port |
|-----------|-----------|------|
| Swing Scheduler | ignis_swing | - |
| Scanner Scheduler | ignis_scanner | - |
| Telegram Bot | ignis_tg_bot | - |
| Payment Monitor | ignis_tg_payment | - |
| MySQL | ignis_mysql | 3307 |
| Redis | ignis_redis | 6379 |

**Deployment path**: See deployment configuration
**Branch**: See deployment configuration

---

## 6. Quick Links

### Strategy & Analysis
- [Swing Strategy Spec](analysis/SWING_STRATEGY.md) - Entry/exit conditions, backtest data
- [Scanner Spec](analysis/SCANNER.md) - Movement detection, spread monitoring

### Development & Architecture
- [System Architecture](development/ARCHITECTURE.md) - DI, MessageBus, Saga
- [Architecture Decision Records](adr/) - ADR-001 aiogram, ADR-002 Router redundancy

### Operations & Deployment
- [Command Reference](operations/COMMANDS.md) - Day-to-day operations commands

### Product & User Guide
- [Telegram Bot Guide](telegram/TELEGRAM_BOT_GUIDE.md) - Full feature documentation

---

## 7. Key Database Tables

| Table | Purpose |
|-------|---------|
| `positions` | Position records (single source of truth) |
| `saga_instances` | Saga execution state |
| `saga_steps` | Saga step state |
| `idempotency_keys` | Idempotency deduplication |
| `orders` | Subscription orders |
| `users` | User information |
| `subscriptions` | Subscription status |

---

## 8. FAQ

### Q: A signal was generated but not executed — what should I do?
A: The Saga recovery mechanism automatically resumes incomplete trading flows on restart.

### Q: How do I close a position manually?
A: Close the position directly on the Binance exchange. The system will sync the state during the next reconciliation.

### Q: How do I add a new trading symbol?
A: Modify `src/strategies/swing/config.py` and add the symbol configuration and strategy mapping.

---

*This document is the system entry point. Refer to the linked documents for detailed information.*
