# Changelog

All notable changes to Ignis are documented here.

Format: [Semantic Versioning](https://semver.org/)

---

## [Unreleased]

## [3.0.0] - 2026-01-01

Scanner v3 — production-grade monitoring infrastructure.

- Z-Score dynamic thresholds replace static percentage alerts
- Spread monitoring: detects abnormal bid-ask spreads via Binance orderbook
- Orderbook depth monitoring: flags sudden liquidity withdrawal
- Saga orchestrator: coordinates multi-step signal workflows with rollback support
- 4-channel Telegram architecture: separate channels for signals, alerts, pulse, and ops

## [2.0.0] - 2025-12-01

Scanner system — market monitoring alongside swing trading.

- Volatility spike alerts based on price and volume anomalies
- Daily Pulse AI report: automated market summary via Telegram
- Multi-symbol scanner running on configurable schedule
- Redis-backed deduplication to suppress repeated alerts

## [1.0.0] - 2025-09-01

Initial release — trend-following swing strategy with Telegram delivery.

- Swing strategy: trend-following entry signals based on multi-period Donchian channel ensemble
- Telegram bot: signal delivery with entry, stop-loss, and take-profit levels
- Binance data feed: OHLCV via REST API
- MySQL persistence for signal history and position tracking
