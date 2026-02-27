# Ignis Scanner (Market Intelligence Scanner)

**Version**: v3.2 | **Updated**: 2026-01-03

---

## System Positioning

> **"Ignis AI radar scans 200+ coins across the market 24/7, capturing major movements, delivered in real time by the quant system."**

### Core Philosophy

- The system is "eyes", not "hands"
- Monitors the market for users; does not make decisions for users
- Outputs objective facts; does not predict direction

---

## Research Conclusions Summary

Scanner provides market intelligence rather than automated trading signals.

---

## Scanner v3.0 Specification

### Technical Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Ignis Scanner v3.0                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Binance API (full market, 200+ symbols)                    │
│         ↓                                                   │
│  MarketScanner (liquidity filter: 24h volume > $10M)        │
│         ↓                                                   │
│  AlertDetector (v3.0 Z-Score dynamic threshold)             │
│         ↓                                                   │
│  Scheduler (v3.0 direction-aware cooldown)                  │
│         ↓                                                   │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐   │
│  │ Volatility  │     │ Daily Pulse │     │ Trend Pulse │   │
│  │ Alert       │     │ (daily 8AM) │     │ (heartbeat) │   │
│  │ (real-time) │     │             │     │             │   │
│  └─────────────┘     └─────────────┘     └─────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Output Content

#### A. Volatility Alert

**Key Design Decisions**:

| Feature | Behavior | Effect |
|---------|----------|--------|
| Price threshold | Z-Score dynamic | Adapts to each symbol's volatility |
| Cooldown mechanism | Direction-aware | Captures V-shaped reversals |

**Trigger conditions** (any one):

| Condition | Threshold | Notes |
|-----------|-----------|-------|
| Price move | Z-Score > 2.5 | Dynamic threshold; exceeds 2.5x historical volatility |
| Volume spike | 15-min volume > 24h average × 3 | Abnormal capital inflow |
| Funding rate spike | > 0.05% or < -0.03% | Extreme long/short sentiment shift |
| OI move | 1h change > 5% | Large capital entry |

**Z-Score dynamic threshold principle**:

```
threshold = Z_SCORE × historical_volatility(std)

Where:
- Z_SCORE = 2.5 (statistically ~1.2% extreme events)
- Historical volatility = std of returns over last 20 5-minute candles
- Caching: stored in Redis, 1-hour TTL

Examples:
- BTC volatility 0.17%: threshold = 2.5 × 0.17% = 0.43%
- WIF volatility 0.42%: threshold = 2.5 × 0.42% = 1.05%
```

**Push strategy**:

| Parameter | Value | Notes |
|-----------|-------|-------|
| Scan interval | 3 minutes | Real-time guarantee |
| Global cooldown | 15 minutes | Minimum interval between pushes |
| Per-symbol cooldown | 4 hours / direction | Same-direction cooldown; opposite direction triggers immediately |
| Top N | 3 | Maximum 3 alerts per scan |
| Silent mode | Enabled | No push when no alerts |

**Direction-aware cooldown**:

```
Scenario: BTC surges at 10:00, triggers PUMP alert

- 10:00 PUMP alert ✓ (sets PUMP cooldown)
- 10:30 DROP alert ✓ (DROP direction has no cooldown; captures V-reversal)
- 14:00 PUMP alert ✓ (PUMP cooldown has expired after 4 hours)
```

**Frequency**: ~3-10 times per day (varies with market volatility)

#### B. Daily Pulse (Market Daily Report)

**Push time**: Daily 08:00 UTC+8

**Content structure**:

```
📈 Ignis Daily Pulse | 12-22
━━━━━━━━━━━━━━━━━━━━

[ Market Sentiment ]
😰 Fear (F&G 25) | 🐋 Whales strongly long (L/S 2.26)
BTC $89,500 (+1.0%) | ETH $3,035 (+1.3%)

[ Scanner Full Market Radar ]
• Sectors: ALPACA/BNX/BEAT leading gains
• Alerts: ALPACA +391% (volume surge) | BNX +66% (volume surge)

[ Quant View ]
📍 Slightly bullish · Two-dimension resonance · $90k pivot

• On-chain: Exchange outflows surged 59%, net outflow 679 BTC → whale accumulation
• Derivatives: L/S 2.26 + FR -0.0088% → longs dominant, shorts under pressure
• Narrative: ETF weekly outflow $497M, MSTR holds $59B → short-term profit-taking but institutional demand stable

Summary: Holding $90k = bull dominant; break below $87k = consolidation.

━━━━━━━━━━━━━━━━━━━━
Ignis Quant | NFA・DYOR
```

**Dual versions**:
- FULL version (Premium group): complete content
- HOOK version (Basic group): key levels partially hidden

**AI integration**:
- API gateway: OpenRouter
- Model: Grok 4.1 Fast + Live Search
- Function: Real-time search of X/Twitter and web, generates three-dimensional analysis

#### C. Trend Pulse (Swing Heartbeat)

**Trigger condition**: When Swing strategy has open positions

**Content**: Displays position status, unrealized P&L, stop-loss status

**Frequency**: 0-2 times per day

#### D. Spread Alert

**Added in v3.1**: Spot-futures spread anomaly detection

| Parameter | Premium | Basic |
|-----------|---------|-------|
| Trigger threshold | >= 3% | >= 10% |
| Scan frequency | Every 1 minute | Every 1 minute |
| Cooldown | 2 hours / symbol | 2 hours / symbol |
| FOMO copy | None | Yes |

**Dynamic threshold adjustment**: Stored in Redis; supports runtime modification

**Frequency**: ~0-5 times per day (varies with market conditions)

#### E. Orderbook Imbalance Monitor

**Added in v3.2**: Detects buy/sell depth imbalance; identifies large "wall" orders

| Parameter | Premium | Basic |
|-----------|---------|-------|
| Trigger threshold | >= 74% imbalance | >= 87% extreme |
| Scan frequency | Every 5 minutes | Every 5 minutes |
| Cooldown | 1 hour / symbol | 1 hour / symbol |
| FOMO copy | None | Yes |

**Monitored universe**: Top 50 + held symbols

**Imbalance calculation**:

```
imbalance_ratio = abs(bid_depth - ask_depth) / (bid_depth + ask_depth)
```

**Wall detection**:

```
Wall condition: top 3 levels on one side > total depth of the other side
```

**Frequency**: ~0-3 times per day (varies with market depth conditions)

---

## Run Commands

```bash
# Check status
python -m src.scanner.scheduler --status

# Run scan immediately
python -m src.scanner.scheduler --scan-now

# Generate daily report
python -m src.scanner.scheduler --daily-brief

# Check trend pulse heartbeat
python -m src.scanner.scheduler --heartbeat

# Run spread scan immediately
python -m src.scanner.scheduler --spread-scan

# Start scheduler service (persistent)
python -m src.scanner.scheduler
```

---

## File Structure

```
src/scanner/
├── scheduler.py          # Scheduler main program
├── market_scanner.py     # Full market scan
├── alert_detector.py     # Alert detection + data retrieval
├── spread_detector.py    # Spread monitor (v3.1)
├── orderbook_detector.py # Orderbook depth monitor (v3.2)
├── formatter.py          # Message formatting (Daily Pulse)
├── ai_client.py          # OpenRouter AI client (Grok 4.1)
└── sector_aggregator.py  # Sector aggregation
```

---

