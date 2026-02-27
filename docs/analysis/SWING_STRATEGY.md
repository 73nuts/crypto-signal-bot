# Swing Strategy Definition (Daily Trend Following)

**Version**: Swing v1.0 | **Updated**: 2025-12-14

---

## I. Trading Plan Overview

| Dimension | Choice | Notes |
|-----------|--------|-------|
| **Timeframe** | Trend (Daily) | Check daily at UTC 00:01 |
| **Position Size** | 2% per trade | size = (Account × 2%) / (ATR × 2) |
| **Trading Mode** | Trend following | Long only, no directional prediction |
| **Market** | Futures (USDT perpetual) | Binance Futures |
| **Entry** | Breakout | Multi-period Donchian channel breakout |
| **Profit Target** | Trailing exit | Trailing stop (N-day low) / Fixed TP (SOL) |

---

## II. Strategy Configuration

| Symbol | Strategy | Parameters | Exit Method |
|--------|----------|------------|-------------|
| BTC | swing-ensemble | trailing_mult=0.5 | Trailing stop (25-day low) |
| ETH | swing-ensemble | trailing_mult=0.3 | Trailing stop (15-day low) |
| BNB | swing-ensemble | trailing_mult=0.3 | Trailing stop (15-day low) |
| SOL | swing-breakout | period=20 | Fixed TP/SL |

---

## III. Strategy Definitions

### 3.1 swing-ensemble (BTC/ETH/BNB)

```
Timeframe: 1D (Daily)
Direction: Long only

Entry conditions:
  1. Calculate Donchian channel upper band for 5 periods: [20, 35, 50, 65, 80]
  2. For each period P:
     signal[P] = +1 if close > highest_high(P, shift=1)
     signal[P] = 0  otherwise
  3. Ensemble signal = mean(all signals)
  4. Entry: ensemble signal > 0.4

Exit conditions:
  Trailing stop: price breaks below N-day low (N = 50 × trailing_mult)
  - BTC: N = 50 × 0.5 = 25 days
  - ETH/BNB: N = 50 × 0.3 = 15 days

Position sizing:
  size = (Account × 2%) / (ATR × 2)
```

### 3.2 swing-breakout (SOL)

```
Timeframe: 1D (Daily)
Direction: Long only

Entry conditions:
  close > previous_day_highest_high(20)

Exit conditions:
  Stop loss: entry_price - 2 × ATR
  Take profit: entry_price + 6 × ATR
  Timeout: 60 days

Position sizing:
  size = (Account × 2%) / (ATR × 2)
```

**SOL Strategy Notes**:
- Uses fixed TP/SL instead of trailing stop
- Reason: SOL is highly volatile; trailing stops get shaken out too easily
- Fixed 3:1 reward-to-risk ratio (6R TP / 2R SL) suits SOL's characteristics better
- Insufficient sample size (44 trades / 5 years); any optimization cannot be reliably validated

---

## IV. Validation Data

### 4.1 Data Split (6:2:2)

| Dataset | Time Range | Purpose |
|---------|------------|---------|
| Discovery set | 2020-01 ~ 2023-06 | Strategy development |
| Validation set | 2023-07 ~ 2024-08 | Parameter tuning |
| Test set | 2024-09 ~ 2025-11 | Final validation |

### 4.2 Backtest Results (Realistic Engine)

> Uses unified backtest engine including: T+1 open price entry, commission (0.04%), slippage (5-10bps), pessimistic intrabar rules

**Per-symbol results (2020-2025)**:

| Symbol | Strategy | Trades | Win Rate | Profit Factor | Net P&L ($) |
|--------|----------|--------|----------|---------------|-------------|
| BTC | swing-ensemble | 17 | 52.9% | 5.06:1 | +$12,376 |
| ETH | swing-ensemble | 22 | 50.0% | 3.26:1 | +$10,604 |
| BNB | swing-ensemble | 21 | 47.6% | 3.08:1 | +$13,632 |
| SOL | swing-breakout | 44 | 50.0% | 2.38:1 | +$13,471 |

**Portfolio statistics**:

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Total trades | 104 | - | - |
| Win rate | 50.0% | 55% | Below target |
| Profit factor (PF) | 3.18:1 | 3:1 | Met |
| Max drawdown | 10.6% | <20% | Met |
| Total costs | $1,295 | - | 2.6% of returns |

### 4.3 Annual Performance

**Backtest parameter note**:
- Backtest script uses 20x leverage; returns are amplified
- Live execution uses a maximum of 3x leverage; risk is controlled

| Year | Trades | Win Rate | Price Return | 3x Leveraged Return |
|------|--------|----------|--------------|---------------------|
| 2020 | 11 | 72.7% | +3.2% | +20.9% |
| 2021 | 26 | 65.4% | +78.5% | +290.1% |
| 2022 | 11 | 9.1% | -3.9% | **-8.3%** |
| 2023 | 20 | 50.0% | +9.0% | +4.1% |
| 2024 | 20 | 50.0% | +6.9% | +14.3% |
| 2025 | 13 | 61.5% | +4.6% | +4.5% |

**Key clarifications**:
- 2022 bear market price return was only -3.9%; 3x leverage loss of -8.3% (manageable)
- 3x leverage 5-year cumulative: $10,000 -> $60,084 (+500.8%)
- Includes $1,295 in trading costs (commission + slippage), representing 2.6% of returns

### 4.4 Holding Period Analysis

**By year**:

| Year | Trades | Avg Hold | Median | Range |
|------|--------|----------|--------|-------|
| 2020 | 11 | 36.5 days | 43 days | 2-59 days |
| 2021 | 26 | 32.7 days | 21 days | 1-179 days |
| 2022 | 11 | 8.5 days | 10 days | 1-15 days |
| 2023 | 20 | 21.4 days | 20 days | 1-56 days |
| 2024 | 20 | 36.5 days | 34 days | 5-88 days |
| 2025 | 13 | 41.6 days | 43 days | 9-86 days |

**Patterns**:
- Bear market (2022) had the shortest hold time (8.5 days); strategy stops out quickly
- Bull markets allow longer holds, letting profits run
- BTC/BNB hold longer due to larger trailing_mult

---

## V. Risk Disclosures

1. **Historical backtests do not predict future results**: The strategy depends on trending markets
2. **SOL win rate below target**: 46.5% < 55% target, compensated by profit factor
3. **Leverage risk**: Execution layer limits maximum leverage to 3x
4. **Execution costs**: Live trading must account for slippage and commissions
5. **Market condition dependency**: Long-only strategy underperforms in bear markets (manageable at 3x leverage)
6. **Sample size limitation**: SOL averages ~7 trades per year; statistical inference is unreliable

---

## Appendix A: Academic Research Foundation

### A.1 Core References

**1. Swiss Finance Institute 2025**

- Paper: Catching Crypto Trends: A Tactical Approach for Bitcoin and Altcoins
- Authors: Carlo Zarattini, Alberto Pagani, Andrea Barbon
- Link: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907

Key findings:
- Multi-period Donchian channel ensemble method
- Volatility-based position sizing
- Sharpe Ratio > 1.5, annualized Alpha 10.8%

**2. QuantPedia Trend Following Research**

- Link: https://quantpedia.com/trend-following-and-mean-reversion-in-bitcoin/

Key findings:
- Time-series momentum is persistently effective on BTC
- Trend following: use when trend is clear
- Mean reversion: only at extreme levels

**3. HTX Research - Turtle Trading Study**

- Link: https://medium.com/huobi-research/huobi-quant-academy-3-220714ccde9f

Key findings:
- Directly applying Turtle Trading to crypto yields no significant profits
- System 2 (55-day slow) outperforms System 1 (20-day fast)
- Adaptation required rather than direct application

**4. SSRN - Quantitative Alpha in Crypto Markets**

- Link: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5225612

Important warnings:
- Backtests before 2020 have limited validity
- Most altcoins behave like penny stocks
- Implementation challenges are significant

### A.2 Swing Design Rationale Mapping

| Component | Design | Academic Basis |
|-----------|--------|----------------|
| Entry | Multi-period Donchian breakout | Swiss Finance Institute 2025 |
| Periods | [20, 35, 50, 65, 80] | Multi-period ensemble research |
| Ensemble | Signal average, threshold 0.4 | Trend band method |
| Position | ATR volatility adjustment | Risk parity research |
| Stop | Trailing stop (N-day low) | Improved Turtle |
| Data | 2020-2025 | Academic warning: pre-2020 invalid |

### A.3 Falsifiability Criteria

| Metric | Failure Threshold | Source |
|--------|-------------------|--------|
| Sharpe Ratio | < 0.5 | Academic standard |
| Annualized return | < 0% | Basic requirement |
| vs Buy-and-hold | Underperforms | Alpha validation |
| Max drawdown | > 60% | Risk control |

---

## Appendix B: Related Files

| File | Purpose |
|------|---------|
| src/strategies/swing/scheduler.py | Scheduler main program |
| src/strategies/swing/executor.py | Executor |
| src/strategies/swing/ensemble_strategy.py | BTC/ETH/BNB strategy |
| src/strategies/swing/breakout_strategy.py | SOL strategy |
| scripts/analysis/swing_backtest_report.py | Backtest report |

