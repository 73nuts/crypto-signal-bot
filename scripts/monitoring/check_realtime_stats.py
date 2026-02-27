#!/usr/bin/env python3
"""
Real-time system monitoring statistics script.

Reads real-time monitoring counters from Redis for debugging and tracing.
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List

import redis


class RealtimeStatsChecker:
    """Real-time system statistics checker."""

    def __init__(self, redis_host: str = 'localhost'):
        """Initialize the checker.

        Args:
            redis_host: Redis host address
        """
        redis_password = os.getenv('REDIS_PASSWORD')
        self.redis_client = redis.Redis(
            host=redis_host,
            port=6379,
            password=redis_password if redis_password else None,
            decode_responses=True
        )

    def get_daily_stats(self, symbol: str, date: str = None) -> Dict:
        """Get statistics for the specified date.

        Args:
            symbol: Asset symbol (ETH/SOL/BNB/BTC)
            date: Date in YYYY-MM-DD format; defaults to today

        Returns:
            {
                'date': '2025-11-06',
                'symbol': 'ETH',
                'pushed': 3,       # PriceMonitor push count
                'consumed': 3,     # SignalWorker consume count
                'queue_length': 0  # Current queue depth
            }
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        pushed_key = f'trigger_pushed_{symbol}_{date}'
        consumed_key = f'trigger_consumed_{symbol}_{date}'
        queue_name = f'trigger_queue_{symbol}'

        pushed = int(self.redis_client.get(pushed_key) or 0)
        consumed = int(self.redis_client.get(consumed_key) or 0)
        queue_length = self.redis_client.llen(queue_name)

        return {
            'date': date,
            'symbol': symbol,
            'pushed': pushed,
            'consumed': consumed,
            'queue_length': queue_length
        }

    def get_recent_stats(self, symbol: str, days: int = 7) -> List[Dict]:
        """Get statistics for the last N days.

        Args:
            symbol: Asset symbol
            days: Number of days

        Returns:
            List of daily stats dicts
        """
        stats = []
        for i in range(days):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            stats.append(self.get_daily_stats(symbol, date))

        return stats

    def check_system_health(self, symbol: str) -> Dict:
        """Check system health status.

        Args:
            symbol: Asset symbol

        Returns:
            {
                'is_healthy': True/False,
                'issues': [list of issues],
                'current_stats': {...}
            }
        """
        stats = self.get_daily_stats(symbol)
        issues = []

        # Check for queue backlog
        if stats['queue_length'] > 10:
            issues.append(f"Queue backlog: {stats['queue_length']} messages pending")

        # Check push/consume mismatch
        diff = stats['pushed'] - stats['consumed']
        if diff > 5:
            issues.append(f"Consume lag: pushed {stats['pushed']}, consumed {stats['consumed']}")

        return {
            'is_healthy': len(issues) == 0,
            'issues': issues,
            'current_stats': stats
        }

    def print_stats(self, symbol: str = None):
        """Print formatted statistics.

        Args:
            symbol: Asset symbol (None = all symbols)
        """
        symbols = [symbol] if symbol else ['ETH', 'SOL', 'BNB', 'BTC']

        print("=" * 60)
        print("Real-time System Monitoring Statistics")
        print("=" * 60)
        print()

        for sym in symbols:
            print(f"[{sym}]")
            print("-" * 60)

            # Today's stats
            today_stats = self.get_daily_stats(sym)
            print(f"Today ({today_stats['date']}):")
            print(f"  Push count (PriceMonitor):    {today_stats['pushed']}")
            print(f"  Consume count (SignalWorker): {today_stats['consumed']}")
            print(f"  Current queue depth:          {today_stats['queue_length']}")

            # Health check
            health = self.check_system_health(sym)
            if health['is_healthy']:
                print("  Health: OK")
            else:
                print("  Health: DEGRADED")
                for issue in health['issues']:
                    print(f"    - {issue}")

            # Last 7 days
            print()
            print("Last 7 days:")
            print(f"  {'Date':<12} {'Pushed':<8} {'Consumed':<10} {'Diff':<8}")
            print(f"  {'-' * 12} {'-' * 8} {'-' * 10} {'-' * 8}")

            recent_stats = self.get_recent_stats(sym, days=7)
            for stats in recent_stats:
                diff = stats['pushed'] - stats['consumed']
                print(f"  {stats['date']:<12} {stats['pushed']:<8} {stats['consumed']:<10} {diff:<8}")

            print()


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Real-time system monitoring statistics')
    parser.add_argument('--symbol', type=str, default=None,
                        help='Asset symbol (ETH/SOL/BNB/BTC); default shows all')
    parser.add_argument('--redis-host', type=str, default='localhost',
                        help='Redis host address (default: localhost)')
    parser.add_argument('--days', type=int, default=7,
                        help='Number of days to query (default: 7)')
    parser.add_argument('--json', action='store_true',
                        help='Output JSON format (for script integration)')

    args = parser.parse_args()

    try:
        checker = RealtimeStatsChecker(redis_host=args.redis_host)

        if args.json:
            # JSON output mode (for script integration)
            import json
            symbols = [args.symbol] if args.symbol else ['ETH', 'SOL', 'BNB', 'BTC']
            results = {}
            for sym in symbols:
                results[sym] = {
                    'today': checker.get_daily_stats(sym),
                    'recent': checker.get_recent_stats(sym, days=args.days),
                    'health': checker.check_system_health(sym)
                }
            print(json.dumps(results, indent=2))
        else:
            # Formatted human-readable output
            checker.print_stats(symbol=args.symbol)

    except redis.exceptions.ConnectionError as e:
        print(f"Redis connection failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Execution failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
