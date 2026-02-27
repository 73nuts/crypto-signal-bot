# Command Reference

**Updated**: 2026-01-08

---

## System Information

| Item | Value |
|------|-------|
| Deployment path | /opt/ignis (example — use actual config) |
| Git branch | See actual config |
| Port | MySQL: 3307 |
| Container prefix | ignis_ |

---

## Swing Scheduler

```bash
# Start
docker-compose up -d swing

# View logs
docker-compose logs -f swing

# Run once manually
python -m src.strategies.swing.scheduler --run-now

# Check status
python -m src.strategies.swing.scheduler --status

# Test notification channel
python -m src.strategies.swing.scheduler --test-notify

# Dry-run mode (log only, no orders placed)
python -m src.strategies.swing.scheduler --dry-run

# Entry test (end-to-end validation)
python -m src.strategies.swing.scheduler --test-entry BTC

# Execute mode
python -m src.strategies.swing.scheduler --execute --testnet   # testnet
python -m src.strategies.swing.scheduler --execute --mainnet   # mainnet
```

---

## Scanner Scheduler (Market Intelligence)

```bash
# Check status
python -m src.scanner.scheduler --status

# Trigger immediate movement scan
python -m src.scanner.scheduler --scan-now

# Generate daily brief
python -m src.scanner.scheduler --daily-brief

# Check trend heartbeat
python -m src.scanner.scheduler --heartbeat

# Trigger immediate spread scan
python -m src.scanner.scheduler --spread-scan

# Trigger immediate order book scan
python -m src.scanner.scheduler --orderbook-scan

# Check sector mapping update (manual trigger)
python -m src.scanner.scheduler --sector-check

# Start scheduler service (persistent)
python -m src.scanner.scheduler
```

Scanner features:
- Movement Radar: Price >3%/5min, Volume >3x, Funding >0.05%, OI >5%
- Spread Monitor: Spot-futures spread ≥3% (Premium) / ≥10% (Basic)
- Order Book Monitor: Buy/sell depth imbalance ≥74% (Premium) / ≥87% (Basic)
- Daily Market Brief: Daily at 08:00 UTC+8
- Trend Heartbeat: Detects symbols approaching breakout
- Sector Mapping: Auto-checks for updates every Monday at 10:00 UTC+8

---

## Status Queries

```sql
-- View open positions
SELECT symbol, side, entry_price, current_stop, stop_type, strategy_name
FROM positions
WHERE status = 'OPEN';

-- View trailing stop positions
SELECT symbol, entry_price, current_stop, highest_since_entry
FROM positions
WHERE status = 'OPEN' AND stop_type = 'TRAILING';

-- View trade history
SELECT symbol, side, entry_price, exit_price, pnl_percent, closed_at
FROM positions
WHERE status = 'CLOSED'
ORDER BY closed_at DESC LIMIT 10;
```

---

## System Operations

```bash
# Update system (standard flow)
cd /opt/ignis
git pull origin main
docker-compose down <service>
docker-compose build --no-cache <service>
docker-compose up -d <service>

# Check status
docker-compose ps

# Restart a service
docker-compose restart swing

# Rebuild container (after code update)
docker-compose down swing
docker-compose build --no-cache swing
docker-compose up -d swing
```

---

## Telegram VIP

```bash
# Start
docker-compose up -d tg-bot tg-payment

# View logs
docker-compose logs -f tg-bot

# Rebuild (after code update)
docker-compose down tg-bot tg-payment
docker-compose build --no-cache tg-bot tg-payment
docker-compose up -d tg-bot tg-payment
```

---

## Database

```bash
# Connect to MySQL directly
docker exec -it ignis_mysql mysql -uroot -p crypto_signals
```

Recommended: connect via Navicat:
- Host: <server_ip>
- Port: 3307

---

## Local Development

```bash
# Environment setup
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# Test run
python -m src.strategies.swing.scheduler --status
python -m src.strategies.swing.scheduler --run-now
```

---

## Troubleshooting

```bash
# View logs
docker-compose logs --tail=100 swing

# Check environment variables
docker exec ignis_swing env | grep -E "MYSQL|TELEGRAM|BINANCE"

# Check MySQL connection
docker exec ignis_swing python3 -c "
from src.trading.position_manager import PositionManager
import os
pm = PositionManager(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', 3306)),
    password=os.getenv('MYSQL_PASSWORD')
)
print('MySQL connection successful')
print('Open positions:', len(pm.get_open_positions()))
"

# Check Binance connection
docker exec ignis_swing python3 -c "
from src.trading.binance_trading_client import BinanceTradingClient
import os
client = BinanceTradingClient(
    os.getenv('BINANCE_TESTNET_API_KEY'),
    os.getenv('BINANCE_TESTNET_API_SECRET'),
    testnet=True, symbol='BTC'
)
print('Binance connection successful')
print('Balance:', client.get_balance())
"
```

---

## Notes

1. After modifying environment variables, you must stop -> rm -> up
2. Do not commit the `.env` file to Git
3. Config priority: config_futures.local.yaml > config_futures.yaml > .env

---

## Related Documents

- [ARCHITECTURE.md](../development/ARCHITECTURE.md) - System architecture
- [SWING_STRATEGY.md](../analysis/SWING_STRATEGY.md) - Strategy definition
