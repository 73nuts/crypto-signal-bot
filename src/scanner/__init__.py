"""
Ignis Scanner - Market Scanning System

Features:
  - Alert Radar: Price/Volume/Funding/OI anomaly detection
  - Daily Market Brief: Daily market overview
  - Heartbeat: Trend approaching/leaving breakout notifications

Usage:
  python -m src.scanner.scheduler --status     # Show status
  python -m src.scanner.scheduler --scan-now   # Run scan immediately
  python -m src.scanner.scheduler --daily-brief # Generate daily brief
  python -m src.scanner.scheduler --heartbeat  # Check heartbeat
  python -m src.scanner.scheduler              # Start scheduler
"""

from .alert_detector import AlertDetector, Alert, AlertType
from .formatter import ScannerFormatter
from .trend_pulse import TrendPulseMonitor, TrendStatus
from .scheduler import ScannerScheduler

__all__ = [
    'AlertDetector', 'Alert', 'AlertType',
    'ScannerFormatter',
    'TrendPulseMonitor', 'TrendStatus',
    'ScannerScheduler'
]
